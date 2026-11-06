# Locker v4 — implementation specification for the hardening pass

**Status: implemented, frozen.** The repository is `locker-v4`
(16 families / 69 subcases). Everything below was the exact work to make the
hidden suite v4; it is retained as the build record. `HARD-TASK-PLAN.md`
remains the design history; this file is the authoritative, step-by-step
build sheet for v4.

This document is written for a worker model that has **not** read the design
discussion. Every code change names the file, the function and the insertion
point. Texts marked **verbatim** must be copied byte-for-byte. Where the
document says "decide", the decision has already been made and is stated;
do not re-decide it. If something is genuinely impossible as written, stop,
report the exact conflict, and do not weaken an assertion or a prompt clause
to get past it.

---

## 0. Why v4 exists (two paragraphs, then no more motivation)

A local mid-size model reportedly saturated `locker-v3` (51/51). The P1
protocol families (range, conditional, listing, multipart) are the S3 subset
that public conformance suites cover, so they measure recall. The families
with no public counterpart (kill / race / progress) were never shown to catch
*realistic* bugs — the negative controls are conspicuous corruptions. Nothing
in v3 forces a storage-design decision; the most common clone layout (one data
file + one metadata file per key) passes everything.

v4 adds three dimensions that have no public-benchmark counterpart and cannot
be pattern-matched: (1) an **atomic same-bucket batch** operation that forces a
multi-key atomic commit and a blob-sharing/lifetime decision, (2) a
**full-disk fault family** on a 32 MiB tmpfs, (3) **generated interaction
histories** checked against an independent Python state model, including
≤3-way concurrent groups checked for a valid serialization. It also adds a
**realistic mutant campaign** so every race/crash family is shown to detect a
plausible bug, not just a planted corruption.

---

## 1. Hard rules (read before touching anything)

