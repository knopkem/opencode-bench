"""Independent state model for the documented Locker contract.

This module is derived from ``PROMPT.md`` only. It has no I/O, no threads and no
knowledge of the Go reference; the hidden history checker uses it as the oracle
for generated operation sequences.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

import oracles


def quote_etag(body: bytes) -> str:
    return '"' + hashlib.md5(body).hexdigest() + '"'


def _matches(if_match: str, etag: str) -> bool:
    if if_match == "*":
        return True
    return if_match.strip('"') == etag.strip('"')


@dataclass(frozen=True)
class Obj:
    body: bytes
    content_type: str

    @property
    def etag(self) -> str:
        return quote_etag(self.body)


@dataclass
class Upload:
    bucket: str
    key: str
    content_type: str
    parts: dict[int, bytes] = field(default_factory=dict)


@dataclass
class State:
    buckets: dict[str, dict[str, Obj]] = field(default_factory=dict)
    uploads: dict[str, Upload] = field(default_factory=dict)

    def clone(self) -> "State":
        return State(
            buckets={name: dict(objects) for name, objects in self.buckets.items()},
            uploads={
                upload_id: Upload(
                    upload.bucket,
                    upload.key,
                    upload.content_type,
                    dict(upload.parts),
                )
                for upload_id, upload in self.uploads.items()
            },
        )


@dataclass
class Expected:
    status: int
    etag: str | None = None
    body: bytes | None = None
    content_length: int | None = None
    content_range: str | None = None
    content_type: str | None = None
    code: str | None = None
    listing: list[tuple[str, str]] | None = None
    truncated: bool | None = None
    parts: list[tuple[int, str, int]] | None = None


def _error(status: int, code: str) -> Expected:
    return Expected(status=status, code=code)


def _content_type(value: str | None) -> str:
    return value or "application/octet-stream"


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def create_bucket(state: State, bucket: str) -> Expected:
    state.buckets.setdefault(bucket, {})
    return Expected(status=200)


def delete_bucket(state: State, bucket: str) -> Expected:
    objects = state.buckets.get(bucket)
    if objects is None:
        return _error(404, "NoSuchBucket")
    if objects:
        return _error(409, "BucketNotEmpty")
    if any(upload.bucket == bucket for upload in state.uploads.values()):
        return _error(409, "BucketNotEmpty")
    del state.buckets[bucket]
    return Expected(status=204)


def put(state: State, bucket: str, key: str, body: bytes, content_type: str | None) -> Expected:
    objects = state.buckets.get(bucket)
    if objects is None:
        return _error(404, "NoSuchBucket")
    obj = Obj(body, _content_type(content_type))
    objects[key] = obj
    return Expected(status=200, etag=obj.etag)


def delete(state: State, bucket: str, key: str) -> Expected:
    objects = state.buckets.get(bucket)
    if objects is None:
        return _error(404, "NoSuchBucket")
    objects.pop(key, None)
    return Expected(status=204)


def get(
    state: State,
    bucket: str,
    key: str,
    range_spec: str | None = None,
    if_match: str | None = None,
    if_none_match: str | None = None,
) -> Expected:
    objects = state.buckets.get(bucket)
    if objects is None:
        return _error(404, "NoSuchBucket")
    obj = objects.get(key)
    if obj is None:
        return _error(404, "NoSuchKey")

    action = oracles.conditional_action(
        if_match=if_match,
        if_none_match=if_none_match,
        etag=obj.etag,
        modified=_EPOCH,
    )
    if action == "precondition-failed":
        return Expected(status=412)
    if action == "not-modified":
        return Expected(status=304, etag=obj.etag)

    size = len(obj.body)
    if range_spec is not None:
        span = oracles.expected_range(size, range_spec)
        if span is None:
            return Expected(
                status=416,
                content_range=f"bytes */{size}",
            )
        start, end = span
        return Expected(
            status=206,
            etag=obj.etag,
            body=obj.body[start : end + 1],
            content_length=end - start + 1,
            content_range=f"bytes {start}-{end}/{size}",
            content_type=obj.content_type,
        )
    return Expected(
        status=200,
        etag=obj.etag,
        body=obj.body,
        content_length=size,
        content_type=obj.content_type,
    )


def head(
    state: State,
    bucket: str,
    key: str,
    range_spec: str | None = None,
    if_match: str | None = None,
    if_none_match: str | None = None,
) -> Expected:
    expected = get(state, bucket, key, range_spec, if_match, if_none_match)
    if expected.body is not None:
        expected.body = None
    return expected


def list_page(
    state: State,
    bucket: str,
    prefix: str = "",
    delimiter: str = "",
    max_keys: int = 1000,
    page_index: int = 0,
) -> Expected:
    objects = state.buckets.get(bucket)
    if objects is None:
        return _error(404, "NoSuchBucket")
    items = oracles.effective_listing(sorted(objects), prefix, delimiter)
    pages = oracles.paginate(items, max_keys)
    page = pages[page_index] if page_index < len(pages) else []
    return Expected(
        status=200,
        listing=page,
        truncated=page_index < len(pages) - 1,
    )


def initiate(
    state: State, bucket: str, key: str, content_type: str | None, upload_id: str
) -> Expected:
    if bucket not in state.buckets:
        return _error(404, "NoSuchBucket")
    state.uploads[upload_id] = Upload(bucket, key, _content_type(content_type))
    return Expected(status=200)


def _upload(state: State, bucket: str, key: str, upload_id: str) -> Upload | None:
    upload = state.uploads.get(upload_id)
    if upload is None or upload.bucket != bucket or upload.key != key:
        return None
    return upload


def upload_part(
    state: State, bucket: str, key: str, upload_id: str, number: int, body: bytes
) -> Expected:
    upload = _upload(state, bucket, key, upload_id)
    if upload is None:
        return _error(404, "NoSuchUpload")
    upload.parts[number] = body
    return Expected(status=200, etag=quote_etag(body))


def list_parts(state: State, bucket: str, key: str, upload_id: str) -> Expected:
    upload = _upload(state, bucket, key, upload_id)
    if upload is None:
        return _error(404, "NoSuchUpload")
    parts = [
        (number, quote_etag(body), len(body))
        for number, body in sorted(upload.parts.items())
    ]
    return Expected(status=200, parts=parts)


def complete(
    state: State,
    bucket: str,
    key: str,
    upload_id: str,
    requested: list[tuple[int, str]],
) -> Expected:
    upload = _upload(state, bucket, key, upload_id)
    if upload is None:
        return _error(404, "NoSuchUpload")
    seen: set[int] = set()
    numbers: list[int] = []
    for number, etag in requested:
        if number in seen or number not in upload.parts:
            return _error(400, "InvalidArgument")
        if not _matches(etag, quote_etag(upload.parts[number])):
            return _error(400, "InvalidArgument")
        seen.add(number)
        numbers.append(number)
    if not numbers:
        return _error(400, "InvalidArgument")
    body = b"".join(upload.parts[number] for number in sorted(numbers))
    objects = state.buckets.get(bucket)
    if objects is None:
        return _error(404, "NoSuchBucket")
    obj = Obj(body, upload.content_type)
    objects[key] = obj
    del state.uploads[upload_id]
    return Expected(status=200, etag=obj.etag)


def abort(state: State, bucket: str, key: str, upload_id: str) -> Expected:
    if _upload(state, bucket, key, upload_id) is None:
        return _error(404, "NoSuchUpload")
    del state.uploads[upload_id]
    return Expected(status=204)


_BATCH_LIMIT = 32


def batch(state: State, bucket: str, ops: list[tuple]) -> Expected:
    objects = state.buckets.get(bucket)
    if objects is None:
        return _error(404, "NoSuchBucket")
    if not 1 <= len(ops) <= _BATCH_LIMIT:
        return _error(400, "InvalidArgument")

    working = dict(objects)
    for op in ops:
        kind = op[0]
        if kind in ("copy", "rename"):
            if len(op) != 4:
                return _error(400, "InvalidArgument")
            _, source, target, if_match = op
            if not source or not target or source == target:
                return _error(400, "InvalidArgument")
            if if_match is not None and not (
                if_match == "*"
                or (len(if_match) >= 3 and if_match.startswith('"') and if_match.endswith('"'))
            ):
                return _error(400, "InvalidArgument")
            src = working.get(source)
            if src is None or (if_match is not None and not _matches(if_match, src.etag)):
                return Expected(status=412, code="PreconditionFailed")
            working[target] = Obj(src.body, src.content_type)
            if kind == "rename":
                del working[source]
        elif kind == "delete":
            if len(op) != 3:
                return _error(400, "InvalidArgument")
            _, key, if_match = op
            if not key:
                return _error(400, "InvalidArgument")
            if if_match is not None:
                if if_match != "*" and not (
                    len(if_match) >= 3 and if_match.startswith('"') and if_match.endswith('"')
                ):
                    return _error(400, "InvalidArgument")
                current = working.get(key)
                if current is None or not _matches(if_match, current.etag):
                    return Expected(status=412, code="PreconditionFailed")
            working.pop(key, None)
        else:
            return _error(400, "InvalidArgument")

    state.buckets[bucket] = working
    return Expected(status=200)
