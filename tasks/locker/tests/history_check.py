"""Execute generated histories against a Locker server and compare with the model.

Sequential histories compare every response field with ``state_model``. The
concurrent history explores every serialization consistent with real-time
precedence and then filters candidate states with quiescent probes.
"""

from __future__ import annotations

import itertools
import threading
import time
import urllib.parse
import xml.sax.saxutils

import helpers as h
import state_model as m
from history_gen import BODIES

CaseFailure = h.CaseFailure


def _fail(index: int, op, field: str, expected, got) -> None:
    raise CaseFailure(
        f"op #{index} {op.kind} {op.args}: {field} expected {expected!r} got {got!r}"
    )


def _mp_path(bucket: str, key: str, upload_id: str, part: int | None = None) -> str:
    query = [("uploadId", upload_id)]
    if part is not None:
        query.append(("partNumber", str(part)))
    return h.object_path(bucket, key) + "?" + urllib.parse.urlencode(query)


def _completion_xml(parts: list) -> bytes:
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


def _batch_xml(ops: list) -> bytes:
    parts = ["<Batch>"]
    for op in ops:
        kind = op[0]
        if kind in ("copy", "rename"):
            _, source, target, if_match = op
            tag = "Copy" if kind == "copy" else "Rename"
            parts.append(
                f"<{tag}><Source>{xml.sax.saxutils.escape(source)}</Source>"
                f"<Target>{xml.sax.saxutils.escape(target)}</Target>"
            )
        else:
            _, key, if_match = op
            tag = "Delete"
            parts.append(f"<Delete><Key>{xml.sax.saxutils.escape(key)}</Key>")
        if if_match is not None:
            parts.append(f"<IfMatch>{xml.sax.saxutils.escape(if_match)}</IfMatch>")
        parts.append(f"</{tag}>")
    parts.append("</Batch>")
    return "".join(parts).encode("utf-8")


def _headers(args: dict) -> dict[str, str]:
    headers = {}
    if args.get("range") is not None:
        headers["Range"] = args["range"]
    if args.get("if_match") is not None:
        headers["If-Match"] = args["if_match"]
    if args.get("if_none_match") is not None:
        headers["If-None-Match"] = args["if_none_match"]
    return headers


def _execute(port: int, op, slots: dict) -> tuple[h.Response, str | None]:
    args = op.args
    kind = op.kind
    if kind == "create_bucket":
        return h.create_bucket(port, args["bucket"]), None
    if kind == "delete_bucket":
        return h.delete_bucket(port, args["bucket"]), None
    if kind == "put":
        return (
            h.put_object(
                port,
                args["bucket"],
                args["key"],
                BODIES[args["body_index"]],
                args["content_type"],
            ),
            None,
        )
    if kind == "delete":
        return h.request(port, "DELETE", h.object_path(args["bucket"], args["key"])), None
    if kind in ("get", "head"):
        call = h.get_object if kind == "get" else h.head_object
        return call(port, args["bucket"], args["key"], _headers(args)), None
    if kind == "batch":
        return (
            h.request(
                port,
                "POST",
                h.bucket_path(args["bucket"]) + "?batch",
                body=_batch_xml(args["ops"]),
                headers={"Content-Type": "application/xml"},
            ),
            None,
        )
    if kind == "list_bad":
        return h.request(port, "GET", h.bucket_path(args["bucket"]) + "?" + args["query"]), None
    if kind == "initiate":
        headers = {}
        if args["content_type"] is not None:
            headers["Content-Type"] = args["content_type"]
        response = h.request(
            port, "POST", h.object_path(args["bucket"], args["key"]) + "?uploads", headers=headers
        )
        upload_id = None
        if response.status == 200:
            upload_id = h.text(h.parse_xml(response.body), "UploadId")
        return response, upload_id
    if kind == "part":
        info = slots[args["slot"]]
        return (
            h.request(
                port,
                "PUT",
                _mp_path(info["bucket"], info["key"], info["upload_id"], args["number"]),
                body=BODIES[args["body_index"]],
                headers={"Content-Type": "application/octet-stream"},
            ),
            None,
        )
    if kind == "list_parts":
        info = slots[args["slot"]]
        return (
            h.request(port, "GET", _mp_path(info["bucket"], info["key"], info["upload_id"])),
            None,
        )
    if kind == "complete":
        info = slots[args["slot"]]
        return (
            h.request(
                port,
                "POST",
                _mp_path(info["bucket"], info["key"], info["upload_id"]),
                body=_completion_xml(args["parts"]),
                headers={"Content-Type": "application/xml"},
            ),
            None,
        )
    if kind == "abort":
        info = slots[args["slot"]]
        return (
            h.request(port, "DELETE", _mp_path(info["bucket"], info["key"], info["upload_id"])),
            None,
        )
    if kind == "dead_use":
        info = slots[args["slot"]]
        what = args["what"]
        if what == "part":
            return (
                h.request(
                    port,
                    "PUT",
                    _mp_path(info["bucket"], info["key"], info["upload_id"], 1),
                    body=BODIES[0],
                    headers={"Content-Type": "application/octet-stream"},
                ),
                None,
            )
        if what == "list_parts":
            return (
                h.request(port, "GET", _mp_path(info["bucket"], info["key"], info["upload_id"])),
                None,
            )
        if what == "complete":
            return (
                h.request(
                    port,
                    "POST",
                    _mp_path(info["bucket"], info["key"], info["upload_id"]),
                    body=_completion_xml([(1, m.quote_etag(BODIES[0]))]),
                    headers={"Content-Type": "application/xml"},
                ),
                None,
            )
        return (
            h.request(port, "DELETE", _mp_path(info["bucket"], info["key"], info["upload_id"])),
            None,
        )
    raise CaseFailure(f"unsupported history operation {kind}")


