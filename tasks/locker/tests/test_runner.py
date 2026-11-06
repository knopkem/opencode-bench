"""Hidden black-box families for Locker.

Every case is scored independently. A setup failure fails that case; it never
turns into a pass or shrinks the manifest denominator. The runner is stdlib
only and never imports candidate or reference packages.
"""

from __future__ import annotations

import json
import os
import random
import socket
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import xml.sax.saxutils
from datetime import timedelta
from typing import Callable

import helpers as h
import history_check
import history_gen
from helpers import CaseFailure, Response, check, fail
from oracles import effective_listing


Limits = dict[str, int]
CaseFunc = Callable[[h.Server, Limits], None]


# ---------------------------------------------------------------------------
# Small assertion and multipart helpers


def log(message: str) -> None:
    print(f"  {message}", file=sys.stderr)


def expect(response: Response, status: int, label: str) -> Response:
    check(
        response.status == status,
        f"{label}: expected {status}, got {response.status}: {response.body[:160]!r}",
    )
    return response


def expect_object(
    response: Response, body: bytes, content_type: str, label: str
) -> Response:
    expect(response, 200, label)
    check(response.body == body, f"{label}: body mismatch")
    check(response.header("etag") == h.md5_etag(body), f"{label}: ETag mismatch")
    check(
        response.header("content-length") == str(len(body)),
        f"{label}: Content-Length mismatch",
    )
    if content_type:
        check(
            response.header("content-type") == content_type,
            f"{label}: Content-Type {response.header('content-type')!r} != {content_type!r}",
        )
    check(bool(response.header("last-modified")), f"{label}: missing Last-Modified")
    h.parse_http_date(response.header("last-modified"))
    return response


def make_buckets(port: int, *names: str) -> None:
    for name in names:
        expect(h.create_bucket(port, name), 200, f"create bucket {name}")


def seed_objects(port: int, bucket: str, keys: list[str]) -> dict[str, bytes]:
    bodies: dict[str, bytes] = {}
    for key in keys:
        body = f"value:{key}".encode("utf-8")
        bodies[key] = body
        expect(h.put_object(port, bucket, key, body), 200, f"seed {key}")
    return bodies


