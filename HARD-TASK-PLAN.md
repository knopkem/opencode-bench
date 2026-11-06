# Hard-task benchmark plan — Locker

Status: design locked, not implemented.
Branch: `hard_task` (SpriteForge and its leaderboard stay on `main`).

## 1. Constraints

These are the constraints that drive every decision below.

### Real-world, not a common benchmark

- The task must be a real product/system that an engineer could plausibly be
  paid to build or fix: an object store, a database, a service, a CLI tool.
- Do **not** use math puzzles, LeetCode-style algorithm problems, HumanEval
  clones, SWE-bench-style issue fixing, Advent of Code, or other tasks that
  appear in common public test suites. Frontier cloud models are benchmaxxed on
  those; they mostly measure memorization and pattern matching, not engineering.
- Do **not** make the task harder by adding more UI surface or more checklist
  features. Hardness must come from production behavior: crash consistency,
  concurrency, protocol edge cases, security, memory and integration.
- The task should be recognizable as real work but not a clone of a famous
  project whose implementation the model may have memorized. Locker is an
  S3-like local object store, not a reimplementation of MinIO.

### As hard as possible within the budget

- The goal is a benchmark where frontier models do **not** all score 100%.
  Target a spread of roughly 70–90% for the strongest models on the first
  calibration, with clear separation from weaker models.
- Hardness comes from the last 20%: a basic implementation should be easy, but
  a production-grade one should require correct crash handling, atomic
  metadata, multipart semantics, concurrency and security.
- Use partial credit and P2 tests so a model can be useful without being
  perfect; do not make the whole suite all-or-nothing.

### Minimum cost and time

- Target **12–15 minutes** of agent wall time, **18-minute hard cap**, **5-minute
  idle timeout**. The model is told to aim for 12 minutes and prioritise P0,
  then P1, then P2.
- Target **under ~$2 per frontier-model run** and **2–3 models × 2 reps** for
  calibration, not a large matrix.
- No browser, no Playwright, no large dependency tree, no 4-hour runs. The task
  is a native Go service/CLI; grading must finish in under a minute.
- Use a brownfield seed that already implements ~85% of the product. The model
  should spend its budget on the hard 20%, not boilerplate.
- Keep fixture sizes small: most tests use kilobytes to a few megabytes; one
  P2 test uses a 128 MB object to check streaming.

### Objective, fair and reproducible

- Hidden tests are black-box and behavioural. They invoke the built binary over
  HTTP and the CLI; they do not import internal packages or inspect the
  repository format.
- The reference implementation is never mounted into the agent workdir. Hidden
  tests are mounted only after the agent exits.
- The seed builds and passes P0; hidden P1/P2 tests are the real
  discriminator.
- No new dependencies, no network for build or tests, Go standard library only.
  The agent gets the same seed, prompt, toolchain and time budget for every run.
- Pin the Go image/tag, record the harness commit, suite version and test
  counts. A suite change invalidates comparisons with earlier results.
- Do not special-case tests. Hidden tests use random keys, bodies and sizes.

### Mixed surfaces, one product

- The task should exercise **database/storage, CLI and HTTP** behaviour in one
  codebase. That gives broader coverage without the cost of three separate
  tasks, and it exposes integration bugs (CLI → HTTP → storage).
- Three disconnected mini-tasks are explicitly rejected for the first version:
  three seeds, three references, three hidden suites and a much longer run.

### Separate hard-task leaderboard

- This suite gets its own results file and leaderboard view. It should not
  pollute the existing local-model SpriteForge leaderboard.
- For the spike, the code lives on `hard_task`; the fork-vs-multi-suite decision
  waits until the task is calibrated.
- The cloud leaderboard ranks quality first, then cost/tokens, then wall time;
  it does not reuse the current quality/wall/compactions weighting blindly.

### Fairness before publishing

- The agent container needs outbound network for cloud model calls. A frontier
  model could also use `curl`/web tools to fetch solutions. Before publishing a
  public frontier leaderboard, add an egress allowlist/proxy for the model
  provider or otherwise restrict non-provider network access.
- Record model ref, variant, reasoning effort, provider, cost/tokens and wall
  time so results are attributable.

## 2. Why this task and these tests

### Why not another UI app

SpriteForge is a greenfield, fully specified UI build. Frontier models have seen
thousands of these; the hidden suite checks feature presence more than
adversarial behavior. Adding more UI features makes the run longer, not harder.
Locker is a systems product: the happy path is easy, but crash recovery,
concurrency, multipart semantics and security are where strong models still
fail.