def _expected(state: m.State, op, slots: dict) -> m.Expected:
    args = op.args
    kind = op.kind
    if kind == "create_bucket":
        return m.create_bucket(state, args["bucket"])
    if kind == "delete_bucket":
        return m.delete_bucket(state, args["bucket"])
    if kind == "put":
        return m.put(
            state, args["bucket"], args["key"], BODIES[args["body_index"]], args["content_type"]
        )
    if kind == "delete":
        return m.delete(state, args["bucket"], args["key"])
    if kind == "get":
        return m.get(
            state,
            args["bucket"],
            args["key"],
            args["range"],
            args["if_match"],
            args["if_none_match"],
        )
    if kind == "head":
        return m.head(
            state,
            args["bucket"],
            args["key"],
            args["range"],
            args["if_match"],
            args["if_none_match"],
        )
    if kind == "batch":
        return m.batch(state, args["bucket"], [tuple(item) for item in args["ops"]])
    if kind == "part":
        info = slots[args["slot"]]
        return m.upload_part(
            state,
            info["bucket"],
            info["key"],
            info["upload_id"],
            args["number"],
            BODIES[args["body_index"]],
        )
    if kind == "list_parts":
        info = slots[args["slot"]]
        return m.list_parts(state, info["bucket"], info["key"], info["upload_id"])
    if kind == "complete":
        info = slots[args["slot"]]
        return m.complete(
            state,
            info["bucket"],
            info["key"],
            info["upload_id"],
            [(int(number), etag) for number, etag in args["parts"]],
        )
    if kind == "abort":
        info = slots[args["slot"]]
        return m.abort(state, info["bucket"], info["key"], info["upload_id"])
    if kind == "dead_use":
        return m.Expected(status=404, code="NoSuchUpload")
    if kind == "list_bad":
        return m.Expected(status=400, code="InvalidArgument")
    raise CaseFailure(f"unsupported history operation {kind}")


def _response_matches(exp: m.Expected, response: h.Response, kind: str) -> bool:
    if response.status != exp.status:
        return False
    if exp.code is not None and kind != "head":
        try:
            root = h.parse_xml(response.body)
        except CaseFailure:
            return False
        if root.tag != "Error" or h.text(root, "Code", "") != exp.code:
            return False
    if exp.etag is not None and kind != "complete" and response.header("etag") != exp.etag:
        return False
    if exp.content_length is not None and response.header("content-length") != str(exp.content_length):
        return False
    if exp.content_range is not None and response.header("content-range") != exp.content_range:
        return False
    if exp.content_type is not None and response.header("content-type") != exp.content_type:
        return False
    if exp.body is not None and response.body != exp.body:
        return False
    if kind == "head" and exp.status in (200, 206) and response.body != b"":
        return False
    return True