def upload_body(server: h.Server, name: str, data: bytes) -> str:
    path = os.path.join(server.root, name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def multipart_path(
    bucket: str, key: str, upload_id: str | None = None, part: int | None = None
) -> str:
    path = h.object_path(bucket, key)
    query = []
    if upload_id is not None:
        query.append(("uploadId", upload_id))
    if part is not None:
        query.append(("partNumber", str(part)))
    if query:
        path += "?" + urllib.parse.urlencode(query)
    return path


def initiate(port: int, bucket: str, key: str, content_type: str) -> str:
    response = expect(
        h.request(
            port,
            "POST",
            h.object_path(bucket, key) + "?uploads",
            headers={"Content-Type": content_type},
        ),
        200,
        "initiate multipart",
    )
    root = h.parse_xml(response.body)
    check(root.tag == "InitiateMultipartUploadResult", "bad initiate XML root")
    check(h.text(root, "Bucket") == bucket, "initiate echoed wrong bucket")
    check(h.text(root, "Key") == key, "initiate echoed wrong key")
    upload_id = h.text(root, "UploadId")
    check(bool(upload_id), "initiate returned empty UploadId")
    return upload_id


def upload_part(
    port: int,
    bucket: str,
    key: str,
    upload_id: str,
    number: int,
    body: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    response = expect(
        h.request(
            port,
            "PUT",
            multipart_path(bucket, key, upload_id, number),
            body=body,
            headers={"Content-Type": content_type},
        ),
        200,
        f"upload part {number}",
    )
    etag = response.header("etag")
    check(etag == h.md5_etag(body), f"part {number} ETag mismatch")
    return etag


def list_parts(port: int, bucket: str, key: str, upload_id: str) -> list[dict]:
    response = expect(
        h.request(port, "GET", multipart_path(bucket, key, upload_id)),
        200,
        "list parts",
    )
    root = h.parse_xml(response.body)
    check(root.tag == "ListPartsResult", "bad ListPartsResult root")
    parts = []
    for element in root.findall("Part"):
        parts.append(
            {
                "number": int(h.text(element, "PartNumber")),
                "etag": h.text(element, "ETag"),
                "size": int(h.text(element, "Size")),
            }
        )
    return parts


def completion_xml(parts: list[tuple[int, str]]) -> bytes:
    chunks = ["<CompleteMultipartUpload>"]
    for number, etag in parts:
        chunks.append(
            "<Part><PartNumber>"
            + str(number)
            + "</PartNumber><ETag>"
            + xml.sax.saxutils.escape(etag)
            + "</ETag></Part>"
        )
    chunks.append("</CompleteMultipartUpload>")
    return "".join(chunks).encode("utf-8")


def complete_upload(
    port: int,
    bucket: str,
    key: str,
    upload_id: str,
    parts: list[tuple[int, str]],
    xml_body: bytes | None = None,
) -> Response:
    body = completion_xml(parts) if xml_body is None else xml_body
    return h.request(
        port,
        "POST",
        multipart_path(bucket, key, upload_id),
        body=body,
        headers={"Content-Type": "application/xml"},
    )


def abort_upload(port: int, bucket: str, key: str, upload_id: str) -> Response:
    return h.request(port, "DELETE", multipart_path(bucket, key, upload_id))


def raw_put_payload(bucket: str, key: str, body: bytes, content_type: str) -> bytes:
    return h.request_bytes(
        "PUT",
        h.object_path(bucket, key),
        headers={"Content-Type": content_type},
        body=body,
    )


# ---------------------------------------------------------------------------
# Batch helpers


BatchOp = tuple


def batch_xml(ops: list[BatchOp]) -> bytes:
    parts = ["<Batch>"]
    for op in ops:
        kind = op[0]
        if kind in ("copy", "rename"):
            _, src, dst, if_match = op
            tag = "Copy" if kind == "copy" else "Rename"
            parts.append(f"<{tag}><Source>{xml.sax.saxutils.escape(src)}</Source><Target>{xml.sax.saxutils.escape(dst)}</Target>")
        else:
            _, key, if_match = op
            tag = "Delete"
            parts.append(f"<Delete><Key>{xml.sax.saxutils.escape(key)}</Key>")
        if if_match is not None:
            parts.append(f"<IfMatch>{xml.sax.saxutils.escape(if_match)}</IfMatch>")
        parts.append(f"</{tag}>")
    parts.append("</Batch>")
    return "".join(parts).encode("utf-8")


def batch(port: int, bucket: str, ops: list[BatchOp], *, timeout: float = 60.0) -> Response:
    return h.request(
        port,
        "POST",
        h.bucket_path(bucket) + "?batch",
        body=batch_xml(ops),
        headers={"Content-Type": "application/xml"},
        timeout=timeout,
    )


def raw_batch(port: int, path: str, body: bytes, *, timeout: float = 60.0) -> Response:
    return h.request(
        port,
        "POST",
        path,
        body=body,
        headers={"Content-Type": "application/xml"},
        timeout=timeout,
    )


def expect_error(response: Response, status: int, code: str, label: str) -> None:
    expect(response, status, label)
    root = h.parse_xml(response.body)
    check(
        root.tag == "Error" and h.text(root, "Code") == code,
        f"{label}: expected <Error><Code>{code}</Code>, got {response.body[:200]!r}",
    )


def bucket_snapshot(port: int, bucket: str) -> dict[str, tuple[bytes, str, str]]:
    """key -> (body, etag, content-type) for every listed key."""
    contents, _prefixes = h.collect_listing(port, bucket)
    out = {}
    for key, size, etag in contents:
        response = expect(h.get_object(port, bucket, key), 200, f"snapshot GET {key}")
        check(
            len(response.body) == size and response.header("etag") == etag,
            f"snapshot listing/GET disagree for {key}",
        )
        out[key] = (response.body, etag, response.header("content-type"))
    return out


def expect_absent(port: int, bucket: str, *keys: str, label: str) -> None:
    for key in keys:
        expect(h.get_object(port, bucket, key), 404, f"{label}: {key} should be absent")


def expect_present(port: int, bucket: str, key: str, body: bytes, etag: str, content_type: str, label: str) -> Response:
    response = expect(h.get_object(port, bucket, key), 200, label)
    check(response.body == body, f"{label}: body mismatch")
    check(response.header("etag") == etag, f"{label}: ETag mismatch")
    check(response.header("content-length") == str(len(body)), f"{label}: Content-Length mismatch")
    check(response.header("content-type") == content_type, f"{label}: Content-Type mismatch")
    return response


def check_version(
    port: int,
    bucket: str,
    key: str,
    versions: list[tuple[bytes, str]],
    label: str,
) -> bytes:
    response = expect(h.get_object(port, bucket, key), 200, label)
    for body, content_type in versions:
        if (
            response.body == body
            and response.header("etag") == h.md5_etag(body)
            and response.header("content-length") == str(len(body))
            and response.header("content-type") == content_type
        ):
            return body
    fail(f"{label}: response is not one complete expected version")


# ---------------------------------------------------------------------------
# P0 families


def p0_cli_roundtrip(server: h.Server, limits: Limits) -> None:
    expect_result(server.cli("mb", "s3://cli-roundtrip"), 0, "mb")
    data = b"cli round trip \x00\x01\x02 bytes"
    local = upload_body(server, "upload.bin", data)
    result = server.cli("cp", local, "s3://cli-roundtrip/dir/key.bin")
    check(result.returncode == 0, f"cp upload failed: {result.stderr}")
    check(result.stdout == "", "cp upload wrote to stdout")
    download = os.path.join(server.root, "download.bin")
    result = server.cli("cp", "s3://cli-roundtrip/dir/key.bin", download)
    check(result.returncode == 0, f"cp download failed: {result.stderr}")
    check(result.stdout == "", "cp download wrote to stdout")
    with open(download, "rb") as handle:
        check(handle.read() == data, "download bytes differ")


def p0_cli_list_cleanup(server: h.Server, limits: Limits) -> None:
    expect_result(server.cli("mb", "s3://cli-list"), 0, "mb")
    bodies = {"b/one": b"one", "a/two": b"two"}
    for key, data in bodies.items():
        local = upload_body(server, "list-" + key.replace("/", "-"), data)
        expect_result(server.cli("cp", local, "s3://cli-list/" + key), 0, "cp")
    result = server.cli("ls", "s3://cli-list")
    check(result.returncode == 0, f"ls failed: {result.stderr}")
    lines = [json.loads(line) for line in result.stdout.splitlines() if line]
    check([line["key"] for line in lines] == sorted(bodies), "ls keys/order wrong")
    for line in lines:
        body = bodies[line["key"]]
        check(line["size"] == len(body), "ls size wrong")
        check(line["etag"] == h.md5_etag(body), "ls etag wrong")
    for key in bodies:
        expect_result(server.cli("rm", "s3://cli-list/" + key), 0, "rm")
    result = server.cli("ls", "s3://cli-list")
    check(result.returncode == 0 and result.stdout == "", "ls after rm not empty")
    expect_result(server.cli("rb", "s3://cli-list"), 0, "rb")


def p0_cli_errors(server: h.Server, limits: Limits) -> None:
    expect_result(server.cli("mb", "s3://cli-errors"), 0, "mb")
    result = server.cli("cp", "s3://cli-errors/missing", os.path.join(server.root, "no"))
    check(result.returncode == 1, f"missing download exit {result.returncode}")
    check(result.stderr.strip() != "", "missing download stderr empty")
    check(result.stdout == "", "missing download wrote stdout")
    for args in (("cp",), ("nonsense",), ("cp", "a", "b")):
        result = server.cli(*args)
        check(result.returncode == 2, f"{args} exit {result.returncode}, expected 2")
        check(result.stderr.strip() != "", f"{args} stderr empty")


def expect_result(result, code: int, label: str) -> None:
    check(result.returncode == code, f"{label}: exit {result.returncode}: {result.stderr}")
    if code == 0:
        check(result.stdout == "", f"{label}: unexpected stdout {result.stdout!r}")


def p0_http_crud(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "crud")
    body = b"hello locker"
    expect(h.put_object(port, "crud", "key", body, "text/plain"), 200, "put")
    expect_object(h.get_object(port, "crud", "key"), body, "text/plain", "get")
    head = expect(h.head_object(port, "crud", "key"), 200, "head")
    check(head.body == b"", "HEAD returned a body")
    check(head.header("etag") == h.md5_etag(body), "HEAD ETag mismatch")
    check(head.header("content-length") == str(len(body)), "HEAD length mismatch")
    expect(h.request(port, "DELETE", h.object_path("crud", "key")), 204, "delete")
    expect(h.get_object(port, "crud", "key"), 404, "get deleted")
    expect(h.request(port, "DELETE", h.object_path("crud", "key")), 204, "delete absent key")


def p0_http_headers(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "headers")
    body = b"header body"
    expect(h.put_object(port, "headers", "k", body, "text/plain; charset=utf-8"), 200, "put")
    get = expect_object(
        h.get_object(port, "headers", "k"), body, "text/plain; charset=utf-8", "get"
    )
    head = expect(h.head_object(port, "headers", "k"), 200, "head")
    check(head.body == b"", "HEAD returned a body")
    for name in ("etag", "content-length", "content-type", "last-modified"):
        check(head.header(name) == get.header(name), f"HEAD/GET {name} differ")


def p0_http_empty_missing(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "empty")
    expect(h.put_object(port, "empty", "zero", b""), 200, "put empty")
    response = expect(h.get_object(port, "empty", "zero"), 200, "get empty")
    check(response.body == b"", "empty body not empty")
    check(response.header("etag") == h.md5_etag(b""), "empty ETag mismatch")
    check(response.header("content-length") == "0", "empty length mismatch")
    check(h.head_object(port, "empty", "zero").body == b"", "HEAD empty body")
    for response, label in (
        (h.put_object(port, "absent", "k", b"x"), "put absent bucket"),
        (h.get_object(port, "empty", "missing"), "get absent key"),
        (h.get_object(port, "absent", "k"), "get absent bucket"),
    ):
        expect(response, 404, label)


def p0_restart_objects(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "persist")
    objects = {
        "café/ключ/🙂": "unicode".encode("utf-8"),
        "nested/a/b": b"nested",
        "a": b"single",
    }
    for key, body in objects.items():
        expect(h.put_object(port, "persist", key, body), 200, "put")
    server.restart()
    port = server.port
    assert port is not None
    for key, body in objects.items():
        expect_object(h.get_object(port, "persist", key), body, "", "get after restart")
    names = {item[0] for item in h.collect_listing(port, "persist")[0]}
    check(names == set(objects), f"listing after restart = {names}")


def p0_restart_metadata(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "meta")
    body = b"metadata survives"
    content_type = "application/x-locker-test"
    expect(h.put_object(port, "meta", "key", body, content_type), 200, "put")
    before = expect_object(
        h.get_object(port, "meta", "key"), body, content_type, "before restart"
    )
    captured = {name: before.header(name) for name in ("etag", "content-length", "content-type", "last-modified")}
    server.restart()
    port = server.port
    assert port is not None
    after = expect_object(h.get_object(port, "meta", "key"), body, content_type, "after restart")
    for name, value in captured.items():
        check(after.header(name) == value, f"{name} changed across restart")
    contents = h.collect_listing(port, "meta")[0]
    check(contents[0][0] == "key" and contents[0][1] == len(body), "listing metadata changed")


# ---------------------------------------------------------------------------
# P1 families


def p1_range_closed(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "range")
    body = b"0123456789"
    expect(h.put_object(port, "range", "k", body), 200, "put")
    response = expect(h.get_object(port, "range", "k", {"Range": "bytes=2-5"}), 206, "range")
    check(response.body == body[2:6], "range body mismatch")
    check(response.header("content-range") == "bytes 2-5/10", "Content-Range mismatch")
    check(response.header("content-length") == "4", "range length mismatch")
    check(response.header("accept-ranges") == "bytes", "Accept-Ranges missing")
    head = expect(h.head_object(port, "range", "k", {"Range": "bytes=2-5"}), 206, "range HEAD")
    check(head.body == b"", "HEAD range returned a body")
    check(head.header("content-range") == "bytes 2-5/10", "HEAD Content-Range mismatch")


def p1_range_open_suffix(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "range")
    body = b"0123456789"
    expect(h.put_object(port, "range", "k", body), 200, "put")
    for spec, expected in (
        ("bytes=4-", body[4:]),
        ("bytes=-3", body[-3:]),
        ("bytes=-999", body),
    ):
        response = expect(h.get_object(port, "range", "k", {"Range": spec}), 206, spec)
        check(response.body == expected, f"{spec} body mismatch")
        check(response.header("content-length") == str(len(expected)), f"{spec} length")


def p1_range_bounds(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "range")
    body = b"0123456789"
    expect(h.put_object(port, "range", "k", body), 200, "put")
    response = expect(h.get_object(port, "range", "k", {"Range": "bytes=9-9"}), 206, "last byte")
    check(response.body == b"9", "last byte mismatch")
    for spec in ("bytes=10-", "bytes=5-2", "bytes=-0", "bytes=0-1,3-4"):
        response = expect(h.get_object(port, "range", "k", {"Range": spec}), 416, spec)
        check(
            response.header("content-range") == "bytes */10",
            f"{spec} Content-Range {response.header('content-range')!r}",
        )
    expect(h.put_object(port, "range", "empty", b""), 200, "put empty")
    response = expect(h.get_object(port, "range", "empty", {"Range": "bytes=0-0"}), 416, "empty range")
    check(response.header("content-range") == "bytes */0", "empty Content-Range")


def p1_range_after_replacement(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "range")
    first = b"short"
    second = b"a much longer replacement body"
    expect(h.put_object(port, "range", "k", first), 200, "put first")
    expect(h.put_object(port, "range", "k", second), 200, "put second")
    response = expect(h.get_object(port, "range", "k", {"Range": "bytes=2-5"}), 206, "range")
    check(response.body == second[2:6], "range used old body")
    check(
        response.header("content-range") == f"bytes 2-5/{len(second)}",
        "range used old size",
    )


def p1_conditional_etags(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "cond")
    body = b"conditional body"
    expect(h.put_object(port, "cond", "k", body), 200, "put")
    etag = h.md5_etag(body)
    check(h.get_object(port, "cond", "k", {"If-Match": etag}).body == body, "If-Match match")
    expect(h.get_object(port, "cond", "k", {"If-Match": '"nope"'}), 412, "If-Match fail")
    check(h.get_object(port, "cond", "k", {"If-Match": "*"}).body == body, "If-Match star")
    not_modified = expect(h.get_object(port, "cond", "k", {"If-None-Match": etag}), 304, "If-None-Match")
    check(not_modified.body == b"", "304 had a body")
    check(not_modified.header("etag") == etag, "304 ETag missing")
    expect(h.get_object(port, "cond", "k", {"If-None-Match": "*"}), 304, "If-None-Match star")
    expect(h.get_object(port, "cond", "k", {"If-None-Match": '"nope"'}), 200, "If-None-Match miss")


def p1_conditional_dates(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "cond")
    body = b"date body"
    expect(h.put_object(port, "cond", "k", body), 200, "put")
    modified = h.parse_http_date(h.get_object(port, "cond", "k").header("last-modified"))
    before = h.format_http_date(modified.replace(microsecond=0) - timedelta(seconds=1))
    at = h.format_http_date(modified)
    after = h.format_http_date(modified.replace(microsecond=0) + timedelta(seconds=1))
    expect(h.get_object(port, "cond", "k", {"If-Modified-Since": at}), 304, "IMS at")
    expect(h.get_object(port, "cond", "k", {"If-Modified-Since": after}), 304, "IMS after")
    expect(h.get_object(port, "cond", "k", {"If-Modified-Since": before}), 200, "IMS before")
    expect(h.get_object(port, "cond", "k", {"If-Unmodified-Since": before}), 412, "IUS before")
    expect(h.get_object(port, "cond", "k", {"If-Unmodified-Since": at}), 200, "IUS at")
    expect(h.get_object(port, "cond", "k", {"If-Unmodified-Since": after}), 200, "IUS after")
    expect(h.get_object(port, "cond", "k", {"If-Modified-Since": "not a date"}), 200, "invalid date")


def p1_conditional_precedence(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "cond")
    body = b"precedence"
    expect(h.put_object(port, "cond", "k", body), 200, "put")
    etag = h.md5_etag(body)
    modified = h.parse_http_date(h.get_object(port, "cond", "k").header("last-modified"))
    before = h.format_http_date(modified - timedelta(seconds=1))
    after = h.format_http_date(modified + timedelta(seconds=1))
    expect(
        h.get_object(
            port, "cond", "k", {"If-Match": etag, "If-Unmodified-Since": before}
        ),
        200,
        "If-Match overrides If-Unmodified-Since",
    )
    expect(
        h.get_object(
            port, "cond", "k", {"If-Match": '"nope"', "If-Unmodified-Since": after}
        ),
        412,
        "If-Match failure",
    )
    expect(
        h.get_object(
            port, "cond", "k", {"If-None-Match": '"nope"', "If-Modified-Since": after}
        ),
        200,
        "If-None-Match overrides If-Modified-Since",
    )
    response = expect(
        h.get_object(
            port,
            "cond",
            "k",
            {"If-None-Match": etag, "Range": "bytes=0-1"},
        ),
        304,
        "304 wins over Range",
    )
    check(response.body == b"", "304 range response had a body")


def p1_conditional_after_restart(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "cond")
    first = b"first version"
    second = b"second version with a different length"
    expect(h.put_object(port, "cond", "k", first, "text/plain"), 200, "put first")
    old_etag = h.md5_etag(first)
    expect(h.put_object(port, "cond", "k", second, "application/x-second"), 200, "put second")
    new_etag = h.md5_etag(second)
    server.restart()
    port = server.port
    assert port is not None
    expect(h.get_object(port, "cond", "k", {"If-Match": old_etag}), 412, "old ETag")
    expect_object(
        h.get_object(port, "cond", "k", {"If-Match": new_etag}),
        second,
        "application/x-second",
        "new ETag",
    )
    expect(h.get_object(port, "cond", "k", {"If-None-Match": old_etag}), 200, "old tag miss")


def p1_multipart_roundtrip(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    parts = {1: b"part-one|", 2: b"part-two|", 3: b"part-three"}
    upload_id = initiate(port, "multi", "key", "application/x-multipart")
    etags = {}
    for number in (2, 1, 3):
        etags[number] = upload_part(port, "multi", "key", upload_id, number, parts[number])
    listed = list_parts(port, "multi", "key", upload_id)
    check([part["number"] for part in listed] == [1, 2, 3], "parts not numerically ordered")
    check([part["etag"] for part in listed] == [etags[n] for n in (1, 2, 3)], "part ETags wrong")
    response = expect(
        complete_upload(port, "multi", "key", upload_id, [(3, etags[3]), (1, etags[1]), (2, etags[2])]),
        200,
        "complete",
    )
    expected = parts[1] + parts[2] + parts[3]
    root = h.parse_xml(response.body)
    check(h.text(root, "ETag") == h.md5_etag(expected), "completion ETag wrong")
    expect_object(h.get_object(port, "multi", "key"), expected, "application/x-multipart", "readback")
    expect(h.request(port, "GET", multipart_path("multi", "key", upload_id)), 404, "consumed upload")
    expect(
        h.request(
            port,
            "PUT",
            multipart_path("multi", "key", upload_id, 1),
            body=b"x",
        ),
        404,
        "consumed part upload",
    )


def p1_multipart_stale_retry(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    old = b"object A"
    expect(h.put_object(port, "multi", "key", old, "text/plain"), 200, "put A")
    before = h.get_object(port, "multi", "key")
    upload_id = initiate(port, "multi", "key", "application/x-multipart")
    b, c, d = b"BBBB", b"CCCC", b"DDDD"
    etag_b = upload_part(port, "multi", "key", upload_id, 1, b)
    etag_c = upload_part(port, "multi", "key", upload_id, 2, c)
    etag_d = upload_part(port, "multi", "key", upload_id, 2, d)
    listed = list_parts(port, "multi", "key", upload_id)
    check(listed[1]["etag"] == etag_d and listed[1]["size"] == len(d), "part replacement failed")
    expect(
        complete_upload(port, "multi", "key", upload_id, [(1, etag_b), (2, etag_c)]),
        400,
        "stale completion",
    )
    after = h.get_object(port, "multi", "key")
    expect_object(after, old, "text/plain", "object preserved after failed completion")
    check(after.header("last-modified") == before.header("last-modified"), "failed completion changed timestamp")
    listed = list_parts(port, "multi", "key", upload_id)
    check(listed[1]["etag"] == etag_d, "failed completion lost the upload")
    expect(
        complete_upload(port, "multi", "key", upload_id, [(1, etag_b), (2, etag_d)]),
        200,
        "corrected completion",
    )
    expect_object(h.get_object(port, "multi", "key"), b + d, "application/x-multipart", "corrected readback")


def p1_multipart_invalid_complete(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    old = b"old object"
    expect(h.put_object(port, "multi", "key", old, "text/plain"), 200, "put old")
    part = b"payload"
    upload_id = initiate(port, "multi", "key", "application/x-multipart")
    etag = upload_part(port, "multi", "key", upload_id, 1, part)
    expect(complete_upload(port, "multi", "key", upload_id, [(1, etag), (2, h.md5_etag(b"missing"))]), 400, "missing part")
    expect(complete_upload(port, "multi", "key", upload_id, [(1, etag), (1, etag)]), 400, "duplicate part")
    expect(
        complete_upload(port, "multi", "key", upload_id, [], xml_body=b"<not-xml"),
        400,
        "malformed XML",
    )
    expect(
        complete_upload(port, "multi", "key", upload_id, [], xml_body=b"<CompleteMultipartUpload></CompleteMultipartUpload>"),
        400,
        "empty list",
    )
    expect_object(h.get_object(port, "multi", "key"), old, "text/plain", "old object preserved")
    expect(complete_upload(port, "multi", "key", upload_id, [(1, etag)]), 200, "retry after invalid")
    expect_object(h.get_object(port, "multi", "key"), part, "application/x-multipart", "retry readback")


def p1_multipart_abort_scope(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi", "other")
    first = b"first"
    second = b"second"
    expect(h.put_object(port, "multi", "key", first), 200, "put first")
    expect(h.put_object(port, "other", "key", second), 200, "put second")
    upload_id = initiate(port, "multi", "key", "application/x-multipart")
    upload_part(port, "multi", "key", upload_id, 1, b"part")
    expect(h.request(port, "GET", multipart_path("multi", "wrong", upload_id)), 404, "wrong key")
    expect(h.request(port, "GET", multipart_path("other", "key", upload_id)), 404, "wrong bucket")
    expect(abort_upload(port, "multi", "key", upload_id), 204, "abort")
    expect_object(h.get_object(port, "multi", "key"), first, "", "first object preserved")
    expect_object(h.get_object(port, "other", "key"), second, "", "second object preserved")
    for response in (
        h.request(port, "GET", multipart_path("multi", "key", upload_id)),
        h.request(port, "PUT", multipart_path("multi", "key", upload_id, 1), body=b"x"),
        complete_upload(port, "multi", "key", upload_id, [(1, h.md5_etag(b"part"))]),
        abort_upload(port, "multi", "key", upload_id),
    ):
        expect(response, 404, "aborted upload reuse")


def p1_multipart_concurrent_replace(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    old = b"old object"
    expect(h.put_object(port, "multi", "key", old, "text/plain"), 200, "old object")
    content_type = "application/x-race"
    upload_id = initiate(port, "multi", "key", content_type)
    large = b"B" * (32 * 1024 * 1024)
    small = b"C" * 4096
    replacement = b"D" * 4096
    etag_large = upload_part(port, "multi", "key", upload_id, 1, large)
    etag_small = upload_part(port, "multi", "key", upload_id, 2, small)

    outcome: dict[str, object] = {}

    def finish() -> None:
        try:
            outcome["response"] = complete_upload(
                port,
                "multi",
                "key",
                upload_id,
                [(1, etag_large), (2, etag_small)],
            )
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    thread = threading.Thread(target=finish)
    thread.start()
    time.sleep(0.005)
    replaced = h.request(
        port,
        "PUT",
        multipart_path("multi", "key", upload_id, 2),
        body=replacement,
        timeout=float(limits["request_deadline_s"]),
    )
    thread.join(timeout=float(limits["request_deadline_s"]))
    check(not thread.is_alive(), "completion did not finish")
    check("response" in outcome, f"completion raised: {outcome.get('error')}")
    response = outcome["response"]
    assert isinstance(response, Response)
    check(
        replaced.status in (200, 404),
        f"concurrent part replacement returned {replaced.status}: {replaced.body[:160]!r}",
    )
    if replaced.status == 200:
        check(replaced.header("etag") == h.md5_etag(replacement), "replacement ETag mismatch")
    # Replacement-first makes the old ETag stale (400); completion-first
    # consumes the session so the replacement finds no upload (404).
    check(
        (response.status, replaced.status) in ((200, 404), (400, 200)),
        f"impossible outcome pair: completion {response.status}, replacement {replaced.status}",
    )

    if response.status == 200:
        readback = expect(h.get_object(port, "multi", "key"), 200, "race readback")
        check(
            readback.body == large + small,
            "completion used bytes from a part replaced after its ETag was validated",
        )
        expect_object(readback, large + small, content_type, "race version")
    elif response.status in (400, 404):
        expect_object(h.get_object(port, "multi", "key"), old, "text/plain", "object after failed completion")
    else:
        fail(f"completion after concurrent replacement returned {response.status}")


def p1_multipart_abort_complete_race(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    old = b"old object before abort race"
    expect(h.put_object(port, "multi", "key", old, "text/plain"), 200, "old object")
    content_type = "application/x-abort-race"
    upload_id = initiate(port, "multi", "key", content_type)
    large = b"B" * (32 * 1024 * 1024)
    small = b"C" * 4096
    upload_part(port, "multi", "key", upload_id, 1, large)
    upload_part(port, "multi", "key", upload_id, 2, small)
    parts = list_parts(port, "multi", "key", upload_id)
    etags = {part["number"]: part["etag"] for part in parts}

    outcome: dict[str, object] = {}

    def finish() -> None:
        try:
            outcome["response"] = complete_upload(
                port,
                "multi",
                "key",
                upload_id,
                [(1, etags[1]), (2, etags[2])],
            )
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    thread = threading.Thread(target=finish)
    thread.start()
    time.sleep(0.005)
    aborted = h.request(
        port,
        "DELETE",
        multipart_path("multi", "key", upload_id),
        timeout=float(limits["request_deadline_s"]),
    )
    thread.join(timeout=float(limits["request_deadline_s"]))
    check(not thread.is_alive(), "completion did not finish")
    check("response" in outcome, f"completion raised: {outcome.get('error')}")
    response = outcome["response"]
    assert isinstance(response, Response)
    check(
        aborted.status in (204, 404),
        f"abort returned {aborted.status}: {aborted.body[:160]!r}",
    )

    if response.status == 200:
        check(aborted.status == 404, "completion and abort both reported success")
        expect_object(
            h.get_object(port, "multi", "key"),
            large + small,
            content_type,
            "completion winner",
        )
    elif response.status == 404:
        check(aborted.status == 204, "completion and abort were both rejected")
        expect_object(h.get_object(port, "multi", "key"), old, "text/plain", "abort winner")
    else:
        fail(f"completion after concurrent abort returned {response.status}")


def p1_multipart_concurrent_complete(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    content_type = "application/x-double-complete"
    upload_id = initiate(port, "multi", "key", content_type)
    large = b"B" * (32 * 1024 * 1024)
    small = b"C" * 4096
    upload_part(port, "multi", "key", upload_id, 1, large)
    upload_part(port, "multi", "key", upload_id, 2, small)
    parts = list_parts(port, "multi", "key", upload_id)
    etags = {part["number"]: part["etag"] for part in parts}
    barrier = threading.Barrier(2)
    statuses: list[int] = []
    errors: list[str] = []

    def complete() -> None:
        try:
            barrier.wait(timeout=30)
            response = complete_upload(
                port,
                "multi",
                "key",
                upload_id,
                [(1, etags[1]), (2, etags[2])],
            )
            statuses.append(response.status)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=complete) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=float(limits["request_deadline_s"]))
    check(not any(thread.is_alive() for thread in threads), "concurrent completions did not finish")
    check(not errors, "; ".join(errors))
    check(
        sorted(statuses) == [200, 404],
        f"concurrent completions returned {sorted(statuses)}, expected [200, 404]",
    )
    expect_object(h.get_object(port, "multi", "key"), large + small, content_type, "completed object")
    expect(h.request(port, "GET", multipart_path("multi", "key", upload_id)), 404, "consumed upload")


def p1_multipart_abort_during_part(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    old = b"old object during part upload"
    expect(h.put_object(port, "multi", "key", old, "text/plain"), 200, "old object")
    upload_id = initiate(port, "multi", "key", "application/x-abort-part")
    total = 32 * 1024 * 1024
    prefix = b"P" * 4096
    gate = h.GatedUpload(
        port,
        multipart_path("multi", "key", upload_id, 1),
        total,
        timeout=float(limits["request_deadline_s"]),
    )
    outcome: dict[str, object] = {}

    def finish_part() -> None:
        try:
            outcome["response"] = gate.finish(
                b"R" * (total - len(prefix)),
                timeout=float(limits["request_deadline_s"]),
            )
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    try:
        gate.wait_interim(timeout=float(limits["request_deadline_s"]))
        gate.send(prefix)
        finisher = threading.Thread(target=finish_part)
        finisher.start()
        # Give the part upload time to enter body input, then abort. A
        # serialized implementation may make the abort wait for the part
        # upload; the finisher keeps sending the remainder so neither side
        # deadlocks.
        time.sleep(0.05)
        aborted = abort_upload(port, "multi", "key", upload_id)
        expect(aborted, 204, "abort during part")
        finisher.join(timeout=float(limits["request_deadline_s"]))
        check(not finisher.is_alive(), "part upload did not finish after abort")
        check("response" in outcome, f"part upload raised: {outcome.get('error')}")
        final = outcome["response"]
        assert isinstance(final, Response)
        check(
            final.status in (200, 404),
            f"part upload after abort returned {final.status}, expected 200 or 404",
        )
    finally:
        gate.close()
    expect_object(h.get_object(port, "multi", "key"), old, "text/plain", "object after aborted part")
    expect(h.request(port, "GET", multipart_path("multi", "key", upload_id)), 404, "aborted upload id")


def p1_multipart_list_during_complete(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    content_type = "application/x-list-race"
    upload_id = initiate(port, "multi", "key", content_type)
    large = b"B" * (32 * 1024 * 1024)
    small = b"C" * 4096
    upload_part(port, "multi", "key", upload_id, 1, large)
    upload_part(port, "multi", "key", upload_id, 2, small)
    parts = list_parts(port, "multi", "key", upload_id)
    etags = {part["number"]: part["etag"] for part in parts}
    outcome: dict[str, object] = {}

    def finish() -> None:
        try:
            outcome["response"] = complete_upload(
                port,
                "multi",
                "key",
                upload_id,
                [(1, etags[1]), (2, etags[2])],
            )
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    thread = threading.Thread(target=finish)
    thread.start()
    time.sleep(0.005)
    listed = h.request(
        port,
        "GET",
        multipart_path("multi", "key", upload_id),
        timeout=float(limits["request_deadline_s"]),
    )
    thread.join(timeout=float(limits["request_deadline_s"]))
    check(not thread.is_alive(), "completion did not finish")
    check("response" in outcome, f"completion raised: {outcome.get('error')}")
    response = outcome["response"]
    assert isinstance(response, Response)
    expect(response, 200, "completion during list")
    check(listed.status in (200, 404), f"list during completion returned {listed.status}")
    if listed.status == 200:
        root = h.parse_xml(listed.body)
        parts_seen = root.findall("Part")
        check(len(parts_seen) == 2, "list raced past completion and returned an empty part list")
    expect(
        h.request(port, "GET", multipart_path("multi", "key", upload_id)),
        404,
        "list after completion",
    )
    expect_object(h.get_object(port, "multi", "key"), large + small, content_type, "completed object")


def p1_multipart_list_during_abort(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    old = b"old object before abort/list race"
    expect(h.put_object(port, "multi", "key", old, "text/plain"), 200, "old object")
    upload_id = initiate(port, "multi", "key", "application/x-abort-list")
    first = b"first part"
    upload_part(port, "multi", "key", upload_id, 1, first)
    total = 32 * 1024 * 1024
    prefix = b"P" * 4096
    gate = h.GatedUpload(
        port,
        multipart_path("multi", "key", upload_id, 2),
        total,
        timeout=float(limits["request_deadline_s"]),
    )
    outcome: dict[str, object] = {}

    def finish_part() -> None:
        try:
            outcome["part"] = gate.finish(
                b"R" * (total - len(prefix)),
                timeout=float(limits["request_deadline_s"]),
            )
        except Exception as exc:  # noqa: BLE001
            outcome["part_error"] = exc

    try:
        gate.wait_interim(timeout=float(limits["request_deadline_s"]))
        gate.send(prefix)
        finisher = threading.Thread(target=finish_part)
        finisher.start()

        def abort() -> None:
            try:
                outcome["abort"] = h.request(
                    port,
                    "DELETE",
                    multipart_path("multi", "key", upload_id),
                    timeout=float(limits["request_deadline_s"]),
                )
            except Exception as exc:  # noqa: BLE001
                outcome["abort_error"] = exc

        def list_now() -> None:
            try:
                outcome["list"] = h.request(
                    port,
                    "GET",
                    multipart_path("multi", "key", upload_id),
                    timeout=float(limits["request_deadline_s"]),
                )
            except Exception as exc:  # noqa: BLE001
                outcome["list_error"] = exc

        abort_thread = threading.Thread(target=abort)
        abort_thread.start()
        time.sleep(0.02)
        list_thread = threading.Thread(target=list_now)
        list_thread.start()
        for thread in (finisher, abort_thread, list_thread):
            thread.join(timeout=float(limits["request_deadline_s"]))
            check(not thread.is_alive(), "multipart abort/list workers did not finish")
        check("abort" in outcome, f"abort raised: {outcome.get('abort_error')}")
        aborted = outcome["abort"]
        assert isinstance(aborted, Response)
        expect(aborted, 204, "abort during part/list race")
        part_status = None
        if "part" in outcome:
            part_response = outcome["part"]
            assert isinstance(part_response, Response)
            part_status = part_response.status
            check(part_status in (200, 404), f"part upload returned {part_status}")
        elif "part_error" in outcome:
            fail(f"part upload raised: {outcome['part_error']}")
        check("list" in outcome, f"list raised: {outcome.get('list_error')}")
        listed = outcome["list"]
        assert isinstance(listed, Response)
        check(listed.status in (200, 404), f"list during abort returned {listed.status}")
        if listed.status == 200:
            root = h.parse_xml(listed.body)
            numbers = [int(h.text(part, "PartNumber")) for part in root.findall("Part")]
            allowed = [[1]]
            if part_status == 200:
                allowed.append([1, 2])
            check(numbers in allowed, f"list returned incoherent parts {numbers} (part upload {part_status})")
    finally:
        gate.close()
    expect_object(h.get_object(port, "multi", "key"), old, "text/plain", "object after abort race")
    expect(h.request(port, "GET", multipart_path("multi", "key", upload_id)), 404, "aborted upload id")


def p1_multipart_parameter_errors(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    old = b"old object"
    expect(h.put_object(port, "multi", "key", old, "text/plain"), 200, "old object")
    upload_id = initiate(port, "multi", "key", "application/x-params")
    etag = upload_part(port, "multi", "key", upload_id, 1, b"part")
    path = h.object_path("multi", "key")
    completion = completion_xml([(1, etag)])
    invalid_requests = [
        ("PUT", multipart_path("multi", "key", upload_id, 0), None),
        ("PUT", multipart_path("multi", "key", upload_id, 10001), None),
        ("PUT", multipart_path("multi", "key", upload_id, -1), None),
        ("PUT", path + "?uploadId=" + urllib.parse.quote(upload_id) + "&partNumber=abc", None),
        ("PUT", path + "?partNumber=1", None),
        ("PUT", path + "?uploadId=" + urllib.parse.quote(upload_id), None),
        ("GET", path + "?uploadId=" + urllib.parse.quote(upload_id) + "&partNumber=1", None),
        ("DELETE", path + "?uploadId=" + urllib.parse.quote(upload_id) + "&partNumber=1", None),
        ("POST", path + "?uploadId=" + urllib.parse.quote(upload_id) + "&partNumber=1", completion),
        ("PUT", path + "?uploads", b"x"),
        ("GET", path + "?uploads", None),
        ("DELETE", path + "?uploads", None),
        ("POST", path + "?uploads&uploadId=" + urllib.parse.quote(upload_id), completion),
        ("POST", path + "?uploads&partNumber=1", completion),
    ]
    for method, target, body in invalid_requests:
        response = h.request(port, method, target, body=body, timeout=float(limits["request_deadline_s"]))
        check(
            response.status == 400,
            f"{method} {target} returned {response.status}, expected 400",
        )
    expect_object(h.get_object(port, "multi", "key"), old, "text/plain", "old object preserved")
    listed = list_parts(port, "multi", "key", upload_id)
    check(len(listed) == 1 and listed[0]["number"] == 1, "invalid requests changed the session")
    expect(abort_upload(port, "multi", "key", upload_id), 204, "abort after invalid requests")


def p1_listing_list_type(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "list")
    seed_objects(port, "list", ["a", "b"])
    base = h.bucket_path("list")
    for value in ("1", "0", "abc", ""):
        response = h.request(
            port,
            "GET",
            base + "?list-type=" + urllib.parse.quote(value),
            timeout=float(limits["request_deadline_s"]),
        )
        check(response.status == 400, f"list-type={value!r} returned {response.status}, expected 400")
    page = h.list_page(port, "list", max_keys=1000)
    check(page.key_count == 2 and not page.is_truncated, "valid list-type listing is wrong")


def p1_listing_max_keys(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "list")
    seed_objects(port, "list", ["a", "b", "c"])
    base = h.bucket_path("list")
    for value in ("", "0", "1001", "abc"):
        response = h.request(
            port,
            "GET",
            base + "?list-type=2&max-keys=" + urllib.parse.quote(value),
            timeout=float(limits["request_deadline_s"]),
        )
        check(
            response.status == 400,
            f"max-keys={value!r} returned {response.status}, expected 400",
        )
    page = h.list_page(port, "list", max_keys=1)
    check(page.max_keys == 1, "valid max-keys was not honored")
    check(page.key_count == 1 and page.is_truncated, "max-keys=1 produced the wrong page")


def p1_listing_page_limits(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "list")
    keys = ["a", "b", "c", "d", "e"]
    seed_objects(port, "list", keys)
    for page_size in (1, 2, 1000):
        token = None
        collected: list[tuple[str, int, str]] = []
        while True:
            page = h.list_page(port, "list", max_keys=page_size, token=token)
            check(page.max_keys == page_size, "MaxKeys echo wrong")
            check(page.key_count == len(page.contents) + len(page.prefixes), "KeyCount wrong")
            collected.extend(page.contents)
            if not page.is_truncated:
                break
            token = page.next_token
        check([item[0] for item in collected] == sorted(keys), f"max-keys={page_size} page union wrong")


def p1_listing_pages(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "list")
    keys = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
    bodies = seed_objects(port, "list", keys)
    expected = [(key, len(bodies[key]), h.md5_etag(bodies[key])) for key in sorted(keys)]
    for page_size in (1, 2):
        collected = []
        token = None
        pages = 0
        while True:
            page = h.list_page(port, "list", max_keys=page_size, token=token)
            check(page.max_keys == page_size, "MaxKeys echo wrong")
            check(page.key_count == len(page.contents) + len(page.prefixes), "KeyCount wrong")
            collected.extend(page.contents)
            pages += 1
            if not page.is_truncated:
                check(page.next_token == "", "token present on final page")
                break
            check(bool(page.next_token), "truncated page has no token")
            token = page.next_token
            check(pages < 100, "pagination did not terminate")
        check(collected == expected, f"page size {page_size}: listing mismatch")


def p1_listing_groups(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "list")
    seed_objects(port, "list", ["a/1", "a/2", "b/1", "c"])
    expected = effective_listing(["a/1", "a/2", "b/1", "c"], delimiter="/")
    expected_pages = [
        [item] for item in expected
    ]
    collected = []
    token = None
    for index in range(10):
        page = h.list_page(port, "list", delimiter="/", max_keys=1, token=token)
        check(page.max_keys == 1, "MaxKeys echo wrong")
        check(page.key_count == len(page.contents) + len(page.prefixes), "KeyCount wrong")
        collected.extend(("object", item[0]) for item in page.contents)
        collected.extend(("prefix", item) for item in page.prefixes)
        if not page.is_truncated:
            break
        token = page.next_token
    check(collected == expected, f"grouped listing {collected} != {expected}")
    page = h.list_page(port, "list", prefix="a/", delimiter="/")
    check([item[0] for item in page.contents] == ["a/1", "a/2"], "prefix listing wrong")


def p1_listing_tokens_restart(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "list", "other")
    keys = ["a", "b", "c", "d", "e"]
    bodies = seed_objects(port, "list", keys)
    seed_objects(port, "other", ["x", "y"])
    first = h.list_page(port, "list", max_keys=2)
    check(first.is_truncated and first.next_token, "first page not truncated")
    token = first.next_token
    collected = list(first.contents)
    server.restart()
    port = server.port
    assert port is not None
    while True:
        page = h.list_page(port, "list", max_keys=2, token=token)
        collected.extend(page.contents)
        if not page.is_truncated:
            break
        token = page.next_token
    expected = [(key, len(bodies[key]), h.md5_etag(bodies[key])) for key in sorted(keys)]
    check(collected == expected, "restart continuation mismatch")
    expect(h.request(port, "GET", h.bucket_path("list") + "?list-type=2&max-keys=2&continuation-token=not-base64"), 400, "malformed token")
    expect(h.request(port, "GET", h.bucket_path("other") + "?list-type=2&max-keys=2&continuation-token=" + urllib.parse.quote(first.next_token)), 400, "wrong-scope token")
    for value in ("0", "1001", "abc"):
        expect(h.request(port, "GET", h.bucket_path("list") + "?list-type=2&max-keys=" + value), 400, f"max-keys {value}")


def _churn_case(server: h.Server, limits: Limits, *, delete_writes: bool) -> None:
    port = server.port
    assert port is not None
    size = 64 * 1024
    versions = {
        bytes([(index * 31 + 7) % 251]) * size: f"application/x-churn-{index}"
        for index in range(8)
    }
    bodies = list(versions)
    writers = 8
    readers = 32
    rounds = 30
    reads = 100
    attempts = 4

    for attempt in range(attempts):
        bucket = f"churn-{attempt}"
        make_buckets(port, bucket)
        initial = next(iter(versions))
        expect(h.put_object(port, bucket, "key", initial, versions[initial]), 200, "initial")
        barrier = threading.Barrier(writers + readers)
        errors: list[str] = []

        def writer(writer_id: int) -> None:
            client = h.PersistentClient(port, timeout=float(limits["request_deadline_s"]))
            try:
                barrier.wait(timeout=30)
                for round_number in range(rounds):
                    body = bodies[(writer_id + round_number) % len(bodies)]
                    response = client.request(
                        "PUT",
                        h.object_path(bucket, "key"),
                        body=body,
                        headers={"Content-Type": versions[body]},
                    )
                    check(response.status == 200, f"churn PUT status {response.status}")
                    if delete_writes:
                        response = client.request("DELETE", h.object_path(bucket, "key"))
                        check(response.status == 204, f"churn DELETE status {response.status}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"writer {writer_id}: {exc}")
            finally:
                client.close()

        def reader() -> None:
            client = h.PersistentClient(port, timeout=float(limits["request_deadline_s"]))
            try:
                barrier.wait(timeout=30)
                for _ in range(reads):
                    response = client.request("GET", h.object_path(bucket, "key"))
                    if response.status == 404 and delete_writes:
                        continue
                    check(response.status == 200, f"churn GET status {response.status}")
                    check(
                        response.header("etag") == h.md5_etag(response.body),
                        "churn GET body/ETag incoherent",
                    )
                    check(
                        response.header("content-length") == str(len(response.body)),
                        "churn GET body/length incoherent",
                    )
                    check(response.body in versions, "churn GET returned an unknown body")
                    check(
                        response.header("content-type") == versions.get(response.body),
                        "churn GET body/content-type incoherent",
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"reader: {exc}")
            finally:
                client.close()

        threads = [threading.Thread(target=writer, args=(index,)) for index in range(writers)]
        threads += [threading.Thread(target=reader) for _ in range(readers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=float(limits["request_deadline_s"]))
        check(not any(thread.is_alive() for thread in threads), "churn workers did not finish")
        check(not errors, "; ".join(errors[:5]))
        final = b"final serialized version"
        expect(h.put_object(port, bucket, "key", final, "application/x-final"), 200, "final PUT")
        expect_object(h.get_object(port, bucket, "key"), final, "application/x-final", "final GET")


def p1_concurrency_churn_replace(server: h.Server, limits: Limits) -> None:
    _churn_case(server, limits, delete_writes=False)


def p1_concurrency_churn_delete(server: h.Server, limits: Limits) -> None:
    _churn_case(server, limits, delete_writes=True)


def p1_concurrency_coherent(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "concurrent")
    bodies = [
        (
            (f"writer-{index}-" + "x" * (index + 1)).encode(),
            f"application/x-writer-{index}",
        )
        for index in range(4)
    ]
    initial_body, initial_type = bodies[0]
    expect(h.put_object(port, "concurrent", "hot", initial_body, initial_type), 200, "initial")
    expected = {(h.md5_etag(body), body, ctype) for body, ctype in bodies}
    barrier = threading.Barrier(7)
    errors: list[str] = []

    def writer(body: bytes, ctype: str) -> None:
        try:
            barrier.wait(timeout=10)
            for _ in range(5):
                response = h.put_object(port, "concurrent", "hot", body, ctype)
                check(response.status == 200, f"writer status {response.status}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"writer: {exc}")

    def reader() -> None:
        try:
            barrier.wait(timeout=10)
            for _ in range(20):
                response = h.get_object(port, "concurrent", "hot")
                check(response.status == 200, f"reader status {response.status}")
                check(response.header("etag") == h.md5_etag(response.body), "body/ETag incoherent")
                check(response.header("content-length") == str(len(response.body)), "body/length incoherent")
                check(
                    (response.header("etag"), response.body, response.header("content-type")) in expected,
                    "reader saw a mixed version",
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"reader: {exc}")

    threads = [threading.Thread(target=writer, args=pair) for pair in bodies]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    check(not any(thread.is_alive() for thread in threads), "concurrency workers did not finish")
    check(not errors, "; ".join(errors))
    final_body, final_type = b"serialized final", "application/x-final"
    expect(h.put_object(port, "concurrent", "hot", final_body, final_type), 200, "final")
    expect_object(h.get_object(port, "concurrent", "hot"), final_body, final_type, "final read")


def p1_concurrency_no_lost_writes(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "concurrent")
    bodies = {f"key-{index}": f"value-{index}".encode() * 100 for index in range(8)}
    barrier = threading.Barrier(len(bodies))
    errors: list[str] = []

    def writer(key: str, body: bytes) -> None:
        try:
            barrier.wait(timeout=10)
            response = h.put_object(port, "concurrent", key, body, "application/x-batch")
            check(response.status == 200, f"{key} status {response.status}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{key}: {exc}")

    threads = [threading.Thread(target=writer, args=item) for item in bodies.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    check(not errors, "; ".join(errors))
    server.restart()
    port = server.port
    assert port is not None
    for key, body in bodies.items():
        expect_object(h.get_object(port, "concurrent", key), body, "application/x-batch", key)


def p1_concurrency_progress(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "concurrent")
    old = b"old object while an upload is stalled"
    expect(h.put_object(port, "concurrent", "held", old, "text/plain"), 200, "old")
    prefix = b"P" * 4096
    rest = b"R" * 4096
    expected_new = prefix + rest
    gate = h.GatedUpload(
        port,
        h.object_path("concurrent", "held"),
        len(expected_new),
        content_type="application/x-gated",
        timeout=float(limits["progress_deadline_s"]),
    )
    try:
        gate.wait_interim(timeout=float(limits["progress_deadline_s"]))
        gate.send(prefix)
        deadline = time.monotonic() + float(limits["progress_deadline_s"])

        def remaining() -> float:
            return max(0.5, deadline - time.monotonic())

        try:
            response = h.get_object(port, "concurrent", "held", timeout=remaining())
            expect_object(response, old, "text/plain", "old object during stalled upload")
            response = h.put_object(port, "concurrent", "unrelated", b"independent", "text/plain", timeout=remaining())
            expect(response, 200, "unrelated PUT during stalled upload")
            response = h.get_object(port, "concurrent", "unrelated", timeout=remaining())
            expect_object(response, b"independent", "text/plain", "unrelated GET")
        except (TimeoutError, OSError) as exc:
            fail(f"no progress within {limits['progress_deadline_s']}s while an upload was stalled: {exc}")
        response = gate.finish(rest)
        expect(response, 200, "gated upload completion")
    finally:
        gate.close()
    expect_object(h.get_object(port, "concurrent", "held"), expected_new, "application/x-gated", "new object")


# ---------------------------------------------------------------------------
# P2 overwrite / crash families
# ---------------------------------------------------------------------------


def p2_overwrite_incomplete_disconnect(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "overwrite")
    old = b"old version"
    expect(h.put_object(port, "overwrite", "key", old, "text/plain"), 200, "put old")
    prefix = b"partial body"
    payload = h.request_bytes(
        "PUT",
        h.object_path("overwrite", "key"),
        headers={"Content-Type": "application/x-incomplete"},
        body=prefix,
    )
    # Replace the automatic Content-Length with a larger declared value.
    payload = payload.replace(
        f"Content-Length: {len(prefix)}".encode(),
        f"Content-Length: {len(prefix) + 100}".encode(),
        1,
    )
    status, _detail = h.raw_half_close(port, payload, timeout=float(limits["request_deadline_s"]))
    check(status is None or not 200 <= status < 300, f"incomplete upload succeeded with {status}")
    expect_object(h.get_object(port, "overwrite", "key"), old, "text/plain", "old preserved")
    expect(h.put_object(port, "overwrite", "key", b"new", "text/plain"), 200, "subsequent put")
    expect_object(h.get_object(port, "overwrite", "key"), b"new", "text/plain", "subsequent read")


def p2_overwrite_incomplete_kill(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "overwrite")
    old = b"old before kill"
    expect(h.put_object(port, "overwrite", "key", old, "text/plain"), 200, "put old")
    prefix = b"unfinished"
    payload = h.request_bytes(
        "PUT",
        h.object_path("overwrite", "key"),
        headers={"Content-Type": "application/x-kill"},
        body=prefix,
    )
    payload = payload.replace(
        f"Content-Length: {len(prefix)}".encode(),
        f"Content-Length: {len(prefix) + 1000}".encode(),
        1,
    )
    log(f"incomplete kill: sending {len(prefix)} of {len(prefix) + 1000} bytes, no acknowledgment")
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        sock.sendall(payload)
        time.sleep(0.05)
        server.stop(kill=True)
    finally:
        sock.close()
    server.restart()
    port = server.port
    assert port is not None
    expect_object(h.get_object(port, "overwrite", "key"), old, "text/plain", "old after kill")
    expect(h.put_object(port, "overwrite", "key", b"after kill", "text/plain"), 200, "put after kill")


def p2_overwrite_publication_crash(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "overwrite")
    old = b"old coherent version" * 100
    new = b"new coherent version" * 100
    trials = max(2, min(int(limits["crash_trials"]), 6))
    for trial in range(trials):
        expect(h.put_object(port, "overwrite", "key", old, "text/plain"), 200, f"reset {trial}")
        payload = raw_put_payload("overwrite", "key", new, "application/x-new")
        log(f"publication trial {trial}: sent {len(new)} bytes, acknowledgment=no")
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            sock.sendall(payload)
            time.sleep(0.005)
            server.stop(kill=True)
        finally:
            sock.close()
        server.restart()
        port = server.port
        assert port is not None
        check_version(
            port,
            "overwrite",
            "key",
            [(old, "text/plain"), (new, "application/x-new")],
            f"publication trial {trial}",
        )
    expect(h.put_object(port, "overwrite", "key", b"recovered", "text/plain"), 200, "recovery put")
    expect_object(h.get_object(port, "overwrite", "key"), b"recovered", "text/plain", "recovery read")


def p2_overwrite_acknowledged(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "overwrite")
    old = b"before acknowledged overwrite"
    new = b"acknowledged version"
    expect(h.put_object(port, "overwrite", "key", old, "text/plain"), 200, "put old")
    response = expect(h.put_object(port, "overwrite", "key", new, "application/x-ack"), 200, "acknowledged put")
    check(response.header("etag") == h.md5_etag(new), "acknowledged ETag")
    server.restart(kill=True)
    port = server.port
    assert port is not None
    expect_object(h.get_object(port, "overwrite", "key"), new, "application/x-ack", "new after kill")


def p2_multipart_cross_feature(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    old = b"old object"
    expect(h.put_object(port, "multi", "key", old, "text/plain"), 200, "put old")
    first, second = b"0123456789", b"abcdefghij"
    upload_id = initiate(port, "multi", "key", "application/x-cross")
    etag1 = upload_part(port, "multi", "key", upload_id, 1, first)
    etag2 = upload_part(port, "multi", "key", upload_id, 2, second)
    expect(complete_upload(port, "multi", "key", upload_id, [(1, etag1), (2, etag2)]), 200, "complete")
    expected = first + second
    expect_object(h.get_object(port, "multi", "key"), expected, "application/x-cross", "full")
    response = expect(h.get_object(port, "multi", "key", {"Range": "bytes=8-12"}), 206, "range")
    check(response.body == expected[8:13], "multipart range mismatch")
    expect(h.get_object(port, "multi", "key", {"If-Match": h.md5_etag(expected)}), 200, "conditional")
    expect(h.get_object(port, "multi", "key", {"If-None-Match": h.md5_etag(expected)}), 304, "not modified")
    server.restart()
    port = server.port
    assert port is not None
    expect_object(h.get_object(port, "multi", "key"), expected, "application/x-cross", "after restart")
    contents = h.collect_listing(port, "multi")[0]
    check(contents[0][0] == "key" and contents[0][1] == len(expected), "listing after restart")
    local = os.path.join(server.root, "cross-download")
    result = server.cli("cp", "s3://multi/key", local)
    check(result.returncode == 0, f"CLI download failed: {result.stderr}")
    with open(local, "rb") as handle:
        check(handle.read() == expected, "CLI download bytes differ")


def _prepare_crash_upload(port: int, bucket: str, key: str, content_type: str) -> tuple[str, bytes]:
    first, second = b"crash-part-one", b"crash-part-two"
    upload_id = initiate(port, bucket, key, content_type)
    upload_part(port, bucket, key, upload_id, 1, first)
    upload_part(port, bucket, key, upload_id, 2, second)
    return upload_id, first + second


def _kill_during_completion(
    server: h.Server, bucket: str, key: str, upload_id: str, parts: list[tuple[int, str]], acknowledged: bool
) -> None:
    port = server.port
    assert port is not None
    body = completion_xml(parts)
    if acknowledged:
        expect(complete_upload(port, bucket, key, upload_id, parts), 200, "acknowledged completion")
        server.stop(kill=True)
        return
    payload = h.request_bytes(
        "POST",
        multipart_path(bucket, key, upload_id),
        headers={"Content-Type": "application/xml"},
        body=body,
    )
    log(
        f"multipart completion {bucket}/{key}: acknowledged={acknowledged}, "
        f"parts={len(parts)}"
    )
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        sock.sendall(payload)
        time.sleep(0.005)
        server.stop(kill=True)
    finally:
        sock.close()


def p2_multipart_crash_existing(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    old = b"old multipart target"
    trials = max(2, min(int(limits["crash_trials"]), 6))
    for trial in range(trials + 1):
        acknowledged = trial == trials
        expect(h.put_object(port, "multi", "key", old, "text/plain"), 200, f"reset {trial}")
        upload_id, assembled = _prepare_crash_upload(port, "multi", "key", "application/x-crash")
        # Reconstruct the ETags from the session so completion can be either
        # accepted or interrupted. The server validates them only on success.
        listed = list_parts(port, "multi", "key", upload_id)
        parts = [(part["number"], part["etag"]) for part in listed]
        _kill_during_completion(server, "multi", "key", upload_id, parts, acknowledged)
        server.restart()
        port = server.port
        assert port is not None
        if acknowledged:
            expect_object(h.get_object(port, "multi", "key"), assembled, "application/x-crash", "acknowledged")
        else:
            check_version(
                port,
                "multi",
                "key",
                [(old, "text/plain"), (assembled, "application/x-crash")],
                f"multipart crash trial {trial}",
            )
    expect(h.put_object(port, "multi", "key", b"after multipart crash", "text/plain"), 200, "recovery put")


def p2_multipart_crash_absent(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "multi")
    trials = max(2, min(int(limits["crash_trials"]), 6))
    for trial in range(trials + 1):
        key = f"fresh-{trial}"
        acknowledged = trial == trials
        upload_id, assembled = _prepare_crash_upload(port, "multi", key, "application/x-absent")
        listed = list_parts(port, "multi", key, upload_id)
        parts = [(part["number"], part["etag"]) for part in listed]
        _kill_during_completion(server, "multi", key, upload_id, parts, acknowledged)
        server.restart()
        port = server.port
        assert port is not None
        response = h.get_object(port, "multi", key)
        if acknowledged:
            expect_object(response, assembled, "application/x-absent", "acknowledged absent")
        elif response.status == 404:
            pass
        else:
            expect_object(response, assembled, "application/x-absent", f"absent crash trial {trial}")
        if response.status == 200:
            check_version(port, "multi", key, [(assembled, "application/x-absent")], f"absent trial {trial}")
    expect(h.put_object(port, "multi", "recovery", b"usable", "text/plain"), 200, "recovery put")


# ---------------------------------------------------------------------------
# P2 isolation and performance families
# ---------------------------------------------------------------------------


def p2_isolation_identity(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "one", "two")
    pairs = {
        ("one", "a"): b"one/a",
        ("one", "a/b"): b"one/a/b",
        ("one", "café/🙂"): b"unicode",
        ("two", "a"): b"two/a",
    }
    for (bucket, key), body in pairs.items():
        expect(h.put_object(port, bucket, key, body), 200, f"put {bucket}/{key}")
    for (bucket, key), body in pairs.items():
        expect_object(h.get_object(port, bucket, key), body, "", f"{bucket}/{key}")
    one_keys = {item[0] for item in h.collect_listing(port, "one")[0]}
    two_keys = {item[0] for item in h.collect_listing(port, "two")[0]}
    check(one_keys == {"a", "a/b", "café/🙂"}, f"bucket one keys {one_keys}")
    check(two_keys == {"a"}, f"bucket two keys {two_keys}")


def p2_isolation_escaping(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "escape")
    keys = ["%2F", "/", "a/b", 'x<&>"\'', "hash#q?", "%2e%2e"]
    bodies = {key: f"body:{key}".encode("utf-8") for key in keys}
    for key, body in bodies.items():
        expect(h.put_object(port, "escape", key, body), 200, f"put {key!r}")
    for key, body in bodies.items():
        response = expect_object(h.get_object(port, "escape", key), body, "", f"get {key!r}")
        check(response.header("etag") == h.md5_etag(body), f"{key!r} ETag")
    listed = {item[0] for item in h.collect_listing(port, "escape")[0]}
    check(listed == set(keys), f"escaping listing {listed} != {set(keys)}")


def p2_isolation_traversal(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "traverse", "neighbor")
    safe_body = b"neighbour"
    neighbor_body = b"neighbor escape"
    expect(h.put_object(port, "traverse", "safe", safe_body), 200, "safe object")
    expect(h.put_object(port, "neighbor", "escape", neighbor_body), 200, "neighbor object")
    sentinel = os.path.join(server.root, "sentinel.txt")
    with open(sentinel, "wb") as handle:
        handle.write(b"unchanged sentinel")
    attempts = [
        ("/traverse/../escape", b"raw traversal"),
        ("/traverse/..%2Fescape", b"encoded traversal"),
        ("/traverse/%2e%2e%2f%2e%2e%2fescape", b"double traversal"),
        ("/traverse/..", b"dot dot key"),
    ]
    for path, body in attempts:
        response = h.request(port, "PUT", path, body=body)
        if response.status == 200:
            readback = h.request(port, "GET", path)
            expect(readback, 200, f"readback {path}")
            check(readback.body == body, f"readback {path} body")
        else:
            check(
                response.status == 400,
                f"{path}: expected 400 or safe storage, got {response.status}",
            )
        expect_object(h.get_object(port, "traverse", "safe"), safe_body, "", f"neighbour after {path}")
        expect_object(h.get_object(port, "neighbor", "escape"), neighbor_body, "", f"neighbor escape after {path}")
        with open(sentinel, "rb") as handle:
            check(handle.read() == b"unchanged sentinel", f"sentinel changed by {path}")
        check(not os.path.exists(os.path.join(server.root, "escape")), f"escape file appeared for {path}")
    expect(h.put_object(port, "traverse", "final", b"usable"), 200, "store usable")


def p2_perf_family(server: h.Server, limits: Limits) -> dict[str, tuple[bool, str | None]]:
    """Run one shared 128 MiB workload and score its three independent cases."""
    results: dict[str, tuple[bool, str | None]] = {}
    port = server.port
    assert port is not None
    try:
        make_buckets(port, "perf")
        body = h.DeterministicBody(int(limits["stream_bytes"]), seed=int(limits.get("fixture_seed", 1)))
        deadline = time.monotonic() + float(limits["operation_deadline_s"])
        response = h.stream_request(
            port,
            "PUT",
            h.object_path("perf", "big"),
            body.chunks(),
            body.total,
            headers={"Content-Type": "application/octet-stream"},
            timeout=float(limits["operation_deadline_s"]),
        )
        check(response.status == 200, f"large PUT status {response.status}")
        check(response.header("etag") == body.etag(), "large PUT ETag mismatch")
        download = h.stream_download(
            port,
            h.object_path("perf", "big"),
            timeout=float(limits["operation_deadline_s"]),
        )
        check(download.status == 200, f"large GET status {download.status}")
        check(download.length == body.total, "large GET length mismatch")
        check(download.digest == body.digest(), "large GET digest mismatch")
        check(download.headers.get("etag") == body.etag(), "large GET ETag mismatch")
        results["stream-roundtrip"] = (True, None)
    except Exception as exc:  # noqa: BLE001
        message = f"stream-roundtrip: {exc}"
        return {
            "stream-roundtrip": (False, message),
            "range": (False, "large object unavailable"),
            "rss-budget": (False, "large workload did not complete"),
        }

    try:
        start, end = 1_000_000, 2_000_001
        response = h.get_object(
            port,
            "perf",
            "big",
            {"Range": f"bytes={start}-{end - 1}"},
            timeout=float(limits["operation_deadline_s"]),
        )
        expect(response, 206, "large range")
        check(response.body == body.slice(start, end), "large range body mismatch")
        check(
            response.header("content-range") == f"bytes {start}-{end - 1}/{body.total}",
            "large range Content-Range mismatch",
        )
        check(time.monotonic() <= deadline, "large PUT+GET+range workload exceeded the published deadline")
        results["range"] = (True, None)
    except Exception as exc:  # noqa: BLE001
        results["range"] = (False, f"range: {exc}")

    rss = server.rss_high_water()
    if rss is None:
        results["rss-budget"] = (False, "server high-water RSS was not measurable")
    elif rss > int(limits["server_rss_bytes"]):
        results["rss-budget"] = (
            False,
            f"server VmHWM {rss} exceeds cap {limits['server_rss_bytes']}",
        )
    else:
        results["rss-budget"] = (True, None)
    return results


# ---------------------------------------------------------------------------
# WP4: p1.batch / p2.batch


def p1_batch_copy_rename_delete(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "batch")
    alpha = b"alpha"
    xray = b"xray"
    etag = h.md5_etag(alpha)
    expect(h.put_object(port, "batch", "a", alpha, "text/plain"), 200, "put a")
    expect(h.put_object(port, "batch", "x", xray, "application/json"), 200, "put x")
    lm_a = h.parse_http_date(expect(h.head_object(port, "batch", "a"), 200, "head a").header("last-modified"))
    lm_x = h.parse_http_date(expect(h.head_object(port, "batch", "x"), 200, "head x").header("last-modified"))

    response = batch(
        port,
        "batch",
        [
            ("copy", "a", "b", None),
            ("rename", "b", "c", None),
            ("delete", "a", None),
            ("copy", "c", "x", None),
        ],
    )
    expect(response, 200, "batch chain")
    check(len(response.body) == 0, f"batch success body should be empty, got {response.body[:100]!r}")
    expect_absent(port, "batch", "a", "b", label="after chain")

    c = expect_present(port, "batch", "c", alpha, etag, "text/plain", "get c")
    expect_present(port, "batch", "x", alpha, etag, "text/plain", "get x")
    check(c.header("accept-ranges") == "bytes", "copy lost Accept-Ranges")
    check(
        h.parse_http_date(c.header("last-modified")) >= lm_a,
        "copy Last-Modified is older than its source's previous modification",
    )
    check(
        h.parse_http_date(expect(h.head_object(port, "batch", "x"), 200, "head x").header("last-modified")) >= lm_x,
        "copy-over Last-Modified went backwards",
    )

    contents, prefixes = h.collect_listing(port, "batch")
    check(prefixes == [], f"unexpected common prefixes {prefixes}")
    check(contents == [("c", 5, etag), ("x", 5, etag)], f"listing after chain: {contents}")

    expect(batch(port, "batch", [("delete", "c", None), ("delete", "never-existed", None)]), 200, "delete batch")
    contents, _prefixes = h.collect_listing(port, "batch")
    check(contents == [("x", 5, etag)], f"listing after delete batch: {contents}")


def p1_batch_preconditions(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "pre")
    a1, b1 = b"A1", b"B1"
    expect(h.put_object(port, "pre", "a", a1, "text/plain"), 200, "put a")
    expect(h.put_object(port, "pre", "b", b1, "text/plain"), 200, "put b")
    ea = h.md5_etag(a1)
    eb = h.md5_etag(b1)
    wrong = h.md5_etag(b"not-this")

    expect(batch(port, "pre", [("copy", "a", "c", ea)]), 200, "copy with matching IfMatch")
    expect_present(port, "pre", "c", a1, ea, "text/plain", "copy c")

    before = bucket_snapshot(port, "pre")
    expect_error(
        batch(port, "pre", [("copy", "a", "d", eb)]),
        412,
        "PreconditionFailed",
        "mismatched copy IfMatch",
    )
    expect_absent(port, "pre", "d", label="after mismatched IfMatch")
    check(bucket_snapshot(port, "pre") == before, "mismatched IfMatch changed the bucket")

    expect(
        batch(port, "pre", [("copy", "a", "d", None), ("delete", "b", "*")]),
        200,
        "copy and wildcard delete",
    )
    expect_present(port, "pre", "d", a1, ea, "text/plain", "copy d")
    expect_absent(port, "pre", "b", label="wildcard delete")

    before = bucket_snapshot(port, "pre")
    expect_error(
        batch(port, "pre", [("copy", "missing", "e", None)]),
        412,
        "PreconditionFailed",
        "absent copy source",
    )
    expect_absent(port, "pre", "e", label="after absent source")
    check(bucket_snapshot(port, "pre") == before, "absent source changed the bucket")

    before = bucket_snapshot(port, "pre")
    expect_error(
        batch(
            port,
            "pre",
            [
                ("rename", "a", "f", None),
                ("copy", "c", "g", None),
                ("delete", "c", wrong),
            ],
        ),
        412,
        "PreconditionFailed",
        "failure in last operation",
    )
    expect_present(port, "pre", "a", a1, ea, "text/plain", "a after late failure")
    expect_absent(port, "pre", "f", "g", label="after late failure")
    check(bucket_snapshot(port, "pre") == before, "late failure applied partial effects")

    expect(
        batch(port, "pre", [("copy", "a", "h", None), ("delete", "h", ea)]),
        200,
        "working-copy delete",
    )
    expect_absent(port, "pre", "h", label="working-copy delete")
    expect_present(port, "pre", "a", a1, ea, "text/plain", "a after working-copy delete")
    expect_error(
        batch(port, "pre", [("copy", "a", "h", None), ("delete", "h", wrong)]),
        412,
        "PreconditionFailed",
        "working-copy stale delete",
    )
    expect_absent(port, "pre", "h", label="after stale working-copy delete")

    expect_error(
        batch(port, "pre", [("delete", "nothing", "*")]),
        412,
        "PreconditionFailed",
        "wildcard delete of absent key",
    )
    expect(batch(port, "pre", [("delete", "nothing", None)]), 200, "delete absent without IfMatch")

    expect(batch(port, "pre", [("rename", "a", "a2", "*")]), 200, "rename wildcard")
    expect_absent(port, "pre", "a", label="after rename wildcard")
    expect_present(port, "pre", "a2", a1, ea, "text/plain", "a2")


def p1_batch_errors(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "err")
    value = b"A"
    expect(h.put_object(port, "err", "a", value, "text/plain"), 200, "put a")
    before = bucket_snapshot(port, "err")

    invalid = [
        b"<Batch><Copy>",
        b"<Batch></Batch>",
        b"<Ops><Delete><Key>a</Key></Delete></Ops>",
        batch_xml([("delete", f"k{i}", None) for i in range(33)]),
        b"<Batch><Move><Source>a</Source><Target>b</Target></Move></Batch>",
        b"<Batch><Copy><Source>a</Source></Copy></Batch>",
        b"<Batch><Rename><Source></Source><Target>b</Target></Rename></Batch>",
        b"<Batch><Delete></Delete></Batch>",
        b"<Batch><Copy><Source>a</Source><Target>a</Target></Copy></Batch>",
        b"<Batch><Delete><Key>a</Key><IfMatch></IfMatch></Delete></Batch>",
    ]
    for index, body in enumerate(invalid):
        expect_error(raw_batch(port, "/err?batch", body), 400, "InvalidArgument", f"invalid batch {index}")
        check(bucket_snapshot(port, "err") == before, f"invalid batch {index} changed the bucket")

    good = batch_xml([("delete", "zz", None)])
    expect_error(
        raw_batch(port, h.object_path("err", "a") + "?batch", good),
        400,
        "InvalidArgument",
        "batch on an object path",
    )
    expect_error(
        raw_batch(port, "/err?batch&list-type=2", good),
        400,
        "InvalidArgument",
        "batch with listing parameter",
    )
    expect_error(
        raw_batch(port, "/err?batch&uploads", good),
        400,
        "InvalidArgument",
        "batch with multipart parameter",
    )
    expect_error(
        raw_batch(port, "/nobucket-batch-errors?batch", good),
        404,
        "NoSuchBucket",
        "batch on absent bucket",
    )

    expect(batch(port, "err", [("delete", f"k{i}", None) for i in range(32)]), 200, "32-op boundary")
    expect_present(port, "err", "a", value, h.md5_etag(value), "text/plain", "a after error cases")


def p1_batch_independence(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "ind")

    expect(h.put_object(port, "ind", "a", b"orig", "text/plain"), 200, "put a")
    expect(batch(port, "ind", [("copy", "a", "b", None)]), 200, "copy a to b")
    expect(h.put_object(port, "ind", "a", b"new-a", "text/csv"), 200, "overwrite a")
    expect_present(port, "ind", "b", b"orig", h.md5_etag(b"orig"), "text/plain", "b after a overwrite")
    expect(h.request(port, "DELETE", h.object_path("ind", "a")), 204, "delete a")
    expect_present(port, "ind", "b", b"orig", h.md5_etag(b"orig"), "text/plain", "b after a delete")

    big = h.DeterministicBody(65536, seed=5)
    big_bytes = b"".join(big.chunks())
    expect(h.put_object(port, "ind", "c", big_bytes, "application/octet-stream"), 200, "put c")
    expect(batch(port, "ind", [("copy", "c", "d", None)]), 200, "copy c to d")
    expect(h.request(port, "DELETE", h.object_path("ind", "c")), 204, "delete c")
    expect_present(port, "ind", "d", big_bytes, h.md5_etag(big_bytes), "application/octet-stream", "d after c delete")
    ranged = expect(
        h.get_object(port, "ind", "d", {"Range": "bytes=100-199"}),
        206,
        "range on copy",
    )
    check(ranged.body == big_bytes[100:200], "range on copy returned the wrong bytes")
    check(ranged.header("content-range") == "bytes 100-199/65536", "range on copy header mismatch")

    expect(h.put_object(port, "ind", "r0", b"chain", "text/plain"), 200, "put r0")
    expect(
        batch(
            port,
            "ind",
            [("rename", "r0", "r1", None), ("rename", "r1", "r2", None), ("rename", "r2", "r3", None)],
        ),
        200,
        "rename chain",
    )
    expect_present(port, "ind", "r3", b"chain", h.md5_etag(b"chain"), "text/plain", "r3")
    expect_absent(port, "ind", "r0", "r1", "r2", label="after rename chain")

    expect(h.put_object(port, "ind", "e", b"", "text/plain"), 200, "put empty")
    expect(batch(port, "ind", [("copy", "e", "e2", None)]), 200, "copy empty")
    empty = expect(h.get_object(port, "ind", "e2"), 200, "get empty copy")
    check(empty.body == b"", "empty copy body mismatch")
    check(empty.header("content-length") == "0", "empty copy length mismatch")
    check(empty.header("etag") == h.md5_etag(b""), "empty copy ETag mismatch")

    p1 = b"p" * (5 * 1024)
    p2 = b"q" * (3 * 1024)
    uid = initiate(port, "ind", "m", "application/x-parts")
    e1 = upload_part(port, "ind", "m", uid, 1, p1)
    e2 = upload_part(port, "ind", "m", uid, 2, p2)
    expect(complete_upload(port, "ind", "m", uid, [(1, e1), (2, e2)]), 200, "complete m")
    expect(batch(port, "ind", [("copy", "m", "m2", None)]), 200, "copy multipart object")
    expect_present(port, "ind", "m2", p1 + p2, h.md5_etag(p1 + p2), "application/x-parts", "m2")

    expect(
        batch(port, "ind", [("copy", "d", "d1", None), ("copy", "d", "d2", None), ("copy", "d", "d3", None)]),
        200,
        "fan-out copies",
    )
    expect(h.request(port, "DELETE", h.object_path("ind", "d")), 204, "delete d")
    expect(h.request(port, "DELETE", h.object_path("ind", "d1")), 204, "delete d1")
    expect(batch(port, "ind", [("delete", "d2", None)]), 200, "delete d2 by batch")
    expect_present(port, "ind", "d3", big_bytes, h.md5_etag(big_bytes), "application/octet-stream", "d3 after fan-out")

    server.restart()
    port = server.port
    assert port is not None
    expect_present(port, "ind", "b", b"orig", h.md5_etag(b"orig"), "text/plain", "b after restart")
    expect_present(port, "ind", "d3", big_bytes, h.md5_etag(big_bytes), "application/octet-stream", "d3 after restart")
    expect_present(port, "ind", "r3", b"chain", h.md5_etag(b"chain"), "text/plain", "r3 after restart")
    expect_present(port, "ind", "e2", b"", h.md5_etag(b""), "text/plain", "e2 after restart")
    expect_present(port, "ind", "m2", p1 + p2, h.md5_etag(p1 + p2), "application/x-parts", "m2 after restart")
    expect_absent(port, "ind", "a", "c", "d", "d1", "d2", "r0", label="after restart")


def p1_batch_cross_feature(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "xf")
    body = h.DeterministicBody(1000, seed=7)
    payload = b"".join(body.chunks())
    etag = h.md5_etag(payload)

    expect(h.put_object(port, "xf", "a", payload, "application/x-test"), 200, "put a")
    expect(batch(port, "xf", [("copy", "a", "b", None)]), 200, "copy a to b")

    ranged = expect(h.get_object(port, "xf", "b", {"Range": "bytes=10-19"}), 206, "range on b")
    check(ranged.body == payload[10:20], "range body mismatch")
    check(ranged.header("content-range") == "bytes 10-19/1000", "range Content-Range mismatch")
    check(ranged.header("etag") == etag, "range ETag mismatch")

    not_modified = expect(h.get_object(port, "xf", "b", {"If-None-Match": etag}), 304, "If-None-Match on b")
    check(not_modified.body == b"", "304 response had a body")
    check(not_modified.header("etag") == etag, "304 response lost the ETag")

    expect(h.get_object(port, "xf", "b", {"If-Match": h.md5_etag(b"other")}), 412, "If-Match on b")

    head = expect(h.head_object(port, "xf", "b"), 200, "HEAD b")
    check(head.body == b"", "HEAD b had a body")
    check(head.header("etag") == etag, "HEAD ETag mismatch")
    check(head.header("content-length") == "1000", "HEAD Content-Length mismatch")
    check(head.header("content-type") == "application/x-test", "HEAD Content-Type mismatch")

    unsatisfiable = expect(h.get_object(port, "xf", "b", {"Range": "bytes=1000-"}), 416, "range past end")
    check(unsatisfiable.header("content-range") == "bytes */1000", "416 Content-Range mismatch")

    contents, _prefixes = h.collect_listing(port, "xf")
    check(contents == [("a", 1000, etag), ("b", 1000, etag)], f"listing after copy: {contents}")

    uid = initiate(port, "xf", "m", "text/plain")
    part_etag = upload_part(port, "xf", "m", uid, 1, b"part-one")
    expect(h.put_object(port, "xf", "m", b"plain", "text/plain"), 200, "put m")
    expect(batch(port, "xf", [("rename", "m", "m-old", None)]), 200, "rename m")
    parts = list_parts(port, "xf", "m", uid)
    check(
        [(item["number"], item["etag"], item["size"]) for item in parts] == [(1, part_etag, len(b"part-one"))],
        f"session parts changed by rename: {parts}",
    )
    expect(complete_upload(port, "xf", "m", uid, [(1, part_etag)]), 200, "complete after rename")
    expect_present(port, "xf", "m", b"part-one", h.md5_etag(b"part-one"), "text/plain", "m after complete")
    expect_present(port, "xf", "m-old", b"plain", h.md5_etag(b"plain"), "text/plain", "m-old")

    expect(h.put_object(port, "xf", "n", b"n0", "text/plain"), 200, "put n")
    uid2 = initiate(port, "xf", "n", "text/plain")
    part_etag2 = upload_part(port, "xf", "n", uid2, 1, b"n-part")
    expect(batch(port, "xf", [("delete", "n", None)]), 200, "delete n")
    expect_absent(port, "xf", "n", label="after delete n")
    expect(complete_upload(port, "xf", "n", uid2, [(1, part_etag2)]), 200, "complete after delete")
    expect_present(port, "xf", "n", b"n-part", h.md5_etag(b"n-part"), "text/plain", "n after complete")

    expect(
        batch(
            port,
            "xf",
            [("delete", "m-old", None), ("delete", "m", None), ("delete", "n", None), ("delete", "a", None), ("delete", "b", None)],
        ),
        200,
        "empty the bucket",
    )
    contents, _prefixes = h.collect_listing(port, "xf")
    check(contents == [], f"bucket should be empty, got {contents}")
    expect(h.delete_bucket(port, "xf"), 204, "delete empty bucket")


def _run_parallel(calls: list[Callable[[], Response]], timeout: float) -> list[Response]:
    results: list[Response | None] = [None] * len(calls)
    errors: list[BaseException | None] = [None] * len(calls)
    barrier = threading.Barrier(len(calls))

    def runner(index: int, func: Callable[[], Response]) -> None:
        try:
            barrier.wait(timeout=10)
            results[index] = func()
        except BaseException as exc:  # noqa: BLE001
            errors[index] = exc

    threads = [threading.Thread(target=runner, args=(index, func), daemon=True) for index, func in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout)
    for thread in threads:
        check(not thread.is_alive(), "concurrent request thread hung")
    for error in errors:
        if error is not None:
            raise error
    return [result for result in results if result is not None]


def p1_batch_atomic_visibility(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "vis")
    body_a = bytes((index * 7 + 3) % 251 for index in range(2048))
    body_b = bytes((index * 11 + 5) % 251 for index in range(2048))
    expect(h.put_object(port, "vis", "a", body_a, "text/plain"), 200, "put a")
    expect(h.put_object(port, "vis", "b", body_b, "text/plain"), 200, "put b")
    etag_a = h.md5_etag(body_a)
    etag_b = h.md5_etag(body_b)

    errors: list[str] = []
    swaps = [0]
    reads = [0]
    writer_done = threading.Event()
    barrier = threading.Barrier(5)

    def writer() -> None:
        try:
            barrier.wait(timeout=10)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not errors:
                response = batch(
                    port,
                    "vis",
                    [("rename", "a", "tmp", None), ("rename", "b", "a", None), ("rename", "tmp", "b", None)],
                    timeout=5,
                )
                if response.status != 200:
                    errors.append(f"swap batch returned {response.status}: {response.body[:200]!r}")
                    return
                swaps[0] += 1
        except BaseException as exc:  # noqa: BLE001
            errors.append(f"writer: {exc}")
        finally:
            writer_done.set()

    def reader() -> None:
        try:
            barrier.wait(timeout=10)
            while not writer_done.is_set() and not errors:
                page = h.list_page(port, "vis", timeout=5)
                keys = sorted(key for key, _size, _etag in page.contents)
                if keys != ["a", "b"]:
                    errors.append(f"listing saw keys {keys}")
                    return
                if any(size != 2048 for _key, size, _etag in page.contents):
                    errors.append("listing saw a wrong size")
                    return
                if {etag for _key, _size, etag in page.contents} != {etag_a, etag_b}:
                    errors.append("listing saw a mixed or missing ETag set")
                    return
                if page.prefixes:
                    errors.append(f"listing saw prefixes {page.prefixes}")
                    return
                response = h.get_object(port, "vis", "a", timeout=5)
                if response.status != 200:
                    errors.append(f"GET a returned {response.status}")
                    return
                if response.body not in (body_a, body_b):
                    errors.append("GET a returned half of a version")
                    return
                if response.header("etag") != h.md5_etag(response.body):
                    errors.append("GET a headers do not describe its body")
                    return
                if response.header("content-length") != "2048":
                    errors.append("GET a Content-Length mismatch")
                    return
                reads[0] += 1
        except BaseException as exc:  # noqa: BLE001
            errors.append(f"reader: {exc}")

    threads = [threading.Thread(target=writer, daemon=True)]
    threads.extend(threading.Thread(target=reader, daemon=True) for _ in range(4))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=float(limits["request_deadline_s"]) + 10)
    for thread in threads:
        check(not thread.is_alive(), "atomic-visibility thread hung")

    check(not errors, f"atomic visibility violation: {errors[:3]}")
    check(swaps[0] >= 5 and reads[0] >= 20, f"too few operations completed in 3 s (swaps={swaps[0]}, reads={reads[0]})")

    final_a = expect(h.get_object(port, "vis", "a"), 200, "final GET a")
    final_b = expect(h.get_object(port, "vis", "b"), 200, "final GET b")
    expected = (body_a, body_b) if swaps[0] % 2 == 0 else (body_b, body_a)
    check((final_a.body, final_b.body) == expected, "final state does not match the swap parity")


def _concurrent_batches_round(port: int, limits: Limits, bucket: str) -> None:
    timeout = float(limits["request_deadline_s"])
    make_buckets(port, bucket)

    body_a = b"A" * 64
    body_b = b"B" * 64
    expect(h.put_object(port, bucket, "a", body_a, "text/plain"), 200, "round1 put a")
    expect(h.put_object(port, bucket, "b", body_b, "text/plain"), 200, "round1 put b")
    r1, r2 = _run_parallel(
        [
            lambda: batch(port, bucket, [("rename", "a", "b", None)], timeout=timeout),
            lambda: batch(port, bucket, [("rename", "b", "a", None)], timeout=timeout),
        ],
        timeout + 10,
    )
    for index, response in enumerate((r1, r2)):
        check(response.status < 500, f"round1 batch {index} returned {response.status}")
        expect(response, 200, f"round1 batch {index}")
    snapshot = bucket_snapshot(port, bucket)
    allowed = [
        {"a": (body_a, h.md5_etag(body_a), "text/plain")},
        {"b": (body_b, h.md5_etag(body_b), "text/plain")},
    ]
    check(snapshot in allowed, f"round1 final snapshot is not a serial outcome: {snapshot}")

    body_x = b"X" * 32
    body_y = b"Y" * 32
    expect(h.put_object(port, bucket, "x", body_x, "text/plain"), 200, "round2 put x")
    expect(h.put_object(port, bucket, "y", body_y, "text/plain"), 200, "round2 put y")
    ex = h.md5_etag(body_x)
    s1, s2 = _run_parallel(
        [
            lambda: batch(
                port,
                bucket,
                [("copy", "x", "z", ex), ("delete", "y", "*")],
                timeout=timeout,
            ),
            lambda: batch(
                port,
                bucket,
                [("delete", "y", "*"), ("copy", "x", "w", None)],
                timeout=timeout,
            ),
        ],
        timeout + 10,
    )
    for index, response in enumerate((s1, s2)):
        check(response.status < 500, f"round2 batch {index} returned {response.status}")
    statuses = sorted((s1.status, s2.status))
    check(statuses == [200, 412], f"round2 expected one 200 and one 412, got {statuses}")
    expect_absent(port, bucket, "y", label="round2 y")
    if s1.status == 200:
        expect_present(port, bucket, "z", body_x, ex, "text/plain", "round2 z")
        expect_absent(port, bucket, "w", label="round2 w")
    else:
        expect_present(port, bucket, "w", body_x, ex, "text/plain", "round2 w")
        expect_absent(port, bucket, "z", label="round2 z")

    bodies = [f"K{index}".encode() for index in range(4)]
    for index, body in enumerate(bodies):
        expect(h.put_object(port, bucket, f"k{index}", body, "text/plain"), 200, f"round3 put k{index}")

    stop = threading.Event()
    reader_errors: list[str] = []
    reader_stats = {"reads": 0, "saw200": False}

    def reader() -> None:
        try:
            while not stop.is_set():
                response = h.get_object(port, bucket, "shared", timeout=timeout)
                if response.status == 404 and not reader_stats["saw200"]:
                    continue
                if response.status != 200:
                    reader_errors.append(f"GET shared returned {response.status}")
                    return
                reader_stats["saw200"] = True
                if response.body not in bodies:
                    reader_errors.append("GET shared returned a mixed body")
                    return
                if response.header("etag") != h.md5_etag(response.body):
                    reader_errors.append("GET shared headers do not describe its body")
                    return
                reader_stats["reads"] += 1
        except BaseException as exc:  # noqa: BLE001
            reader_errors.append(f"reader: {exc}")

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    def worker(index: int) -> list[Response]:
        responses = []
        for _ in range(10):
            responses.append(
                batch(
                    port,
                    bucket,
                    [("copy", f"k{index}", "shared", None), ("copy", "shared", f"own{index}", None)],
                    timeout=timeout,
                )
            )
        return responses

    worker_calls: list[Callable[[], Response]] = []
    batches: list[Response] = []
    worker_threads = [threading.Thread(target=lambda index=index: batches.extend(worker(index)), daemon=True) for index in range(4)]
    for thread in worker_threads:
        thread.start()
    for thread in worker_threads:
        thread.join(timeout=timeout * 4 + 10)
    stop.set()
    reader_thread.join(timeout=10)
    for thread in worker_threads:
        check(not thread.is_alive(), "round3 worker hung")
    check(not reader_thread.is_alive(), "round3 reader hung")
    check(not reader_errors, f"round3 reader violation: {reader_errors[:3]}")
    check(len(batches) == 40, f"round3 expected 40 batches, got {len(batches)}")
    for index, response in enumerate(batches):
        check(response.status < 500, f"round3 batch {index} returned {response.status}")
        expect(response, 200, f"round3 batch {index}")
    for index, body in enumerate(bodies):
        expect_present(port, bucket, f"own{index}", body, h.md5_etag(body), "text/plain", f"round3 own{index}")
    shared = expect(h.get_object(port, bucket, "shared"), 200, "round3 shared")
    check(shared.body in bodies and shared.header("etag") == h.md5_etag(shared.body), "round3 shared is not a whole version")

    body_k0 = bodies[0]
    late = b"late"
    b1, b2 = _run_parallel(
        [
            lambda: batch(port, bucket, [("rename", "k0", "k0b", None)], timeout=timeout),
            lambda: h.put_object(port, bucket, "k0", late, "text/plain", timeout=timeout),
        ],
        timeout + 10,
    )
    check(b1.status < 500 and b2.status < 500, "round4 returned a 5xx")
    expect(b1, 200, "round4 batch")
    expect(b2, 200, "round4 PUT")
    restricted = {
        key: value
        for key, value in bucket_snapshot(port, bucket).items()
        if key in ("k0", "k0b")
    }
    allowed4 = [
        {"k0b": (late, h.md5_etag(late), "text/plain")},
        {
            "k0b": (body_k0, h.md5_etag(body_k0), "text/plain"),
            "k0": (late, h.md5_etag(late), "text/plain"),
        },
    ]
    check(restricted in allowed4, f"round4 snapshot is not a serial outcome: {restricted}")


def p1_batch_concurrent_batches(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    for suffix in ("1", "2"):
        _concurrent_batches_round(port, limits, f"cc{suffix}")


# ---------------------------------------------------------------------------
# WP4: p2.batch


def p2_batch_kill_during_swap(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "ks")
    body_a = bytes((index * 3 + 1) % 251 for index in range(4096))
    body_b = bytes((index * 13 + 7) % 251 for index in range(4096))
    expect(h.put_object(port, "ks", "a", body_a, "text/plain"), 200, "put a")
    expect(h.put_object(port, "ks", "b", body_b, "text/plain"), 200, "put b")
    rng = random.Random(int(limits["fixture_seed"]) * 31 + 7)

    for trial in range(int(limits["crash_trials"])):
        kill_flag = threading.Event()
        problems: list[str] = []

        def writer() -> None:
            while not kill_flag.is_set():
                try:
                    response = batch(
                        port,
                        "ks",
                        [("rename", "a", "tmp", None), ("rename", "b", "a", None), ("rename", "tmp", "b", None)],
                        timeout=5,
                    )
                except Exception:  # noqa: BLE001 - the kill closes the connection
                    return
                if response.status != 200 and not kill_flag.is_set():
                    problems.append(f"trial {trial}: swap batch returned {response.status}")
                    return

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        time.sleep(rng.uniform(0.02, 0.25))
        kill_flag.set()
        server.stop(kill=True)
        thread.join(timeout=10)
        check(not thread.is_alive(), f"trial {trial}: writer hung after the kill")
        check(not problems, f"trial {trial}: {problems}")

        server.restart()
        port = server.port
        assert port is not None
        page = h.list_page(port, "ks")
        keys = sorted(key for key, _size, _etag in page.contents)
        check(keys == ["a", "b"], f"trial {trial}: keys after recovery are {keys}")
        check(all(size == 4096 for _key, size, _etag in page.contents), f"trial {trial}: wrong size after recovery")
        first_a = expect(h.get_object(port, "ks", "a"), 200, f"trial {trial}: GET a")
        first_b = expect(h.get_object(port, "ks", "b"), 200, f"trial {trial}: GET b")
        for label, response in (("a", first_a), ("b", first_b)):
            check(response.body in (body_a, body_b), f"trial {trial}: {label} is not a complete version")
            check(response.header("etag") == h.md5_etag(response.body), f"trial {trial}: {label} ETag mismatch")
            check(response.header("content-length") == "4096", f"trial {trial}: {label} length mismatch")
        check(first_a.body != first_b.body, f"trial {trial}: both keys hold the same body")

        expect(
            batch(
                port,
                "ks",
                [("rename", "a", "tmp", None), ("rename", "b", "a", None), ("rename", "tmp", "b", None)],
            ),
            200,
            f"trial {trial}: swap after recovery",
        )
        second_a = expect(h.get_object(port, "ks", "a"), 200, f"trial {trial}: GET a after swap")
        second_b = expect(h.get_object(port, "ks", "b"), 200, f"trial {trial}: GET b after swap")
        check(
            (second_a.body, second_b.body) == (first_b.body, first_a.body),
            f"trial {trial}: swap after recovery did not swap the versions",
        )


def p2_batch_kill_after_ack(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "ka")
    for index in range(int(limits["crash_trials"])):
        size = 1024 + index * 900
        p_body = bytes((offset * 5 + index) % 251 for offset in range(size))
        q_body = bytes((offset * 9 + index + 3) % 251 for offset in range(size))
        p_key = f"p{index}"
        q_key = f"q{index}"
        c_key = f"c{index}"
        r_key = f"r{index}"
        expect(h.put_object(port, "ka", p_key, p_body, "text/plain"), 200, f"put {p_key}")
        expect(h.put_object(port, "ka", q_key, q_body, "text/plain"), 200, f"put {q_key}")
        expect(
            batch(
                port,
                "ka",
                [
                    ("copy", p_key, c_key, None),
                    ("rename", q_key, r_key, None),
                    ("delete", p_key, "*"),
                ],
            ),
            200,
            f"trial {index}: acknowledged batch",
        )
        server.stop(kill=True)
        server.restart()
        port = server.port
        assert port is not None
        expect_present(port, "ka", c_key, p_body, h.md5_etag(p_body), "text/plain", f"trial {index}: c")
        expect_present(port, "ka", r_key, q_body, h.md5_etag(q_body), "text/plain", f"trial {index}: r")
        expect_absent(port, "ka", p_key, q_key, label=f"trial {index}: sources")
        contents, _prefixes = h.collect_listing(port, "ka")
        keys = {key for key, _size, _etag in contents}
        check(c_key in keys and r_key in keys, f"trial {index}: listing lost an acknowledged key")
        check(p_key not in keys and q_key not in keys, f"trial {index}: listing kept a deleted source")


def p2_batch_blob_lifetime(server: h.Server, limits: Limits) -> None:
    port = server.port
    assert port is not None
    make_buckets(port, "bl")
    big_body = h.DeterministicBody(65536, seed=9)
    big = b"".join(big_body.chunks())

    expect(h.put_object(port, "bl", "a", b"A", "text/plain"), 200, "put a")
    expect(batch(port, "bl", [("copy", "a", "b", None)]), 200, "copy a to b")
    expect(h.request(port, "DELETE", h.object_path("bl", "a")), 204, "delete a")
    expect_present(port, "bl", "b", b"A", h.md5_etag(b"A"), "text/plain", "b after a delete")
    server.restart()
    port = server.port
    assert port is not None
    expect_present(port, "bl", "b", b"A", h.md5_etag(b"A"), "text/plain", "b after restart")

    expect(batch(port, "bl", [("rename", "b", "c", None)]), 200, "rename b to c")
    expect(h.put_object(port, "bl", "c", b"C2", "text/plain"), 200, "overwrite c")
    server.restart()
    port = server.port
    assert port is not None
    expect_present(port, "bl", "c", b"C2", h.md5_etag(b"C2"), "text/plain", "c after overwrite restart")
    expect_absent(port, "bl", "b", label="b after overwrite")

    expect(h.put_object(port, "bl", "c", big, "application/octet-stream"), 200, "put big c")
    expect(batch(port, "bl", [("copy", "c", "d", None)]), 200, "copy c to d")
    expect(h.request(port, "DELETE", h.object_path("bl", "c")), 204, "delete big c")
    gated = h.GatedUpload(port, h.object_path("bl", "u"), 65536)
    try:
        gated.wait_interim()
        gated.send(big[:30000])
        server.stop(kill=True)
    finally:
        gated.close()
    server.restart()
    port = server.port
    assert port is not None
    expect_present(port, "bl", "d", big, h.md5_etag(big), "application/octet-stream", "d after interrupted upload")
    ranged = expect(h.get_object(port, "bl", "d", {"Range": "bytes=0-9"}), 206, "range on d")
    check(ranged.body == big[:10], "range on d returned the wrong bytes")
    expect_absent(port, "bl", "u", label="interrupted upload")

    expect(
        batch(port, "bl", [("copy", "d", "d1", None), ("copy", "d", "d2", None), ("copy", "d", "d3", None)]),
        200,
        "fan-out from d",
    )
    expect(h.request(port, "DELETE", h.object_path("bl", "d")), 204, "delete d")
    expect(h.request(port, "DELETE", h.object_path("bl", "d1")), 204, "delete d1")
    expect(batch(port, "bl", [("delete", "d2", None)]), 200, "delete d2")
    server.restart()
    port = server.port
    assert port is not None
    expect_present(port, "bl", "d3", big, h.md5_etag(big), "application/octet-stream", "d3 after fan-out restart")

    expect(batch(port, "bl", [("rename", "d3", "d4", None)]), 200, "rename d3 to d4")
    expect(h.put_object(port, "bl", "d4", b"small", "text/plain"), 200, "overwrite d4")
    server.restart()
    port = server.port
    assert port is not None
    expect_present(port, "bl", "d4", b"small", h.md5_etag(b"small"), "text/plain", "d4 after overwrite restart")
    expect_absent(port, "bl", "d3", label="d3 after rename")
    contents, _prefixes = h.collect_listing(port, "bl")
    check(contents == [("d4", 5, h.md5_etag(b"small"))], f"final blob-lifetime listing: {contents}")


# ---------------------------------------------------------------------------
# WP5: p2.faults


def _fault_body(size: int, seed: int) -> bytes:
    return b"".join(h.DeterministicBody(size, seed=seed).chunks())


def _scratch_server(binary: str, limits: Limits) -> h.Server:
    root = tempfile.mkdtemp(prefix="locker-fault-", dir=limits.get("scratch_dir"))
    return h.Server(
        binary,
        root=root,
        env={"TMPDIR": root, "TMP": root, "TEMP": root},
    ).start()


def oversized_put(
    port: int,
    path: str,
    total: int,
    content_type: str = "application/octet-stream",
) -> tuple[int | None, str]:
    """Stream `total` bytes with a declared length over a raw socket.

    Returns (status, detail); status is None when the connection was reset or
    closed before a status line.
    """
    sock = socket.create_connection(("127.0.0.1", port), timeout=60)
    try:
        head = (
            f"PUT {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            f"Content-Type: {content_type}\r\nContent-Length: {total}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        sock.sendall(head)
        sent = 0
        chunk = bytes(1 << 20)
        try:
            while sent < total:
                sock.sendall(chunk)
                sent += len(chunk)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        try:
            status, _headers, _body = h.read_raw_response(sock, time.monotonic() + 60)
            return status, f"status {status} after {sent} bytes"
        except (ConnectionError, OSError, CaseFailure) as exc:
            return None, f"no status after {sent} bytes: {exc}"
    finally:
        sock.close()


def check_failed_cleanly(status: int | None, detail: str, label: str) -> None:
    check(
        status is None or status >= 500,
        f"{label}: oversized write must fail with 5xx or a closed connection, got {detail}",
    )


def put_expect(
    port: int,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str,
    label: str,
) -> Response:
    """A required successful PUT; a transport failure is a case failure."""
    try:
        response = h.put_object(port, bucket, key, body, content_type)
    except (BrokenPipeError, ConnectionResetError, OSError) as exc:
        fail(f"{label}: write failed at the transport level ({type(exc).__name__}: {exc})")
    return expect(response, 200, label)


def p2_faults_put_preserves_old(binary: str, limits: Limits) -> None:
    server = _scratch_server(binary, limits)
    try:
        port = server.port
        assert port is not None
        make_buckets(port, "fx")
        old = _fault_body(1024 * 1024, seed=13)
        etag = h.md5_etag(old)
        expect(h.put_object(port, "fx", "k", old, "text/x-old"), 200, "put old k")

        status, detail = oversized_put(
            port, h.object_path("fx", "k"), int(limits["fault_write_bytes"]), "text/x-new"
        )
        check_failed_cleanly(status, detail, "put-preserves-old")

        expect_present(port, "fx", "k", old, etag, "text/x-old", "old k after failed write")
        head = expect(h.head_object(port, "fx", "k"), 200, "HEAD k after failed write")
        check(head.body == b"", "HEAD k had a body")
        check(head.header("etag") == etag, "HEAD k ETag mismatch")
        check(head.header("content-length") == "1048576", "HEAD k Content-Length mismatch")
        contents, _prefixes = h.collect_listing(port, "fx")
        check(contents == [("k", 1048576, etag)], f"listing after failed write: {contents}")

        expect(h.request(port, "GET", "/health"), 200, "health after failed write")
        put_expect(port, "fx", "other", b"ok", "text/plain", "unrelated write")

        server.restart()
        port = server.port
        assert port is not None
        expect_present(port, "fx", "k", old, etag, "text/x-old", "old k after restart")
        expect_present(port, "fx", "other", b"ok", h.md5_etag(b"ok"), "text/plain", "other after restart")
    finally:
        server.close()


def p2_faults_space_released(binary: str, limits: Limits) -> None:
    server = _scratch_server(binary, limits)
    try:
        port = server.port
        assert port is not None
        make_buckets(port, "fx")
        small_size = 8 * 1024 * 1024
        small = _fault_body(small_size, seed=17)
        small_etag = h.md5_etag(small)
        expect(h.put_object(port, "fx", "base", b"base", "text/plain"), 200, "put base")

        for round_index, fill_key in enumerate(("fill", "fill2")):
            for attempt in range(2):
                status, detail = oversized_put(
                    port,
                    h.object_path("fx", "big"),
                    int(limits["fault_write_bytes"]),
                )
                check_failed_cleanly(status, detail, f"round {round_index} attempt {attempt}")
                expect(h.get_object(port, "fx", "big"), 404, f"big after failed write {attempt}")
            response = put_expect(
                port, "fx", fill_key, small, "application/octet-stream", f"8 MiB PUT {fill_key}"
            )
            check(response.header("etag") == small_etag, f"{fill_key} ETag mismatch")
            got = expect(h.get_object(port, "fx", fill_key), 200, f"GET {fill_key}")
            check(len(got.body) == small_size, f"{fill_key} length mismatch")
            check(got.header("etag") == small_etag, f"{fill_key} readback ETag mismatch")
            if round_index == 0:
                expect(h.request(port, "DELETE", h.object_path("fx", fill_key)), 204, "delete fill")

        server.restart()
        port = server.port
        assert port is not None
        expect_present(port, "fx", "fill2", small, small_etag, "application/octet-stream", "fill2 after restart")
        expect_present(port, "fx", "base", b"base", h.md5_etag(b"base"), "text/plain", "base after restart")
    finally:
        server.close()


def p2_faults_multipart_staging(binary: str, limits: Limits) -> None:
    server = _scratch_server(binary, limits)
    try:
        port = server.port
        assert port is not None
        make_buckets(port, "fx")
        uid = initiate(port, "fx", "mp", "application/x-mp")
        e1 = upload_part(port, "fx", "mp", uid, 1, b"part-one")

        status, detail = oversized_put(
            port,
            multipart_path("fx", "mp", uid, 2),
            int(limits["fault_write_bytes"]),
        )
        check_failed_cleanly(status, detail, "multipart-staging")

        parts = list_parts(port, "fx", "mp", uid)
        check(
            [(item["number"], item["etag"]) for item in parts] == [(1, e1)],
            f"failed part left a phantom part: {parts}",
        )
        e2 = upload_part(port, "fx", "mp", uid, 2, b"part-two")
        expect(complete_upload(port, "fx", "mp", uid, [(1, e1), (2, e2)]), 200, "complete mp")
        expect_present(
            port, "fx", "mp", b"part-onepart-two", h.md5_etag(b"part-onepart-two"),
            "application/x-mp", "mp after completion",
        )

        small = _fault_body(8 * 1024 * 1024, seed=19)
        put_expect(
            port, "fx", "after", small, "application/octet-stream",
            "8 MiB write after failed part",
        )
        server.restart()
        port = server.port
        assert port is not None
        expect_present(
            port, "fx", "mp", b"part-onepart-two", h.md5_etag(b"part-onepart-two"),
            "application/x-mp", "mp after restart",
        )
        after = expect(h.get_object(port, "fx", "after"), 200, "after after restart")
        check(after.header("etag") == h.md5_etag(small), "after ETag mismatch")
    finally:
        server.close()


FAULT_CASES: dict[str, Callable[[str, Limits], None]] = {
    "put-preserves-old": p2_faults_put_preserves_old,
    "space-released": p2_faults_space_released,
    "multipart-staging": p2_faults_multipart_staging,
}


def _run_fault_case(
    binary: str, func: Callable[[str, Limits], None], limits: Limits
) -> tuple[bool, float, str | None]:
    start = time.monotonic()
    try:
        func(binary, limits)
        return True, time.monotonic() - start, None
    except Exception as exc:  # noqa: BLE001
        return False, time.monotonic() - start, _error_text(exc)


# ---------------------------------------------------------------------------
# WP6: p1.histories


def _gen_seed(limits: Limits) -> int:
    return int(limits.get("generator_seed", limits["fixture_seed"]))


def _history(server: h.Server, limits: Limits, case_id: str) -> None:
    history_check.run_sequential(
        server, history_gen.generate(case_id, _gen_seed(limits)), limits
    )


def p1_histories_objects(server: h.Server, limits: Limits) -> None:
    _history(server, limits, "objects")


def p1_histories_multipart(server: h.Server, limits: Limits) -> None:
    _history(server, limits, "multipart")


def p1_histories_listing(server: h.Server, limits: Limits) -> None:
    _history(server, limits, "listing")


def p1_histories_restart(server: h.Server, limits: Limits) -> None:
    _history(server, limits, "restart")


def p1_histories_concurrent(server: h.Server, limits: Limits) -> None:
    history_check.run_concurrent(
        server, history_gen.generate("concurrent", _gen_seed(limits)), limits
    )


# ---------------------------------------------------------------------------
# Registry and execution


def _not_implemented(server: h.Server, limits: Limits) -> None:
    fail("case not implemented yet")


CASE_FUNCS: dict[str, dict[str, CaseFunc]] = {
    "p0.cli": {
        "roundtrip": p0_cli_roundtrip,
        "list-cleanup": p0_cli_list_cleanup,
        "errors": p0_cli_errors,
    },
    "p0.http": {
        "crud": p0_http_crud,
        "headers": p0_http_headers,
        "empty-missing": p0_http_empty_missing,
    },
    "p0.restart": {
        "objects": p0_restart_objects,
        "metadata": p0_restart_metadata,
    },
    "p1.range": {
        "closed": p1_range_closed,
        "open-suffix": p1_range_open_suffix,
        "bounds": p1_range_bounds,
        "after-replacement": p1_range_after_replacement,
    },
    "p1.conditional": {
        "etags": p1_conditional_etags,
        "dates": p1_conditional_dates,
        "precedence": p1_conditional_precedence,
        "after-restart": p1_conditional_after_restart,
    },
    "p1.multipart": {
        "roundtrip": p1_multipart_roundtrip,
        "stale-retry": p1_multipart_stale_retry,
        "invalid-complete": p1_multipart_invalid_complete,
        "abort-scope": p1_multipart_abort_scope,
        "abort-during-part": p1_multipart_abort_during_part,
        "concurrent-complete": p1_multipart_concurrent_complete,
        "concurrent-replace": p1_multipart_concurrent_replace,
        "abort-complete-race": p1_multipart_abort_complete_race,
        "list-during-complete": p1_multipart_list_during_complete,
        "list-during-abort": p1_multipart_list_during_abort,
        "parameter-errors": p1_multipart_parameter_errors,
    },
    "p1.listing": {
        "pages": p1_listing_pages,
        "groups": p1_listing_groups,
        "tokens-restart": p1_listing_tokens_restart,
        "list-type": p1_listing_list_type,
        "max-keys": p1_listing_max_keys,
        "page-limits": p1_listing_page_limits,
    },
    "p1.concurrency": {
        "coherent": p1_concurrency_coherent,
        "no-lost-writes": p1_concurrency_no_lost_writes,
        "progress": p1_concurrency_progress,
        "churn-replace": p1_concurrency_churn_replace,
        "churn-delete": p1_concurrency_churn_delete,
    },
    "p2.overwrite": {
        "incomplete-disconnect": p2_overwrite_incomplete_disconnect,
        "incomplete-kill": p2_overwrite_incomplete_kill,
        "publication-crash": p2_overwrite_publication_crash,
        "acknowledged": p2_overwrite_acknowledged,
    },
    "p2.multipart": {
        "cross-feature": p2_multipart_cross_feature,
        "crash-existing": p2_multipart_crash_existing,
        "crash-absent": p2_multipart_crash_absent,
    },
    "p2.isolation": {
        "identity": p2_isolation_identity,
        "escaping": p2_isolation_escaping,
        "traversal": p2_isolation_traversal,
    },
    "p2.perf": {},
    "p1.batch": {
        "copy-rename-delete": p1_batch_copy_rename_delete,
        "preconditions": p1_batch_preconditions,
        "errors": p1_batch_errors,
        "independence": p1_batch_independence,
        "cross-feature": p1_batch_cross_feature,
        "atomic-visibility": p1_batch_atomic_visibility,
        "concurrent-batches": p1_batch_concurrent_batches,
    },
    "p1.histories": {
        "objects": p1_histories_objects,
        "multipart": p1_histories_multipart,
        "listing": p1_histories_listing,
        "restart": p1_histories_restart,
        "concurrent": p1_histories_concurrent,
    },
    "p2.batch": {
        "kill-during-swap": p2_batch_kill_during_swap,
        "kill-after-ack": p2_batch_kill_after_ack,
        "blob-lifetime": p2_batch_blob_lifetime,
    },
    "p2.faults": {},
}


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, CaseFailure):
        return str(exc)
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()


def _run_case(binary: str, func: CaseFunc, limits: Limits) -> tuple[bool, float, str | None]:
    start = time.monotonic()
    try:
        with h.Server(binary) as server:
            func(server, limits)
        return True, time.monotonic() - start, None
    except Exception as exc:  # noqa: BLE001
        return False, time.monotonic() - start, _error_text(exc)


def run_family(binary: str, family: dict, limits: Limits) -> dict:
    results = []
    if family["id"] == "p2.perf":
        start = time.monotonic()
        with h.Server(binary) as server:
            outcomes = p2_perf_family(server, limits)
        duration = time.monotonic() - start
        for case_id in family["cases"]:
            ok, error = outcomes[case_id]
            results.append(
                {"id": case_id, "ok": ok, "duration": duration, "error": error}
            )
    elif family["id"] == "p2.faults":
        scratch_error = limits.get("scratch_error")
        for case_id in family["cases"]:
            if scratch_error:
                results.append(
                    {"id": case_id, "ok": False, "duration": 0.0, "error": scratch_error}
                )
                continue
            ok, duration, error = _run_fault_case(binary, FAULT_CASES[case_id], limits)
            results.append(
                {"id": case_id, "ok": ok, "duration": duration, "error": error}
            )
    else:
        functions = CASE_FUNCS[family["id"]]
        for case_id in family["cases"]:
            func = functions[case_id]
            ok, duration, error = _run_case(binary, func, limits)
            results.append(
                {"id": case_id, "ok": ok, "duration": duration, "error": error}
            )
    return {"id": family["id"], "title": family["title"], "cases": results}


def run_suite(
    binary: str,
    manifest: dict,
    *,
    family: str | None = None,
    scratch: str | None = None,
    scratch_error: str | None = None,
    generator_seed: int | None = None,
) -> dict:
    limits = dict(manifest["limits"])
    limits["fixture_seed"] = manifest.get("seed", 1)
    if scratch is not None:
        limits["scratch_dir"] = scratch
    if scratch_error is not None:
        limits["scratch_error"] = scratch_error
    if generator_seed is not None:
        limits["generator_seed"] = generator_seed
    selected = [
        item for item in manifest["families"] if family is None or item["id"] == family
    ]
    if family is not None and not selected:
        raise SystemExit(f"unknown family {family!r}")
    tests = []
    for entry in selected:
        tests.append(run_family(binary, entry, limits))
    return {
        "schema": "opencode-bench/locker-tests@1",
        "suite_version": manifest["suite_version"],
        "seed": manifest.get("seed", 1),
        "diagnostic": family is not None or generator_seed is not None,
        "tests": tests,
    }
