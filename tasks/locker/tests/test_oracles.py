from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import oracles


ROOT = Path(__file__).resolve().parent


class RangeOracleTests(unittest.TestCase):
    def test_closed_open_and_suffix(self) -> None:
        self.assertEqual(oracles.expected_range(10, "bytes=2-5"), (2, 5))
        self.assertEqual(oracles.expected_range(10, "bytes=4-"), (4, 9))
        self.assertEqual(oracles.expected_range(10, "bytes=-3"), (7, 9))
        self.assertEqual(oracles.expected_range(10, "bytes=-999"), (0, 9))

    def test_invalid_and_unsatisfiable(self) -> None:
        for spec in ("bytes=10-", "bytes=5-2", "bytes=-0", "bytes=0-1,3-4", "items=0-1"):
            self.assertIsNone(oracles.expected_range(10, spec), spec)
        self.assertIsNone(oracles.expected_range(0, "bytes=0-0"))

    def test_clipping(self) -> None:
        self.assertEqual(oracles.expected_range(10, "bytes=8-99"), (8, 9))
        self.assertEqual(oracles.expected_range(10, "bytes=9-9"), (9, 9))


class ListingOracleTests(unittest.TestCase):
    def test_grouping_and_order(self) -> None:
        keys = ["a/1", "a/2", "b/1", "c"]
        self.assertEqual(
            oracles.effective_listing(keys, delimiter="/"),
            [("prefix", "a/"), ("prefix", "b/"), ("object", "c")],
        )
        self.assertEqual(
            oracles.effective_listing(keys, prefix="a/", delimiter="/"),
            [("object", "a/1"), ("object", "a/2")],
        )

    def test_pagination_splits_grouped_prefixes(self) -> None:
        items = oracles.effective_listing(["a/1", "a/2", "b/1", "c"], delimiter="/")
        self.assertEqual(
            oracles.paginate(items, 1),
            [[("prefix", "a/")], [("prefix", "b/")], [("object", "c")]],
        )


class ConditionalOracleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.modified = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.etag = '"abc"'

    def action(self, **kwargs):
        return oracles.conditional_action(etag=self.etag, modified=self.modified, **kwargs)

    def test_etags(self) -> None:
        self.assertEqual(self.action(if_match='"abc"'), "continue")
        self.assertEqual(self.action(if_match='"def"'), "precondition-failed")
        self.assertEqual(self.action(if_none_match='"abc"'), "not-modified")
        self.assertEqual(self.action(if_none_match='"def"'), "continue")

    def test_paired_precedence(self) -> None:
        before = "Thu, 01 Jan 2026 11:59:59 GMT"
        self.assertEqual(
            self.action(if_match='"abc"', if_unmodified_since=before), "continue"
        )
        self.assertEqual(
            self.action(if_match='"def"', if_unmodified_since="Thu, 01 Jan 2026 12:00:01 GMT"),
            "precondition-failed",
        )
        self.assertEqual(
            self.action(if_none_match='"def"', if_modified_since="Thu, 01 Jan 2026 12:00:00 GMT"),
            "continue",
        )


class ManifestTests(unittest.TestCase):
    def test_catalog_counts(self) -> None:
        manifest = json.loads((ROOT / "manifest.json").read_text())
        families = manifest["families"]
        self.assertEqual(len(families), 12)
        counts = {"P0": 0, "P1": 0, "P2": 0}
        family_counts = {"P0": 0, "P1": 0, "P2": 0}
        for family in families:
            family_counts[family["tier"]] += 1
            counts[family["tier"]] += len(family["cases"])
            self.assertTrue(family["title"].startswith(f"[{family['tier']}]"))
            self.assertEqual(len(family["cases"]), len(set(family["cases"])))
        self.assertEqual(family_counts, manifest["tiers"])
        self.assertEqual(counts, {"P0": 8, "P1": 30, "P2": 13})
        limits = manifest["limits"]
        self.assertLess(limits["server_rss_bytes"], limits["stream_bytes"])


if __name__ == "__main__":
    unittest.main()