def _compare(index: int, op, exp: m.Expected, response: h.Response) -> None:
    if response.status != exp.status:
        _fail(index, op, "status", exp.status, response.status)
    if exp.code is not None and op.kind != "head":
        root = h.parse_xml(response.body)
        got = h.text(root, "Code", "")
        if root.tag != "Error" or got != exp.code:
            _fail(index, op, "error code", exp.code, got)
    if exp.etag is not None and op.kind != "complete" and response.header("etag") != exp.etag:
        _fail(index, op, "ETag", exp.etag, response.header("etag"))
    if exp.content_length is not None and response.header("content-length") != str(exp.content_length):
        _fail(index, op, "Content-Length", exp.content_length, response.header("content-length"))
    if exp.content_range is not None and response.header("content-range") != exp.content_range:
        _fail(index, op, "Content-Range", exp.content_range, response.header("content-range"))
    if exp.content_type is not None and response.header("content-type") != exp.content_type:
        _fail(index, op, "Content-Type", exp.content_type, response.header("content-type"))
    if exp.body is not None and response.body != exp.body:
        _fail(index, op, "body", exp.body, response.body[:200])
    if op.kind == "head" and exp.status in (200, 206) and response.body != b"":
        _fail(index, op, "HEAD body", b"", response.body[:200])
    if op.kind in ("get", "head") and exp.status in (200, 206):
        if response.header("accept-ranges") != "bytes":
            _fail(index, op, "Accept-Ranges", "bytes", response.header("accept-ranges"))
        try:
            h.parse_http_date(response.header("last-modified"))
        except (ValueError, TypeError) as exc:
            _fail(index, op, "Last-Modified", "an HTTP date", str(exc))
    if op.kind == "list_parts" and exp.parts is not None:
        root = h.parse_xml(response.body)
        if root.tag != "ListPartsResult":
            _fail(index, op, "list-parts root", "ListPartsResult", root.tag)
        got_parts = [
            (int(h.text(item, "PartNumber")), h.text(item, "ETag"), int(h.text(item, "Size")))
            for item in root.findall("Part")
        ]
        if sorted(got_parts) != sorted(exp.parts):
            _fail(index, op, "parts", sorted(exp.parts), sorted(got_parts))
    if op.kind == "complete" and exp.status == 200:
        root = h.parse_xml(response.body)
        got = h.text(root, "ETag", "")
        if got != exp.etag:
            _fail(index, op, "completion ETag", exp.etag, got)


def _list_request(
    port: int,
    bucket: str,
    prefix: str,
    delimiter: str,
    max_keys: int,
    token: str | None,
) -> dict:
    query = [("list-type", "2"), ("prefix", prefix)]
    if delimiter:
        query.append(("delimiter", delimiter))
    if max_keys is not None:
        query.append(("max-keys", str(max_keys)))
    if token:
        query.append(("continuation-token", token))
    path = h.bucket_path(bucket) + "?" + urllib.parse.urlencode(query)
    response = h.request(port, "GET", path)
    result = {"status": response.status, "body": response.body}
    if response.status != 200:
        return result
    root = h.parse_xml(response.body)
    result.update(
        contents=[
            (h.text(item, "Key"), int(h.text(item, "Size")), h.text(item, "ETag"))
            for item in root.findall("Contents")
        ],
        prefixes=[h.text(item, "Prefix") for item in root.findall("CommonPrefixes")],
        max_keys=int(h.text(root, "MaxKeys", "0")),
        key_count=int(h.text(root, "KeyCount", "0")),
        truncated=h.text(root, "IsTruncated", "false").lower() == "true",
        token=h.text(root, "NextContinuationToken", ""),
        prefix=h.text(root, "Prefix", ""),
        delimiter=h.text(root, "Delimiter", ""),
    )
    return result


def _page_matches(exp: m.Expected, got: dict, state: m.State, bucket: str, max_keys: int) -> bool:
    if got.get("status") != 200:
        return False
    expected_objects = []
    expected_prefixes = []
    for kind, name in exp.listing or []:
        if kind == "prefix":
            expected_prefixes.append(name)
        else:
            obj = state.buckets[bucket][name]
            expected_objects.append((name, len(obj.body), obj.etag))
    if sorted(expected_objects) != sorted(got["contents"]):
        return False
    if sorted(expected_prefixes) != sorted(got["prefixes"]):
        return False
    if got["key_count"] != len(exp.listing or []):
        return False
    if got["truncated"] != exp.truncated:
        return False
    if bool(got["token"]) != exp.truncated:
        return False
    if got["max_keys"] != max_keys:
        return False
    return True


