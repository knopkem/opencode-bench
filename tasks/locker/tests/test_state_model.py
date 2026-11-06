from __future__ import annotations

import unittest

import state_model as m


def bucket_with(body: bytes = b"0123456789", content_type: str = "text/plain") -> tuple[m.State, m.Obj]:
    state = m.State()
    m.create_bucket(state, "b")
    m.put(state, "b", "k", body, content_type)
    return state, state.buckets["b"]["k"]


class RangeModelTests(unittest.TestCase):
    def test_closed_open_suffix_and_clip(self) -> None:
        state, obj = bucket_with()
        self.assertEqual(m.get(state, "b", "k", "bytes=2-5").body, b"2345")
        self.assertEqual(m.get(state, "b", "k", "bytes=4-").content_range, "bytes 4-9/10")
        self.assertEqual(m.get(state, "b", "k", "bytes=-3").body, b"789")
        self.assertEqual(m.get(state, "b", "k", "bytes=8-99").body, b"89")
        self.assertEqual(m.get(state, "b", "k", "bytes=0-999").etag, obj.etag)

    def test_unsatisfiable_and_empty(self) -> None:
        state, _ = bucket_with()
        expected = m.get(state, "b", "k", "bytes=10-")
        self.assertEqual(expected.status, 416)
        self.assertEqual(expected.content_range, "bytes */10")
        m.put(state, "b", "empty", b"", None)
        expected = m.get(state, "b", "empty", "bytes=0-0")
        self.assertEqual(expected.status, 416)
        self.assertEqual(expected.content_range, "bytes */0")

    def test_head_never_has_a_body(self) -> None:
        state, _ = bucket_with()
        expected = m.head(state, "b", "k")
        self.assertEqual(expected.status, 200)
        self.assertIsNone(expected.body)
        self.assertEqual(expected.content_length, 10)


class ConditionalModelTests(unittest.TestCase):
    def test_if_match_beats_if_none_match(self) -> None:
        state, obj = bucket_with()
        self.assertEqual(
            m.get(state, "b", "k", if_match='"wrong"', if_none_match=obj.etag).status,
            412,
        )
        self.assertEqual(
            m.get(state, "b", "k", if_match=obj.etag, if_none_match=obj.etag).status,
            304,
        )
        not_modified = m.get(state, "b", "k", if_none_match=obj.etag)
        self.assertEqual(not_modified.etag, obj.etag)
        self.assertIsNone(not_modified.body)


class ListingModelTests(unittest.TestCase):
    def test_grouping_and_page_boundaries(self) -> None:
        state = m.State()
        m.create_bucket(state, "b")
        for key in ("a/1", "a/2", "b/1", "c"):
            m.put(state, "b", key, key.encode(), None)
        first = m.list_page(state, "b", delimiter="/", max_keys=1, page_index=0)
        second = m.list_page(state, "b", delimiter="/", max_keys=1, page_index=1)
        third = m.list_page(state, "b", delimiter="/", max_keys=1, page_index=2)
        self.assertEqual(first.listing, [("prefix", "a/")])
        self.assertEqual(second.listing, [("prefix", "b/")])
        self.assertEqual(third.listing, [("object", "c")])
        self.assertTrue(first.truncated)
        self.assertTrue(second.truncated)
        self.assertFalse(third.truncated)
        self.assertEqual(m.list_page(state, "b", delimiter="/").listing, [
            ("prefix", "a/"), ("prefix", "b/"), ("object", "c")
        ])
        self.assertEqual(
            m.list_page(state, "b", prefix="a/").listing,
            [("object", "a/1"), ("object", "a/2")],
        )