### Why a brownfield seed

The seed already builds, serves HTTP, has a CLI and basic storage. That removes
boilerplate and keeps the run cheap. The model must read and extend an existing
codebase, which is closer to real engineering than a blank page. The seed's
storage layer is deliberately naive (in-place metadata, direct writes, no
locking), so the hard part is a redesign, not just adding handlers.

### Why P0 / P1 / P2

- **P0** is the seed baseline: basic CLI/HTTP/DB round-trip and persistence. It
  catches regressions and makes the score interpretable. It is intentionally
  low-signal because the seed already passes it.
- **P1** is the core production layer: range/conditional requests, multipart,
  listing/pagination, and concurrent access. These are the behaviours most
  implementations get partially right; they create the main quality spread.
- **P2** is the adversarial layer: kill during overwrite, kill during multipart
  complete, path traversal, and large-object memory. Only a production-grade
  implementation passes these; they prevent the suite from saturating at 100%.

### Why 12 tests

Twelve tests are enough for three tiers and a mixed DB/CLI/HTTP matrix while
keeping the suite under a minute and the run cheap. More tests would add
maintenance and grading time; fewer would lose resolution between a basic and a
strong implementation.

### Why these weights

- P0 = 3, P1 = 2, P2 = 1.
- A do-nothing seed scores `9/23 = 0.39` and is ineligible.
- P0 + all P1 scores `19/23 = 0.826` and is eligible.
- P2 is the differentiator without making the whole benchmark all-or-nothing.
- Eligibility requires a successful build, all P0 tests, and quality ≥ 0.80.

### Why Go

- `net/http`, `crypto/md5`, `encoding/xml`, `os`, `sync` and `io` are all in
  the standard library. No vendored crates, no network at build time.
- Builds are fast, so the model can iterate within a 12–15 minute budget.
- Rust would add incidental difficulty (borrow checker, lifetimes, no std HTTP
  server, vendored dependencies, slower builds) and make the benchmark noisier
  and more expensive. Use Rust later for a task where the language itself is the
  point.

## 3. Product: Locker

A local, single-node, S3-compatible object store. One Go binary, no external
dependencies, no auth, loopback only.

```bash
locker server --data ./data --addr 127.0.0.1:9000
locker mb   s3://bucket
locker cp   file.bin s3://bucket/key
locker ls   s3://bucket/prefix
locker rm   s3://bucket/key
```

### HTTP surface

- `PUT/GET/HEAD/DELETE /bucket/key`
- bucket create/list/delete
- `GET /bucket?list-type=2&prefix=&delimiter=&max-keys=&continuation-token=`
- multipart: initiate, upload part, list parts, complete, abort
- range and conditional GET/HEAD
- XML responses for list and multipart; raw bytes for object data
- no XML namespaces, no auth
- `GET /health` returns 200 for test readiness

### Contract details

| Area | Decision |
|---|---|
| ETag | Quoted MD5 of the full object body |
| Multipart ETag | MD5 of the final assembled body, not S3's `-N` form |
| Multipart part size | Any size; parts validated by number and ETag; completed in numeric order |
| Range | Single range only: `start-end`, `start-`, `-suffix`; invalid/unsatisfiable → 416 with `Content-Range: bytes */size` |
| Conditional | GET/HEAD only: `If-Match`, `If-None-Match`, `If-Modified-Since`, `If-Unmodified-Since`; ETag validators take precedence over date validators |
| Listing | Bytewise sorted keys; `prefix`, `delimiter`, `max-keys` (default 1000), opaque continuation token; `CommonPrefixes` for grouped keys |
| Buckets | Non-empty names, no slash; deleting a non-empty bucket → 409 |
| Object keys | UTF-8, slashes allowed; keys with `..` path segments or encoded traversal must be rejected or stored safely without escaping the data directory |
| Persistence | Internal format is free; no compatibility with seed v0 data; metadata and object publication must be atomic |
| Concurrency | Concurrent PUTs to one key are last-write-wins; readers never see a partial/torn body |
| Crash | Kill -9 during PUT or multipart complete leaves the old object or the new full object; server restarts cleanly |
| CLI | Goes through HTTP; `--endpoint` or `LOCKER_ENDPOINT`, default `http://127.0.0.1:9000` |

### Out of scope

Auth, TLS, replication, versioning, lifecycle rules, S3 signatures, multiple
nodes, background compaction, dedup/GC, web UI, SDKs, mobile.

## 4. Seed v0 (85% of the product)

The seed is a working Go v0 that builds and passes P0.