def _compare_page(index: int, op, exp: m.Expected, got: dict, state: m.State, bucket: str, max_keys: int) -> None:
    if got.get("status") != 200:
        _fail(index, op, "list status", 200, got.get("status"))
    expected_objects = []
    expected_prefixes = []
    for kind, name in exp.listing or []:
        if kind == "prefix":
            expected_prefixes.append(name)
        else:
            obj = state.buckets[bucket][name]
            expected_objects.append((name, len(obj.body), obj.etag))
    if sorted(expected_objects) != sorted(got["contents"]):
        _fail(index, op, "list objects", sorted(expected_objects), sorted(got["contents"]))
    if sorted(expected_prefixes) != sorted(got["prefixes"]):
        _fail(index, op, "list prefixes", sorted(expected_prefixes), sorted(got["prefixes"]))
    if got["key_count"] != len(exp.listing or []):
        _fail(index, op, "KeyCount", len(exp.listing or []), got["key_count"])
    if got["truncated"] != exp.truncated:
        _fail(index, op, "IsTruncated", exp.truncated, got["truncated"])
    if bool(got["token"]) != exp.truncated:
        _fail(index, op, "NextContinuationToken", exp.truncated, got["token"])
    if got["max_keys"] != max_keys:
        _fail(index, op, "MaxKeys echo", max_keys, got["max_keys"])
    if got["prefix"] != op.args["prefix"]:
        _fail(index, op, "Prefix echo", op.args["prefix"], got["prefix"])
    if got["delimiter"] != op.args["delimiter"]:
        _fail(index, op, "Delimiter echo", op.args["delimiter"], got["delimiter"])


def _follow_listing(
    index: int,
    op,
    state: m.State,
    port: int,
    token: str | None = None,
    page_index: int = 0,
) -> None:
    args = op.args
    bucket = args["bucket"]
    prefix = args["prefix"]
    delimiter = args["delimiter"]
    max_keys = args["max_keys"]
    while True:
        exp = m.list_page(state, bucket, prefix, delimiter, max_keys, page_index)
        got = _list_request(port, bucket, prefix, delimiter, max_keys, token)
        if exp.status != 200:
            _compare(index, op, exp, h.Response(got["status"], {}, got["body"]))
            return
        _compare_page(index, op, exp, got, state, bucket, max_keys)
        if exp.truncated:
            if len(exp.listing or []) != max_keys:
                _fail(index, op, "page length", max_keys, len(exp.listing or []))
        if not exp.truncated:
            return
        token = got["token"]
        page_index += 1


def run_sequential(server: h.Server, ops: list, limits: dict) -> None:
    port = server.port
    assert port is not None
    state = m.State()
    slots: dict[int, dict] = {}
    pending = None
    for index, op in enumerate(ops):
        if op.kind == "restart":
            server.restart()
            port = server.port
            assert port is not None
            if pending is not None:
                _follow_listing(index, pending["op"], state, port, pending["token"], 1)
                pending = None
            continue
        if pending is not None:
            raise CaseFailure(f"op #{index}: split listing was not followed by a restart")
        if op.kind == "initiate":
            args = op.args
            bucket_ok = args["bucket"] in state.buckets
            response, upload_id = _execute(port, op, slots)
            if bucket_ok:
                if response.status != 200:
                    _fail(index, op, "status", 200, response.status)
                if not upload_id:
                    raise CaseFailure(f"op #{index} initiate: response had no UploadId")
                m.initiate(
                    state, args["bucket"], args["key"], args["content_type"], upload_id
                )
                slots[args["slot"]] = {
                    "bucket": args["bucket"],
                    "key": args["key"],
                    "upload_id": upload_id,
                }
            else:
                if response.status != 404:
                    _fail(index, op, "status", 404, response.status)
                root = h.parse_xml(response.body)
                got_code = h.text(root, "Code", "")
                if root.tag != "Error" or got_code != "NoSuchBucket":
                    _fail(index, op, "error code", "NoSuchBucket", got_code)
            continue
        if op.kind == "list":
            if op.args.get("split"):
                exp = m.list_page(
                    state,
                    op.args["bucket"],
                    op.args["prefix"],
                    op.args["delimiter"],
                    op.args["max_keys"],
                    0,
                )
                got = _list_request(
                    port,
                    op.args["bucket"],
                    op.args["prefix"],
                    op.args["delimiter"],
                    op.args["max_keys"],
                    None,
                )
                if exp.status != 200:
                    _compare(index, op, exp, h.Response(got["status"], {}, got["body"]))
                else:
                    _compare_page(index, op, exp, got, state, op.args["bucket"], op.args["max_keys"])
                    if exp.truncated:
                        pending = {"op": op, "token": got["token"]}
                continue
            _follow_listing(index, op, state, port)
            continue
        response, _extra = _execute(port, op, slots)
        exp = _expected(state, op, slots)
        _compare(index, op, exp, response)


