import contextlib
import http.client
import importlib.util
import io
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "docker" / "egress_proxy.py"
SPEC = importlib.util.spec_from_file_location("egress_proxy", MODULE_PATH)
egress_proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(egress_proxy)


class OriginHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def _handle(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append((self.command, self.path, body, dict(self.headers)))
        response_body = b"" if self.command == "HEAD" else self.command.encode() + b":" + body
        self.send_response(200)
        self.send_header("Content-Length", str(len(response_body)))
        self.send_header("X-Origin", "local")
        self.send_header("Connection", "close")
        self.end_headers()
        if response_body:
            self.wfile.write(response_body)
        self.close_connection = True

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle


class RunningServer:
    def __init__(self, server):
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class ParsingTests(unittest.TestCase):
    def test_allowlist_matching_normalizes_hosts_and_ip_addresses(self):
        allowlist = {
            egress_proxy.normalize_host("API.OPENAI.COM"): frozenset({443}),
            egress_proxy.normalize_host("2001:0db8::1"): frozenset({8443}),
        }
        self.assertTrue(egress_proxy.destination_allowed(allowlist, "api.openai.com", 443))
        self.assertFalse(egress_proxy.destination_allowed(allowlist, "api.openai.com", 80))
        self.assertFalse(egress_proxy.destination_allowed(allowlist, "other.openai.com", 443))
        self.assertTrue(egress_proxy.destination_allowed(allowlist, "2001:db8::1", 8443))

    def test_connect_requires_an_explicit_port_and_handles_ipv6(self):
        self.assertEqual(
            egress_proxy.parse_connect_target("Example.COM:443"),
            ("example.com", 443),
        )
        self.assertEqual(
            egress_proxy.parse_connect_target("[2001:db8::1]:8443"),
            ("2001:db8::1", 8443),
        )
        for target in ("example.com", "2001:db8::1", "[example.com]:443", "example.com:0"):
            with self.subTest(target=target), self.assertRaises(egress_proxy.DestinationError):
                egress_proxy.parse_connect_target(target)

    def test_http_target_uses_port_80_and_rejects_userinfo(self):
        host, port, target, host_header = egress_proxy.parse_http_target(
            "http://Example.COM/a/path?q=yes"
        )
        self.assertEqual((host, port, target, host_header), ("example.com", 80, "/a/path?q=yes", "example.com"))
        with self.assertRaises(egress_proxy.DestinationError):
            egress_proxy.parse_http_target("http://user:password@example.com/private")
        with self.assertRaises(egress_proxy.DestinationError):
            egress_proxy.parse_http_target("https://example.com/")


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.origin = RunningServer(ThreadingHTTPServer(("127.0.0.1", 0), OriginHandler))
        self.origin.server.requests = []
        self.origin.server.daemon_threads = True
        allowlist = {"127.0.0.1": frozenset({self.origin.port})}
        self.proxy = RunningServer(
            egress_proxy.EgressProxyServer(("127.0.0.1", 0), allowlist)
        )

    def tearDown(self):
        self.proxy.close()
        self.origin.close()

    def proxy_request(self, method, target, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.port, timeout=3)
        try:
            connection.request(method, target, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def capture_request(self, *args, **kwargs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = self.proxy_request(*args, **kwargs)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        return result, records, output.getvalue()

    def test_denied_http_is_not_forwarded_and_logs_only_destination(self):
        denied_port = self.origin.port + 1
        (status, body, _), records, raw_log = self.capture_request(
            "GET", f"http://127.0.0.1:{denied_port}/private?token=secret"
        )
        self.assertEqual(status, 403)
        self.assertEqual(body, b"Forbidden\n")
        self.assertEqual(self.origin.server.requests, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event"], "deny")
        self.assertEqual(records[0]["method"], "GET")
        self.assertEqual(records[0]["host"], "127.0.0.1")
        self.assertEqual(records[0]["port"], denied_port)
        self.assertEqual(set(records[0]), {"timestamp", "event", "method", "host", "port"})
        self.assertNotIn("private", raw_log)
        self.assertNotIn("secret", raw_log)

    def test_all_common_http_methods_forward_bodies_and_responses(self):
        for method in egress_proxy.HTTP_METHODS:
            with self.subTest(method=method):
                request_body = method.lower().encode()
                (status, body, headers), records, _ = self.capture_request(
                    method,
                    f"http://127.0.0.1:{self.origin.port}/echo",
                    body=request_body,
                    headers={"Content-Length": str(len(request_body))},
                )
                self.assertEqual(status, 200)
                expected = b"" if method == "HEAD" else method.encode() + b":" + request_body
                self.assertEqual(body, expected)
                self.assertEqual(headers["X-Origin"], "local")
                self.assertEqual([record["event"] for record in records], ["allow"])
                self.assertEqual(self.origin.server.requests[-1][0], method)
                self.assertEqual(self.origin.server.requests[-1][2], request_body)

    def test_allowed_log_does_not_contain_path_query_or_sensitive_headers(self):
        secret_path = "/credentials?query=very-secret"
        (_, _, _), records, raw_log = self.capture_request(
            "POST",
            f"http://127.0.0.1:{self.origin.port}{secret_path}",
            body=b"private request body",
            headers={
                "Authorization": "Bearer authorization-secret",
                "Cookie": "session=cookie-secret",
            },
        )
        self.assertEqual(records[0]["event"], "allow")
        self.assertNotIn("credentials", raw_log)
        self.assertNotIn("very-secret", raw_log)
        self.assertNotIn("authorization-secret", raw_log)
        self.assertNotIn("cookie-secret", raw_log)
        self.assertNotIn("private request body", raw_log)
        origin_request = self.origin.server.requests[-1]
        self.assertEqual(origin_request[1], secret_path)
        self.assertEqual(origin_request[3]["Authorization"], "Bearer authorization-secret")

    def test_denied_connect_returns_403_without_contact(self):
        denied_port = self.origin.port + 1
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with socket.create_connection(("127.0.0.1", self.proxy.port), timeout=3) as client:
                client.sendall(f"CONNECT 127.0.0.1:{denied_port} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
                response = client.recv(4096)
        self.assertTrue(response.startswith(b"HTTP/1.1 403"))
        self.assertEqual(self.origin.server.requests, [])
        record = json.loads(output.getvalue())
        self.assertEqual((record["event"], record["method"], record["port"]), ("deny", "CONNECT", denied_port))

    def test_allowed_connect_relays_bidirectional_traffic(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with socket.create_connection(("127.0.0.1", self.proxy.port), timeout=3) as client:
                client.settimeout(3)
                client.sendall(
                    f"CONNECT 127.0.0.1:{self.origin.port} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self.origin.port}\r\n\r\n".encode()
                )
                response = self.read_until(client, b"\r\n\r\n")
                self.assertTrue(response.startswith(b"HTTP/1.1 200"))
                client.sendall(b"GET /through-tunnel HTTP/1.1\r\nHost: local\r\nConnection: close\r\n\r\n")
                tunneled = self.read_until_close(client)
        self.assertIn(b"200 OK", tunneled)
        self.assertTrue(tunneled.endswith(b"GET:"))
        self.assertEqual(self.origin.server.requests[-1][1], "/through-tunnel")
        self.assertEqual(json.loads(output.getvalue())["event"], "allow")

    @staticmethod
    def read_until(sock, marker):
        data = bytearray()
        while marker not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    @staticmethod
    def read_until_close(sock):
        data = bytearray()
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return bytes(data)
            data.extend(chunk)


if __name__ == "__main__":
    unittest.main()
