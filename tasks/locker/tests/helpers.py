"""Black-box helpers for the Locker hidden suite.

Stdlib only. Nothing in this module imports the candidate or the reference
implementation; every expected value is computed from the public contract.
"""

from __future__ import annotations

import email.utils
import hashlib
import http.client
import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator


class CaseFailure(AssertionError):
    """Raised when a behavioural assertion fails."""


def fail(message: str) -> None:
    raise CaseFailure(message)


def check(condition: object, message: str) -> None:
    if not condition:
        fail(message)


def md5_etag(data: bytes) -> str:
    return '"' + hashlib.md5(data).hexdigest() + '"'


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


class Server:
    """Owns one locker server process and its private data directory."""

    def __init__(self, binary: str, root: str | None = None, env: dict[str, str] | None = None) -> None:
        self.binary = binary
        self.root = root or tempfile.mkdtemp(prefix="locker-test-")
        self.env = env
        self.data = os.path.join(self.root, "data")
        self.log_dir = os.path.join(self.root, "logs")
        os.makedirs(self.log_dir, exist_ok=True)
        self.proc: subprocess.Popen[bytes] | None = None
        self.log_handle = None
        self.port: int | None = None

    @property
    def endpoint(self) -> str:
        if self.port is None:
            fail("server has not started")
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 15.0) -> "Server":
        if self.proc is not None and self.proc.poll() is None:
            fail("server is already running")
        os.makedirs(self.data, exist_ok=True)
        self.port = free_port()
        log_path = os.path.join(self.log_dir, "server.log")
        self.log_handle = open(log_path, "ab")
        self.proc = subprocess.Popen(
            [
                self.binary,
                "server",
                "--data",
                self.data,
                "--addr",
                f"127.0.0.1:{self.port}",
            ],
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            env=None if self.env is None else {**os.environ, **self.env},
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                fail(f"server exited during startup: {self.log_tail()}")
            try:
                response = request(self.port, "GET", "/health", timeout=1.0)
            except OSError:
                pass
            else:
                if response.status == 200 and response.body.strip() == b"ok":
                    return self
            time.sleep(0.03)
        fail(f"server did not become healthy: {self.log_tail()}")

    def log_tail(self, limit: int = 4000) -> str:
        path = os.path.join(self.log_dir, "server.log")
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            return ""
        return data[-limit:].decode("utf-8", "replace")

    def stop(self, kill: bool = False) -> None:
        proc = self.proc
        self.proc = None
        if proc is not None and proc.poll() is None:
            if kill:
                proc.kill()
            else:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

    def restart(self, kill: bool = False) -> "Server":
        self.stop(kill=kill)
        return self.start()

    def rss_high_water(self) -> int | None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return None
        if os.path.exists(f"/proc/{proc.pid}/status"):
            try:
                with open(f"/proc/{proc.pid}/status", encoding="utf-8") as handle:
                    for line in handle:
                        if line.startswith("VmHWM:"):
                            return int(line.split()[1]) * 1024
            except (OSError, ValueError):
                return None
        # Local authoring fallback on macOS. The pinned evaluation image is
        # Linux and uses VmHWM above.
        if platform.system() == "Darwin":
            try:
                output = subprocess.check_output(
                    ["ps", "-o", "rss=", "-p", str(proc.pid)], text=True
                ).strip()
                return int(output) * 1024 if output else None
            except (OSError, ValueError, subprocess.CalledProcessError):
                return None
        return None

    def cli(self, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.binary, "--endpoint", self.endpoint, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def close(self) -> None:
        self.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "Server":
        try:
            return self.start()
        except BaseException:
            self.close()
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def request(
    port: int,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> Response:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        raw = connection.getresponse()
        data = raw.read()
        return Response(
            raw.status,
            {key.lower(): value for key, value in raw.getheaders()},
            data,
        )
    finally:
        connection.close()


class PersistentClient:
    """One HTTP/1.1 connection owned by a worker.

    Persistent clients avoid ephemeral-port exhaustion in concurrency cases
    that issue thousands of requests. A dropped keep-alive connection is
    re-opened once; the operations used by the suite are idempotent.
    """

    def __init__(self, port: int, timeout: float = 60.0) -> None:
        self.port = port
        self.timeout = timeout
        self.connection: http.client.HTTPConnection | None = None

    def _connect(self) -> http.client.HTTPConnection:
        if self.connection is None:
            self.connection = http.client.HTTPConnection(
                "127.0.0.1", self.port, timeout=self.timeout
            )
        return self.connection

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        for attempt in (0, 1):
            connection = self._connect()
            try:
                connection.request(method, path, body=body, headers=headers or {})
                raw = connection.getresponse()
                data = raw.read()
                return Response(
                    raw.status,
                    {key.lower(): value for key, value in raw.getheaders()},
                    data,
                )
            except (
                http.client.RemoteDisconnected,
                ConnectionResetError,
                BrokenPipeError,
            ):
                self.close()
                if attempt:
                    raise
        raise AssertionError("unreachable")

    def close(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            finally:
                self.connection = None


def stream_request(
    port: int,
    method: str,
    path: str,
    chunks: Iterable[bytes],
    length: int,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> Response:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.putrequest(method, path)
        connection.putheader("Content-Length", str(length))
        for key, value in (headers or {}).items():
            connection.putheader(key, value)
        connection.endheaders()
        for chunk in chunks:
            connection.send(chunk)
        raw = connection.getresponse()
        data = raw.read()
        return Response(
            raw.status,
            {key.lower(): value for key, value in raw.getheaders()},
            data,
        )
    finally:
        connection.close()


def request_bytes(
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    host: str = "127.0.0.1",
) -> bytes:
    lines = [f"{method} {path} HTTP/1.1", f"Host: {host}"]
    for key, value in (headers or {}).items():
        lines.append(f"{key}: {value}")
    if body or method in ("PUT", "POST"):
        lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def _read_until(sock: socket.socket, marker: bytes, deadline: float) -> bytes:
    data = b""
    while marker not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaseFailure("timed out while reading response headers")
        sock.settimeout(remaining)
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("connection closed before response headers")
        data += chunk
    return data


def read_raw_response(
    sock: socket.socket, deadline: float
) -> tuple[int, dict[str, str], bytes]:
    buf = _read_until(sock, b"\r\n\r\n", deadline)
    head, rest = buf.split(b"\r\n\r\n", 1)
    lines = head.split(b"\r\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError) as exc:
        raise CaseFailure(f"malformed response status line: {lines[0]!r}") from exc
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" not in line:
            continue
        key, value = line.split(b":", 1)
        headers[key.decode("latin-1").strip().lower()] = value.decode(
            "latin-1"
        ).strip()
    if status == 100:
        return status, headers, b""
    if 100 <= status < 200:
        return status, headers, b""

    length_header = headers.get("content-length")
    if length_header is not None:
        try:
            length = int(length_header)
        except ValueError as exc:
            raise CaseFailure(f"invalid Content-Length: {length_header!r}") from exc
        body = rest
        while len(body) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CaseFailure("timed out while reading response body")
            sock.settimeout(remaining)
            chunk = sock.recv(65536)
            if not chunk:
                raise CaseFailure("connection closed before response body")
            body += chunk
        return status, headers, body[:length]

    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = rest
        while b"\r\n" not in body:
            body += sock.recv(65536)
        output = b""
        while True:
            line, body = body.split(b"\r\n", 1)
            size = int(line.split(b";")[0], 16)
            while len(body) < size + 2:
                body += sock.recv(65536)
            if size == 0:
                return status, headers, output
            output += body[:size]
            body = body[size + 2 :]

    body = rest
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaseFailure("timed out while reading response body")
        sock.settimeout(remaining)
        chunk = sock.recv(65536)
        if not chunk:
            return status, headers, body
        body += chunk


def raw_exchange(
    port: int, payload: bytes, timeout: float = 15.0
) -> tuple[int, dict[str, str], bytes]:
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.sendall(payload)
        return read_raw_response(sock, time.monotonic() + timeout)
    finally:
        sock.close()


def raw_half_close(
    port: int, payload: bytes, timeout: float = 15.0
) -> tuple[int | None, str]:
    """Send a request, half-close our write side and observe the outcome."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        try:
            status, _headers, _body = read_raw_response(
                sock, time.monotonic() + timeout
            )
            return status, ""
        except (ConnectionError, CaseFailure, OSError) as exc:
            return None, str(exc)
    finally:
        sock.close()


class GatedUpload:
    """A PUT that has started body input but is deliberately unfinished."""

    def __init__(
        self,
        port: int,
        path: str,
        length: int,
        content_type: str = "application/octet-stream",
        timeout: float = 10.0,
    ) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        lines = [
            f"PUT {path} HTTP/1.1",
            f"Host: 127.0.0.1:{port}",
            f"Content-Length: {length}",
            f"Content-Type: {content_type}",
            "Expect: 100-continue",
            "Connection: close",
        ]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        self.timeout = timeout
        self.closed = False

    def wait_interim(self, timeout: float = 10.0) -> None:
        status, _headers, _body = read_raw_response(
            self.sock, time.monotonic() + timeout
        )
        if status != 100:
            fail(f"expected interim 100 Continue, got {status}")

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def finish(self, rest: bytes = b"", timeout: float = 60.0) -> Response:
        try:
            if rest:
                self.sock.sendall(rest)
            status, headers, body = read_raw_response(
                self.sock, time.monotonic() + timeout
            )
            return Response(status, headers, body)
        finally:
            self.close()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.sock.close()
            except OSError:
                pass


class DeterministicBody:
    """Deterministic streamed fixture that never allocates the whole body."""

    def __init__(self, total: int, seed: int = 1, chunk_size: int = 1 << 20) -> None:
        self.total = total
        self.seed = seed
        self.chunk_size = chunk_size
        self.pattern = bytes((seed + index) % 251 for index in range(65536))
        self._digest: str | None = None

    def block(self, offset: int, length: int) -> bytes:
        start = offset % len(self.pattern)
        reps = (start + length + len(self.pattern) - 1) // len(self.pattern)
        return (self.pattern * reps)[start : start + length]

    def chunks(self) -> Iterator[bytes]:
        offset = 0
        while offset < self.total:
            length = min(self.chunk_size, self.total - offset)
            yield self.block(offset, length)
            offset += length

    def digest(self) -> str:
        if self._digest is None:
            digest = hashlib.md5()
            for chunk in self.chunks():
                digest.update(chunk)
            self._digest = digest.hexdigest()
        return self._digest

    def etag(self) -> str:
        return '"' + self.digest() + '"'

    def slice(self, start: int, end: int) -> bytes:
        return self.block(start, end - start)


# ---------------------------------------------------------------------------
# URI, XML and listing helpers


def quote_bucket(bucket: str) -> str:
    return urllib.parse.quote(bucket, safe="")


def quote_key(key: str) -> str:
    return urllib.parse.quote(key, safe="/")


def object_path(bucket: str, key: str) -> str:
    return "/" + quote_bucket(bucket) + "/" + quote_key(key)


def bucket_path(bucket: str) -> str:
    return "/" + quote_bucket(bucket)


def parse_xml(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise CaseFailure(f"invalid XML: {exc}: {data[:300]!r}") from exc


def text(element: ET.Element, path: str, default: str | None = None) -> str:
    found = element.find(path)
    if found is None or found.text is None:
        if default is not None:
            return default
        fail(f"missing XML element {path}")
    return found.text


@dataclass
class ListingPage:
    contents: list[tuple[str, int, str]]
    prefixes: list[str]
    max_keys: int
    key_count: int
    is_truncated: bool
    next_token: str


def list_page(
    port: int,
    bucket: str,
    prefix: str = "",
    delimiter: str = "",
    max_keys: int | None = None,
    token: str | None = None,
    timeout: float = 60.0,
) -> ListingPage:
    query = [("list-type", "2"), ("prefix", prefix)]
    if delimiter:
        query.append(("delimiter", delimiter))
    if max_keys is not None:
        query.append(("max-keys", str(max_keys)))
    if token:
        query.append(("continuation-token", token))
    path = bucket_path(bucket) + "?" + urllib.parse.urlencode(query)
    response = request(port, "GET", path, timeout=timeout)
    check(response.status == 200, f"list status {response.status}: {response.body[:300]!r}")
    root = parse_xml(response.body)
    check(root.tag == "ListBucketResult", f"unexpected list root {root.tag}")
    contents = []
    for item in root.findall("Contents"):
        contents.append(
            (
                text(item, "Key"),
                int(text(item, "Size")),
                text(item, "ETag"),
            )
        )
    prefixes = [text(item, "Prefix") for item in root.findall("CommonPrefixes")]
    return ListingPage(
        contents=contents,
        prefixes=prefixes,
        max_keys=int(text(root, "MaxKeys", "0")),
        key_count=int(text(root, "KeyCount", "0")),
        is_truncated=text(root, "IsTruncated", "false").lower() == "true",
        next_token=text(root, "NextContinuationToken", ""),
    )


def collect_listing(
    port: int,
    bucket: str,
    prefix: str = "",
    delimiter: str = "",
    max_keys: int = 1000,
    timeout: float = 60.0,
) -> tuple[list[tuple[str, int, str]], list[str]]:
    contents: list[tuple[str, int, str]] = []
    prefixes: list[str] = []
    token = None
    for _ in range(1000):
        page = list_page(
            port,
            bucket,
            prefix=prefix,
            delimiter=delimiter,
            max_keys=max_keys,
            token=token,
            timeout=timeout,
        )
        contents.extend(page.contents)
        prefixes.extend(page.prefixes)
        if not page.is_truncated:
            return contents, prefixes
        check(bool(page.next_token), "truncated listing has no NextContinuationToken")
        token = page.next_token
    fail("listing did not terminate")


# ---------------------------------------------------------------------------
# Time and high-level object helpers


def parse_http_date(value: str) -> datetime:
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed is None:
        fail(f"invalid HTTP date {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_http_date(value: datetime) -> str:
    return email.utils.format_datetime(value.astimezone(timezone.utc), usegmt=True)


def put_object(
    port: int,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str | None = None,
    timeout: float = 60.0,
) -> Response:
    headers = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    return request(
        port,
        "PUT",
        object_path(bucket, key),
        body=body,
        headers=headers,
        timeout=timeout,
    )


def get_object(
    port: int,
    bucket: str,
    key: str,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> Response:
    return request(port, "GET", object_path(bucket, key), headers=headers, timeout=timeout)


def head_object(
    port: int,
    bucket: str,
    key: str,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> Response:
    return request(port, "HEAD", object_path(bucket, key), headers=headers, timeout=timeout)


@dataclass
class StreamDownload:
    status: int
    headers: dict[str, str]
    length: int
    digest: str


def stream_download(
    port: int,
    path: str,
    headers: dict[str, str] | None = None,
    timeout: float = 180.0,
) -> StreamDownload:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", path, headers=headers or {})
        raw = connection.getresponse()
        digest = hashlib.md5()
        length = 0
        while True:
            chunk = raw.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            length += len(chunk)
        return StreamDownload(
            raw.status,
            {key.lower(): value for key, value in raw.getheaders()},
            length,
            digest.hexdigest(),
        )
    finally:
        connection.close()


def create_bucket(port: int, bucket: str) -> Response:
    return request(port, "PUT", bucket_path(bucket))


def delete_bucket(port: int, bucket: str) -> Response:
    return request(port, "DELETE", bucket_path(bucket))


def sleep_until(deadline: float, description: str) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        fail(f"deadline expired: {description}")
    time.sleep(min(remaining, 0.01))


def deadline(seconds: float) -> float:
    return time.monotonic() + seconds


def seconds_between(start: float, end: float) -> float:
    return max(0.0, end - start)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def date_offset(value: datetime, seconds: int) -> datetime:
    return value + timedelta(seconds=seconds)