1. **Information boundary.** `tasks/locker/tests/` (hidden suite) and
   `tasks/locker/reference/` (Go reference) are private. Never copy any of
   their content — code, case names, generator seeds, mutant names, recipes —
   into `tasks/locker/seed/`, `tasks/locker/PROMPT.md`,
   `docker/locker.Dockerfile`, or any run workdir. The *only* public text is
   `PROMPT.md` (plus the seed's own `AGENTS.md`/`scripts/smoke.py`). The
   batch and fault **behaviour** is public (it is in the prompt); the **tests**
   are not.
2. **Prompt is the oracle.** The Python state model (`state_model.py`) and all
   new assertions are written from `PROMPT.md`, not from the reference. If the
   reference and the model disagree, adjudicate against the prompt; if the
   prompt is ambiguous, clarify the prompt (and record the clarification in
   `tasks/locker/CALIBRATION.md`). Never "fix" a disagreement by making the
   model imitate the reference.
3. **Seed stays naive.** Do not change any Go file under `tasks/locker/seed/`.
   The seed must still build and pass all 8 P0 subcases and must remain
   ineligible. The only seed change is two public checks in
   `tasks/locker/seed/scripts/smoke.py` (§4.6).
4. **Frozen v3 limits do not change.** 128 MiB stream, 96 MiB `VmHWM`,
   180 s / 60 s / 10 s / 15 s deadlines, 6 crash trials. Three limits are
   *added*: `batch_max_ops = 32`, `fault_fs_bytes = 33554432`,
   `fault_write_bytes = 50331648`.
5. **Scoring policy does not change.** Family weights equal within P1/P2;
   quality = mean of all non-P0 family credits; eligibility = build + all P0 +
   quality ≥ 0.80; timeout stays eligible; `diagnostic: true` never ingested.
6. **Stack.** Go standard library only; Python 3.12 standard library only in
   `tasks/locker/tests/` and `bench.py`; no network at build or test time.
7. **Platform.** Linux-dependent cases (`p2.perf`, the new `p2.faults`) run in
   the pinned image `opencode-bench-locker:latest` with `--network none`. On
   macOS, run them through Docker (commands in §11). BSD `sed -i` differs from
   GNU; edit files with Python or an editor, not `sed -i`.
8. **Never mount** `tasks/locker/reference` or `tasks/locker/tests` into an
   agent workdir; hidden tests run only after the agent exits (already how
   `bench.py run_tests` works).
9. **Existing cases are untouched.** Do not rename, re-order or edit the 51
   existing case IDs, their titles or their functions, except where this
   document says so (counts in tests, and the `Limits` dict gaining keys).

---

## 2. v3 → v4 at a glance

| Item | v3 (now) | v4 (target) |
|---|---|---|
| `manifest.suite_version` | `locker-v3` | `locker-v4` |
| Families | 12 (P0 3 / P1 5 / P2 4) | 16 (P0 3 / P1 7 / P2 6) |
| Subcases | 51 (P0 8 / P1 30 / P2 13) | 69 (P0 8 / P1 42 / P2 19) |
| `bench.py` `EXPECTED` | `{"P0": 3, "P1": 5, "P2": 4}` | `{"P0": 3, "P1": 7, "P2": 6}` |
| Quality example in prompt | all P1, no P2 = `5/9 ≈ 0.556` | `7/13 ≈ 0.538` |
| Prompt "aim for" | 12 minutes | 20 minutes (cap 60, idle 5 unchanged) |
| New HTTP surface | — | `POST /b?batch` (copy / rename / delete, `IfMatch`) |
| New error code | — | `PreconditionFailed` (412) |
| New prompt clause | — | operational failures (ENOSPC) paragraph |
| New limits | — | `batch_max_ops`, `fault_fs_bytes`, `fault_write_bytes` |
| Runner CLI | `--binary --manifest --seed --output --family` | + `--scratch DIR`, `--generator-seed N` (author-only, diagnostic) |
| Runner exit codes | 0 all pass, 1 failures, 2 usage/seed mismatch | + 3 = required scratch filesystem missing/wrong size |
| Docker test run | `--network none`, read-only mounts | + `--tmpfs /scratch:rw,size=33554432` |
| Negative controls | 4 faults, "any failure" detection | 13 faults, detection by the intended case, natural vs amplified |
| Grading time (Linux, reference) | ≈ 9.4 s | must stay ≤ 90 s median of 5 runs (estimate 35–45 s) |

New families and case IDs (fixed; copy exactly):

| Family | Tier | Cases (in manifest order) |
|---|---|---|
| `p1.batch` | P1 | `copy-rename-delete`, `preconditions`, `errors`, `independence`, `cross-feature`, `atomic-visibility`, `concurrent-batches` |
| `p1.histories` | P1 | `objects`, `multipart`, `listing`, `restart`, `concurrent` |
| `p2.batch` | P2 | `kill-during-swap`, `kill-after-ack`, `blob-lifetime` |
| `p2.faults` | P2 | `put-preserves-old`, `space-released`, `multipart-staging` |

---

## 3. Work packages and order

Do them in this order. Each has a **gate**; do not start the next package
until the gate passes. Report after each package (format in §13).

| # | Package | Touches | Gate |
|---|---|---|---|
| WP1 | Contract (§4) | `PROMPT.md`, `manifest.json`, `bench.py` constants/`cmd_check`, `tests/test_bench.py`, `tasks/locker/tests/test_oracles.py`, `AGENTS.md`, `README.md`, `HARD-TASK-PLAN.md` | `python3 -m py_compile bench.py`; `python3 -m unittest discover -s tests` passes; `python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'` passes; `./bench.py check` passes |
| WP2 | Reference (§5) | `tasks/locker/reference/internal/{store,server,protocol}` | `go test ./...`, `go vet ./...`, Linux build; full suite run against the new binary: all 51 pre-existing case ids pass, the 18 placeholders fail with "case not implemented yet" |
| WP3 | State model (§6) | new `tasks/locker/tests/state_model.py`, `test_state_model.py` | unit tests pass; written from the prompt only |
| WP4 | Batch cases (§7) | `tasks/locker/tests/test_runner.py` | `p1.batch` 7/7 and `p2.batch` 3/3 against the reference, 10 consecutive diagnostic runs each, 0 flakes |
| WP5 | Faults (§8) | `bench.py run_tests`/`cmd_check`, `run.py`, `test_runner.py`, `tests/test_bench.py` | `p2.faults` 3/3 against the reference in Docker, 10 runs; `run.py` without `--scratch` exits 3 for a full run; admission unit test passes |
| WP6 | Histories (§9) | `test_runner.py`, new `tasks/locker/tests/history_gen.py` and `history_check.py`, author tool `history_sweep.py` | `p1.histories` 5/5 against the reference; 200-seed sweep shows zero model/reference disagreements (or each disagreement adjudicated and documented) |
| WP7 | Mutants (§10) | reference fault switches, `negative_controls.py`, `NEGATIVE_CONTROLS.md` | every control: baseline pass, detected by its intended case; report lists natural vs amplified |
| WP8 | Release (§11) | `CALIBRATION.md`, status lines | all gates in §11 |

Dependencies: WP2, WP3, WP5 need WP1. WP4 and WP6 need WP2 and WP3. WP7 needs
WP4, WP5, WP6. WP8 needs WP7.

---

## 4. WP1 — Contract

### 4.1 `tasks/locker/PROMPT.md` edits

Line references are to the current 328-line file; locate by the quoted text,
not by number, if the file has moved.

**(a) Budget, line 15.** Replace `Aim for about **12 minutes** of work.` with
`Aim for about **20 minutes** of work.` Nothing else in that bullet changes.

**(b) Scoring, the P1/P2 bullet (lines ~46–49).** Replace the bullet with this
**verbatim** text:

```
- **P1** and **P2** are divided into thirteen equally weighted families. A
  family's credit is the fraction of its fixed required subcases that pass.
  Quality is the mean of those thirteen family credits, so all P0 + all P1 +
  no P2 credit is `7/13 ≈ 0.538`.
```

**(c) Scoring, "Scored behavior covers four surfaces" paragraph.** Replace the
parenthesised lists so the paragraph reads (**verbatim**):

```
Scored behavior covers four surfaces in one product: CLI↔HTTP↔storage
integration, ordinary HTTP object operations, protocol interactions (range,
conditional, multipart, listing, same-bucket batch operations), and storage
behavior (coherent publication, concurrency, restart recovery, key isolation,
streaming memory, behavior when the data filesystem is full). The tests use
small fixtures for most cases, one streaming 128 MiB object, and a small data
filesystem for the full-disk cases. Some tests replay generated sequences of
ordinary operations and compare every response against the documented
semantics, so partial implementations of any documented rule lose credit.
```

**(d) Resource limits table (lines ~66–74).** Append these three rows after
the `Bounded crash trials` row (**verbatim**, pipe table rows):

```
| Maximum operations in one batch request | `32` |
| Data filesystem size for full-disk cases | `33,554,432` bytes (32 MiB) |
| Single write attempted on the full data filesystem | `50,331,648` bytes (48 MiB) |
```

`./bench.py check` requires every manifest limit value to appear in the prompt
either as `str(value)` or formatted with thousands separators (`f"{value:,}"`).
The rows above satisfy that for the three new limits.

**(e) HTTP surface route table (line ~106).** Insert this row directly after
the `GET /b` or `GET /b?list-type=2` row (**verbatim**):

```
| `POST /b?batch` | 200 | Atomic same-bucket batch of copy/rename/delete operations; empty body on success |
```

**(f) Routing paragraph (line ~118, "Object operations on an absent bucket
return 404 ...").** Append this sentence to the end of that paragraph
(**verbatim**):

```
The `batch` query parameter is valid only as `POST` on a bucket path; `batch`
combined with any listing or multipart parameter, or present on an object
path, returns 400.
```

**(g) Persistence section (after the bullet "Process kill during publication
leaves the old or new complete version ...").** Insert this bullet
(**verbatim**):

```
- All server state, including temporary and staging files, lives under
  `--data`. If a write fails because the data filesystem is full or for any
  other operational reason, respond 500 `InternalError` or close the connection
  without a success status; leave the published version of every key and every
  upload session unchanged; release the temporary space the failed write
  consumed so that later writes which fit can succeed; keep serving other
  requests; and after a restart load the committed state. Never acknowledge a
  write whose bytes were not completely stored.
```

**(h) New section.** Insert a new `## Batch operations` section between
`## Multipart transitions` and `## XML shapes` (**verbatim**, including the
code block):

````
## Batch operations

`POST /b?batch` applies up to 32 copy, rename and delete operations to keys of
one bucket as a single atomic change. The request body is XML:

```xml
<Batch>
  <Copy><Source>KEY</Source><Target>KEY</Target><IfMatch>"MD5"</IfMatch></Copy>
  <Rename><Source>KEY</Source><Target>KEY</Target></Rename>
  <Delete><Key>KEY</Key><IfMatch>*</IfMatch></Delete>
</Batch>
```

- Operations apply in document order to a working copy of the bucket's
  committed namespace taken when the batch commits, so a later operation sees
  the effects of earlier operations in the same batch.
- `Copy`: `Source` must exist in the working copy. `Target` receives Source's
  bytes, ETag and Content-Type; its Last-Modified is the batch commit time. An
  existing Target is replaced. Source and Target may not be equal.
- `Rename`: a Copy followed by removal of Source.
- `Delete`: removes `Key`. Without `IfMatch`, deleting an absent key is a
  no-op. With `IfMatch`, the key must exist and match.
- `IfMatch` is optional on every operation and names the operation's Source or
  Key. Its value is one quoted ETag, which must equal that key's current ETag
  in the working copy, or `*`, which only requires the key to exist.
- Any failed `IfMatch` or any Copy/Rename whose Source is absent fails the
  whole batch with 412 `PreconditionFailed` and changes nothing.
- Malformed XML, zero operations, more than 32 operations, an unknown
  operation element, a missing or empty Source/Target/Key, Source equal to
  Target, or an `IfMatch` that is neither `*` nor one quoted token returns 400
  `InvalidArgument` and changes nothing. An absent bucket returns 404.
- Success is 200 with an empty body. Afterwards every touched key is readable
  with the stated ETag, Content-Type, Content-Length and bytes, including
  range and conditional requests, and listings report the new sizes and ETags.
- Atomicity: a batch publishes as one version change for every key it
  touches. Any single GET, HEAD or listing request observes either all of a
  batch's effects or none of them. A batch acknowledged with 200 survives a
  process kill; a batch interrupted before acknowledgment leaves either all or
  none of its effects after restart.
- Concurrency: batches, ordinary writes and multipart completions on the same
  bucket serialize in some order consistent with every observation; they never
  produce a 5xx, a mixed version or a deadlock.
- Independence: after a copy or rename, later writes or deletes of either key
  never change the other key's bytes, ETag or Content-Type. Whether a copy
  shares storage with its source internally is your choice; independence and
  survival across restarts are graded.
- Multipart sessions are not affected by batches. A session on a key stays
  usable after that key is renamed, copied over or deleted, and a later
  completion replaces whatever is then at the key.
````

(The inner ```xml fence is part of the verbatim text. In this document the
outer fence uses four backticks only so the nesting renders; copy the content
between the four-backtick fences.)

**(i) XML shapes code block.** Add this shape to the existing ```xml block,
after the `CompleteMultipartUploadResult` shape and before the `<Error>` shape
(**verbatim**):

```
<Batch>
  <Copy><Source>KEY</Source><Target>KEY</Target><IfMatch>"MD5"</IfMatch></Copy>
  <Rename><Source>KEY</Source><Target>KEY</Target></Rename>
  <Delete><Key>KEY</Key></Delete>
</Batch>
```

**(j) Error codes paragraph (lines ~294–296).** Replace the first two
sentences with (**verbatim**):

```
Errors 400/404/409/405/412/500 use the error XML shape above, except HEAD never
has a body. Codes are `InvalidArgument`,
`NoSuchBucket`/`NoSuchKey`/`NoSuchUpload`, `BucketNotEmpty`,
`MethodNotAllowed`, `PreconditionFailed`, and `InternalError`, respectively.
```

**(k) Do not change** the "deduplication and garbage collection are out of
scope" sentence in Budget and priorities; it means those are not *required*
features. Independence after copies is required and is stated in (h).

After editing, re-read the whole prompt once for contradictions (for example
"Operational failures must not be reported as success" already exists at the
end of XML shapes — keep it; it agrees with (g)).

### 4.2 `tasks/locker/tests/manifest.json`

Keep the 12 existing family objects byte-identical. Change/add:

- `"suite_version": "locker-v4"`
- `"tiers": {"P0": 3, "P1": 7, "P2": 6}`
- In `"limits"`, add (keep the existing seven keys unchanged):
  `"batch_max_ops": 32`, `"fault_fs_bytes": 33554432`,
  `"fault_write_bytes": 50331648`
- Append four family objects to `"families"` (**verbatim**):

```json
{
  "id": "p1.batch",
  "tier": "P1",
  "title": "[P1][http] atomic same-bucket batch operations apply in order and all or nothing",
  "cases": ["copy-rename-delete", "preconditions", "errors", "independence", "cross-feature", "atomic-visibility", "concurrent-batches"]
},
{
  "id": "p1.histories",
  "tier": "P1",
  "title": "[P1][db] generated operation histories match the documented state model",
  "cases": ["objects", "multipart", "listing", "restart", "concurrent"]
},
{
  "id": "p2.batch",
  "tier": "P2",
  "title": "[P2][db] interrupted and acknowledged batches leave all or none of their effects",
  "cases": ["kill-during-swap", "kill-after-ack", "blob-lifetime"]
},
{
  "id": "p2.faults",
  "tier": "P2",
  "title": "[P2][db] writes on a full data filesystem fail cleanly and release space",
  "cases": ["put-preserves-old", "space-released", "multipart-staging"]
}
```

`"seed"` stays `20261106`; `"schema"` stays `opencode-bench/locker-manifest@1`.
Validate with `python3 -c "import json;json.load(open('tasks/locker/tests/manifest.json'))"`.

### 4.3 `bench.py` constants and `cmd_check`

- Line 62: `EXPECTED = {"P0": 3, "P1": 7, "P2": 6}`. The dict counts
  **families**; the comment next to it should say so if it does not.
- `cmd_check` (search for `expected_family_ids = {`): add
  `"p1.batch", "p1.histories"` to the P1 line and `"p2.batch", "p2.faults"` to
  the P2 line of that set literal.
- Same function, `expected_cases = {"P0": 8, "P1": 30, "P2": 13}` →
  `expected_cases = {"P0": 8, "P1": 42, "P2": 19}`.
- Same function, the required-limit-keys tuple (starts with
  `"stream_bytes",`): append `"batch_max_ops", "fault_fs_bytes",
  "fault_write_bytes",`.
- The tmpfs preflight and `run_tests` changes belong to WP5 (§8.1); do not do
  them here.
- `tasks/locker/tests/test_runner.py`: `run_family` does
  `functions = CASE_FUNCS[family["id"]]`, so a manifest family without an
  entry crashes the whole run with `KeyError`. In WP1 add placeholders so full
  runs keep working while WP4–WP6 are in progress:

  ```python
  def _not_implemented(server: h.Server, limits: Limits) -> None:
      fail("case not implemented yet")
  ```

  and in `CASE_FUNCS` add `"p1.batch": {case: _not_implemented for case in (...)}`
  for all four new families with their case IDs from §2. Each later package
  replaces its placeholders with real functions.

### 4.4 Unit-test fixtures that hardcode counts

- `tasks/locker/tests/test_oracles.py::test_catalog_counts`: the expected dict
  becomes `{"P0": 8, "P1": 42, "P2": 19}`; it already compares family counts
  against `manifest["tiers"]`.
- `tests/test_bench.py`: every fixture manifest/result that enumerates
  families must include the four new families, otherwise
  `validate_locker_result` reports missing families. Search the file for
  `"p2.perf"` and extend each fixture; where a test computes the expected
  quality by hand, recompute for 13 families. Keep
  `test_abnormal_exit_is_not_admitted` as is (exit-3 handling is added in
  §8.5).

### 4.5 Documentation synchronisation (same commit as 4.1–4.4)

- `AGENTS.md` (repo root), "Hidden-suite and scoring invariants": 16
  families, P0 3 / P1 7 / P2 6, 69 subcases P0 8 / P1 42 / P2 19,
  `EXPECTED = {"P0": 3, "P1": 7, "P2": 6}`, `suite_version: locker-v4`,
  quality = mean of the thirteen P1/P2 credits; add the three new limits to
  the "Seed / prompt / image coupling" list; add "faults cases need
  `--tmpfs /scratch` (run_tests adds it)" to Operational gotchas.
- `README.md` lines ~58–59 (family/subcase counts): same numbers.
- `HARD-TASK-PLAN.md`: in "Implementation status" add one line "v4 hardening
  specified in `HARD-TASK-V4-PLAN.md`"; in §16 "Fixed subcase catalog" change
  the count sentence to 69 / 8 / 42 / 19 and append the 18 new rows using the
  intent column from §7–§9 of this document (one row per case). Do not rewrite
  older sections.
- `tasks/locker/CALIBRATION.md`: add a heading `## locker-v4` with the text
  "Catalog changed from 51 to 69 subcases; v3 results are not comparable."
  (The measurements are filled in at WP8.)

### 4.6 Seed smoke script (public, allowed)

In `tasks/locker/seed/scripts/smoke.py::main`, after the existing checks and
before cleanup, add exactly two checks using the existing `client` and
`check(name, condition, detail)` helpers:

```python
batch = (
    b"<Batch><Copy><Source>a.txt</Source><Target>b.txt</Target></Copy>"
    b"<Delete><Key>missing.txt</Key></Delete></Batch>"
)
status, _headers, _body = client.request(
    "POST", "/smoke?batch", body=batch, headers={"Content-Type": "application/xml"}
)
failures += not check("batch copy", status == 200, f"status {status}")
status, _headers, body = client.request("GET", "/smoke/b.txt")
failures += not check("batch copy visible", status == 200 and body == a_body, f"status {status}")
```

Adapt the bucket name and the source key/body variable to whatever the smoke
script already created (read the file; the object it uploads first is the one
to copy). Both checks **must fail on the seed** (the seed returns 405 for
`POST` on a bucket). Do not add ENOSPC checks or anything that reveals the
hidden recipes.

### 4.7 WP1 gate

```bash
python3 -m py_compile bench.py
python3 -m unittest discover -s tests
python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'
./bench.py check            # needs docker + opencode; must pass
cd tasks/locker/seed && go build -trimpath -o bin/locker ./cmd/locker && python3 scripts/smoke.py; cd -
```

`smoke.py` must report exactly the two new checks failing on the seed.
With the placeholders from §4.3 a full `run.py` run against the reference now
reports the 18 new cases as failed ("case not implemented yet") and the 51
existing cases as passed; that is the expected state between packages.

---

## 5. WP2 — Reference implementation (`tasks/locker/reference`)

The reference is private and exists to validate the hidden suite. It already
has: immutable blobs in `<data>/blobs/`, staging temp files in
`<data>/staging/`, multipart part files under `<data>/multipart/<id>/`, one
`manifest.json` replaced atomically by `saveLocked` (temp + fsync + rename +
dir fsync), a single `Store.mu` protecting `snapshot`, and a `fault string`
field read from `LOCKER_FAULT` in both `store.Open` (store.go:101) and
`server.New` (server.go:41). **`DeleteObject` and overwrites never remove blob
files** (there is no garbage collection), so sharing a blob between a source
and its copy is automatically safe in the reference.

### 5.1 `internal/store/store.go` — batch

Add next to the other sentinel errors:

```go
// ErrPrecondition reports a failed IfMatch or an absent batch Source.
var ErrPrecondition = errors.New("precondition failed")
```

Add the operation type and method:

```go
// BatchOp is one validated batch operation. Kind is "copy", "rename" or
// "delete". Source/Target are used by copy and rename; Key by delete.
// IfMatch is "" when absent, "*" or a quoted ETag otherwise.
type BatchOp struct {
	Kind    string
	Source  string
	Target  string
	Key     string
	IfMatch string
}

// Batch applies ops in order to a working copy of the bucket and publishes
// the result as a single manifest replacement, or changes nothing.
func (s *Store) Batch(bucketName string, ops []BatchOp) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	bucket, ok := s.snapshot.Buckets[bucketName]
	if !ok {
		return ErrNoSuchBucket
	}
	working := cloneBucket(bucket) // shallow copy of the Objects map
	now := time.Now().UTC()
	for _, op := range ops {
		switch op.Kind {
		case "copy", "rename":
			src, ok := working.Objects[op.Source]
			if !ok || !ifMatchOK(op.IfMatch, src.ETag) {
				return ErrPrecondition
			}
			dst := src          // shares Blob, Size, ETag, ContentType
			dst.ModTime = now   // Last-Modified is the batch commit time
			working.Objects[op.Target] = dst
			if op.Kind == "rename" {
				delete(working.Objects, op.Source)
			}
		case "delete":
			cur, ok := working.Objects[op.Key]
			if op.IfMatch != "" && (!ok || !ifMatchOK(op.IfMatch, cur.ETag)) {
				return ErrPrecondition
			}
			delete(working.Objects, op.Key)
		default:
			return ErrInvalid
		}
	}
	buckets := cloneBuckets(s.snapshot.Buckets)
	buckets[bucketName] = working
	next := Manifest{Buckets: buckets}
	if err := s.saveLocked(&next); err != nil {
		return err
	}
	s.snapshot = next
	return nil
}

func ifMatchOK(ifMatch, etag string) bool {
	if ifMatch == "" || ifMatch == "*" {
		return true
	}
	return normalizeETag(ifMatch) == normalizeETag(etag)
}
```

`cloneBucket`, `cloneBuckets`, `normalizeETag`, `saveLocked` already exist.
Important: the precondition is evaluated against `working`, inside the lock,
so "copy a→b then delete b IfMatch(etag of a)" succeeds and a conflicting
concurrent batch sees the committed result of the other. A rename onto the
Source of a *later* op in the same batch works because `working` is updated
op by op.

Multipart sessions are keyed by `(bucket, key, id)` in `s.uploads` and are not
touched by `Batch`; completion later calls `commitObject` which replaces the
key — nothing to do.

### 5.2 `internal/protocol` — parse `<Batch>`

Add `ParseBatch(r io.Reader, maxOps int) ([]store.BatchOp, error)` (or return
a protocol-local struct and convert in the server; either is fine). Rules:

- Read with `xml.NewDecoder`; decode into

  ```go
  type batchXML struct {
  	XMLName xml.Name  `xml:"Batch"`
  	Ops     []opXML   `xml:",any"`
  }
  type opXML struct {
  	XMLName xml.Name
  	Source  *string `xml:"Source"`
  	Target  *string `xml:"Target"`
  	Key     *string `xml:"Key"`
  	IfMatch *string `xml:"IfMatch"`
  }
  ```

  `xml:",any"` is required: `encoding/xml` silently drops unknown elements
  otherwise and an unknown operation like `<Move>` would be ignored instead of
  rejected. Pointers distinguish absent from empty.
- Invalid (return an error the server maps to 400): decode error; root is not
  `Batch`; `len(ops) == 0` or `> maxOps`; `XMLName.Local` not in
  `Copy|Rename|Delete`; Copy/Rename without non-empty Source and Target, or
  Source == Target, or with a `Key` element; Delete without non-empty Key, or
  with Source/Target; `IfMatch` present but neither `*` nor a quoted token
  (starts and ends with `"`, length ≥ 3).
- The server wraps the body in `http.MaxBytesReader(w, r.Body, 1<<20)` before
  parsing; a body over 1 MiB is 400.

### 5.3 `internal/server/server.go` — routing and error mapping

In `serveBucket` (line ~150), **before** the existing `uploadId` check, add:

```go
if _, ok := query["batch"]; ok {
	s.serveBatch(w, r, bucket, query)
	return
}
```

and implement:

```go
func (s *Server) serveBatch(w http.ResponseWriter, r *http.Request, bucket string, query url.Values) {
	for _, forbidden := range []string{"list-type", "prefix", "delimiter", "max-keys", "continuation-token", "uploads", "uploadId", "partNumber"} {
		if _, ok := query[forbidden]; ok {
			writeError(w, r, http.StatusBadRequest, "InvalidArgument", "batch cannot be combined with other parameters")
			return
		}
	}
	if r.Method != http.MethodPost {
		writeError(w, r, http.StatusMethodNotAllowed, "MethodNotAllowed", "batch requires POST")
		return
	}
	ops, err := protocol.ParseBatch(http.MaxBytesReader(w, r.Body, 1<<20), 32)
	if err != nil {
		writeError(w, r, http.StatusBadRequest, "InvalidArgument", err.Error())
		return
	}
	if err := s.store.Batch(bucket, ops); err != nil {
		writeStoreError(w, r, err)
		return
	}
	w.WriteHeader(http.StatusOK)
}
```

In `serveObject` (line ~347), at the top after `query := r.URL.Query()`:

```go
if _, ok := query["batch"]; ok {
	writeError(w, r, http.StatusBadRequest, "InvalidArgument", "batch is only valid on a bucket")
	return
}
```

In `writeStoreError` add before the `default` case:

```go
case errors.Is(err, store.ErrPrecondition):
	writeError(w, r, http.StatusPreconditionFailed, "PreconditionFailed", "a batch precondition failed")
```

Order of checks matters for the `errors` case (§7.3): an absent bucket with a
valid body is 404 (comes from `Store.Batch`); a malformed body on an absent
bucket may be 400 or 404 — the test only sends malformed bodies to an existing
bucket.

### 5.4 Full-disk (ENOSPC) behaviour

The prompt now requires: 500 or connection close (never 2xx), old version and
sessions unchanged, temp space released, keep serving, restart loads committed
state. The reference already removes temp files through `cleanup` defers and
maps unknown errors to 500. Two changes are needed:

1. **Attribute write errors correctly.** In `PutObject`, `UploadPart` and
   `Complete`, `io.Copy(io.MultiWriter(temp, digest), body)` returns
   `ErrIncomplete` (→ 400) on *any* error, including ENOSPC from `temp`.
   Introduce

   ```go
   type errWriter struct {
   	w   io.Writer
   	err error
   }

   func (e *errWriter) Write(p []byte) (int, error) {
   	n, err := e.w.Write(p)
   	if err != nil {
   		e.err = err
   	}
   	return n, err
   }
   ```

   wrap `temp` in it, and after `io.Copy` check `if ew.err != nil { return
   ..., ew.err }` **before** the `ErrIncomplete` checks. A storage write error
   therefore reaches `writeStoreError`'s default branch → 500 `InternalError`.
   `Sync`, `Close`, `Rename`, `syncDir` and `saveLocked` errors already
   propagate → 500.
2. **Do not register anything on failure.** `UploadPart` only sets
   `upload.Parts[number]` after a successful rename (already true); keep it
   that way. `Complete` assembles into a staging temp and only then commits
   (already true). `saveLocked` writes a temp manifest; if that fails the
   snapshot is not replaced (already true).

Go's `net/http` closes the connection after a handler returns without having
read the whole body, so the client sees either the 500 or a reset. Both are
accepted by the tests.

### 5.5 `internal/server` route for `batch` on `/` (bucket list)

`POST /?batch` must not panic: `bucket == ""` already returns 405 for
non-GET/HEAD. No change needed; mention only so nobody "fixes" it.

### 5.6 Fault switches (details in §10)

Add the nine new `LOCKER_FAULT` values exactly as listed in §10.1; each is a
small `if s.fault == "..."` branch at the stated site. The seed, prompt and
image never mention `LOCKER_FAULT`.

### 5.7 WP2 gate

```bash
cd tasks/locker/reference && go vet ./... && go test ./... && cd -
docker run --rm --network none -v "$PWD/tasks/locker/reference:/src:ro" -v /tmp/locker-ref-bin:/out \
  -w /src -e GOFLAGS=-mod=mod -e GOCACHE=/tmp/gocache -e HOME=/tmp \
  opencode-bench-locker:latest go build -trimpath -o /out/locker ./cmd/locker
```

Add Go unit tests in `internal/store/store_test.go` (or a new
`batch_test.go`) for: in-order working copy, 412 leaves the manifest file
unchanged (compare bytes before/after), rename chain, delete without IfMatch on
absent key, >32 ops rejected by the parser, unknown element rejected, ENOSPC
attribution (use a writer that fails after N bytes via a test-only seam is
*not* available — instead test `errWriter` directly). Then run the existing
51 cases against the new Linux binary (full run; new placeholders fail,
everything else must pass):

```bash
docker run --rm --network none -v /tmp/locker-ref-bin:/bin-under-test:ro \
  -v "$PWD/tasks/locker/tests:/opt/opencode-bench/locker-tests:ro" -v /tmp/locker-out:/out \
  -e HOME=/tmp -e TMPDIR=/tmp opencode-bench-locker:latest \
  python3 -B /opt/opencode-bench/locker-tests/run.py --binary /bin-under-test/locker \
  --manifest /opt/opencode-bench/locker-tests/manifest.json --output /out/ref.json
```

---

## 6. WP3 — State model (`tasks/locker/tests/state_model.py`)

**Author this from `PROMPT.md` only** (sections "Version headers and ETags",
"Range GET/HEAD", "Conditional GET/HEAD", "Listing and pagination", "Buckets
and keys", "Persistence…", "Multipart transitions", "Batch operations", "XML
shapes"). Do not open the reference while writing it. Pure Python, standard
library only, no I/O, no threads. Reuse `oracles.py`:
`expected_range(size, spec) -> (start, end) | None`,
`effective_listing(keys, prefix, delimiter) -> [(kind, name)]`,
`paginate(items, max_keys)`, and
`conditional_action(if_match=, if_none_match=, if_modified_since=,
if_unmodified_since=, etag=, modified=) -> "continue" | "not-modified" |
"precondition-failed"`.

### 6.1 Data types

```python
@dataclass(frozen=True)
class Obj:
    body: bytes
    content_type: str
    @property
    def etag(self) -> str: ...          # '"' + md5(body).hexdigest() + '"'

@dataclass
class Upload:
    bucket: str
    key: str
    content_type: str
    parts: dict[int, bytes]             # part number -> bytes

@dataclass
class State:
    buckets: dict[str, dict[str, Obj]]  # bucket -> key -> Obj
    uploads: dict[str, Upload]          # upload_id -> session (ids are server-issued)
    def clone(self) -> "State": ...     # copy both dict levels and each Upload.parts

@dataclass
class Expected:
    status: int
    etag: str | None = None             # exact quoted ETag when the prompt fixes it
    body: bytes | None = None           # None = do not compare (HEAD, 304, 412, 416)
    content_length: int | None = None
    content_range: str | None = None    # "bytes a-b/n" or "bytes */n"
    content_type: str | None = None
    code: str | None = None             # XML error code for 4xx
    listing: list[tuple[str, str]] | None = None   # [(kind, name)] for the requested page
    truncated: bool | None = None
    parts: list[tuple[int, str, int]] | None = None  # (number, etag, size)
```

The model never predicts `Last-Modified`, `NextContinuationToken` or
`UploadId`; the checker treats those as opaque and only checks shape
(RFC1123 date parses; token non-empty iff truncated; id non-empty).

### 6.2 Operations (each mutates `state` and returns `Expected`)

| Function | Rules to encode (all from the prompt) |
|---|---|
| `create_bucket(state, b)` | 200; idempotent |
| `delete_bucket(state, b)` | 404 `NoSuchBucket` if absent; 409 `BucketNotEmpty` if any object **or any live upload** in `b`; else 204 |
| `put(state, b, k, body, ctype)` | 404 if bucket absent; else store `Obj(body, ctype or "application/octet-stream")`, 200 with `etag` |
| `delete(state, b, k)` | 404 if bucket absent; 204 whether or not the key exists |
| `get(state, b, k, range_spec, if_match, if_none_match)` / `head(...)` | 404 `NoSuchBucket`/`NoSuchKey`; then `conditional_action` (dates are never generated, pass `None`; pass any fixed `datetime` as `modified`); `precondition-failed` → 412 no body; `not-modified` → 304 with `etag`, no body; then range: `expected_range` None → 416 with `content_range="bytes */n"`; else 206 with `content_range="bytes a-b/n"`, `content_length=b-a+1`, body slice; otherwise 200 full body. Always `content_type`, `etag`. `head` copies `get` but sets `body=None` |
| `list(state, b, prefix, delimiter, max_keys, page_index)` | 404 if bucket absent; `items = effective_listing(sorted(keys), prefix, delimiter)`; `pages = paginate(items, max_keys)`; return page `page_index` (empty list if no pages and index 0), `truncated = page_index < len(pages)-1` |
| `initiate(state, b, k, ctype, upload_id)` | 404 if bucket absent; register `Upload`; 200. `upload_id` is taken from the server response and passed in by the checker |
| `upload_part(state, b, k, uid, n, body)` | 404 `NoSuchUpload` if `uid` unknown **or** its bucket/key differ from `(b, k)`; else replace `parts[n]`, 200 with part `etag` |
| `list_parts(state, b, k, uid)` | 404 as above; else `parts` sorted by number |
| `complete(state, b, k, uid, requested)` | 404 as above; `requested` = `[(n, etag)]`; 400 `InvalidArgument` if empty, duplicate numbers, number not in parts, or etag mismatch — no change; else object = concat of requested parts in numeric order with the session's content type, session removed, 200 with full-body `etag` |
| `abort(state, b, k, uid)` | 404 as above; else remove session, 204 |
| `batch(state, b, ops)` | 404 if bucket absent; structural validation (0 or >32 ops, unknown kind, missing fields, source==target) → 400; apply in order to a **copy** of the bucket dict exactly as §4.1(h); precondition failure → 412 `PreconditionFailed` and the original dict is kept; success → install copy, 200 |

`ops` for `batch` is a list of tuples `("copy", src, dst, if_match)`,
`("rename", src, dst, if_match)`, `("delete", key, if_match)` with
`if_match` ∈ `{None, "*", '"<md5>"'}`.

Restart: the model has no persistence step; the checker simply keeps the same
`State` across `server.restart()` and drops nothing (the prompt does not
grade in-progress multipart recovery; the generator therefore never uses an
upload across a restart — see §9.2).

### 6.3 Unit tests (`tasks/locker/tests/test_state_model.py`)

Cover at least: range clipping/suffix/416 on empty object; conditional
precedence `If-Match` wins over `If-None-Match`; listing with delimiter
grouping and page boundaries counted over the combined ordering; multipart
re-upload replaces a part, completion with a stale ETag is 400 and leaves the
session usable, abort then complete is 404; batch in-order working copy
(`copy a→b; delete b IfMatch(etag_a)` → 200, `… IfMatch(old etag_b)` → 412
with nothing changed), rename chain, `*` on absent source → 412, delete
without IfMatch on absent key → 200 no-op, 33 ops → 400; bucket delete with a
live upload → 409. Run with
`python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'`.

---

## 7. WP4 — Batch cases in `tasks/locker/tests/test_runner.py`

### 7.1 Shared helpers (add near `raw_put_payload`)

```python
BatchOp = tuple  # ("copy", src, dst, if_match) | ("rename", src, dst, if_match) | ("delete", key, if_match)

def batch_xml(ops: list[BatchOp]) -> bytes:
    parts = ["<Batch>"]
    for op in ops:
        kind = op[0]
        if kind in ("copy", "rename"):
            _, src, dst, if_match = op
            tag = "Copy" if kind == "copy" else "Rename"
            parts.append(f"<{tag}><Source>{xml_escape(src)}</Source><Target>{xml_escape(dst)}</Target>")
        else:
            _, key, if_match = op
            tag = "Delete"
            parts.append(f"<Delete><Key>{xml_escape(key)}</Key>")
        if if_match is not None:
            parts.append(f"<IfMatch>{xml_escape(if_match)}</IfMatch>")
        parts.append(f"</{tag}>")
    parts.append("</Batch>")
    return "".join(parts).encode("utf-8")

def batch(port: int, bucket: str, ops: list[BatchOp], *, timeout: float = 60.0) -> Response:
    return h.request(port, "POST", h.bucket_path(bucket) + "?batch",
                     body=batch_xml(ops), headers={"Content-Type": "application/xml"}, timeout=timeout)

def raw_batch(port: int, path: str, body: bytes) -> Response:
    return h.request(port, "POST", path, body=body, headers={"Content-Type": "application/xml"})

def expect_error(response: Response, status: int, code: str, label: str) -> None:
    expect(response, status, label)
    root = h.parse_xml(response.body)
    check(root.tag == "Error" and h.text(root, "Code") == code,
          f"{label}: expected <Error><Code>{code}</Code>, got {response.body[:200]!r}")

def bucket_snapshot(port: int, bucket: str) -> dict[str, tuple[bytes, str, str]]:
    """key -> (body, etag, content-type) for every listed key, via single-page listing + GETs."""
    contents, prefixes = h.collect_listing(port, bucket)
    out = {}
    for key, size, etag in contents:
        r = expect(h.get_object(port, bucket, key), 200, f"snapshot GET {key}")
        check(len(r.body) == size and r.header("etag") == etag, f"snapshot listing/GET disagree for {key}")
        out[key] = (r.body, etag, r.header("content-type"))
    return out

def expect_absent(port: int, bucket: str, *keys: str, label: str) -> None:
    for key in keys:
        expect(h.get_object(port, bucket, key), 404, f"{label}: {key} should be absent")
```

`xml_escape` is `xml.sax.saxutils.escape`. Use `h.md5_etag(body)` for
expected ETags. Bodies: use distinct literal bytes or
`h.DeterministicBody(n, seed=k)` joined (`b"".join(h.DeterministicBody(...))`
if it is iterable — check the class before use).

Register every function in `CASE_FUNCS["p1.batch"]` / `CASE_FUNCS["p2.batch"]`
under the exact case IDs from §2, replacing the placeholders.

### 7.2 `p1.batch/copy-rename-delete`

1. `make_buckets(port, "batch")`; PUT `a` = `b"alpha"` with
   `Content-Type: text/plain`; PUT `x` = `b"xray"` with `application/json`.
   Record `lm_a`, `lm_x` (`h.parse_http_date(r.header("last-modified"))`).
2. `batch(port, "batch", [("copy","a","b",None), ("rename","b","c",None),
   ("delete","a",None), ("copy","c","x",None)])` → expect 200 and
   `len(response.body) == 0`.
3. GET `a` → 404; GET `b` → 404; GET `c` → 200, body `alpha`, ETag
   `h.md5_etag(b"alpha")`, `content-length` `"5"`, `content-type`
   `text/plain`, `accept-ranges: bytes`; GET `x` → same body/ETag/type as `c`
   (type replaced from `application/json` to `text/plain`).
4. `parse_http_date(c.last-modified) >= lm_a` and
   `parse_http_date(x.last-modified) >= lm_x`.
5. `h.collect_listing(port, "batch")` contents exactly
   `[("c", 5, etag), ("x", 5, etag)]`, no prefixes.
6. `batch(..., [("delete","c",None), ("delete","never-existed",None)])` → 200;
   listing exactly `[("x", 5, etag)]`.

### 7.3 `p1.batch/preconditions`

Setup: bucket `pre`; PUT `a`=`b"A1"`, `b`=`b"B1"`; `ea`, `eb` = their ETags;
`wrong = h.md5_etag(b"not-this")`. After every expected failure call
`bucket_snapshot` and compare with the snapshot taken before the request.

1. `[("copy","a","c",ea)]` → 200; GET `c` == `A1`.
2. `[("copy","a","d",eb)]` → `expect_error(r, 412, "PreconditionFailed")`;
   `d` absent; snapshot unchanged.
3. `[("copy","a","d",None), ("delete","b","*")]` → 200; `d` == `A1`; `b`
   absent.
4. `[("copy","missing","e",None)]` → 412; `e` absent; unchanged.
5. Failure in the last op of a multi-op batch: `[("rename","a","f",None),
   ("copy","c","g",None), ("delete","c",wrong)]` → 412; GET `a` == `A1`; `f`,
   `g` absent; snapshot unchanged.
6. Working-copy evaluation: `[("copy","a","h",None), ("delete","h",ea)]` →
   200; `h` absent, `a` present. Then `[("copy","a","h",None),
   ("delete","h",wrong)]` → 412; `h` absent.
7. `[("delete","nothing","*")]` → 412. `[("delete","nothing",None)]` → 200.
8. `[("rename","a","a2","*")]` → 200; `a` absent, `a2` == `A1`.

### 7.4 `p1.batch/errors`

Setup: bucket `err`; PUT `a`=`b"A"`; `before = bucket_snapshot(...)`.
Each raw body below → `expect_error(raw_batch(port, "/err?batch", body), 400,
"InvalidArgument", label)`; afterwards `bucket_snapshot == before`:

- `b"<Batch><Copy>"` (malformed)
- `b"<Batch></Batch>"` (zero ops)
- `b"<Ops><Delete><Key>a</Key></Delete></Ops>"` (wrong root)
- `batch_xml([("delete", f"k{i}", None) for i in range(33)])` (33 ops)
- `b"<Batch><Move><Source>a</Source><Target>b</Target></Move></Batch>"`
- `b"<Batch><Copy><Source>a</Source></Copy></Batch>"` (missing Target)
- `b"<Batch><Rename><Source></Source><Target>b</Target></Rename></Batch>"`
- `b"<Batch><Delete></Delete></Batch>"` (missing Key)
- `b"<Batch><Copy><Source>a</Source><Target>a</Target></Copy></Batch>"`
- `b"<Batch><Delete><Key>a</Key><IfMatch></IfMatch></Delete></Batch>"`

Routing errors with a **valid** body `good = batch_xml([("delete","zz",None)])`:

- `raw_batch(port, "/err/a?batch", good)` → 400 (object path)
- `raw_batch(port, "/err?batch&list-type=2", good)` → 400
- `raw_batch(port, "/err?batch&uploads", good)` → 400
- `raw_batch(port, "/nobucket-" + token + "?batch", good)` → 404 `NoSuchBucket`

Boundary: `batch_xml([("delete", f"k{i}", None) for i in range(32)])` → 200.
Finally GET `a` == `A`.

### 7.5 `p1.batch/independence`

Bucket `ind`.

1. PUT `a`=`b"orig"` `text/plain`; batch copy `a→b`; PUT `a`=`b"new-a"`
   `text/csv` → GET `b` body `orig`, ETag `md5(orig)`, type `text/plain`;
   DELETE `a` → GET `b` unchanged.
2. `big = 64 KiB` deterministic bytes; PUT `c`=big; batch copy `c→d`; DELETE
   `c`; GET `d` == big; GET `d` with `Range: bytes=100-199` → 206,
   `content-range` `bytes 100-199/65536`, body `big[100:200]`.
3. PUT `r0`=`b"chain"`; batch `[rename r0→r1, rename r1→r2, rename r2→r3]`
   → 200; GET `r3` == `chain`; `r0`,`r1`,`r2` absent.
4. PUT `e`=`b""`; batch copy `e→e2` → 200; GET `e2` → 200, `content-length`
   `"0"`, ETag `md5(b"")`, body empty.
5. Multipart object: `uid = initiate(port,"ind","m","application/x-parts")`;
   `upload_part(... 1, p1)` (5 KiB), `upload_part(... 2, p2)` (3 KiB);
   `complete_upload(port,"ind","m",uid,[(1,etag1),(2,etag2)])` → 200; batch
   copy `m→m2`; GET `m2` body `p1+p2`, ETag `md5(p1+p2)`, type
   `application/x-parts`.
6. Fan-out: batch `[copy d→d1, copy d→d2, copy d→d3]`; DELETE `d`; DELETE
   `d1`; batch `[delete d2]`; GET `d3` == big.
7. `server.restart()`; re-check `b`, `d3`, `r3`, `e2`, `m2` (body + ETag);
   confirm `a`, `c`, `d`, `d1`, `d2`, `r0` absent.

### 7.6 `p1.batch/cross-feature`

Bucket `xf`.

1. PUT `a` = 1000 deterministic bytes, `Content-Type: application/x-test`;
   batch copy `a→b`; `eb = md5(body)`.
2. GET `b` `Range: bytes=10-19` → 206, `content-range: bytes 10-19/1000`,
   body `a[10:20]`, ETag `eb`. GET `b` `If-None-Match: eb` → 304, ETag `eb`,
   empty body. GET `b` `If-Match: md5(b"other")` → 412. HEAD `b` → 200, same
   ETag/`content-length: 1000`/type, empty body. GET `b` `Range: bytes=1000-`
   → 416, `content-range: bytes */1000`.
3. Listing: contents `[("a",1000,eb), ("b",1000,eb)]`.
4. Session survives rename: `uid = initiate(port,"xf","m","text/plain")`;
   `upload_part(...,1,b"part-one")`; PUT `m`=`b"plain"`; batch
   `[rename m→m-old]` → 200; `list_parts(port,"xf","m",uid)` → one part,
   number 1, ETag `md5(b"part-one")`; `complete_upload(..., [(1, etag)])` →
   200; GET `m` == `part-one` with type `text/plain`; GET `m-old` == `plain`.
5. Session survives delete: PUT `n`=`b"n0"`; `uid2 = initiate(...,"n",...)`;
   upload part 1 `b"n-part"`; batch `[delete n None]` → 200; `n` absent;
   complete → 200; GET `n` == `n-part`.
6. Delete-bucket guard: batch `[delete m-old]`, `[delete m]`, `[delete n]`,
   `[delete a]`, `[delete b]` as one batch → 200; listing empty;
   `h.delete_bucket(port,"xf")` → 204.

### 7.7 `p1.batch/atomic-visibility`

Bucket `vis`; `A`, `B` = two distinct 2048-byte bodies; PUT `a`=A, `b`=B;
`eA`, `eB`.

- Writer thread: `deadline = time.monotonic() + 3.0`; loop while
  `time.monotonic() < deadline`: `batch(port,"vis",[("rename","a","tmp",None),
  ("rename","b","a",None), ("rename","tmp","b",None)])`; any non-200 → append
  to `errors` and stop; count `swaps`.
- 4 reader threads (start together with a `threading.Barrier(5)` that the
  writer also joins): while `not writer_done`: alternately
  (i) `page = h.list_page(port, "vis")` (a **single** request): require
  `sorted(k for k,_,_ in page.contents) == ["a","b"]`, every size 2048, ETags
  `{eA, eB}` exactly (two different ETags), no `tmp`;
  (ii) `r = h.get_object(port,"vis","a")`: status 200, body in `(A, B)`,
  `r.header("etag") == h.md5_etag(r.body)`, `content-length` `"2048"`.
  Count `reads`. Any violation → `errors.append(...)` and stop.
- Join all with `timeout=limits["request_deadline_s"] + 10`; a still-alive
  thread is a failure ("hung").
- `check(not errors, ...)`; `check(swaps >= 5 and reads >= 20, "too few
  operations completed in 3 s")`.
- Final: GET `a`, `b`: bodies are `(A,B)` if `swaps % 2 == 0` else `(B,A)`.

### 7.8 `p1.batch/concurrent-batches`

Bucket `cc`. Use `threading.Barrier(n)` immediately before each request and
`h.request(..., timeout=limits["request_deadline_s"])`; join with timeout;
collect statuses; **any status ≥ 500 fails the case**.

- Round 1: PUT `a`=A, `b`=B. T1: `[rename a→b]`, T2: `[rename b→a]`. Both
  must be 200 (each succeeds in either serial order). Serial order T1,T2
  ends in `{a: A}`; order T2,T1 ends in `{b: B}`. The final snapshot must be
  exactly one of those two.
- Round 2 (precondition conflict, detects checking IfMatch outside the commit
  lock): PUT `x`=X, `y`=Y. T1: `[copy x→z IfMatch ex, delete y IfMatch "*"]`;
  T2: `[delete y IfMatch "*", copy x→w None]`. Exactly one response is 200 and
  the other 412; `y` absent; if T1 won: `z`==X and `w` absent; if T2 won:
  `w`==X and `z` absent.
- Round 3: PUT `k0..k3` with distinct bodies `K0..K3`. 4 threads, each 10
  iterations of `[copy k_i→shared, copy shared→own_i]` (second op reads the
  working copy). Meanwhile one reader thread GETs `shared` in a loop: 200 with
  body in `{K0..K3}` and ETag == md5(body) (404 allowed only before the first
  200). All 40 batches 200; final `own_i == K_i` for every i; `shared` ∈
  `{K0..K3}`.
- Round 4: barrier between a batch `[rename k0→k0b]` and an ordinary PUT
  `k0`=`b"late"`. Serial order PUT,rename ends in `{k0b: late}` (no `k0`);
  order rename,PUT ends in `{k0b: K0, k0: late}`. The batch must be 200 (its
  source exists in both orders), the PUT 200, and the snapshot restricted to
  `k0`/`k0b` must be exactly one of those two.

Run the whole case **twice in a loop** inside the function (two fresh
buckets) to raise the chance of catching a race in one run.

### 7.9 `p2.batch/kill-during-swap`

Pattern: see `p2_overwrite_publication_crash` (test_runner.py ~1502) for
`server.stop(kill=True)` / `server.restart()` usage.

1. Bucket `ks`; `A`, `B` distinct 4096-byte bodies; PUT `a`=A, `b`=B.
2. `rng = random.Random(limits["fixture_seed"] * 31 + 7)`.
3. For `trial in range(limits["crash_trials"])` (6):
   - Start a writer thread that loops
     `batch(port,"ks",[rename a→tmp, rename b→a, rename tmp→b])` using
     `h.request` with `timeout=5`, counting 200s in `acked`; it stops on any
     exception (connection reset after the kill) or non-200 **after** the
     kill flag is set. A non-200 *before* the kill flag is a failure.
   - Main: `time.sleep(rng.uniform(0.02, 0.25))`; set `kill_flag`;
     `server.stop(kill=True)`; join the writer (timeout 10).
   - `server.restart()` (raises if `/health` is not up within 15 s → failure
     "did not restart").
   - `page = h.list_page(port,"ks")`: keys exactly `["a","b"]` (no `tmp`),
     sizes 4096.
   - GET `a`, GET `b`: each body in `(A, B)`, ETag == md5(body),
     `content-length` `"4096"`, and the two bodies **differ** (a permutation
     of {A, B}, never A/A or B/B).
   - One more swap batch after restart → 200 and GET `a`/`b` swapped relative
     to the previous read (the store is usable after recovery).
4. Parity is **not** checked: with one in-flight batch both parities are
   legal. The graded invariant is "all three renames or none".

### 7.10 `p2.batch/kill-after-ack`

Bucket `ka`. For `i in range(limits["crash_trials"])`:

1. PUT `p{i}`=P_i, `q{i}`=Q_i (distinct 1–8 KiB bodies).
2. `batch(port,"ka",[("copy",f"p{i}",f"c{i}",None), ("rename",f"q{i}",f"r{i}",None), ("delete",f"p{i}","*")])` → must be 200.
3. Immediately `server.stop(kill=True)` (no sleep), `server.restart()`.
4. GET `c{i}` == P_i (ETag, type), GET `r{i}` == Q_i; `p{i}`, `q{i}` absent;
   listing contains `c{i}`, `r{i}` and not `p{i}`/`q{i}`.

### 7.11 `p2.batch/blob-lifetime`

Bucket `bl`; `big` = 64 KiB deterministic bytes.

1. PUT `a`=`b"A"`; batch copy `a→b`; DELETE `a`; GET `b` == `A`;
   `server.restart()`; GET `b` == `A`.
2. Batch rename `b→c`; PUT `c`=`b"C2"` (overwrite); `server.restart()`; GET
   `c` == `C2`; `b` absent.
3. PUT `c`=big; batch copy `c→d`; DELETE `c`; open
   `g = h.GatedUpload(port, h.object_path("bl","u"), 65536)`; `g.send(big[:30000])`
   (half the body; see the class for the exact method names); `server.stop(kill=True)`;
   `g.close()`; `server.restart()`; GET `d` == big (full and `Range: bytes=0-9`);
   `u` absent.
4. Batch `[copy d→d1, copy d→d2, copy d→d3]`; DELETE `d`, DELETE `d1`; batch
   `[delete d2]`; `server.restart()`; GET `d3` == big.
5. Batch `[rename d3→d4]`; PUT `d4`=`b"small"`; `server.restart()`; GET `d4`
   == `small`; `d3` absent. After steps 1–5 the bucket holds exactly one key:
   `h.collect_listing(port,"bl")` contents must be
   `[("d4", 5, h.md5_etag(b"small"))]`.

### 7.12 WP4 gate

```bash
# build the reference for Linux first (§5.7), then for each family:
for i in $(seq 1 10); do
  docker run --rm --network none -v /tmp/locker-ref-bin:/bin-under-test:ro \
    -v "$PWD/tasks/locker/tests:/opt/opencode-bench/locker-tests:ro" -v /tmp/locker-out:/out \
    -e HOME=/tmp -e TMPDIR=/tmp opencode-bench-locker:latest \
    python3 -B /opt/opencode-bench/locker-tests/run.py --binary /bin-under-test/locker \
    --manifest /opt/opencode-bench/locker-tests/manifest.json --family p1.batch \
    --output /out/p1-batch-$i.json || echo "RUN $i FAILED"
done
```

Repeat with `--family p2.batch`. All 20 runs must report every case `ok`. Also
run the seed binary once against both families and record which cases fail
(the seed returns 405 for `POST /b?batch`, so every batch case must fail —
if one passes, the assertion is vacuous: fix it).

---

## 8. WP5 — Full-disk fault family

Verified on this host (Docker Desktop 29.x, image `opencode-bench-locker`):
`docker run --rm --network none --tmpfs /scratch:rw,size=33554432 …` gives a
filesystem whose `os.statvfs("/scratch")` reports
`f_frsize * f_blocks == 33554432`; writing 48 MiB fails with `errno 28
(ENOSPC)` after exactly 33554432 bytes; after `os.remove` the available space
returns to 33554432. The `size=` option accepts a plain byte count.

### 8.1 `bench.py`

- `run_tests` (line ~1419): after the `"--network", "none",` pair add
  `"--tmpfs", f"/scratch:rw,size={manifest['limits']['fault_fs_bytes']}",`
  and after `"--output", "/out/results.json",` add `"--scratch", "/scratch"`.
  Keep `TMPDIR=/tmp` for the Python runner itself.
- `cmd_check`: directly after the existing Docker smoke block (the
  `bash -lc "opencode --version && go version && python3 --version"` call)
  add a **second** `docker run` (separate call, so no nested shell quoting):

  ```python
  fs = manifest["limits"]["fault_fs_bytes"]
  probe = ["docker", "run", "--rm", "--network", "none",
           "--tmpfs", f"/scratch:rw,size={fs}", IMAGE, "python3", "-c",
           f"import os,sys; s=os.statvfs('/scratch'); t=s.f_frsize*s.f_blocks; sys.exit(0 if abs(t-{fs})<=1048576 else 3)"]
  ```

  A non-zero exit becomes the problem
  `"image cannot mount the fault-injection tmpfs at the frozen size"`
  (use the same `problems.append` pattern as the other checks).
- `run_tests` admission: no change — exit codes other than 0/1 already yield
  `result_valid = False` (phase 1). Add a specific message when
  `proc.returncode == 3`: `"hidden runner reported missing test
  infrastructure (scratch filesystem)"` so operators do not read it as a
  candidate failure.
- `tests/test_bench.py`: extend `test_abnormal_exit_is_not_admitted` with
  `rc 3, all cases error` → `result_valid is False`.

### 8.2 `tasks/locker/tests/run.py`

- Add `--scratch PATH` (default `None`) and `--generator-seed INT` (default
  `None`, author-only; see §9).
- After loading the manifest: if the run includes family `p2.faults`
  (full run, or `--family p2.faults`) then:
  - if `args.scratch is None` → print
    `error: p2.faults requires --scratch pointing at a tmpfs of
    {fault_fs_bytes} bytes` to stderr, **still run everything else**, mark the
    three faults cases `ok: false` with `error: "infrastructure: no scratch
    filesystem"`, write the result file, and `return 3`.
  - else `s = os.statvfs(args.scratch)`; if
    `abs(s.f_frsize*s.f_blocks - fault_fs_bytes) > 1048576` or the directory
    is not writable → same treatment with `error: "infrastructure: scratch
    size X"` and `return 3`.
- Pass `scratch` into `run_suite(..., scratch=args.scratch)` and from there
  into `limits["scratch_dir"]` (a string) so case functions can read it.
- If `--generator-seed` is given, set `result["diagnostic"] = True` and
  `limits["generator_seed"] = args.generator_seed`.

### 8.3 Runner plumbing in `test_runner.py`

`_run_case` creates `h.Server(binary)` with a root under `TMPDIR`. Fault cases
need their root on the scratch filesystem **and** the server's own temp dir on
it (a candidate that stages uploads in `os.TempDir()` must also hit ENOSPC).
So the faults family does **not** go through `_run_case`; it gets a dedicated
branch in `run_family` (like `p2.perf`) and builds its own server:

```python
def _scratch_server(binary: str, limits: Limits) -> h.Server:
    root = tempfile.mkdtemp(prefix="locker-fault-", dir=limits["scratch_dir"])
    return h.Server(binary, root=root, env={"TMPDIR": root, "TMP": root, "TEMP": root}).start()
```

`h.Server.start` runs `Popen(..., env={**os.environ, **self.env})`, so the
dict above *extends* the environment; `h.Server.close()` stops the process and
`shutil.rmtree(self.root)`.

Concretely (`test_runner.py`, next to `p2_perf_family` / `run_family`):

```python
FAULT_CASES: dict[str, Callable[[str, Limits], None]] = {
    "put-preserves-old": p2_faults_put_preserves_old,
    "space-released": p2_faults_space_released,
    "multipart-staging": p2_faults_multipart_staging,
}

def _run_fault_case(binary: str, func: Callable[[str, Limits], None], limits: Limits) -> tuple[bool, float, str | None]:
    start = time.monotonic()
    try:
        func(binary, limits)
        return True, time.monotonic() - start, None
    except Exception as exc:  # noqa: BLE001
        return False, time.monotonic() - start, _error_text(exc)
```

and in `run_family`, after the `if family["id"] == "p2.perf":` block:

```python
    elif family["id"] == "p2.faults":
        for case_id in family["cases"]:
            ok, duration, error = _run_fault_case(binary, FAULT_CASES[case_id], limits)
            results.append({"id": case_id, "ok": ok, "duration": duration, "error": error})
```

Set `CASE_FUNCS["p2.faults"] = {}` (same convention as `"p2.perf": {}`),
replacing the §4.3 placeholders. Each of the three functions takes
`(binary, limits)`, does `server = _scratch_server(binary, limits)` first and
`server.close()` in a `finally` (that `rmtree`s the root and frees the tmpfs
for the next case). No default server is started on `/tmp` for this family.


### 8.4 Large-write helper

```python
def oversized_put(port: int, path: str, total: int, content_type: str = "application/octet-stream") -> tuple[int | None, str]:
    """Stream `total` bytes with Content-Length=total over a raw socket.
    Returns (status, detail): status is the HTTP status if the server answered,
    None if the connection was reset/closed before a status line."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=60)
    try:
        head = (f"PUT {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: {content_type}\r\n"
                f"Content-Length: {total}\r\nConnection: close\r\n\r\n").encode()
        sock.sendall(head)
        sent = 0
        chunk = bytes(1 << 20)
        try:
            while sent < total:
                sock.sendall(chunk)
                sent += len(chunk)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass  # the server may answer or close before reading everything
        try:
            status, _headers, _body = h.read_raw_response(sock, time.monotonic() + 60)
            return status, f"status {status} after {sent} bytes"
        except (ConnectionError, OSError, CaseFailure) as exc:
            return None, f"no status after {sent} bytes: {exc}"
    finally:
        sock.close()

def check_failed_cleanly(status: int | None, detail: str, label: str) -> None:
    check(status is None or status >= 500, f"{label}: oversized write must fail with 5xx or a closed connection, got {detail}")
```

Put the helper in `test_runner.py`; it mirrors `h.raw_half_close` (helpers.py
~411) and uses `h.read_raw_response`. Use `total = limits["fault_write_bytes"]`
(48 MiB) and the content-free `bytes(1 << 20)` chunk (content does not matter).

### 8.5 Cases (`FAULT_CASES`, dispatched via the dedicated branch)

Each case: `server = _scratch_server(binary, limits)`, `port = server.port`,
create bucket `fx`; `SMALL = 8 * 1024 * 1024` (fits twice in 32 MiB even if
the candidate keeps unreferenced blobs around, as the reference does).
"Deterministic body" means `b"".join(h.DeterministicBody(n, seed=…))`.

`put-preserves-old`
1. PUT `k` = 1 MiB deterministic body `old`, type `text/x-old`; record ETag.
2. `status, detail = oversized_put(port, h.object_path("fx","k"), limits["fault_write_bytes"], "text/x-new")`;
   `check_failed_cleanly`.
3. GET `k` → 200, body `old`, ETag `md5(old)`, `content-length` `"1048576"`,
   type `text/x-old`. HEAD `k` same headers. Listing shows `k` size 1048576.
4. GET `/health` → 200 (still serving). PUT `other` = `b"ok"` → 200 (unrelated
   small write works).
5. `server.restart()`; GET `k` == `old` again; GET `other` == `ok`.

`space-released`
1. PUT `base` = `b"base"`.
2. Twice: `oversized_put(... "fx/big" ...)` → `check_failed_cleanly`; GET
   `big` → 404.
3. PUT `fill` = `SMALL` deterministic bytes → **must be 200**; GET `fill`
   byte-exact (stream-compare lengths and ETag; the body is 8 MiB — fine to
   hold in the test client).
4. DELETE `fill` → 204. Repeat steps 2–3 once more with key `fill2`
   (a leak of a single staged temp would make the second 8 MiB PUT fail).
5. `server.restart()`; GET `fill2` == bytes; GET `base` == `base`.

`multipart-staging`
1. `uid = initiate(port,"fx","mp","application/x-mp")`;
   `e1 = upload_part(port,"fx","mp",uid,1,b"part-one")`.
2. `oversized_put(port, multipart_path("fx","mp",uid,2), limits["fault_write_bytes"])`
   → `check_failed_cleanly`.
3. `parts = list_parts(port,"fx","mp",uid)` (returns `list[dict]` with keys
   `number`, `etag`, `size`) → `[p["number"] for p in parts] == [1]` and
   `parts[0]["etag"] == e1` (no phantom part 2).
4. `e2 = upload_part(... 2, b"part-two")` → 200;
   `complete_upload(port,"fx","mp",uid,[(1,e1),(2,e2)])` → 200; GET `mp` ==
   `b"part-onepart-two"`, type `application/x-mp`.
5. PUT `after` = `SMALL` bytes → 200 (the failed part's temp space was
   released). `server.restart()`; GET `mp` unchanged; GET `after` ETag
   matches.

The seed (reads the whole body into memory, then writes directly to the final
path) is expected to **fail** `put-preserves-old` (it truncates/overwrites the
final file) and may pass or fail the others; record what it does in
`CALIBRATION.md`.

### 8.6 WP5 gate

- In Docker with `--tmpfs /scratch:rw,size=33554432 … --scratch /scratch
  --family p2.faults`: reference 3/3, ten consecutive runs.
- Same command **without** `--tmpfs`/`--scratch` on a full run: exit code 3,
  `results.json` present with the three faults cases marked error.
- `./bench.py check` passes (tmpfs preflight).
- `python3 -m unittest discover -s tests` (exit-3 admission test).

---

## 9. WP6 — Generated histories (`p1.histories`)

Two new private modules plus five case functions:

- `tasks/locker/tests/history_gen.py` — deterministic operation generator.
- `tasks/locker/tests/history_check.py` — executes ops against a server and
  compares every response with `state_model` (sequential and concurrent).
- `tasks/locker/tests/history_sweep.py` — author-only tool (§9.5).

### 9.1 Generator (`history_gen.py`)

```python
@dataclass(frozen=True)
class Op:
    kind: str          # see table
    args: dict         # JSON-serialisable; keys below
    group: int = 0     # concurrent case only: ops with the same group run together

def generate(case_id: str, seed: int) -> list[Op]:
    rng = random.Random(f"{seed}:{case_id}")
    ...
```

`seed` is `limits["generator_seed"]` if present (author-only), else
`limits["fixture_seed"]` (= manifest seed 20261106). The sequence must be a
pure function of `(case_id, seed)`; never call `random` elsewhere and never
depend on wall-clock time or dict iteration of unordered sets.

Fixed pools (define as module constants):

- Buckets: `"h1"`, `"h2"` (both created by the first two ops of every history).
- Keys: `["a", "b", "c", "dir/x", "dir/y", "dir/sub/z", "e", "k-1", "k-2",
  "k-10", "ü-key", "sp ace"]` (12; the last two exercise URL quoting via
  `h.object_path`, which already percent-encodes).
- Bodies: 16 entries, sizes drawn from `[0, 1, 7, 100, 1000, 4096, 8192]`,
  content `hashlib.sha256(f"body{i}".encode()).digest()` repeated/truncated to
  the size; all 16 are pairwise distinct (sizes may repeat, contents must not;
  make body 0 the empty body exactly once).
- Content types: `[None, "text/plain", "application/json",
  "image/png; charset=binary"]` (`None` = omit the header; expected type is
  then `application/octet-stream`).

Op kinds and `args`:

| kind | args | notes |
|---|---|---|
| `create_bucket` | `bucket` | |
| `delete_bucket` | `bucket` | expected 409 while objects/uploads exist, 204 when empty, 404 when absent |
| `put` | `bucket,key,body_index,content_type` | |
| `get` / `head` | `bucket,key,range,if_match,if_none_match` | `range` ∈ `None` or one of `"bytes=0-0"`, `"bytes=5-"`, `"bytes=-3"`, `"bytes=2-1"` (reversed → 416), `"bytes=100000-"` (beyond → 416), `"bytes=0-99999"` (clip). Conditionals: `None`, `"*"`, the model's current ETag of that key, the ETag of another pool body (mismatch) — never dates |
| `delete` | `bucket,key` | |
| `list` | `bucket,prefix,delimiter,max_keys` | the checker follows **all** pages immediately; no mutation in between. `prefix` ∈ `["", "dir/", "dir/sub/", "k-"]`, `delimiter` ∈ `["", "/"]`, `max_keys` ∈ `[1, 2, 3, 5, 1000]` |
| `list_bad` | `bucket,query` | one of `max-keys=0`, `max-keys=1001`, `max-keys=`, `max-keys=x`, `list-type=3`, `continuation-token=bogus` → 400 |
| `initiate` | `bucket,key,content_type,slot` | `slot` 0–2 names a generator-local session handle; the real UploadId is captured at run time |
| `part` | `slot,number,body_index` | `number` 1–3 |
| `list_parts` | `slot` | |
| `complete` | `slot,mode` | `mode` ∈ `"ok"` (all current parts), `"stale"` (one ETag from a previous version of a re-uploaded part — only generated after a re-upload), `"missing"` (adds a number never uploaded), `"dup"` (duplicate number), `"empty"` |
| `abort` | `slot` | |
| `dead_use` | `slot,what` | an op on a completed/aborted slot (`part`/`list_parts`/`complete`/`abort`) → 404 |
| `batch` | `bucket,ops` | 1–4 ops in the §6.2 tuple form; `IfMatch` ∈ `None`, `"*"`, current model ETag of the source (70 %), or a mismatching pool ETag (30 %) |
| `restart` | — | `restart` case only |

Per case:

| case | ops | mix |
|---|---|---|
| `objects` | 80 | put 30 %, get 25 %, head 10 %, delete 10 %, batch 15 %, delete_bucket 5 % (choose `h2` mostly), create_bucket 5 % (re-create after delete) |
| `multipart` | 50 | initiate 15 %, part 30 %, list_parts 10 %, complete 15 %, abort 8 %, dead_use 7 %, put/get on the same keys 15 % |
| `listing` | 40 | first 24: put 60 % / delete 20 % / batch 20 % over both buckets; last 16: list 75 %, list_bad 25 % |
| `restart` | 80 | as `objects`, plus `restart` at op indexes 26, 53 and 79 (exactly three); op indexes 25 and 52 are **forced** to be `list` ops on `h1` with `prefix=""`, `delimiter=""`, `max_keys ∈ {1, 2, 3}` and `args["split"] = True`: the checker fetches the first page before the restart and the remaining pages **after** it with the pre-restart token (token reuse across restart) |
| `concurrent` | 12 groups | each group: pick 1–2 keys from a 4-key subset; 2–3 ops among put / delete / get / head / get-with-range / batch (1–2 ops on those keys); between groups a quiescent probe (§9.3) |

Generation must respect the model: for `complete "ok"` the generator needs
the model's view of which parts exist, so `generate` runs its own
`state_model.State` alongside (using placeholder upload ids `f"slot{n}"`)
purely to pick arguments. Keep the generator's model usage read-only for
argument choice; the checker owns the authoritative model instance at run
time. For the `concurrent` case the generator applies each group's ops to its
private model in document order (just to pick plausible ETags/keys for the
next group); the real outcome may differ, which is exactly what the checker
in §9.3 explores.

`list_bad` queries are each backed by a sentence in `PROMPT.md` §Listing
(`max-keys` range/empty/non-decimal, `list-type` ≠ 2, malformed token → 400);
do not add others without a prompt sentence.

### 9.2 Sequential checker (`history_check.run_sequential(server, ops, limits)`)

For each op (index `i`):

1. Execute with helpers (`h.put_object`, `h.get_object(..., headers=...)`,
   `h.head_object`, `h.request` for DELETE/bucket ops, `h.list_page` loops,
   the multipart helpers from `test_runner.py`, `batch(...)` from §7.1,
   `server.restart()` for `restart`).
2. Compute `exp = state_model.<kind>(state, ...)`. For `initiate`, pass the
   server-issued `UploadId` (from the response XML) into the model and store it
   in `slots[slot]`.
3. Compare and fail with `f"op #{i} {op.kind} {op.args}: {field} expected
   {exp_value!r} got {got!r}"`:
   - always `status`;
   - `code` when `exp.code` (parse the error XML; HEAD has no body → skip the
     body check but still status);
   - `etag`, `content_type`, `content_length`, `content_range` when set;
   - `body` when not `None`;
   - `list`: items per page `[(kind, name)]` and for objects `(Size, ETag)`
     from the model, `KeyCount == len(page items)`, `IsTruncated`,
     `NextContinuationToken` present iff truncated, echoed `Prefix`/
     `Delimiter`/`MaxKeys`; the concatenation of all pages equals the model's
     full listing; pages before the last have exactly `max_keys` items;
   - `list_parts`: `(number, etag, size)` list.
4. Successful object GET/HEAD must also have `accept-ranges: bytes` and a
   parseable `last-modified` (value not compared).

### 9.3 Concurrent checker (`history_check.run_concurrent(server, ops, limits)`)

```
candidates = [State()]   # after the two create_bucket ops
for group in groups:
    start all ops of the group on separate threads behind a Barrier
    each thread records (t_start, t_end, Response) using time.monotonic_ns()
    join with timeout = limits["request_deadline_s"] + 10; alive thread → fail("hung")
    any status >= 500 → fail
    next_candidates = {}
    for state in candidates:
        for order in itertools.permutations(range(len(group))):
            if any(t_end[a] < t_start[b] and order.index(a) > order.index(b)): continue  # real-time precedence
            trial = state.clone(); ok = True
            for idx in order:
                exp = model_apply(trial, group[idx]); ok &= matches(exp, observed[idx])
                if not ok: break
            if ok: next_candidates[canonical(trial)] = trial
    check(next_candidates, f"group {g}: no serialization explains {observed summary}")
    # quiescent probe
    probes = [get(key) for key in touched_keys] + [list_page(bucket)]
    candidates = [s for s in next_candidates.values() if all(matches(model(s, p), observed_p) for p ...)]
    check(candidates, f"group {g}: no candidate state matches the quiescent reads")
```

`canonical(state)` = `repr(sorted(...))` of bucket→key→(etag, content_type)
plus upload ids/parts; the concurrent case uses no multipart, so objects
suffice. `matches` reuses the field comparison of §9.2 but **ignores**
`content_length` when `status` is 304/412 and never compares
`last-modified`. 2–3 ops give ≤ 6 permutations per candidate; keep
`len(candidates) ≤ 6` by construction (dedupe) — if it ever exceeds 32,
`fail("checker state explosion")` so the generator is fixed rather than the
suite running unbounded.

### 9.4 Case functions

```python
def p1_histories_objects(server, limits):    _history(server, limits, "objects")
def p1_histories_multipart(server, limits):  _history(server, limits, "multipart")
def p1_histories_listing(server, limits):    _history(server, limits, "listing")
def p1_histories_restart(server, limits):    _history(server, limits, "restart")
def p1_histories_concurrent(server, limits): run_concurrent(server, generate("concurrent", _gen_seed(limits)), limits)
```

`_gen_seed(limits)` returns `limits.get("generator_seed", limits["fixture_seed"])`;
`_history(server, limits, case_id)` is
`run_sequential(server, generate(case_id, _gen_seed(limits)), limits)`.
Register the five functions under `CASE_FUNCS["p1.histories"]` (replacing
the §4.3 placeholders).

### 9.5 Author-only sweep (`history_sweep.py`)

```bash
python3 tasks/locker/tests/history_sweep.py --binary /abs/path/locker --seeds 200 [--case objects]
```

For each seed `s` in `range(seeds)`: start a fresh `h.Server`, run the
requested cases with `limits["generator_seed"] = s`, collect the first failure
message per case. Print a table `seed | case | failure` and a summary; exit 1
if any failure. Run it against the **reference** (Linux build; the sweep has
no Linux-only dependency so macOS is acceptable here) and reach **zero
disagreements**. Every disagreement is adjudicated against `PROMPT.md`:

- model wrong → fix `state_model.py` + add a unit test;
- generator wandering into ungraded territory (e.g., mutating between pages,
  reusing an upload across restart) → fix the generator;
- reference wrong → fix the reference (and note it in `CALIBRATION.md`);
- prompt ambiguous → clarify the prompt (§4.1 style, verbatim) and then fix
  model/reference; record the clarification in `CALIBRATION.md`.

Never resolve a disagreement by making the model copy the reference's
behaviour without finding the prompt sentence that justifies it.

### 9.6 WP6 gate

- `p1.histories` 5/5 against the reference, 10 consecutive Docker runs
  (`--family p1.histories`).
- Sweep: 200 seeds × 5 cases, zero failures.
- Seed binary: `objects`, `listing`, `restart`, `concurrent` must **fail**
  (seed ignores range/conditional, no pagination, no batch); `multipart`
  fails (501). If any passes, the checker is not comparing something — fix.
- Timing: the five cases together must take < 20 s against the reference in
  Docker (they are small-body sequences; expect 3–6 s).

---

## 10. WP7 — Realistic mutant campaign (`negative_controls.py`)

Purpose: prove that every race/crash/fault case detects a *realistic* bug, not
only the conspicuous corruptions used today. All switches live in the private
reference and are read from `LOCKER_FAULT` (`store.go:101`, `server.go:41`).
They are never mentioned in the prompt, seed or image.

### 10.1 New fault switches (reference)

Add each as an `if s.fault == "…"` branch at the stated site. "Amplified"
means the natural fault window is widened with a `time.Sleep` so the detector
can hit it deterministically; the control runner reports natural and amplified
results separately (§10.2), and an *amplified-only* detection is acceptable
but must be labelled as such.

| fault | realistic bug | where (reference) | intended detector | natural / amplified |
|---|---|---|---|---|
| `per-op-commit` | batch commits after every op instead of once | `Store.Batch` (§5.1): after each op `s.saveLocked()` and install the working copy into `s.buckets` immediately; on a later precondition failure do **not** roll back | `p1.batch/preconditions` (partial effects after 412), `p1.batch/atomic-visibility`, `p2.batch/kill-during-swap` | natural |
| `precondition-at-parse` | IfMatch evaluated against the committed state before the lock, then applied in order without re-checking | `Store.Batch`: under a short `s.mu.RLock()` that is released again, evaluate every `ifMatchOK` against the committed `s.buckets[bucket]`; then take the write lock and apply the ops **without** re-checking (check-then-act with the lock dropped in between; `-race` stays clean) | `p1.batch/preconditions` (copy a→b then delete b IfMatch(etag a) must succeed; the mutant sees old b and returns 412), `p1.batch/concurrent-batches` round 2 | natural |
| `share-no-refcount` | delete/overwrite removes a blob still referenced by a copy | `DeleteObject` and `commitObject` (overwrite path): `os.Remove(blob path of the replaced/removed object)` unconditionally | `p1.batch/independence` (GET target → 500/404 after source delete), `p2.batch/blob-lifetime` | natural |
| `ack-before-publish` | 200 sent before the manifest rename | `serveBatch`: write the 200 header and flush (`http.Flusher`) **before** calling `store.Batch`; amplified: `time.Sleep(50*time.Millisecond)` between the flush and the call | `p2.batch/kill-after-ack` | natural + amplified |
| `leak-temp-on-error` | staged temp file not removed after a write error | `PutObject`/`UploadPart` error path (§5.4): skip `os.Remove(temp)` when the copy returned ENOSPC | `p2.faults/space-released` (second 8 MiB PUT fails) | natural |
| `ignore-write-error` | truncated blob published as success | `PutObject`: ignore the `io.Copy`/`Sync`/`Close` error and continue to `commitObject` with the bytes actually written | `p2.faults/put-preserves-old` (200 for the 48 MiB PUT, or GET returns a short body) | natural |
| `phantom-part` | part registered before its body is fully written | `UploadPart`: insert the part record (with the eventual ETag unknown → use the running hash so far) into the session **before** `io.Copy` finishes; on error leave it | `p2.faults/multipart-staging` (part 2 listed after the failed upload) | natural |
| `stale-snapshot-clone` | commit clones a bucket map captured before acquiring the lock | `commitObject`: take `old := cloneBucket(...)` under a short `RLock` that is released **before** `s.mu.Lock()`, then install `old` + the new key (a concurrent commit between the two locks is lost) | `p1.histories/concurrent` (lost put/delete visible in the quiescent probe), `p1.concurrency/no-lost-writes` | natural |
| `metadata-before-blob` | metadata/manifest written before the blob rename | `commitObject`: `saveLocked()` first, then `os.Rename(temp, blob)`; amplified: `time.Sleep(20*time.Millisecond)` between them | `p1.concurrency/coherent` (GET 200 with missing body → 500/short), `p1.histories/concurrent` | natural + amplified |
| existing: `split-publication`, `lost-writes`, `global-lock`, `validate-after-replace` | unchanged | unchanged | unchanged | natural |

Rules for switch code: no behaviour change when `fault == ""`; each switch is
≤ 15 lines; `go test ./...` keeps passing with `LOCKER_FAULT` unset; a Go
unit test asserts `Open` with `LOCKER_FAULT=unknown-name` fails (already the
`default:` branch at `store.go:114` — keep it, and add the new names to the
accepted list there).

### 10.2 Control runner changes

Replace the 6-tuple `CONTROLS` entries with dicts so an entry can name several
detectors and an amplified variant:

```python
CONTROLS = [
    {
        "label": "split metadata from body",
        "fault": "split-publication",
        "cases": [("p1.concurrency", "coherent", runner.p1_concurrency_coherent)],
        "overrides": {"progress_deadline_s": 5, "request_deadline_s": 10},
        "amplified": None,          # or the fault name of the widened variant, e.g. "ack-before-publish-amp"
        "needs_scratch": False,
    },
    ...
]
```

Implement the amplified variant as a second fault name (`<fault>-amp`) in the
reference rather than a second env var, so one `LOCKER_FAULT` value selects it.

Runner semantics per entry (keep the existing baseline-first structure):

1. Baseline: every listed case passes on the unfaulted binary; otherwise
   print `BASELINE` and count as failure (unchanged).
2. Natural: start the server with `LOCKER_FAULT=<fault>`; run each listed
   case; **detected** if at least one raises `h.CaseFailure`; print the
   detecting case ids. If none detects and `amplified` is set, run step 3;
   if none detects and no amplified variant exists → `MISSED` (failure).
3. Amplified: same with `LOCKER_FAULT=<fault>-amp`; detected → print
   `detected* (amplified only)`; undetected → `MISSED`.
4. Any non-`CaseFailure` exception → `ERROR` (failure), unchanged.
5. Entries with `needs_scratch: True` (the three ENOSPC mutants) require
   `--scratch DIR` (same validation as `run.py`); without it they are printed
   as `SKIPPED (no scratch)` and the script exits **3** at the end even if
   everything else passed, so a non-Linux author cannot mistake a partial
   campaign for a complete one. Build their server with the §8.3
   `_scratch_server` helper.
6. Final summary line: `controls: N total, D detected natural, A detected
   amplified-only, M missed, E errors, S skipped`; exit 0 only when
   `M == E == S == 0`.

Add `--control NAME` to run a single entry (author convenience) and
`--repeat K` (default 1) that repeats the fault run K times and reports the
detection *rate* (`detected 10/10`) — the release gate uses `--repeat 10`
and requires 10/10 for natural detections and ≥ 9/10 for amplified ones.

### 10.3 `NEGATIVE_CONTROLS.md`

Rewrite to a table with columns `fault | realistic bug | detecting cases |
natural rate | amplified rate | notes`, filled from an actual
`--repeat 10` run. Keep the opening paragraph that explains the information
boundary (controls are private; the prompt never mentions them). Record the
command used and the reference commit hash.

### 10.4 WP7 gate

- `python3 tasks/locker/tests/negative_controls.py --binary <linux ref bin>
  --scratch /scratch --repeat 10` inside the Docker image with the tmpfs:
  exit 0; every fault detected by at least one of its *intended* cases (not
  merely by some other case — print the case ids and compare with the table).
- Natural detection 10/10 for all naturally detectable faults; amplified
  ≥ 9/10 for `ack-before-publish`, `metadata-before-blob`.
- `go test ./...` and `go vet ./...` pass with and without `LOCKER_FAULT`
  switches present in the source.

---

## 11. WP8 — Release gates (run in this order; all must pass)

All Docker commands use the pinned image (`./bench.py build` first). `REF` is
the Linux reference binary built inside the image:

```bash
docker run --rm --network none -v "$PWD/tasks/locker/reference:/src:ro" -v /tmp/locker-ref-bin:/out \
  -w /src -e GOFLAGS=-mod=mod -e GOCACHE=/tmp/gocache -e HOME=/tmp -e TMPDIR=/tmp \
  opencode-bench-locker:latest bash -lc 'go vet ./... && go test ./... && go build -trimpath -o /out/locker ./cmd/locker'
REF=/tmp/locker-ref-bin/locker
```

1. **Static**: `python3 -m py_compile bench.py`;
   `python3 -m unittest discover -s tests`;
   `python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'`
   (includes `test_state_model.py`); `./bench.py check` (16 families,
   69 cases, new limits, tmpfs preflight).
2. **Full reference run, five times, timed** (Linux, network off, tmpfs):

   ```bash
   for i in 1 2 3 4 5; do
     /usr/bin/time -p docker run --rm --network none --tmpfs /scratch:rw,size=33554432 \
       -v /tmp/locker-ref-bin:/bin-ref:ro -v "$PWD/tasks/locker/tests:/tests:ro" -v /tmp/locker-out:/out \
       -e HOME=/tmp -e TMPDIR=/tmp opencode-bench-locker:latest \
       python3 -B /tests/run.py --binary /bin-ref/locker --manifest /tests/manifest.json \
       --seed 20261106 --scratch /scratch --output /out/results-$i.json
   done
   ```

   Each run: `test_ok: true`, 69/69, `diagnostic` absent/false,
   `suite_version: locker-v4`. **Median wall time ≤ 90 s** (v3 ≈ 9.4 s;
   expected 35–45 s). If above 90 s, cut the generated-history op counts
   or the batch-visibility duration — never the crash trials.
3. **Flake gate**: 30 consecutive diagnostic runs of
   `--family p1.batch`, `p2.batch`, `p2.faults`, `p1.histories`,
   `p1.concurrency`, `p2.crash` against `REF` — zero failures. Any failure
   is an evaluator bug: fix the oracle/timing and restart the count from 0.
4. **Mutant campaign**: §10.4 (`--repeat 10`, exit 0, intended detectors).
5. **Sweep**: §9.5, 200 seeds × 5 cases, zero disagreements.
6. **Seed baseline**: build the seed in the image, run the full suite with
   tmpfs. Required: 8/8 P0, `build_ok`, **ineligible**; expected: every
   `p1.batch`/`p2.batch` case fails (405), `p2.faults/put-preserves-old`
   fails, `p1.histories` 0/5 (§9.6 requires every history case to fail on
   the seed).
   Record the per-family credits in `CALIBRATION.md` — do not assume zero.
7. **Harness end-to-end**: `./bench.py rescore --run <existing run id>` on a
   v3-era workdir completes (its result becomes a v4 score; expected low on
   the new families) and `./bench.py run --model <any configured model>
   --dry-run` prints the `--tmpfs` flag in the test command.
8. **Independent review** (a second agent/person who did not write the
   reference): read `PROMPT.md` §Batch, §Operational failures and
   `state_model.py` side by side; list every sentence in the prompt without
   a model rule and every model rule without a prompt sentence. Both lists
   must be empty before freezing.
9. **Documentation**: `CALIBRATION.md` gains `## locker-v4` with: reference
   commit, image digest, the five timings, flake-gate counts, mutant table
   (natural/amplified), sweep result, seed baseline credits, the prompt
   clarifications made during the sweep. `HARD-TASK-PLAN.md` Implementation
   status → v4 numbers; this file's status line → `implemented, frozen`.
10. **Handoff**: tell the user the suite is frozen and that the only remaining
    measurement is their rerun of the saturating local model
    (`./bench.py run --model <local model>`), with the request to report
    the per-family credits, `wall_s` and whether the run hit the 60-minute
    cap. If a correct implementation still saturates v4, report the ceiling
    rather than add more families.

---

## 12. Pitfalls (read before starting each package)

- **macOS vs Linux**: `sed -i` needs `''` on BSD sed — use Python for
  in-place edits. `VmHWM` and the tmpfs behaviour exist only in the Linux
  image; the sweep and unit tests are fine on macOS.
- **`docker run` and stdin**: heredocs are not forwarded without `-i`; mount
  scripts with `-v` instead.
- **Nested code fences** in markdown: wrap an outer block that contains
  ``` with four backticks.
- **Information boundary**: never `-v` the `reference/` or `tests/` directory
  into an *agent* workdir, the seed, or the image. `bench.py run_tests` mounts
  `tests/` only after the agent exits — keep it that way.
- **`cmd_check` limit rule**: every value in the manifest `limits` block must
  appear verbatim in `PROMPT.md` (digits as written). Adding
  `fault_fs_bytes` etc. without the §4.1(d) table rows fails `check`.
- **Hardcoded counts** live in `bench.py EXPECTED`, `cmd_check`,
  `test_oracles.py:93`, `tests/test_bench.py` fixtures, `README.md`,
  `AGENTS.md`, `HARD-TASK-PLAN.md`, `PROMPT.md` scoring text. Grep for `51`,
  `30`, `13`, `12 families`, `nine` after editing.
- **`encoding/xml` and unknown elements**: `xml:",any"` collects unknown
  children; the parser must reject them explicitly (400), and must reject
  `<Batch/>` with zero ops.
- **`io.Copy` error attribution**: the reference currently maps any copy
  error to `ErrIncomplete` (400). Without the `errWriter` split (§5.4) ENOSPC
  becomes 400 and `p2.faults` fails on the reference.
- **ETag quoting**: ETags on the wire are quoted (`"md5"`); `IfMatch`
  comparisons normalise with `normalizeETag`; the model must compare
  unquoted hex.
- **Last-Modified** has second precision; never compare it for equality
  across operations; the model stores the observed value only for `≥`
  checks in `p1.batch/copy-rename-delete`.
- **Threads**: always `join(timeout=…)` and fail on `is_alive()`; a hung
  thread otherwise hangs the whole suite past the deadlines.
- **`h.Server.close()`** rmtree's the root — never point `root` at a shared
  directory; for scratch servers create a fresh `mkdtemp` under the tmpfs.
- **Raw sockets**: writing 48 MiB into a server that answered early raises
  `BrokenPipeError`/`ConnectionResetError` on macOS *and* Linux, sometimes
  as plain `OSError(EPIPE)`; catch `OSError`.
- **tmpfs `size=`** is in bytes; `k`/`m` suffixes also work but keep the
  byte count so the manifest, prompt and `--tmpfs` string are identical.
- **`run_family` KeyError**: an unknown family id crashes the runner; the
  `_not_implemented` placeholders from §4.3 exist so WP1 can land before
  WP4–WP6.
- **Diagnostic results**: `--family` and `--generator-seed` set
  `diagnostic: true`; such results are never ingested by `bench.py`. Do not
  "fix" that.
- **Do not calibrate the seed**: if the seed accidentally passes a new case,
  the case is too weak — strengthen the case, never weaken the seed.

## 13. Reporting format (per work package)

When a package is finished, append to `CALIBRATION.md` (under `## locker-v4`,
subsection per package) and post to the user:

```
WP<N> <name> — done
commands run: <exact commands>
results: <counts / timings / detection rates>
deviations from HARD-TASK-V4-PLAN.md: <none | list with reasons>
prompt clarifications: <none | quoted sentences added/changed>
open questions: <none | list>
```

Any deviation from a verbatim text in §4.1 or a status/semantics in §6–§9
must be listed here and mirrored back into this document so the spec and the
implementation never diverge.
