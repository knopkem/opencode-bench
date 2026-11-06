"""Hidden black-box families for Locker.

Every case is scored independently. A setup failure fails that case; it never
turns into a pass or shrinks the manifest denominator. The runner is stdlib
only and never imports candidate or reference packages.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import xml.sax.saxutils
from datetime import timedelta
from typing import Callable

import helpers as h
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
        if "part" in outcome:
            part_response = outcome["part"]
            assert isinstance(part_response, Response)
            check(part_response.status in (200, 404), f"part upload returned {part_response.status}")
        elif "part_error" in outcome:
            fail(f"part upload raised: {outcome['part_error']}")
        check("list" in outcome, f"list raised: {outcome.get('list_error')}")
        listed = outcome["list"]
        assert isinstance(listed, Response)
        check(listed.status in (200, 404), f"list during abort returned {listed.status}")
        if listed.status == 200:
            root = h.parse_xml(listed.body)
            check(len(root.findall("Part")) == 1, "list raced past abort and returned an empty part list")
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
        response = h.get_object(port, "concurrent", "held", timeout=max(1.0, deadline - time.monotonic()))
        expect_object(response, old, "text/plain", "old object during stalled upload")
        response = h.put_object(port, "concurrent", "unrelated", b"independent", "text/plain")
        expect(response, 200, "unrelated PUT during stalled upload")
        response = h.get_object(port, "concurrent", "unrelated")
        expect_object(response, b"independent", "text/plain", "unrelated GET")
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
        body = h.DeterministicBody(int(limits["stream_bytes"]), seed=1)
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
# Registry and execution


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
    else:
        functions = CASE_FUNCS[family["id"]]
        for case_id in family["cases"]:
            func = functions[case_id]
            ok, duration, error = _run_case(binary, func, limits)
            results.append(
                {"id": case_id, "ok": ok, "duration": duration, "error": error}
            )
    return {"id": family["id"], "title": family["title"], "cases": results}


def run_suite(binary: str, manifest: dict, *, family: str | None = None) -> dict:
    limits = manifest["limits"]
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
        "diagnostic": family is not None,
        "tests": tests,
    }