class MultipartModelTests(unittest.TestCase):
    def test_reupload_stale_completion_and_abort(self) -> None:
        state = m.State()
        m.create_bucket(state, "b")
        m.initiate(state, "b", "k", "text/plain", "u1")
        first = m.upload_part(state, "b", "k", "u1", 1, b"one")
        self.assertEqual(m.upload_part(state, "b", "k", "u1", 1, b"two").status, 200)
        stale = m.complete(state, "b", "k", "u1", [(1, first.etag)])
        self.assertEqual(stale.status, 400)
        self.assertEqual(stale.code, "InvalidArgument")
        self.assertIn("u1", state.uploads)
        self.assertEqual(
            m.list_parts(state, "b", "k", "u1").parts,
            [(1, m.quote_etag(b"two"), 3)],
        )
        done = m.complete(state, "b", "k", "u1", [(1, m.quote_etag(b"two"))])
        self.assertEqual(done.status, 200)
        self.assertEqual(state.buckets["b"]["k"].body, b"two")
        self.assertEqual(state.buckets["b"]["k"].content_type, "text/plain")

        m.initiate(state, "b", "k2", None, "u2")
        m.upload_part(state, "b", "k2", "u2", 2, b"part")
        self.assertEqual(m.abort(state, "b", "k2", "u2").status, 204)
        self.assertEqual(m.complete(state, "b", "k2", "u2", [(2, m.quote_etag(b"part"))]).status, 404)


class BatchModelTests(unittest.TestCase):
    def test_working_copy_order(self) -> None:
        state = m.State()
        m.create_bucket(state, "b")
        m.put(state, "b", "a", b"A1", "text/plain")
        a = state.buckets["b"]["a"]
        self.assertEqual(
            m.batch(state, "b", [("copy", "a", "h", None), ("delete", "h", a.etag)]).status,
            200,
        )
        self.assertNotIn("h", state.buckets["b"])
        self.assertIn("a", state.buckets["b"])

    def test_failure_changes_nothing(self) -> None:
        state = m.State()
        m.create_bucket(state, "b")
        m.put(state, "b", "a", b"A", None)
        m.put(state, "b", "b", b"B", None)
        old_b = state.buckets["b"]["b"].etag
        expected = m.batch(
            state,
            "b",
            [("copy", "a", "c", None), ("delete", "b", old_b), ("delete", "c", '"wrong"')],
        )
        self.assertEqual(expected.status, 412)
        self.assertEqual(expected.code, "PreconditionFailed")
        self.assertEqual(set(state.buckets["b"]), {"a", "b"})
        self.assertEqual(state.buckets["b"]["b"].body, b"B")

    def test_rename_chain_and_absent_cases(self) -> None:
        state = m.State()
        m.create_bucket(state, "b")
        m.put(state, "b", "r0", b"chain", None)
        self.assertEqual(
            m.batch(state, "b", [
                ("rename", "r0", "r1", None),
                ("rename", "r1", "r2", None),
                ("rename", "r2", "r3", None),
            ]).status,
            200,
        )
        self.assertEqual(set(state.buckets["b"]), {"r3"})
        self.assertEqual(state.buckets["b"]["r3"].body, b"chain")
        self.assertEqual(m.batch(state, "b", [("copy", "missing", "x", "*")]).status, 412)
        self.assertEqual(m.batch(state, "b", [("delete", "nothing", None)]).status, 200)
        self.assertEqual(m.batch(state, "b", [("delete", "nothing", "*")]).status, 412)

    def test_structural_errors(self) -> None:
        state = m.State()
        m.create_bucket(state, "b")
        self.assertEqual(m.batch(state, "b", []).status, 400)
        self.assertEqual(
            m.batch(state, "b", [("delete", f"k{i}", None) for i in range(33)]).status,
            400,
        )
        self.assertEqual(m.batch(state, "b", [("move", "a", "b")]).status, 400)
        self.assertEqual(m.batch(state, "b", [("copy", "a", "a", None)]).status, 400)


class BucketModelTests(unittest.TestCase):
    def test_live_upload_blocks_delete(self) -> None:
        state = m.State()
        m.create_bucket(state, "b")
        m.initiate(state, "b", "k", None, "u")
        self.assertEqual(m.delete_bucket(state, "b").status, 409)
        m.abort(state, "b", "k", "u")
        self.assertEqual(m.delete_bucket(state, "b").status, 204)
        self.assertEqual(m.delete_bucket(state, "b").status, 404)


if __name__ == "__main__":
    unittest.main()
