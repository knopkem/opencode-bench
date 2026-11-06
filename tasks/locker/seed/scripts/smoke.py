#!/usr/bin/env python3
"""Public Locker smoke checks.

This is a development aid, not the hidden grading suite. It starts the seed
server on a free loopback port and reports a mix of passing and failing checks.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Client:
    def __init__(self, port: int) -> None:
        self.port = port

    def request(self, method: str, path: str, body: bytes | None = None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()


def check(name: str, condition: bool, detail: str = "") -> bool:
    print(("PASS " if condition else "FAIL ") + name + ("" if not detail else f": {detail}"))
    return condition


def main() -> int:
    binary = ROOT / "bin" / "locker"
    if not binary.exists():
        print(f"missing {binary}; run: go build -trimpath -o bin/locker ./cmd/locker")
        return 2
    data = tempfile.mkdtemp(prefix="locker-smoke-")
    port = free_port()
    log = open(os.path.join(data, "server.log"), "wb")
    proc = subprocess.Popen(
        [str(binary), "server", "--data", os.path.join(data, "store"), "--addr", f"127.0.0.1:{port}"],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    client = Client(port)
    failures = 0
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if client.request("GET", "/health")[0] == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            print("server did not start")
            return 1

        failures += not check("health", client.request("GET", "/health")[0] == 200)
        failures += not check("create bucket", client.request("PUT", "/smoke")[0] == 200)

        body = b"smoke object"
        status, headers, _ = client.request(
            "PUT", "/smoke/key", body, {"Content-Type": "text/plain"}
        )
        failures += not check("put object", status == 200)
        status, headers, returned = client.request("GET", "/smoke/key")
        failures += not check("get object", status == 200 and returned == body)
        failures += not check(
            "etag",
            headers.get("ETag") == '"' + hashlib.md5(body).hexdigest() + '"',
            headers.get("ETag", ""),
        )
        status, _, head_body = client.request("HEAD", "/smoke/key")
        failures += not check("head has no body", status == 200 and head_body == b"")

        status, headers, returned = client.request(
            "GET", "/smoke/key", headers={"Range": "bytes=0-4"}
        )
        failures += not check(
            "range request",
            status == 206 and returned == body[:5],
            f"status={status} body={returned!r}",
        )

        status, _, returned = client.request(
            "GET", "/smoke/key", headers={"If-Match": '"not-the-etag"'}
        )
        failures += not check("conditional Etag", status == 412, f"status={status}")

        status, _, returned = client.request("POST", "/smoke/key?uploads")
        failures += not check("initiate multipart", status == 200, f"status={status}")

        status, _, returned = client.request(
            "GET", "/smoke?list-type=2&max-keys=1"
        )
        truncated = b"<IsTruncated>true</IsTruncated>" in returned
        failures += not check("listing pagination", status == 200 and truncated)

        local = os.path.join(data, "local.bin")
        with open(local, "wb") as handle:
            handle.write(body)
        cli = subprocess.run(
            [str(binary), "--endpoint", f"http://127.0.0.1:{port}", "ls", "s3://smoke"],
            capture_output=True,
            text=True,
        )
        lines = [json.loads(line) for line in cli.stdout.splitlines() if line]
        failures += not check(
            "cli ls",
            cli.returncode == 0 and lines and lines[0]["key"] == "key",
            cli.stderr,
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        shutil.rmtree(data, ignore_errors=True)
    print(f"{failures} public check(s) fail")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
