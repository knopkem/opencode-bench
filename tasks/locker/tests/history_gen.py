"""Deterministic operation-history generator for the Locker hidden suite.

The sequence is a pure function of ``(case_id, seed)``. The generator keeps a
private state model only to choose plausible keys, bodies and validators; the
checker owns the authoritative model when the history runs.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

import state_model as m


BUCKETS = ("h1", "h2")
KEYS = [
    "a",
    "b",
    "c",
    "dir/x",
    "dir/y",
    "dir/sub/z",
    "e",
    "k-1",
    "k-2",
    "k-10",
    "ü-key",
    "sp ace",
]
SIZES = [0, 1, 7, 100, 1000, 4096, 8192]
CONTENT_TYPES = [None, "text/plain", "application/json", "image/png; charset=binary"]
RANGES = [None, "bytes=0-0", "bytes=5-", "bytes=-3", "bytes=2-1", "bytes=100000-", "bytes=0-99999"]
PREFIXES = ["", "dir/", "dir/sub/", "k-"]
DELIMITERS = ["", "/"]
MAX_KEYS = [1, 2, 3, 5, 1000]
LIST_BAD_QUERIES = [
    "max-keys=0",
    "max-keys=1001",
    "max-keys=",
    "max-keys=x",
    "list-type=3",
    "continuation-token=bogus",
]


def _make_bodies() -> list[bytes]:
    bodies = []
    for index in range(16):
        size = 0 if index == 0 else SIZES[1 + (index - 1) % 6]
        if size == 0:
            bodies.append(b"")
            continue
        raw = hashlib.sha256(f"body{index}".encode()).digest()
        bodies.append((raw * ((size // len(raw)) + 1))[:size])
    return bodies


BODIES = _make_bodies()


@dataclass(frozen=True)
class Op:
    kind: str
    args: dict
    group: int = 0


class _Gen:
    def __init__(self, case_id: str, seed: int) -> None:
        self.rng = random.Random(f"{seed}:{case_id}")
        self.state = m.State()
        self.slots: dict[int, dict] = {}
        self.live: set[int] = set()
        self.dead: set[int] = set()
        self.part_history: dict[int, dict[int, list[str]]] = {}
        self.ops: list[Op] = []

    # -- primitives ---------------------------------------------------------

    def emit(self, op: Op) -> Op:
        self.ops.append(op)
        return op

    def conditional(self, bucket: str, key: str) -> str | None:
        roll = self.rng.random()
        if roll < 0.5:
            return None
        if roll < 0.7:
            return "*"
        obj = self.state.buckets.get(bucket, {}).get(key)
        if obj is not None and roll < 0.9:
            return obj.etag
        return m.quote_etag(self.rng.choice(BODIES))

    def create_bucket(self, bucket: str | None = None) -> Op:
        name = bucket or self.rng.choice(BUCKETS)
        m.create_bucket(self.state, name)
        return self.emit(Op("create_bucket", {"bucket": name}))

    def delete_bucket(self) -> Op:
        name = "h2" if self.rng.random() < 0.8 else "h1"
        m.delete_bucket(self.state, name)
        return self.emit(Op("delete_bucket", {"bucket": name}))

    def put(self, bucket: str | None = None, key: str | None = None) -> Op:
        name = bucket or self.rng.choice(BUCKETS)
        target = key or self.rng.choice(KEYS)
        body_index = self.rng.randrange(len(BODIES))
        content_type = self.rng.choice(CONTENT_TYPES)
        m.put(self.state, name, target, BODIES[body_index], content_type)
        return self.emit(
            Op(
                "put",
                {
                    "bucket": name,
                    "key": target,
                    "body_index": body_index,
                    "content_type": content_type,
                },
            )
        )

    def delete(self, bucket: str | None = None, key: str | None = None) -> Op:
        name = bucket or self.rng.choice(BUCKETS)
        target = key or self.rng.choice(KEYS)
        m.delete(self.state, name, target)
        return self.emit(Op("delete", {"bucket": name, "key": target}))

    def read(
        self,
        kind: str,
        bucket: str | None = None,
        key: str | None = None,
        range_spec: str | None = None,
    ) -> Op:
        name = bucket or self.rng.choice(BUCKETS)
        target = key or self.rng.choice(KEYS)
        spec = self.rng.choice(RANGES) if range_spec is None else range_spec
        if_match = self.conditional(name, target)
        if_none_match = self.conditional(name, target)
        args = {
            "bucket": name,
            "key": target,
            "range": spec,
            "if_match": if_match,
            "if_none_match": if_none_match,
        }
        if kind == "get":
            m.get(self.state, name, target, spec, if_match, if_none_match)
        else:
            m.head(self.state, name, target, spec, if_match, if_none_match)
        return self.emit(Op(kind, args))

    def batch(self, bucket: str | None = None, keys: list[str] | None = None) -> Op:
        name = bucket or self.rng.choice(BUCKETS)
        pool = keys or KEYS
        count = self.rng.randint(1, 4)
        ops: list[list] = []
        for _ in range(count):
            kind = self.rng.choice(["copy", "rename", "delete"])
            if kind in ("copy", "rename"):
                source = self.rng.choice(pool)
                target = self.rng.choice(pool)
                if target == source:
                    target = self.rng.choice([key for key in KEYS if key != source])
                ops.append([kind, source, target, self.conditional(name, source)])
            else:
                key = self.rng.choice(pool)
                ops.append(["delete", key, self.conditional(name, key)])
        m.batch(self.state, name, [tuple(item) for item in ops])
        return self.emit(Op("batch", {"bucket": name, "ops": ops}))

    def listing(self, split: bool = False) -> Op:
        name = self.rng.choice(BUCKETS)
        prefix = self.rng.choice(PREFIXES)
        delimiter = self.rng.choice(DELIMITERS)
        max_keys = self.rng.choice(MAX_KEYS)
        m.list_page(self.state, name, prefix, delimiter, max_keys, 0)
        args = {
            "bucket": name,
            "prefix": prefix,
            "delimiter": delimiter,
            "max_keys": max_keys,
        }
        if split:
            args["split"] = True
        return self.emit(Op("list", args))

    def list_bad(self) -> Op:
        name = self.rng.choice(BUCKETS)
        query = self.rng.choice(LIST_BAD_QUERIES)
        return self.emit(Op("list_bad", {"bucket": name, "query": query}))

    # -- multipart ----------------------------------------------------------

    def initiate(self) -> Op:
        free = [slot for slot in range(3) if slot not in self.live]
        if not free:
            return self.put()
        slot = self.rng.choice(free)
        name = self.rng.choice(BUCKETS)
        key = self.rng.choice(KEYS)
        content_type = self.rng.choice(CONTENT_TYPES)
        upload_id = f"slot{slot}"
        m.initiate(self.state, name, key, content_type, upload_id)
        self.slots[slot] = {
            "bucket": name,
            "key": key,
            "upload_id": upload_id,
            "content_type": content_type,
        }
        self.live.add(slot)
        self.dead.discard(slot)
        self.part_history[slot] = {}
        return self.emit(
            Op(
                "initiate",
                {
                    "bucket": name,
                    "key": key,
                    "content_type": content_type,
                    "slot": slot,
                },
            )
        )

    def part(self) -> Op:
        if not self.live:
            return self.put()
        slot = self.rng.choice(sorted(self.live))
        info = self.slots[slot]
        number = self.rng.randint(1, 3)
        body_index = self.rng.randrange(len(BODIES))
        m.upload_part(
            self.state, info["bucket"], info["key"], info["upload_id"], number, BODIES[body_index]
        )
        etag = m.quote_etag(BODIES[body_index])
        self.part_history[slot].setdefault(number, []).append(etag)
        return self.emit(Op("part", {"slot": slot, "number": number, "body_index": body_index}))

    def list_parts(self) -> Op:
        if not self.live:
            return self.read("get")
        slot = self.rng.choice(sorted(self.live))
        info = self.slots[slot]
        m.list_parts(self.state, info["bucket"], info["key"], info["upload_id"])
        return self.emit(Op("list_parts", {"slot": slot}))

    def complete(self) -> Op:
        if not self.live:
            return self.put()
        slot = self.rng.choice(sorted(self.live))
        info = self.slots[slot]
        upload = self.state.uploads[info["upload_id"]]
        current = dict(upload.parts)
        modes = ["ok", "empty"]
        if current:
            modes.append("dup")
        if len(current) < 3:
            modes.append("missing")
        if any(len(versions) > 1 for versions in self.part_history[slot].values()):
            modes.append("stale")
        mode = self.rng.choice(modes)

        if mode == "ok":
            parts = [[number, m.quote_etag(body)] for number, body in sorted(current.items())]
        elif mode == "stale":
            number = self.rng.choice(
                [n for n, versions in self.part_history[slot].items() if len(versions) > 1]
            )
            parts = [[number, self.part_history[slot][number][-2]]]
        elif mode == "missing":
            number = self.rng.choice([n for n in (1, 2, 3) if n not in current])
            parts = [[number, m.quote_etag(self.rng.choice(BODIES))]]
        elif mode == "dup":
            number = self.rng.choice(sorted(current))
            etag = m.quote_etag(current[number])
            parts = [[number, etag], [number, etag]]
        else:
            parts = []

        m.complete(
            self.state,
            info["bucket"],
            info["key"],
            info["upload_id"],
            [(int(number), etag) for number, etag in parts],
        )
        if info["upload_id"] not in self.state.uploads:
            self.live.discard(slot)
            self.dead.add(slot)
        return self.emit(Op("complete", {"slot": slot, "mode": mode, "parts": parts}))

    def abort(self) -> Op:
        if not self.live:
            return self.put()
        slot = self.rng.choice(sorted(self.live))
        info = self.slots[slot]
        m.abort(self.state, info["bucket"], info["key"], info["upload_id"])
        self.live.discard(slot)
        self.dead.add(slot)
        return self.emit(Op("abort", {"slot": slot}))

    def dead_use(self) -> Op:
        if not self.dead:
            return self.put()
        slot = self.rng.choice(sorted(self.dead))
        info = self.slots[slot]
        what = self.rng.choice(["part", "list_parts", "complete", "abort"])
        upload_id = info["upload_id"]
        if what == "part":
            m.upload_part(self.state, info["bucket"], info["key"], upload_id, 1, b"dead")
        elif what == "list_parts":
            m.list_parts(self.state, info["bucket"], info["key"], upload_id)
        elif what == "complete":
            m.complete(
                self.state,
                info["bucket"],
                info["key"],
                upload_id,
                [(1, m.quote_etag(b"dead"))],
            )
        else:
            m.abort(self.state, info["bucket"], info["key"], upload_id)
        return self.emit(
            Op(
                "dead_use",
                {
                    "slot": slot,
                    "what": what,
                    "bucket": info["bucket"],
                    "key": info["key"],
                },
            )
        )


def _create_both(gen: _Gen) -> None:
    for bucket in BUCKETS:
        gen.create_bucket(bucket)


def _objects(gen: _Gen) -> list[Op]:
    _create_both(gen)
    for _ in range(78):
        roll = gen.rng.random()
        if roll < 0.30:
            gen.put()
        elif roll < 0.55:
            gen.read("get")
        elif roll < 0.65:
            gen.read("head")
        elif roll < 0.75:
            gen.delete()
        elif roll < 0.90:
            gen.batch()
        elif roll < 0.95:
            gen.delete_bucket()
        else:
            gen.create_bucket()
    return gen.ops


def _multipart(gen: _Gen) -> list[Op]:
    _create_both(gen)
    for _ in range(48):
        roll = gen.rng.random()
        if roll < 0.15:
            gen.initiate()
        elif roll < 0.45:
            gen.part()
        elif roll < 0.55:
            gen.list_parts()
        elif roll < 0.70:
            gen.complete()
        elif roll < 0.78:
            gen.abort()
        elif roll < 0.85:
            gen.dead_use()
        elif gen.rng.random() < 0.5:
            gen.put()
        else:
            gen.read("get")
    return gen.ops


def _listing(gen: _Gen) -> list[Op]:
    _create_both(gen)
    for _ in range(22):
        roll = gen.rng.random()
        if roll < 0.60:
            gen.put()
        elif roll < 0.80:
            gen.delete()
        else:
            gen.batch()
    for _ in range(16):
        if gen.rng.random() < 0.75:
            gen.listing()
        else:
            gen.list_bad()
    return gen.ops


def _restart(gen: _Gen) -> list[Op]:
    for index in range(80):
        if index < 2:
            gen.create_bucket(BUCKETS[index])
        elif index in (26, 53, 79):
            gen.emit(Op("restart", {}))
        elif index in (25, 52):
            bucket = "h1"
            prefix = ""
            delimiter = ""
            max_keys = gen.rng.choice([1, 2, 3])
            m.list_page(gen.state, bucket, prefix, delimiter, max_keys, 0)
            gen.emit(
                Op(
                    "list",
                    {
                        "bucket": bucket,
                        "prefix": prefix,
                        "delimiter": delimiter,
                        "max_keys": max_keys,
                        "split": True,
                    },
                )
            )
        else:
            roll = gen.rng.random()
            if roll < 0.30:
                gen.put()
            elif roll < 0.55:
                gen.read("get")
            elif roll < 0.65:
                gen.read("head")
            elif roll < 0.75:
                gen.delete()
            elif roll < 0.90:
                gen.batch()
            elif roll < 0.95:
                gen.delete_bucket()
            else:
                gen.create_bucket()
    return gen.ops


def _concurrent(gen: _Gen) -> list[Op]:
    for bucket in BUCKETS:
        m.create_bucket(gen.state, bucket)
        gen.emit(Op("create_bucket", {"bucket": bucket}, group=0))

    subset = gen.rng.sample(KEYS, 4)
    for group in range(1, 13):
        keys = gen.rng.sample(subset, gen.rng.choice([1, 2]))
        count = gen.rng.choice([2, 3])
        kinds = [gen.rng.choice(["put", "delete", "get", "head", "range", "batch"]) for _ in range(count)]
        for kind in kinds:
            bucket = "h1"
            key = gen.rng.choice(keys)
            if kind == "put":
                op = gen.put(bucket, key)
            elif kind == "delete":
                op = gen.delete(bucket, key)
            elif kind == "get":
                op = gen.read("get", bucket, key, range_spec=None)
            elif kind == "head":
                op = gen.read("head", bucket, key, range_spec=None)
            elif kind == "range":
                op = gen.read("get", bucket, key, range_spec=gen.rng.choice([r for r in RANGES if r]))
            else:
                op = gen.batch(bucket, keys)
            gen.ops[-1] = Op(op.kind, op.args, group=group)
    return gen.ops


def generate(case_id: str, seed: int) -> list[Op]:
    gen = _Gen(case_id, seed)
    builders = {
        "objects": _objects,
        "multipart": _multipart,
        "listing": _listing,
        "restart": _restart,
        "concurrent": _concurrent,
    }
    return builders[case_id](gen)
