"""Pure expected-value oracles derived from the public Locker contract.

These functions are deliberately independent of both the reference and the
candidate. The hidden runner uses them to build expectations and the unit tests
below pin them to literal examples from PROMPT.md.
"""

from __future__ import annotations

from datetime import datetime, timezone


class OracleError(ValueError):
    pass


def expected_range(size: int, spec: str) -> tuple[int, int] | None:
    """Return inclusive (start, end), or None for a 416 response."""
    if not spec.startswith("bytes="):
        return None
    body = spec[len("bytes=") :].strip()
    if not body or "," in body:
        return None
    if "-" not in body:
        return None
    left, right = body.split("-", 1)
    left = left.strip()
    right = right.strip()
    if size < 0:
        return None

    def number(value: str) -> int | None:
        if not value.isdigit():
            return None
        return int(value)

    if left == "":
        suffix = number(right)
        if suffix is None or suffix <= 0 or size == 0:
            return None
        if suffix >= size:
            return 0, size - 1
        return size - suffix, size - 1

    first = number(left)
    if first is None or first >= size:
        return None
    if right == "":
        return first, size - 1
    last = number(right)
    if last is None or last < first:
        return None
    return first, min(last, size - 1)


def effective_listing(
    keys: list[str], prefix: str = "", delimiter: str = ""
) -> list[tuple[str, str]]:
    """Return sorted (kind, name) where kind is 'object' or 'prefix'."""
    objects: list[str] = []
    prefixes: set[str] = set()
    for key in keys:
        if not key.startswith(prefix):
            continue
        if delimiter:
            remainder = key[len(prefix) :]
            index = remainder.find(delimiter)
            if index >= 0:
                prefixes.add(prefix + remainder[: index + len(delimiter)])
                continue
        objects.append(key)
    items = [("object", key) for key in objects]
    items.extend(("prefix", group) for group in prefixes)
    return sorted(items, key=lambda item: item[1])


def paginate(items: list[tuple[str, str]], max_keys: int) -> list[list[tuple[str, str]]]:
    return [items[index : index + max_keys] for index in range(0, len(items), max_keys)]


def conditional_action(
    *,
    if_match: str | None = None,
    if_none_match: str | None = None,
    if_modified_since: str | None = None,
    if_unmodified_since: str | None = None,
    etag: str,
    modified: datetime,
) -> str:
    """Return 'continue', 'not-modified' or 'precondition-failed'."""
    from email.utils import parsedate_to_datetime

    def parse_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def matches(value: str) -> bool:
        value = value.strip()
        if value == "*":
            return True
        return value.strip('"') == etag.strip('"')

    modified = modified.astimezone(timezone.utc).replace(microsecond=0)

    if if_match is not None:
        if not matches(if_match):
            return "precondition-failed"
    else:
        unmodified = parse_date(if_unmodified_since)
        if unmodified is not None and modified > unmodified:
            return "precondition-failed"

    if if_none_match is not None:
        if matches(if_none_match):
            return "not-modified"
    else:
        modified_since = parse_date(if_modified_since)
        if modified_since is not None and modified <= modified_since:
            return "not-modified"

    return "continue"