It provides:

- HTTP server with basic `PUT`, `GET`, `HEAD`, `DELETE`, bucket create/list/
  delete and a simple XML list response;
- CLI `mb`, `rb`, `cp`, `ls`, `rm` going through the HTTP API;
- file storage plus a JSON metadata file;
- MD5 ETags, `Content-Type`, `Last-Modified`;
- XML structs/helpers for list and multipart responses;
- `scripts/smoke.py` with a mix of passing and failing checks;
- `AGENTS.md` with stack, commands and rules.

It is deliberately naive:

- metadata is rewritten in place, not atomically;
- object writes go directly to the final path;
- no per-key locking;
- `Range` and conditional headers are ignored;
- multipart endpoints return `501`;
- listing has no pagination or delimiter handling;
- large uploads are read into memory.

The prompt tells the model: “The seed passes P0. Finish the production-grade
20%; you may restructure the storage layer, but keep the CLI/HTTP contract.”

## 5. Hidden test matrix (12 tests)

P0: 3, P1: 5, P2: 4.

| # | Tier | Category | Test |
|---:|---|---|---|
| 1 | P0 | CLI | bucket/object round-trip through `mb/cp/ls/rm` |
| 2 | P0 | HTTP | basic PUT/GET/HEAD/DELETE, status, body, ETag, `Content-Length` |
| 3 | P0 | DB | buckets, Unicode keys and metadata survive restart |
| 4 | P1 | HTTP | range GET/HEAD: closed, open, suffix, invalid 416 |
| 5 | P1 | HTTP | conditional GET/HEAD: ETag and `Last-Modified`, 304/412 |
| 6 | P1 | HTTP | multipart initiate, upload, list parts, complete, abort, validation |
| 7 | P1 | HTTP | ListObjectsV2: prefix, delimiter, `max-keys`, continuation |
| 8 | P1 | DB | concurrent PUT/GET: no torn bodies, last-write-wins |
| 9 | P2 | DB | kill during overwrite: old or new full object, never partial |
| 10 | P2 | HTTP | kill during multipart complete: no partial object, repo usable |
| 11 | P2 | SEC | `../escape`, `%2e%2e/escape`, `a/../../escape` cannot write outside the data dir |
| 12 | P2 | PERF | 128 MB PUT/GET/range within memory budget and time cap |

Exact result titles must start with the tier prefix:

```text
[P0][cli]  cli bucket and object round-trip
[P0][http] http object CRUD and basic headers
[P0][db]   buckets unicode keys and metadata survive restart

[P1][http] range requests support closed open and suffix ranges
[P1][http] conditional requests honor etag and last-modified
[P1][http] multipart upload complete abort and validation
[P1][http] list objects supports prefix delimiter and continuation
[P1][db]   concurrent put and get never expose a torn object

[P2][db]   crash during overwrite leaves old or new object only
[P2][http] crash during multipart complete leaves no partial object
[P2][sec]  path traversal keys cannot escape the data directory
[P2][perf] large object put get and range stay within memory cap
```

### Test details

- **P0 CLI:** start server, `mb`, `cp` upload, `ls`, `cp` download, byte compare,
  `rm`, `ls` empty.
- **P0 HTTP:** small object; status, body, `ETag`, `Content-Length`,
  `Content-Type`, `HEAD` no body, `DELETE`, 404.
- **P0 DB:** PUT Unicode and nested keys, restart server, GET and list; buckets
  persist.
- **P1 range:** known 1 MB body; `bytes=100-199`, `bytes=100-`, `bytes=-100`,
  `bytes=999999-` → 416; `Content-Range`, `Accept-Ranges`.
- **P1 conditional:** ETag/Last-Modified; `If-None-Match` → 304,
  `If-Match` mismatch → 412, `If-Modified-Since` → 304,
  `If-Unmodified-Since` old → 412.
- **P1 multipart:** initiate, upload three parts, list parts, complete, GET
  full body; abort another upload → 404; missing part → 400; ETag mismatch →
  400.
- **P1 listing:** keys `a/1`, `a/2`, `b/1`, `c`; prefix/delimiter/max-keys/
  continuation; sorted; `CommonPrefixes`.
- **P1 concurrency:** four writers alternate 1 MB bodies; four readers GET; each
  response is one full body, never mixed; final is one of them; no 5xx.
- **P2 crash overwrite:** old 1 MB object; start a slow 128 MB overwrite; kill
  the server mid-write; restart; GET is old or new full body, never partial;
  a subsequent PUT works.
