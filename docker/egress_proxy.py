#!/usr/bin/env python3
"""Small, allowlist-only HTTP and HTTPS CONNECT forward proxy."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import select
import socket
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

MAX_REQUEST_BODY = 16 * 1024 * 1024
HEADER_BODY_TIMEOUT = 30.0
CONNECT_TIMEOUT = 15.0
RELAY_BUFFER_SIZE = 64 * 1024
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

# Transfer-Encoding is removed because http.client decodes chunked responses.
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class DestinationError(ValueError):
    """The request destination is missing or malformed."""


def normalize_host(host: str) -> str:
    """Return a canonical host without resolving it or changing its meaning."""
    if not isinstance(host, str) or not host or host != host.strip():
        raise ValueError("invalid host")
    if any(ord(char) <= 32 or ord(char) == 127 for char in host):
        raise ValueError("invalid host")
    if any(char in host for char in "/\\@?#[]%"):
        raise ValueError("invalid host")

    try:
        return ipaddress.ip_address(host).compressed.lower()
    except ValueError:
        pass

    try:
        normalized = host.encode("idna").decode("ascii").lower()
    except (UnicodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid hostname") from exc
    if len(normalized) > 253 or ":" in normalized:
        raise ValueError("invalid hostname")

    labels = normalized.split(".")
    if any(
        not label
        or len(label) > 63
        or any(not (char.isalnum() or char in "-_") for char in label)
        for label in labels
    ):
        raise ValueError("invalid hostname")
    return normalized


def parse_port(value: str) -> int:
    if not value or not value.isascii() or not value.isdigit():
        raise DestinationError("invalid port")
    port = int(value)
    if not 1 <= port <= 65535:
        raise DestinationError("invalid port")
    return port


def parse_connect_target(authority: str) -> tuple[str, int]:
    """Parse authority-form CONNECT targets; a port is always mandatory."""
    if not authority or any(char.isspace() for char in authority):
        raise DestinationError("invalid CONNECT authority")

    if authority.startswith("["):
        closing = authority.find("]")
        if closing < 0 or not authority[closing + 1 :].startswith(":"):
            raise DestinationError("invalid CONNECT authority")
        raw_host = authority[1:closing]
        if "]" in authority[closing + 1 :] or not raw_host:
            raise DestinationError("invalid CONNECT authority")
        try:
            ipaddress.IPv6Address(raw_host)
        except ValueError as exc:
            raise DestinationError("brackets require an IPv6 address") from exc
        raw_port = authority[closing + 2 :]
    else:
        if authority.count(":") != 1:
            raise DestinationError("CONNECT requires an explicit host and port")
        raw_host, raw_port = authority.rsplit(":", 1)

    try:
        host = normalize_host(raw_host)
    except ValueError as exc:
        raise DestinationError("invalid CONNECT host") from exc
    return host, parse_port(raw_port)


def parse_http_target(target: str) -> tuple[str, int, str, str]:
    """Return host, port, origin-form request target, and Host header value."""
    try:
        parsed = urlsplit(target)
        if parsed.scheme.lower() != "http" or not parsed.netloc:
            raise DestinationError("only absolute-form HTTP targets are supported")
        if parsed.username is not None or parsed.password is not None:
            raise DestinationError("userinfo is not accepted")
        if parsed.hostname is None:
            raise DestinationError("missing host")
        host = normalize_host(parsed.hostname)
        port = parsed.port if parsed.port is not None else 80
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, DestinationError):
            raise
        raise DestinationError("invalid HTTP target") from exc

    if not 1 <= port <= 65535:
        raise DestinationError("invalid HTTP port")
    request_target = parsed.path or "/"
    if parsed.query:
        request_target += "?" + parsed.query

    display_host = f"[{host}]" if ":" in host else host
    host_header = display_host if port == 80 else f"{display_host}:{port}"
    return host, port, request_target, host_header


def load_allowlist(path: str) -> dict[str, frozenset[int]]:
    with open(path, "r", encoding="utf-8") as allowlist_file:
        raw: Any = json.load(allowlist_file)
    if not isinstance(raw, dict):
        raise ValueError("allowlist must be a JSON object")

    allowlist: dict[str, frozenset[int]] = {}
    for raw_host, raw_ports in raw.items():
        if not isinstance(raw_host, str) or not isinstance(raw_ports, list):
            raise ValueError("allowlist entries must map hosts to port arrays")
        host = normalize_host(raw_host)
        if host in allowlist:
            raise ValueError("duplicate normalized allowlist host")
        ports: set[int] = set()
        for port in raw_ports:
            if isinstance(port, bool) or not isinstance(port, int):
                raise ValueError("allowlist ports must be integers")
            if not 1 <= port <= 65535:
                raise ValueError("allowlist port out of range")
            ports.add(port)
        allowlist[host] = frozenset(ports)
    return allowlist


def destination_allowed(
    allowlist: dict[str, frozenset[int]], host: str, port: int
) -> bool:
    return port in allowlist.get(host, frozenset())


def log_decision(event: str, method: str, host: str, port: int | None) -> None:
    """Emit only non-sensitive destination metadata, one event per line."""
    record = {
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "event": event,
        "method": method,
        "host": host,
        "port": port,
    }
    print(json.dumps(record, separators=(",", ":")), flush=True)


def connection_header_tokens(headers: Any) -> set[str]:
    tokens: set[str] = set()
    for value in headers.get_all("Connection", []):
        tokens.update(token.strip().lower() for token in value.split(",") if token.strip())
    return tokens


class EgressProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        allowlist: dict[str, frozenset[int]],
    ) -> None:
        self.allowlist = allowlist
        super().__init__(server_address, EgressProxyHandler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Client resets and malformed traffic are normal for an exposed proxy.
        # In particular, never print a traceback containing request metadata.
        pass


class EgressProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "EgressProxy"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(HEADER_BODY_TIMEOUT)

    def log_message(self, format: str, *args: Any) -> None:
        # BaseHTTPRequestHandler's default access log includes request targets.
        pass

    def _deny(self, host: str = "", port: int | None = None) -> None:
        log_decision("deny", self.command, host, port)
        self._send_simple(403, "Forbidden")

    def _send_simple(self, status: int, message: str) -> None:
        body = (message + "\n").encode("ascii")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_CONNECT(self) -> None:
        try:
            host, port = parse_connect_target(self.path)
        except DestinationError:
            self._deny()
            return

        if not destination_allowed(self.server.allowlist, host, port):
            self._deny(host, port)
            return
        log_decision("allow", self.command, host, port)

        try:
            upstream = socket.create_connection((host, port), CONNECT_TIMEOUT)
        except OSError:
            self._send_simple(502, "Bad Gateway")
            return

        try:
            self.close_connection = True
            self.connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.connection.settimeout(None)
            upstream.settimeout(None)
            self._relay(upstream)
        except (ConnectionError, OSError):
            pass
        finally:
            upstream.close()
            self.close_connection = True

    def _relay(self, upstream: socket.socket) -> None:
        client = self.connection
        readable = {client, upstream}
        while readable:
            ready, _, _ = select.select(list(readable), [], [])
            for source in ready:
                destination = upstream if source is client else client
                try:
                    data = source.recv(RELAY_BUFFER_SIZE)
                except (ConnectionError, OSError):
                    return
                if not data:
                    readable.discard(source)
                    try:
                        destination.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    continue
                try:
                    destination.sendall(data)
                except (ConnectionError, OSError):
                    return

    def _handle_http(self) -> None:
        try:
            host, port, request_target, host_header = parse_http_target(self.path)
        except DestinationError:
            self._deny()
            return

        if not destination_allowed(self.server.allowlist, host, port):
            self._deny(host, port)
            return
        log_decision("allow", self.command, host, port)

        try:
            body = self._read_request_body()
        except ValueError as exc:
            self._send_simple(413 if str(exc) == "body too large" else 400, "Bad Request")
            return
        if body is None:
            self.close_connection = True
            return

        upstream = http.client.HTTPConnection(host, port, timeout=CONNECT_TIMEOUT)
        response_started = False
        try:
            skip = HOP_BY_HOP_HEADERS | connection_header_tokens(self.headers)
            upstream.putrequest(
                self.command,
                request_target,
                skip_host=True,
                skip_accept_encoding=True,
            )
            upstream.putheader("Host", host_header)
            for name, value in self.headers.items():
                lower_name = name.lower()
                if lower_name in skip or lower_name in {"host", "content-length"}:
                    continue
                upstream.putheader(name, value)
            if self.headers.get("Content-Length") is not None:
                upstream.putheader("Content-Length", str(len(body)))
            upstream.putheader("Connection", "close")
            upstream.endheaders(body if body else None)

            response = upstream.getresponse()
            if upstream.sock is not None:
                # A response may be a long-lived, idle event stream.
                upstream.sock.settimeout(None)
            self.close_connection = True
            response_started = True
            self.send_response(response.status, response.reason)
            response_skip = HOP_BY_HOP_HEADERS | connection_header_tokens(response.headers)
            for name, value in response.getheaders():
                if name.lower() in response_skip:
                    continue
                self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()

            if self.command != "HEAD":
                while True:
                    chunk = response.read(RELAY_BUFFER_SIZE)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (OSError, http.client.HTTPException, ValueError):
            if not response_started:
                try:
                    self._send_simple(502, "Bad Gateway")
                except (ConnectionError, OSError):
                    pass
        finally:
            upstream.close()
            self.close_connection = True

    def _read_request_body(self) -> bytes | None:
        if self.headers.get("Transfer-Encoding") is not None:
            raise ValueError("transfer encoding is not supported")
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            return b""
        if len(lengths) != 1:
            raise ValueError("ambiguous content length")
        value = lengths[0]
        if not value or not value.isascii() or not value.isdigit():
            raise ValueError("invalid content length")
        length = int(value)
        if length > MAX_REQUEST_BODY:
            raise ValueError("body too large")
        body = self.rfile.read(length)
        if len(body) != length:
            return None
        return body

    def do_GET(self) -> None:
        self._handle_http()

    def do_POST(self) -> None:
        self._handle_http()

    def do_PUT(self) -> None:
        self._handle_http()

    def do_PATCH(self) -> None:
        self._handle_http()

    def do_DELETE(self) -> None:
        self._handle_http()

    def do_HEAD(self) -> None:
        self._handle_http()

    def do_OPTIONS(self) -> None:
        self._handle_http()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--allowlist", required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        allowlist = load_allowlist(args.allowlist)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"cannot load allowlist: {exc}")

    server = EgressProxyServer(("0.0.0.0", args.port), allowlist)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