def _touched(ops: list) -> list[tuple[str, str]]:
    seen = []
    for op in ops:
        args = op.args
        if op.kind in ("put", "delete", "get", "head"):
            seen.append((args["bucket"], args["key"]))
        elif op.kind == "batch":
            for item in args["ops"]:
                if item[0] in ("copy", "rename"):
                    seen.append((args["bucket"], item[1]))
                    seen.append((args["bucket"], item[2]))
                else:
                    seen.append((args["bucket"], item[1]))
    out = []
    for item in seen:
        if item not in out:
            out.append(item)
    return out


def _canonical(state: m.State) -> str:
    buckets = sorted(
        (name, sorted((key, obj.etag, obj.content_type) for key, obj in objects.items()))
        for name, objects in state.buckets.items()
    )
    uploads = sorted(
        (upload_id, upload.bucket, upload.key, sorted((n, m.quote_etag(b)) for n, b in upload.parts.items()))
        for upload_id, upload in state.uploads.items()
    )
    return repr((buckets, uploads))


def _real_time_ok(order: tuple[int, ...], starts: list[int], ends: list[int]) -> bool:
    position = {index: slot for slot, index in enumerate(order)}
    count = len(order)
    for first in range(count):
        for second in range(count):
            if first == second:
                continue
            if ends[first] < starts[second] and position[first] > position[second]:
                return False
    return True


def run_concurrent(server: h.Server, ops: list, limits: dict) -> None:
    port = server.port
    assert port is not None
    state = m.State()
    slots: dict[int, dict] = {}
    for op in [item for item in ops if item.group == 0]:
        response, _extra = _execute(port, op, slots)
        _compare(0, op, _expected(state, op, slots), response)

    groups: dict[int, list] = {}
    for op in ops:
        if op.group > 0:
            groups.setdefault(op.group, []).append(op)

    candidates = [state]
    timeout = float(limits["request_deadline_s"]) + 10
    for group_index in sorted(groups):
        group_ops = groups[group_index]
        count = len(group_ops)
        observed: list[h.Response | None] = [None] * count
        starts = [0] * count
        ends = [0] * count
        errors: list[BaseException | None] = [None] * count
        barrier = threading.Barrier(count)

        def runner(index: int) -> None:
            try:
                barrier.wait(timeout=10)
                starts[index] = time.monotonic_ns()
                observed[index] = _execute(port, group_ops[index], slots)[0]
                ends[index] = time.monotonic_ns()
            except BaseException as exc:  # noqa: BLE001
                errors[index] = exc

        threads = [threading.Thread(target=runner, args=(index,), daemon=True) for index in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=timeout)
        for thread in threads:
            if thread.is_alive():
                raise CaseFailure(f"group {group_index}: concurrent request hung")
        for error in errors:
            if error is not None:
                raise error
        for response in observed:
            assert response is not None
            if response.status >= 500:
                raise CaseFailure(f"group {group_index}: concurrent request returned {response.status}")

        next_candidates: dict[str, m.State] = {}
        for candidate in candidates:
            for order in itertools.permutations(range(count)):
                if not _real_time_ok(order, starts, ends):
                    continue
                trial = candidate.clone()
                good = True
                for index in order:
                    exp = _expected(trial, group_ops[index], slots)
                    assert observed[index] is not None
                    if not _response_matches(exp, observed[index], group_ops[index].kind):
                        good = False
                        break
                if good:
                    next_candidates[_canonical(trial)] = trial
        if not next_candidates:
            raise CaseFailure(
                f"group {group_index}: no serialization explains "
                f"{[(op.kind, op.args) for op in group_ops]} observed "
                f"{[(response.status, response.body[:120]) for response in observed]}"
            )
        if len(next_candidates) > 32:
            raise CaseFailure("checker state explosion")

        touched = _touched(group_ops)
        observed_probes = []
        for bucket, key in touched:
            observed_probes.append((bucket, key, h.get_object(port, bucket, key)))
        buckets = []
        for bucket, _key in touched:
            if bucket not in buckets:
                buckets.append(bucket)
        list_probes = []
        for bucket in buckets:
            list_probes.append((bucket, _list_request(port, bucket, "", "", 1000, None)))

        filtered = []
        for candidate in next_candidates.values():
            good = True
            for bucket, key, response in observed_probes:
                if not _response_matches(m.get(candidate, bucket, key), response, "get"):
                    good = False
                    break
            if not good:
                continue
            for bucket, got in list_probes:
                exp = m.list_page(candidate, bucket, "", "", 1000, 0)
                if not _page_matches(exp, got, candidate, bucket, 1000):
                    good = False
                    break
            if good:
                filtered.append(candidate)
        if not filtered:
            raise CaseFailure(
                f"group {group_index}: no candidate state matches the quiescent reads"
            )
        candidates = filtered