- **P2 multipart crash:** multipart with parts; send complete; kill during;
  restart; GET is 404 or full body; no partial; repo usable.
- **P2 security:** keys with raw and percent-encoded traversal; server may
  reject or store safely; no file outside the data directory.
- **P2 perf:** PUT and GET a 128 MB object, plus a range read; poll
  `/proc/<pid>/status` for peak RSS; cap below the full object size; time cap
  generous but finite.

### Test runner

- Python 3 standard library only.
- Starts `locker server` on a free loopback port, waits for `/health`.
- Uses `http.client`/`urllib` and `xml.etree.ElementTree`.
- Uses `subprocess` for CLI.
- Kills/restarts the server for crash tests.
- Writes `results.json`:

  ```json
  {"tests": [{"title": "[P0][cli] ...", "ok": true, "duration": 0.1, "error": null}]}
  ```

- Exits nonzero if any test fails; cleans up processes and temp dirs.

## 6. Reference implementation

`tasks/locker/reference/` contains the complete Go implementation. It is never
mounted into the agent workdir. It must use:

- streaming copies, not `io.ReadAll` for object bodies;
- temp file + `fsync` + rename for atomic publication;
- per-key or global locking where needed for concurrent access;
- atomic metadata writes;
- multipart part storage and cleanup;
- listing/pagination and XML serialisation;
- safe key-to-path mapping.

Validation:

- Reference passes 12/12 three consecutive times.
- Seed passes P0 3/3 and fails P1/P2.
- The hidden suite is deterministic across runs.

## 7. Harness integration

For the spike, change the task in place on `hard_task`; keep SpriteForge on
`main`.

- `TASK_DIR = ROOT / "tasks" / "locker"`.
- `PROMPT_PATH = TASK_DIR / "PROMPT.md"`.
- `EXPECTED = {"P0": 3, "P1": 5, "P2": 4}`.
- `WEIGHT = {"P0": 3, "P1": 2, "P2": 1}`.
- Separate `results-locker.csv`, `report-locker.md`, `locker-leaderboard.html`.
- Run IDs include `locker` to avoid collisions with SpriteForge runs.
- `build_app()` runs `go build -trimpath -o /work/bin/locker ./cmd/locker`.
- `run_tests()` mounts the workdir read-only, mounts hidden tests, runs Python
  with `--network none`, and parses `results.json`.
- `run_one()` checks for `work/bin/locker` instead of `dist/index.html`.
- `start_agent()` counts `go build`, `go test`, `scripts/smoke.py` as
  self-tests.
- `cmd_check()` validates the 3/5/4 counts, seed files, hidden test titles and
  image.
- `tests/test_bench.py` gains Locker scoring fixtures; existing SpriteForge
  tests remain on `main`.
- No submission/CI work until the task passes calibration.

### Docker

Lean Go image, not the Playwright image:

- Ubuntu 24.04 base;
- `ca-certificates`, `curl`, `git`, `ripgrep`, `procps`, `unzip`, `python3`,
  `golang-go`;
- OpenCode 2 installed via the v2 installer;
- `/work` workdir;
- no Node, Playwright or browser.

Build and test containers use the same image with `--network none`.

## 8. File layout

```text
tasks/locker/
  PROMPT.md
  seed/
    go.mod
    cmd/locker/main.go
    internal/server/...
    internal/store/...
    internal/cli/...
    scripts/smoke.py
    AGENTS.md
  reference/
    ...complete working implementation...
  tests/
    run.py
    ...hidden black-box tests...

docker/locker.Dockerfile
```

## 9. Scoring and leaderboard

- Quality = weighted pass / weighted total with P0=3, P1=2, P2=1.
- Eligible = build succeeded, all P0 passed, quality ≥ 0.80.
- Report tier counts, failed test titles, outcome, wall, cost/tokens, steps and
  compactions.
- For cloud runs: quality is the primary rank; cost/tokens is the second axis;
  wall is a tie-breaker. Show a Pareto view or two tables (Capability and
  Value) rather than the current local-model composite.
- Two reps per model; aggregate by median and show the range.
- Timeout/incomplete runs are shown but marked ineligible when appropriate.

## 10. Calibration plan

1. Build reference; run the hidden suite three times → 12/12.
2. Build seed; run hidden suite → P0 3/3, P1/P2 fail.
3. Run the harness against a local model once to debug image/test plumbing
   (free).
4. Run 2–3 frontier models × 2 reps with:

   ```bash
   ./bench.py run --model provider/model \
     --timeout 1080 --idle-timeout 300 --reps 2
   ```

5. Inspect `score.json`, `test.log`, `results.json`, `COMPLETION.md` and
   `agent.log`.
6. Adjust:
   - all models near 100% → add/strengthen P2 (multipart abort crash,
     concurrent multipart complete, dedup/GC);
   - all models below ~50% → put more implementation in the seed or relax P1;
   - flaky tests → fix the tests, not the model;
   - cost/time above target → reduce fixtures or tests.

### Calibration targets

- Reference: 12/12 three times.
- Seed: P0 only.
- Top models: roughly 70–90%, not all 100%.
- Spread: at least ~20 quality points between the strongest and a mid model.
- Median wall: 12–15 minutes; p95 under 18.
- Hidden suite: under 60 seconds.
- Cost: under roughly $2 per run.
- No flaky crash/concurrency tests.

## 11. Implementation order

| Step | Work | Estimate |
|---:|---|---|
| 0 | Finalize `PROMPT.md`, contract details, seed shape, 12-test matrix | 0.5–1 day |
| 1 | Build seed v0 + public smoke; seed passes P0 | 1–2 days |
| 2 | Build complete reference; validate 12/12 ×3 | 2–3 days |
| 3 | Write hidden tests; validate against reference and seed | 1–2 days |
| 4 | Harness spike: Go image, build/test runner, separate results | 1 day |
| 5 | Calibration with local + frontier models; adjust | 1–2 days |
| 6 | Only after calibration: leaderboard/submissions/docs and fork-vs-suite decision | 1–2 days |

The MVP is roughly one focused week; a solid version with calibration and
leaderboard is roughly two weeks.

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| HTTP/crash tests are flaky | Free-port allocation with retries, `/health` readiness, invariant-based crash checks, no timing assumptions beyond generous caps |
| Seed is too helpful or too thin | 85% target; calibrate before expanding; move one feature between seed and hidden tests if needed |
| Task saturates at 100% | Add/strengthen P2; make P1 multipart/listing edge cases harder |
| Task is too hard, everyone fails P1 | Put range/conditional helpers or more of multipart in the seed; relax P1 assertions |
| Cost exceeds budget | 18-minute cap, 5-minute idle, two reps, smaller fixtures, fewer tests |
| Agent uses the internet to find solutions | Egress allowlist/proxy for model provider before publishing; log non-provider network use |
| Go image/version drift | Pin image tag/digest and Go version; build/test with `--network none` |
| Hidden tests leak into the seed | Reference and tests stay outside the workdir; tests mounted only after the agent exits |
| Results are not comparable after changes | Record harness commit, suite version, image tag and test counts; version the suite |

## 13. Alternatives considered

- **Cask — deduplicating backup CLI.** Strong storage/compression task and very
  deterministic, but only one surface. Locker subsumes the interesting storage
  parts while adding HTTP/CLI.
- **ForgeDB — MVCC key/value store.** Excellent database task, but crash and
  concurrency grading is harder to make deterministic and it is more work to
  build.
- **EdgeCache — HTTP caching proxy.** Real-world and hard, but a lot of HTTP
  boilerplate before the interesting cache semantics; Locker's HTTP edge cases
  are more varied.
- **Porting task.** Good code-comprehension test, but the difficulty is mostly
  volume and wall time; better as a second suite with a longer budget.
- **Physics/optimization.** Hard to grade fairly (tolerances, determinism,
  hardware-dependent time budgets); later candidate.
- **Three separate DB/CLI/HTTP tasks.** Broader coverage, but three references,
  three hidden suites and a much longer run; rejected for the first version.

## 14. Non-goals

- Replacing SpriteForge or changing its leaderboard.
- A full S3 implementation, auth, TLS, replication or versioning.
- Web UI, SDKs, mobile or browser tests.
- Dedup/GC in the first version (possible P2 stretch if calibration shows
  saturation).
- Multi-suite refactoring before the task is validated.
- Community submissions/CI before the task is calibrated.

## 15. Definition of done

- `tasks/locker/PROMPT.md` is final and consistent with the hidden tests.
- Seed builds, runs, passes P0 and fails P1/P2.
- Reference passes 12/12 three consecutive times.
- Hidden suite is deterministic and under 60 seconds.
- Harness on `hard_task` builds/tests Locker and writes separate results.
- Calibration with 2–3 frontier models × 2 reps shows a useful spread, median
  12–15 minutes and acceptable cost.
- A short calibration report records scores, failures, wall and cost.
- Fork-vs-suite and public leaderboard decisions are made after calibration.
