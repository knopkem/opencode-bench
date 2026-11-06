# Hard-task benchmark plan — Locker

Status: packages A–E implemented on `hard_task`; model calibration (package F)
pending.
Branch: `hard_task` (SpriteForge and its leaderboard stay on `main`).

## Implementation status

- v4 hardening (atomic same-bucket batch, full-disk fault family, generated
  histories against an independent state model, realistic mutant campaign) is
  implemented and frozen; `HARD-TASK-V4-PLAN.md` is the build record.
- Frozen suite: `locker-v4`, 16 families, 69 subcases (P0 8 / P1 42 / P2 19),
  fixture seed `20261106`.
- Frozen limits: 134,217,728-byte stream fixture; 100,663,296-byte server
  `VmHWM` cap; 180 s large-workload deadline; 10 s progress deadline; 60 s
  request deadline; 15 s startup deadline; 6 bounded crash trials; 32 batch
  operations; 33,554,432-byte fault filesystem; 50,331,648-byte oversized
  write.
- Reference: 69/69 subcases on macOS and in the pinned Linux image. Five timed
  Linux runs had a 19.6 s median wall time; 210 consecutive diagnostic
  race/crash/fault runs were clean; all 13 realistic mutants were detected
  10/10 (both amplified variants 10/10); a 200-seed history sweep passed
  1000/1000.
- Seed baseline: all 8 P0 subcases pass and the build succeeds. Credit is P1
  range 0/4, conditional 0/4, multipart 0/11, listing 0/6, concurrency 2/5;
  P2 overwrite 4/4, multipart 0/3, isolation 0/3, perf 1/3, batch 0/7,
  histories 0/5, faults 0/3. Total 15/69 and quality 0.1333; always ineligible.
  The seed is deliberately naive, so the overwrite passes are measured rather
  than assumed away.
- Hardening: a first local model run saturated v1 (39/39). Suite v2 added ten
  multipart/concurrency/parameter subcases; the same binary then scored 40/49
  (quality ≈0.857). A fresh v2 model run adapted and scored 48/49. Suite v3
  added two bounded multipart list/complete/abort races (51 cases); a fresh v3
  run scored 50/51 but hit the then-current 18-minute hard wall cap (now 60
  minutes). Suite v4 adds batch, fault, history and mutant dimensions
  (69 cases). See `tasks/locker/CALIBRATION.md`.
- Harness: Locker builds `bin/locker`, mounts the workdir read-only for the
  hidden suite, rejects diagnostic/malformed results, scores fixed-denominator
  family fractions and writes `results-locker.csv`, `report-locker.md` and
  `locker-leaderboard.html`.
- Not done: the six paid screening runs in section 10, leaderboard/submission
  publication, and any final decision about whether Locker is a frontier
  discriminator.

Locker is a promising systems-engineering task, not yet a demonstrated frontier
discriminator. Keep the product small; establish difficulty through interacting
invariants and calibration, not the "production-grade" label or feature count.

Audience: the engineer/model **authoring the benchmark**, not a benchmark
candidate. Section 3 supplies the public behavioral contract. Section 16 is a
private implementation playbook with reference design, test recipes and gated
work packages. Never copy this document wholesale into the seed or agent prompt.

## 1. Constraints

These are the constraints that drive every decision below.

### Real-world, not a common benchmark

- The task must be a real product/system that an engineer could plausibly be
  paid to build or fix: an object store, a database, a service, a CLI tool.
- Do **not** use math puzzles, LeetCode-style algorithm problems, HumanEval
  clones, SWE-bench-style issue fixing, Advent of Code, or other tasks that
  appear in common public test suites. Avoid reusing benchmark tasks, but do not
  assume novelty proves difficulty or that familiar tasks only test memorization.
- Do **not** make the task harder by adding more UI surface or more checklist
  features. Hardness must come from production behavior: crash consistency,
  concurrency, protocol edge cases, security, memory and integration.
- The task should be recognizable as real work but not a clone of a famous
  project whose implementation the model may have memorized. Locker is an
  S3-like local object store, not a reimplementation of MinIO.
- Standard patterns and library helpers are legitimate solutions. Do not ban a
  mutex, atomic rename or `http.ServeContent` to manufacture difficulty.

### As hard as possible within the budget

- The goal is a benchmark where frontier models do **not** all score 100%.
  Roughly 70–90% for the strongest models is a screening hypothesis, not a
  promised result or a band to force by adding gotchas.
- Hardness should come from interactions: object/metadata consistency,
  multipart state transitions, interrupted operations, concurrent access and
  progress while another request is stalled.
- Use independently scored subcases within bounded-weight test families. P0 is
  a regression gate, not free quality credit; P1/P2 provide the challenge score.
- This is one Go storage/HTTP task under a particular agent/provider budget,
  not a comprehensive measurement of frontier capability.

### Minimum cost and time

- Target **12–15 minutes** of agent wall time, **60-minute hard cap**, **5-minute
  idle timeout**. The model is told to aim for 12 minutes and prioritise P0,
  then P1, then P2.
- Target **under ~$2 per frontier-model run**. Start with **two frontier models
  and one mid-tier model, two reps each**, not a large matrix.
- Declare a token or monetary budget before calibration, alongside the wall
  cap. Record pricing, reasoning/cache token accounting and whether the client
  or provider can enforce the budget. An unenforced dollar target is not a hard
  limit; a wall timeout does not guarantee a particular API bill.
- No browser, no Playwright, no large dependency tree, no 4-hour runs. The task
  is a native Go service/CLI; grading must finish in under a minute.
- Use a brownfield seed with working CLI/server plumbing and mechanical
  parsing/XML helpers. Budget by observed implementation effort, not an
  unsupported claim that the product is "85% complete".
- Keep fixture sizes small: most tests use kilobytes to a few megabytes; one
  P2 family uses a 128 MiB object to check streaming. Crash tests do not need
  another 128 MiB transfer.
- Reuse healthy server processes and small fixtures across independent
  subcases. More local assertions need not mean more model tokens or process
  launches. Stronger requirements can still increase reasoning time; measure it.
- Hidden grading happens after the agent exits. Reducing hidden fixtures/tests
  can reduce evaluator time, but generally does not reduce API spend.

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
  family/subcase counts. A suite change invalidates comparisons with earlier
  results.
- All graded behavior, including failure semantics and numeric resource limits,
  belongs in the public contract. Hide concrete examples, not requirements.
- Do not special-case tests. Generate keys, bodies and sizes from recorded
  seeds, using the same seed schedule across models. Seeded inputs do not make
  OS scheduling or crash timing deterministic.
- Freeze the contract, seed, scoring manifest and evaluator limits before
  model calibration. Validate evaluator sensitivity on known-bad variants first.

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

SpriteForge is a greenfield, fully specified UI build whose hidden suite checks
feature presence more than adversarial behavior. Adding more UI features risks
making the run longer rather than more discriminating. Locker instead exercises
storage and protocol interactions. Whether those interactions separate the
selected models must be measured, not assumed.

### Why a brownfield seed

The seed already builds, serves HTTP, has a CLI and basic storage. That removes
boilerplate and keeps the run cheap. The model must read and extend an existing
codebase, which is closer to real engineering than a blank page. The seed's
storage layer is deliberately naive (in-place metadata, direct writes, no
locking), so the hard part is a redesign, not just adding handlers.

Supply mechanical helpers to leave room for that redesign within the same
budget. Missing atomic storage and concurrency are core architecture work, not
necessarily a small "last 20%" just because most handlers already exist.

### Why P0 / P1 / P2

- **P0** is the seed baseline: basic CLI/HTTP/DB round-trip and persistence. It
  catches regressions and gates eligibility, but contributes no quality points.
- **P1** checks protocol behavior and interactions: range/conditional requests
  after updates, multipart transitions, pagination equivalence, coherent
  concurrent reads and independent progress.
- **P2** checks interrupted publication, recovery, key isolation and bounded
  memory. Passing these sampled checks is not proof of production readiness or
  power-loss durability.

### Why 12 families, not 12 binary scores

Keep twelve families for a readable DB/CLI/HTTP breakdown, but give each a fixed
set of independently scored behavioral subcases. This avoids losing an entire
multipart score to one edge case and gives more resolution than twelve booleans.
Add short sequences and assertions, not endpoints or large fixtures. Keep family
weights fixed so splitting a test does not increase that capability's weight.

### Why these weights

- P0 = 0; each P1 and P2 family = 1. A family's credit is its passed required
  subcases divided by its fixed total.
- Quality is the mean credit across the nine P1/P2 families. P2 accounts for
  `4/9` of the challenge score, rather than `4/23` of the old weighted score.
- All P0 + all P1 + no P2 credit scores `5/9 = 0.556`, not an eligible 0.826.
- Eligibility requires a successful build, every P0 subcase, and quality
  ≥ 0.80. Eligibility is a benchmark label, not a production-readiness badge.
- Measure and publish the seed's P1/P2 baseline; some subcases may already pass.
  Do not invent a zero baseline. The unchanged seed must remain ineligible.
- These weights are provisional until the suite is frozen, not knobs to tune
  to a preferred model ranking.

### Why Go

- `net/http`, `crypto/md5`, `encoding/xml`, `os`, `sync` and `io` are all in
  the standard library. No vendored crates, no network at build time.
- Builds are fast, so the model can iterate within a 12–15 minute budget.
- Rust would add incidental difficulty (borrow checker, lifetimes, no std HTTP
  server, vendored dependencies, slower builds) and make the benchmark noisier
  and more expensive. Use Rust later for a task where the language itself is the
  point.

## 3. Product: Locker

A local, single-node, S3-inspired object store implementing the explicit subset
below, not full S3 compatibility. One Go binary, no external dependencies, no
auth, loopback only.

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
| Multipart parts | Any body size; positive part numbers; re-upload replaces a part; completion validates current ETags and assembles the requested parts in numeric order |
| Range | GET/HEAD, single range only: `start-end`, `start-`, `-suffix`; invalid/unsatisfiable → 416 with `Content-Range: bytes */size`; HEAD has the corresponding GET headers/status but no body |
| Conditional | GET/HEAD only; paired validator precedence and second-resolution dates, as specified below; evaluate preconditions before Range |
| Listing | Bytewise sorted keys; `prefix`, `delimiter`, `max-keys` (default 1000), opaque continuation token; `CommonPrefixes` count toward the page limit |
| Buckets | Non-empty UTF-8 names, no slash; `.`, `..` and `health` are reserved; deleting a bucket with objects or live uploads → 409 |
| Object keys | UTF-8, slashes allowed; decode URL escaping exactly once; preserve distinct key identities and bucket isolation; traversal must be rejected or stored safely |
| Persistence | Internal format is free; no compatibility with seed v0 data; body and metadata must publish as one coherent version |
| Concurrency | Per-key writes have an atomic publication order consistent with non-overlapping operations; readers see a complete version; writes to different keys are not lost |
| Progress | An incomplete upload must not block reads of an existing object or operations on unrelated keys beyond the published progress deadline |
| Crash | Process kill during publication leaves the old or new complete version; a write acknowledged before the kill must survive; power-loss durability is not graded |
| CLI | Goes through HTTP; `--endpoint` or `LOCKER_ENDPOINT`, default `http://127.0.0.1:9000` |

### Cross-operation semantics

- **Coherent versions:** bytes, ETag, length, content type and modification time
  belong to one published version. Check a concurrent GET's headers against its
  own body. Separate GET/HEAD/list requests may legitimately observe different
  versions while writes are active; compare them after writes stop or restart.
- **Write ordering:** if write A finishes before B starts, B must replace A.
  Overlapping writes may publish in either order, consistent with observations;
  do not infer the winner from client thread start or response arrival order.
  A final non-overlapping write must be visible everywhere after it succeeds.
- **Incomplete PUT:** a body shorter than its declared length cannot replace an
  existing object. A disconnected client that already sent the entire body may
  still have committed; do not confuse this with an incomplete-body failure.
- **Multipart transitions:** upload IDs are scoped to their bucket/key.
  Same-session mutations serialize: a part re-upload or abort cannot change
  the parts a successful completion validated. If completion wins, a
  concurrent abort/re-upload returns 404; if abort wins, completion returns 404
  and the published object is unchanged. These races must not produce a 5xx or
  a mixed version. Completion supplies a non-empty list of unique part numbers
  and current ETags; missing parts, duplicate numbers or stale ETags return 400
  without changing the object or upload, so a corrected completion can succeed.
  Completion assembles only the requested parts and consumes the upload.
  Abort removes the upload without changing the published object. Operations
  using an aborted, completed or unknown upload ID return 404.
- **Conditional precedence:** evaluate `If-Match` when present, otherwise
  `If-Unmodified-Since`; then `If-None-Match` when present, otherwise
  `If-Modified-Since`. Stop on a failed precondition or not-modified result.
  Use HTTP date precision of whole seconds and ignore invalid dates. Support
  one quoted ETag or `*`; weak tags and lists of validators are outside this
  subset. No sleeps to cross timestamp boundaries in tests.
- **Pagination:** group delimiter prefixes before applying the page limit.
  Order keys and grouped prefixes bytewise and continue without duplicates or
  omissions. Tokens are valid for the same bucket/prefix/delimiter query;
  malformed or mismatched tokens return 400. `max-keys` is a decimal integer
  from 1 through 1000 inclusive; omitted means 1000, other values return 400
  (an explicitly empty value is invalid). When present, `list-type` must be
  `2`; other values return 400. Tokens remain usable across restart when the
  query and namespace are unchanged. Namespace mutation between pages is
  outside the contract.
- **Key identity:** `a` and `a/b` can coexist; identical keys in different
  buckets are independent. For example, URL path `%252F` names a literal
  `%2F`, not `/`. Accepting a traversal-looking key must not alias another key,
  overwrite unrelated data, or escape the data directory.
- **Recovery:** without a success acknowledgment, an interrupted publication
  may leave the old or new coherent version; on a previously absent key, the
  old state is 404. After an acknowledgment, only the new version is allowed.
  In-progress multipart session recovery and orphan cleanup after a kill are
  not graded. The published object and subsequent ordinary operations are.

### Wire contract to copy into the candidate prompt

These choices define Locker's subset; do not silently substitute full S3
behavior. Paths use `/b` for a bucket and `/b/k` for a non-empty object key.
Split the escaped path into bucket/key, then percent-decode each exactly once;
do not clean dot segments or decode a second time. Malformed encoding, invalid
UTF-8 and invalid bucket names return 400. `/health` is reserved for readiness.
Query parameters use ordinary URL query decoding.

| Request | Success | Required response |
|---|---|---|
| `GET /health` | 200 | Plain text `ok` |
| `GET /` | 200 | `ListAllMyBucketsResult`, names bytewise sorted |
| `PUT /b` | 200 | Empty body; creating an existing bucket is idempotent |
| `DELETE /b` | 204 | Empty body; absent bucket 404, non-empty bucket 409 |
| `GET /b` or `GET /b?list-type=2` | 200 | `ListBucketResult`; supports the documented listing parameters |
| `PUT /b/k` | 200 | Empty body; quoted ETag header; bucket must already exist |
| `GET /b/k` | 200 or 206 | Object bytes and version headers |
| `HEAD /b/k` | Same as GET | Same version/range headers, never a body |
| `DELETE /b/k` | 204 | Empty body, including an absent key in an existing bucket |
| `POST /b/k?uploads` | 200 | `InitiateMultipartUploadResult`; bucket must exist |
| `PUT /b/k?uploadId=U&partNumber=N` | 200 | Empty body and quoted MD5 ETag of the part |
| `GET /b/k?uploadId=U` | 200 | `ListPartsResult`, numeric part order |
| `POST /b/k?uploadId=U` | 200 | Completion XML request; `CompleteMultipartUploadResult` response |
| `DELETE /b/k?uploadId=U` | 204 | Abort, empty body |

Object operations on an absent bucket return 404. GET/HEAD of an absent key
return 404. Multipart routing takes precedence over ordinary object routing;
invalid/combined multipart action parameters return 400. Unsupported methods
return 405. Part numbers are decimal integers from 1 through 10000 inclusive.
Malformed completion XML or an empty part list returns 400 without mutation.
Valid object/part PUTs support `Expect: 100-continue`: send the interim 100
response when beginning to read the body, before waiting for the remaining
bytes. This allows clients to coordinate an unfinished upload without guessing
whether the server has started handling it.

Successful object GET/HEAD includes `ETag`, `Content-Length`, `Content-Type`,
`Last-Modified` (HTTP date) and `Accept-Ranges: bytes`. Preserve the upload's
`Content-Type`, defaulting to `application/octet-stream`. Multipart uses the
type supplied at initiation, not individual part types. A completed object's
ETag is the quoted lowercase MD5 of its entire body. Empty objects are valid.

For a body of length `n`, clip a range end above `n-1`; an oversized positive
suffix returns the whole body with 206. Negative/reversed bounds, zero suffix,
multiple ranges and ranges starting at or beyond `n` return 416, including
any range on an empty object. All 416 responses include `bytes */n` in
`Content-Range`; their body is not graded. `If-Range` is outside the subset.
304 and 412 responses have no body; 304 includes the current ETag. Dates at or
after modification time yield 304 for `If-Modified-Since`; dates before it
yield 412 for `If-Unmodified-Since`, subject to paired precedence above.

XML uses UTF-8, no namespaces, and `Content-Type: application/xml`.
Whitespace, XML declarations and sibling field order are not significant.
The following are structural examples; uppercase values are substitutions,
not literal expected data:

```xml
<ListAllMyBucketsResult>
  <Buckets><Bucket><Name>BUCKET</Name></Bucket></Buckets>
</ListAllMyBucketsResult>

<ListBucketResult>
  <Name>BUCKET</Name><Prefix>PREFIX</Prefix><Delimiter>/</Delimiter>
  <MaxKeys>2</MaxKeys><KeyCount>2</KeyCount><IsTruncated>true</IsTruncated>
  <Contents><Key>KEY</Key><Size>3</Size><ETag>"MD5"</ETag>
    <LastModified>2026-01-01T00:00:00Z</LastModified></Contents>
  <CommonPrefixes><Prefix>GROUP/</Prefix></CommonPrefixes>
  <NextContinuationToken>TOKEN</NextContinuationToken>
</ListBucketResult>

<InitiateMultipartUploadResult>
  <Bucket>BUCKET</Bucket><Key>KEY</Key><UploadId>ID</UploadId>
</InitiateMultipartUploadResult>

<ListPartsResult>
  <Bucket>BUCKET</Bucket><Key>KEY</Key><UploadId>ID</UploadId>
  <Part><PartNumber>1</PartNumber><ETag>"MD5"</ETag><Size>3</Size></Part>
</ListPartsResult>

<CompleteMultipartUpload>
  <Part><PartNumber>1</PartNumber><ETag>"MD5"</ETag></Part>
</CompleteMultipartUpload>

<CompleteMultipartUploadResult>
  <Bucket>BUCKET</Bucket><Key>KEY</Key><ETag>"FULL_BODY_MD5"</ETag>
</CompleteMultipartUploadResult>

<Error><Code>NoSuchKey</Code><Message>Human-readable explanation</Message></Error>
```

Repeat `Bucket`, `Contents`, `CommonPrefixes` and `Part` elements as needed;
empty collections have no entries. Echo listing prefix/delimiter, using empty
strings when omitted. `KeyCount` includes objects and grouped prefixes.
Compute page boundaries from the combined bytewise ordering; XML may serialize
objects and prefixes in separate groups, sorted within each group.
`NextContinuationToken` is required only when truncated; it is otherwise absent.
XML object timestamps are UTC RFC3339 at whole-second precision.

Errors 400/404/409/405/500 use the error XML shape above, except HEAD never has
a body. Codes are `InvalidArgument`, `NoSuchBucket`/`NoSuchKey`/`NoSuchUpload`,
`BucketNotEmpty`, `MethodNotAllowed`, and `InternalError`, respectively.
Messages must be non-empty but their wording is not graded. If several inputs
are invalid, no particular validation order is required. Operational failures
must not be reported as success or reset the store silently.

### CLI contract

Use `locker [--endpoint URL] COMMAND ...`; the flag overrides `LOCKER_ENDPOINT`,
which overrides `http://127.0.0.1:9000`. Server mode uses its own documented
`--data` and `--addr` flags. Client commands never access the data directory.

| Command | Required behavior |
|---|---|
| `mb s3://b` / `rb s3://b` | Create/delete bucket through HTTP |
| `cp LOCAL s3://b/k` | Stream upload; no automatic bucket creation |
| `cp s3://b/k LOCAL` | Stream download, preserving exact bytes |
| `ls s3://b/prefix` | List all matching objects, following continuation tokens |
| `rm s3://b/k` | Delete object through HTTP |

CLI S3 URIs use the same percent-escaping convention as HTTP. `ls` prints one
JSON object per line with `key`, `size` and `etag`, bytewise sorted; no headers,
colors or diagnostic text on stdout. An empty listing produces no lines.
Other successful client commands are silent on stdout. Exit 0 on success,
1 on HTTP/network/local-file failure, and 2 on invalid CLI usage. Errors go to
stderr and are non-empty. Recursive copy, stdin/stdout transfer and remote-to-
remote copy are outside scope.

### Resource limits to freeze before model calibration

Use one **128 MiB = 134,217,728 byte** streaming fixture. On the pinned reference
runner, choose and publish an exact server RSS limit below that size, a total
PUT/GET/range deadline, a progress deadline for unrelated operations during a
stalled upload, and the bounded crash-trial count. Include all values in the
prompt and suite manifest. Select generous headroom from reference measurements,
not from model results; no unspecified numeric limits at the calibration freeze.

Measure the server process's `VmHWM` in `/proc/<pid>/status`, not just occasional
`VmRSS` samples. Keep the Python client's allocations out of that measurement
and stream its fixture generation/checks too. State the measurement scope
explicitly; it is not a general whole-system memory benchmark.

### Out of scope

Auth, TLS, replication, versioning, lifecycle rules, S3 signatures, multiple
nodes, background compaction, dedup/GC, web UI, SDKs, mobile.

## 4. Seed v0 (working plumbing, unfinished integrity)

The seed is a working Go v0 that builds and passes P0.

It provides:

- HTTP server with basic `PUT`, `GET`, `HEAD`, `DELETE`, bucket create/list/
  delete and a simple XML list response;
- CLI `mb`, `rb`, `cp`, `ls`, `rm` going through the HTTP API;
- file storage plus a JSON metadata file;
- MD5 ETags, `Content-Type`, `Last-Modified`;
- XML structs/helpers for list and multipart responses, request parsing and
  mechanical range/conditional helpers, with public examples of their use;
- `scripts/smoke.py` with a mix of passing and failing checks;
- `AGENTS.md` with stack, commands and rules.

It is deliberately naive:

- metadata is rewritten in place, not atomically;
- object writes go directly to the final path;
- no correct synchronization of publication and metadata updates;
- `Range` and conditional headers are ignored;
- multipart endpoints return `501`;
- listing has no pagination or delimiter handling;
- large uploads are read into memory.

Helpers need not already be wired into every handler. Their purpose is to
remove repetitive parsing work, not solve atomic storage or state transitions.
Record which challenge subcases the unchanged seed passes.

The prompt tells the model: "The seed passes P0. Implement the documented
storage and protocol guarantees. You may restructure the storage layer, but
keep the CLI/HTTP contract." Do not promise that this is only 20% of the effort.

## 5. Hidden test matrix (12 families with scored subcases)

Family counts: P0: 3, P1: 5, P2: 4. A trusted suite manifest defines the fixed
subcase IDs, family membership, input seeds and resource limits. Keep sequences
short and independently assessable; shared setup failure must not create
vacuous passes for downstream checks.

Section 16 fixes the initial 39 subcase IDs and supplies authoring recipes;
the hardening revision adds twelve state-transition/parameter/concurrency subcases (51 total).
The manifest must match that catalog, not infer cases from implementation output.

| # | Tier | Category | Family |
|---:|---|---|---|
| 1 | P0 | CLI | bucket/object round-trip through `mb/cp/ls/rm` |
| 2 | P0 | HTTP | basic PUT/GET/HEAD/DELETE, status, body, ETag, `Content-Length` |
| 3 | P0 | DB | buckets, Unicode keys and metadata survive restart |
| 4 | P1 | HTTP | range GET/HEAD and byte equivalence after object replacement |
| 5 | P1 | HTTP | validators, paired precedence and metadata after replacement/restart |
| 6 | P1 | HTTP | multipart replacement, failed completion, retry and abort transitions |
| 7 | P1 | HTTP | listing/pagination equivalence, grouped-prefix boundaries and restart |
| 8 | P1 | DB | coherent concurrent reads, no lost writes, progress during a stalled PUT |
| 9 | P2 | DB | incomplete PUT and interrupted overwrite preserve coherent publication |
| 10 | P2 | HTTP | multipart publication/recovery and cross-feature readback |
| 11 | P2 | SEC | traversal containment, distinct key identities and bucket isolation |
| 12 | P2 | PERF | 128 MiB PUT/GET/range within the published memory and time limits |

Family result titles must start with the tier prefix:

```text
[P0][cli]  cli bucket and object round-trip
[P0][http] http object CRUD and basic headers
[P0][db]   buckets unicode keys and metadata survive restart

[P1][http] range requests preserve byte equivalence after replacement
[P1][http] conditional requests preserve validator semantics after updates
[P1][http] multipart transitions preserve objects and permit corrected completion
[P1][http] paginated listings match complete grouped listings
[P1][db]   concurrent operations preserve coherent versions and independent progress

[P2][db]   interrupted writes preserve coherent publication and recovery
[P2][http] multipart publication survives interruption and cross-feature reads
[P2][sec]  keys preserve identity bucket isolation and traversal containment
[P2][perf] large object put get and range stay within memory cap
```

### Test details

- **P0 CLI:** start server, `mb`, `cp` upload, `ls`, `cp` download, byte compare,
  `rm`, `ls` empty.
- **P0 HTTP:** small object; status, body, `ETag`, `Content-Length`,
  `Content-Type`, `HEAD` no body, `DELETE`, 404.
- **P0 DB:** PUT Unicode and nested keys, restart server, GET and list; buckets
  persist.
- **P1 range:** known small body of length `n`; closed, open and suffix ranges
  equal slices of full GET, including after replacement. `bytes=n-` returns
  416; `bytes=(n-1)-` is valid. Derive boundaries from the actual length:
  `bytes=999999-` is valid on either a decimal 1 MB or binary 1 MiB object.
  Check `Content-Range`, `Accept-Ranges` and HEAD's lack of body.
- **P1 conditional:** matching/mismatching ETags, 304/412, paired precedence,
  and a conditional request with Range. Replace with a different body/type;
  validators must describe the new version and remain coherent after restart.
  Derive date cases from returned `Last-Modified`, allowing equal timestamps
  for writes within one second.
- **P1 multipart:** initiate, upload/list parts, replace a part, reject its
  stale ETag, then complete with current ETags. Separately exercise missing or
  duplicate parts and abort. Failed completion preserves the previous object
  and permits retry. Verify exact assembled bytes and upload invalidation.
- **P1 listing:** seeded nested keys; prefix/delimiter and small page limits
  that split grouped-prefix boundaries. Concatenating all pages equals the
  expected complete listing with no omissions/duplicates and correct order.
  Check token scope and repeat after restart, without concurrent mutations.
- **P1 concurrency:** bounded writers/readers use distinguishable bodies,
  sizes and content types. Every GET has one complete body and matching
  metadata; no 5xx. Different-key writes all survive restart. After overlapping
  writes finish, a final serialized PUT must win. Hold another PUT open before
  its body ends and require old-object reads and unrelated operations to make
  progress within the published deadline.
- **P2 overwrite:** start with a known object. Disconnect an incomplete upload
  and check the old version is unchanged. In a separate case, kill with an
  incomplete request in flight and require the same after restart. Use a
  bounded crash stress case around complete overwrites for old-or-new coherent
  recovery, plus a kill after a confirmed success that requires the new
  version. Check metadata/readback and a subsequent write, not just readiness.
- **P2 multipart:** complete over an existing object, then range/conditional
  GET, restart and list/CLI download with exact readback. Separately run bounded
  kill trials during completion: old or new complete version before an
  acknowledgment, new only after one. Include an initially absent key, where
  only the unacknowledged case permits 404. Recovery must leave the store usable.
- **P2 isolation:** exercise raw/encoded traversal, both `a` and `a/b`, literal
  percent escapes, Unicode and identical keys across buckets. Check independent
  readback, unchanged neighboring objects and no escape from the data directory.
- **P2 perf:** stream PUT and GET of one 128 MiB object, plus a range read;
  verify exact bytes, server `VmHWM`, and the frozen operation deadline.

### Concurrency and crash-test limits

Use client-controlled body gates and completed-response barriers rather than
short sleeps to establish operation ordering. A global mutex is allowed if it
does not block required progress while waiting on network input. Never compare
separate concurrent reads as though they must observe the same version.

An incomplete-body kill checks premature publication, not the metadata commit
window. The black-box interface cannot guarantee a kill at a particular internal
commit step. Bound the stress trials, record acknowledgments and observed
phases, and measure detection against known-bad variants. If a claimed defect is
not reliably detected within the evaluator budget, revise the test or narrow
the claim; do not call probabilistic coverage deterministic.

`SIGKILL` leaves the kernel page cache intact. These tests do not certify
power-loss durability or correct `fsync` ordering.

### Test runner

- Python 3 standard library only.
- Starts `locker server` on a free loopback port, waits for `/health`.
- Uses `http.client`/`urllib` and `xml.etree.ElementTree`.
- Uses `subprocess` for CLI.
- Kills/restarts the server for crash tests.
- Reuses healthy servers with isolated keys/buckets for independent subcases;
  restarts only when the scenario needs it. Continue independent cases after a
  failure; unmet prerequisites fail affected cases rather than skipping them
  out of the denominator.
- Uses the trusted manifest for expected IDs/counts, limits and seeded fixture
  schedules. Record the suite version and input seed in every result.
- Writes `results.json`:

  ```json
  {
    "schema": "opencode-bench/locker-tests@1",
    "suite_version": "locker-v1",
    "seed": 1,
    "diagnostic": false,
    "tests": [{
      "id": "p1.range",
      "title": "[P1][http] range requests preserve byte equivalence after replacement",
      "cases": [
        {"id": "closed", "ok": true, "duration": 0.1, "error": null},
        {"id": "after-replacement", "ok": false, "duration": 0.1, "error": "byte mismatch"}
      ]
    }]
  }
  ```

  This illustrates one family, not a complete valid suite result.

- Exits nonzero if any subcase fails; cleans up processes, gated connections and
  temp dirs. Distinguish evaluator infrastructure errors from application
  failures; infrastructure errors invalidate a comparison.

## 6. Reference implementation

`tasks/locker/reference/` contains the complete Go implementation. It is never
mounted into the agent workdir. It must use:

- streaming copies, not `io.ReadAll` for object bodies;
- coherent body/metadata publication, for example immutable object files plus
  one atomically replaced manifest; independently renaming two files is not
  sufficient to guarantee a coherent version;
- appropriate file/directory syncing as implementation hygiene, while making
  no claim that process-kill tests prove power-loss durability;
- per-key or global synchronization without holding a blocking lock over an
  incomplete network upload;
- multipart part storage and cleanup;
- listing/pagination and XML serialisation;
- safe key-to-path mapping.

Evaluator validation, before paid model calibration:

- Reference passes every subcase in all 12 families on the frozen seed schedule.
  Three full passes are an initial smoke check, not evidence of low flake rates.
- Run the crash/concurrency subset repeatedly with no model calls, recording
  attempts and failure rates. Use at least 50 repetitions for this subset before
  freezing; zero observed flakes is a release check, not proof of determinism.
- Seed passes all P0 subcases and remains ineligible. Record its actual P1/P2
  credits rather than assuming every challenge subcase fails.
- Validate against deliberately broken reference variants: mismatched
  body/metadata publication, lost unrelated writes, changing an object before
  multipart validation finishes, and holding a global lock during request-body
  input. Each must fail its intended invariant reliably. Keep these variants
  with evaluator fixtures, never in the agent workdir.
- Log the defect-to-subcase mapping and detection results. Passing the reference
  and failing a very incomplete seed alone does not establish test sensitivity.
- Obtain independent review of the wire contract, assertion oracles and
  publication/recovery logic before paid calibration. Agreement between a
  reference and tests written by the same model is not independent evidence.
  Review may be by a qualified human or another capable model; it must resolve
  findings, not merely rerun the same tests.

## 7. Harness integration

For the spike, change the task in place on `hard_task`; keep SpriteForge on
`main`.

- `TASK_DIR = ROOT / "tasks" / "locker"`.
- `PROMPT_PATH = TASK_DIR / "PROMPT.md"`.
- `EXPECTED = {"P0": 3, "P1": 5, "P2": 4}` counts families, not subcases.
- `WEIGHT = {"P0": 0, "P1": 1, "P2": 1}` weights family fractions.
- Replace binary title scoring for Locker with manifest-backed subcase scoring.
  Missing required cases earn zero; duplicate/unknown IDs or malformed results
  are invalid. Never derive expected totals from the submitted result.
  Scored results must explicitly have `diagnostic: false`; diagnostic subsets
  and results missing that field are not eligible for ingestion.
- Separate `results-locker.csv`, `report-locker.md`, `locker-leaderboard.html`.
- Run IDs include `locker` to avoid collisions with SpriteForge runs.
- `build_app()` runs `go build -trimpath -o /work/bin/locker ./cmd/locker`.
- `run_tests()` mounts the workdir read-only, mounts hidden tests, runs Python
  with `--network none`, and parses the versioned `results.json` shape.
- `run_one()` checks for `work/bin/locker` instead of `dist/index.html`.
- `start_agent()` counts `go build`, `go test`, `scripts/smoke.py` as
  self-tests.
- `cmd_check()` validates family/subcase IDs and counts against the manifest,
  matching prompt/resource limits, seed files, hidden family titles and image.
- `tests/test_bench.py` gains Locker fixtures for fractional family credit, P0
  gating, fixed denominators, missing/duplicate IDs and malformed results;
  existing SpriteForge tests remain on `main`.
- Record the fixture seed, suite/seed version, budget configuration, price
  snapshot, token accounting and budget-enforcement status with run metrics.
  Unknown cost is explicitly unknown, not zero.
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
    manifest.json
    run.py
    helpers.py
    test_oracles.py
    test_runner.py
    ...hidden black-box tests...
    ...known-bad evaluator validation fixtures...

docker/locker.Dockerfile
```

## 9. Scoring and leaderboard

- For each family, credit = passed required subcases / fixed expected subcases.
  Quality = sum of the nine P1/P2 family credits / 9. P0 contributes no quality
  points; it is reported separately as regression coverage.
- Eligible = build succeeded, every P0 subcase passed, quality ≥ 0.80.
- Report family credits, subcase counts/failures and the measured seed baseline,
  plus outcome, wall, cost/tokens, steps and compactions. Do not hide integrity
  failures behind an aggregate score or describe eligibility as production-safe.
- For cloud runs: quality is the primary rank; cost/tokens is the second axis;
  wall is a tie-breaker. Show a Pareto view or two tables (Capability and
  Value) rather than the current local-model composite.
- For the initial two reps, show both results and their range. Any displayed
  two-sample median equals the mean and is not a robust estimate. Treat close
  rankings as unresolved rather than using tiny cost/time differences to imply
  confidence.
- Timeout/incomplete runs are shown but marked ineligible when appropriate.

## 10. Calibration plan

1. Build the reference and evaluator together. Obtain full subcase passes,
   repeat the crash/concurrency subset, and demonstrate sensitivity using the
   known-bad variants. Measure evaluator runtime separately from agent time.
2. Build seed; require all P0 subcases and record its challenge baseline.
   Freeze prompt semantics, seed, manifest, weights and reference-derived
   resource limits before looking at model scores.
3. Run the harness against a local model once to debug image/test plumbing
   without cloud API spend; this still consumes local time/compute.
4. Declare the wall and token/cost budgets, pricing and enforcement limits.
   Screen two frontier models and one mid-tier model, two reps each, with a
   matched fixture-seed schedule. The command below sets wall/idle caps only,
   not a dollar ceiling:

   ```bash
   ./bench.py run --model provider/model \
     --timeout 3600 --idle-timeout 300 --reps 2
   ```

5. Inspect `score.json`, `test.log`, `results.json`, `COMPLETION.md` and
   `agent.log`. Attribute failures to documented behavior, not just aggregate
   scores. Use extra model repetitions only for close rankings or unexplained
   differences; paired seeds improve comparability but do not eliminate noise.
6. Adjust:
   - all models near 100% → first strengthen existing consistency and
     state-transition checks where sensitivity is weak; do not add endpoints,
     dedup/GC or obscure requirements merely to force lower scores;
   - all models low or over agent budget → move mechanical implementation
     into the seed, simplify task scope or revise the budget explicitly;
   - flaky tests → fix the evaluator, not the model; recheck negative controls;
   - grader over 60 seconds → reduce repeated setup, large transfers or
     redundant trials, preserving demonstrated sensitivity;
   - API cost above target → adjust seed scope, context/reasoning usage or
     token/cost controls; fewer hidden assertions are not an API-cost fix.
7. Version any contract, seed, scoring or evaluator change and rerun comparable
   calibration. Check revisions on reserved fixture seeds and avoid tailoring
   assertions to particular model outputs. If the task still does not separate
   models within budget, report that result rather than manufacturing a spread.

### Calibration targets

- Reference: all subcases pass; repeated crash/concurrency runs have no observed
  flakes, and known-bad variants fail their intended checks reliably.
- Seed: all P0, measured challenge baseline, ineligible.
- Top models: roughly 70–90% is a hypothesis; useful, explainable separation
  matters more than hitting a predetermined score band.
- A roughly 20-point strongest/mid-tier gap is a screening aspiration, not
  proof of general model capability. Two reps cannot establish fine rankings.
- Agent wall: target 12–15 minutes, hard cap 18. Report each observed run and
  cap hits; six runs and a timeout do not establish a meaningful p95.
- Hidden suite: under 60 seconds, including bounded crash trials.
- Cost: target under roughly $2 per run, with actual/estimated/unknown cost
  distinguished and budget-enforcement limitations disclosed.

## 11. Implementation order

These are planning estimates, not a single "implement everything" assignment.
Use the smaller, gated work packages in section 16 for model implementation.

| Step | Work | Estimate |
|---:|---|---|
| 0 | Draft explicit contract, seed helpers, 12-family/subcase matrix and scoring manifest | 0.5–1 day |
| 1 | Build seed v0 + public smoke; seed passes P0 | 1–2 days |
| 2 | Build reference alongside initial evaluator cases; establish resource headroom | 2–3 days |
| 3 | Finish evaluator; repeated subset runs, negative controls and seed baseline; freeze suite | 1–2 days |
| 4 | Harness spike: Go image, build/test runner, separate results | 1 day |
| 5 | Calibration with local + frontier models; adjust | 1–2 days |
| 6 | Only after calibration: leaderboard/submissions/docs and fork-vs-suite decision | 1–2 days |

These are provisional engineering estimates, with reference/evaluator work
iterating together. Crash-test sensitivity and stability are explicit gates,
not work to skip to meet a one- or two-week estimate. Repeated evaluator runs
add authoring-time compute, not paid model calls to every benchmark run.

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| HTTP/crash tests are flaky | Readiness and client gates, bounded deadlines/trials, repeated reference runs; seeded data is not deterministic scheduling |
| Crash tests miss publication defects | Known-bad variants, acknowledgment-aware oracles, detection measurements; narrow claims where black-box timing cannot cover the defect |
| Seed is too helpful or too thin | Measure its challenge baseline and agent effort; move mechanical work into helpers rather than target an arbitrary completion percentage |
| Task saturates at 100% | Strengthen existing interaction coverage; no feature inflation or preferred-ranking tuning |
| Task is too hard, everyone fails P1 | Inspect whether failures reflect ambiguity, boilerplate or genuine semantics; simplify scope or supply helpers before weakening integrity requirements |
| API cost exceeds budget | Seed/context/reasoning scope, declared token/cost controls and accounting; wall caps and smaller hidden fixtures do not guarantee a bill |
| Grading exceeds one minute | Reuse servers, small fixtures and bounded trials; retain measured defect sensitivity |
| Sparse results look more certain than they are | Show both reps, baseline and failures; targeted extra reps, no p95 or fine-ranking claims from six runs |
| Agent uses the internet to find solutions | Egress allowlist/proxy for model provider before publishing; log non-provider network use |
| Go image/version drift | Pin image tag/digest and Go version; build/test with `--network none` |
| Hidden tests leak into the seed | Reference and tests stay outside the workdir; tests mounted only after the agent exits |
| Authoring model writes mutually wrong reference/tests | Exact wire fixtures, independent expected-value calculations, per-case negative controls and independent critical review |
| Authoring model loses scope or changes acceptance to pass | One gated work package per invocation; expected outputs fixed before implementation; unresolved contract conflicts require a decision |
| Results are not comparable after changes | Record harness/seed/suite versions, image, manifest, fixture seeds and budgets; rerun calibration after changes |

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
- Dedup/GC or other feature expansion merely to lower calibration scores.
- Power-loss durability certification, exhaustive interleaving coverage, or
  broad frontier-capability claims from one task.
- Multi-suite refactoring before the task is validated.
- Community submissions/CI before the task is calibrated.

## 15. Definition of done

- `tasks/locker/PROMPT.md` states every graded guarantee and exact resource
  limit, consistent with the frozen suite manifest and seed.
- Seed builds, runs, passes all P0 subcases and is ineligible; its challenge
  baseline is recorded.
- Reference passes all subcases; repeated crash/concurrency results and
  known-bad variant detection are documented, including coverage limitations.
- Independent critical review has resolved contract, oracle and publication
  issues; no acceptance rule was weakened solely to match the reference.
- Hidden suite finishes under 60 seconds with fixed seeded inputs and bounded
  trials; no claim of deterministic crash scheduling.
- Harness on `hard_task` builds/tests Locker, scores fixed-denominator family
  fractions, gates on P0 and writes separate results.
- Two frontier models and one mid-tier model, two reps each, provide an initial
  screening of separation, agent time and cost. Unclear differences remain
  unresolved or receive targeted additional repetitions.
- A calibration report records both runs, seed baseline, family/subcase
  failures, fixture seeds, outcomes, wall, cost/accounting and budget limits.
- Publish as a frontier discriminator only if results support that claim within
  budget; otherwise record an unsuccessful calibration rather than declare the
  design validated.
- Fork-vs-suite and public leaderboard decisions are made after calibration.

## 16. Implementer-only playbook

### Handoff rules and information boundary

Do not ask the authoring model to invent the protocol, storage design, evaluator
and harness simultaneously. Assign one work package below per invocation.
Supply section 3 plus only the relevant architecture/recipe passages and current
files. Require concrete files and command results, not a claim of completion.

| Material | Candidate visibility |
|---|---|
| Section 3 contract and frozen numeric limits | Copy into `tasks/locker/PROMPT.md` |
| Naive seed, mechanical helpers, public smoke tests | Candidate-visible |
| This plan, reference architecture/source/binary, hidden recipes/manifest, negative controls | Author/evaluator only |

Do not place private material in the seed, build image or candidate workdir,
including comments that reveal hidden cases or a reference implementation.
Build reference binaries during authoring/evaluation, not into the agent image.
The reference design below is a recipe for the author, not an architecture
restriction on benchmark candidates.

No silent design changes: if a contract is contradictory or a proposed oracle
rejects a valid implementation, stop that work package and report the exact
conflict. A test failure is not permission to change the expected result.
Resource numbers explicitly reserved for measurement are different: measure
them on the reference runner, record the evidence, and freeze before calibration.

### Reference storage recipe

Use immutable blobs plus one atomically published document manifest. Prefer
this simple, serialized commit design over introducing a WAL or database engine.

- Manifest: buckets and their object maps. Each object entry contains an opaque
  blob filename, size, quoted ETag, content type and modification time.
  Bucket/key strings are map keys, never filesystem paths.
- Files: private staging directory, immutable blob directory, manifest JSON,
  and separate multipart staging. All publication renames stay on one filesystem.
  Generate blob/upload names internally; never use client identifiers as paths.
- Synchronization: one short manifest mutex for publication and snapshot lookup;
  one mutex per multipart session. If both are needed, session lock precedes
  manifest lock. Never wait for a session lock while holding the manifest lock.
- Old/orphan blobs may remain until the evaluation data directory is removed.
  GC is not required. Avoid deleting a blob that an in-flight reader may use.

**PUT publication sequence:**

1. Stream request bytes to a unique staging file while computing MD5 and size.
   Do not hold the manifest mutex over network input. Reject read errors or
   incomplete declared bodies; do not publish partial content.
2. Sync/close the file, move it to an immutable blob name, and sync the blob
   directory. This blob is not yet a visible object.
3. Acquire the manifest mutex; recheck bucket existence. Base the next manifest
   on the **current committed manifest**, not a snapshot from before the upload.
4. Replace the key's entry in a copy, set its commit timestamp, and write a unique
   manifest temporary file. Sync/close it, rename it over the live manifest,
   and sync its directory. This rename is the publication point.
5. Install the matching in-memory snapshot, release the mutex, then acknowledge
   success. Check all I/O errors. If a failure after the rename makes commit
   status uncertain, fail closed rather than continue with divergent disk and
   memory state. Orphaned staged blobs are acceptable, success-shaped errors are not.

GET/HEAD resolves one entry and opens its immutable blob while holding the
manifest mutex, then releases it before streaming. Headers come from that same
entry, not a second lookup. Listing snapshots metadata under the mutex.
DELETE and bucket changes use the same manifest commit mechanism.
On restart, load the committed manifest; a genuinely new directory starts empty.
A malformed existing manifest or missing referenced blob is an explicit startup
error, never a reason to silently initialize a fresh store.

**Multipart recipe:** keep process-local sessions keyed by generated upload IDs,
bound to bucket/key and initiation content type. Stream each part to a unique
file before replacing the session's part entry under its mutex. Completion
holds that session's mutex, validates the entire supplied list before touching
the object, then streams immutable part files in numeric order to a new blob.
Publish through the same object commit path, then invalidate the session.
Abort invalidates only the session and its unneeded staging files. Do not hold
the global manifest mutex over part input or assembly. Same-session mutations
serialize; unrelated requests remain independent. Uncommitted sessions may be
lost on restart, as allowed by the public contract.

### Shared evaluator rules

Implement these helpers before writing concurrency cases:

- A server context manager owns an explicit PID, unique data directory and logs.
  It waits for `/health` under a monotonic startup deadline, detects early exit,
  and always closes sockets/joins workers/stops its process in `finally`.
  Crash cases kill only that PID, wait for exit, then restart on the same data.
- An HTTP helper returns status, headers and bytes without following redirects
  or normalizing paths. Use `http.client` with already escaped request targets.
  Each concurrent worker owns its own connection. Every socket operation has a
  deadline; worker exceptions must propagate to the case result.
- The gated-upload helper uses a raw socket and bounded interim-header parser:
  `http.client.getresponse()` skips 100 responses and would wait for the final
  response, defeating this gate. Parse the interim response explicitly before
  waiting on the client-controlled body gate.
- A streaming helper generates deterministic chunks and hashes them without
  allocating a whole large body. Small-object expected bytes/metadata come from
  fixture inputs, not the reference's reply. XML is parsed structurally.
- A case wrapper records its fixed ID, outcome, duration and actionable error.
  Cleanup failure is reported. Required setup failure fails dependent assertions;
  it must never count as a pass or shrink the scoring denominator.
- Per-family setup may be shared only if state cannot leak between independent
  cases. Use fresh keys/buckets. Failed requests must not poison later clients.
  Known-bad validation modes are private fixture variants, not candidate APIs.
- Containment cases use an owned temporary root with a data subdirectory and
  sibling sentinels. Inspect only that owned area for escaped writes; never
  target real host files or rely on a particular internal storage layout.

For each case, implement its required setup, actions and oracle from the catalog
below. Use the published request/progress/resource deadline as applicable.
All cases use the context-manager cleanup above; crash cases additionally join
the upload worker before restarting. Define literal expected results for oracle
unit tests without importing reference code or deriving expectations from it.

### Fixed subcase catalog

The catalog has **69 cases: P0 = 8, P1 = 42, P2 = 19**. Family weights remain
equal within P1/P2 regardless of these counts. Case IDs are scoped by family.
The last column identifies a private negative control or injected bad response
that the corresponding assertion must reject; it is not permission to inspect
candidate source. Hand-authored oracle fixtures can cover protocol errors;
storage/concurrency controls must also exercise an actual broken server.

| Family / case ID | Setup and actions | Required oracle | Negative control |
|---|---|---|---|
| `p0.cli/roundtrip` | CLI create bucket, upload bytes, download to another file | Exit 0, exact byte equality, silent transfer stdout | CLI reports success without transfer |
| `p0.cli/list-cleanup` | CLI list two objects, remove both, list, remove bucket | Sorted JSON lines with exact fields; empty second list; successful deletion | CLI omits an object or ignores removal |
| `p0.cli/errors` | Download absent key; separately invoke invalid arguments | Exit 1 vs 2, non-empty stderr, no success stdout | Always exits 0 |
| `p0.http/crud` | Create bucket, PUT/GET/HEAD/DELETE, GET again | Exact statuses/body; final 404; HEAD body empty | DELETE is a no-op |
| `p0.http/headers` | PUT known bytes with explicit type, GET/HEAD | MD5, byte length and type match; valid modification date | Stale or fabricated headers |
| `p0.http/empty-missing` | PUT empty object, GET/HEAD; access absent bucket/key | Valid empty-object MD5/length; documented 404 errors | Confuses empty with absent |
| `p0.restart/objects` | Store Unicode/nested keys, restart, GET/list | All exact objects and names survive | In-memory-only index |
| `p0.restart/metadata` | Store explicit type, capture metadata, restart | Buckets, ETags, sizes, types and timestamps unchanged | Reconstructs wrong metadata on boot |
| `p1.range/closed` | GET/HEAD range 2-5 of known body | 206, four expected bytes for GET, exact range headers | Off-by-one end |
| `p1.range/open-suffix` | GET open and suffix ranges, including oversized suffix | Exact slices, clipped bounds, matching lengths | Treats suffix as start offset |
| `p1.range/bounds` | Last byte, start at length, reversed range, zero suffix, empty object | Last-byte range succeeds; invalid cases 416 with `bytes */n` | Accepts unsatisfiable range |
| `p1.range/after-replacement` | Replace with different-length body, repeat a range | Slice and headers from replacement only | Cached old size/body |
| `p1.conditional/etags` | GET/HEAD with matching, nonmatching and wildcard validators | Documented 200/304/412 and no forbidden response body | Ignores validators |
| `p1.conditional/dates` | Use returned modification date and times before/after it; invalid date | Second-resolution 200/304/412; invalid date ignored | Wrong boundary or invalid-date handling |
| `p1.conditional/precedence` | Conflicting paired ETag/date headers; matching If-None-Match plus Range | ETag overrides its paired date; 304 wins over Range | Evaluates date/range first |
| `p1.conditional/after-restart` | Replace A with B, restart, query using A/B ETags | Old tag does not identify B; new tag and metadata survive | Keeps A's ETag |
| `p1.multipart/roundtrip` | Initiate, upload parts out of order, list, complete with unordered unique list | Numeric part/assembly order; exact bytes, full-body MD5 and initiation type | Concatenates arrival order |
| `p1.multipart/stale-retry` | Execute the detailed recipe below | Failed completion preserves A; corrected completion succeeds | Publishes before validating |
| `p1.multipart/invalid-complete` | Independent uploads: missing part, duplicate number, malformed XML, empty list | 400, old object unchanged, session still usable | Accepts invalid list or destroys session |
| `p1.multipart/abort-scope` | Wrong-key/bucket use of ID; abort real session; reuse ID | Wrong scope 404; old objects unchanged; aborted ID 404 | Global unscoped ID lookup |
| `p1.multipart/abort-during-part` | Abort a session while a large part body is still being read | Part upload terminates with 404, old object unchanged, aborted ID 404 | Abort races into a 500 or publishes the part |
| `p1.multipart/concurrent-replace` | Start completion with current ETags; re-upload part 2 during assembly | Success must use the validated part; no mixed/partial version | Part file replaced after ETag validation |
| `p1.multipart/concurrent-complete` | Two completions of the same upload start together | Exactly one 200, one 404; one exact completed object; upload consumed | Both completions report success |
| `p1.multipart/abort-complete-race` | Start completion then abort the same upload | One operation wins: 200 completion/404 abort with new object, or 404 completion/204 abort with old object; never 5xx | Abort wins but completion still publishes, or abort races into a 500 |
| `p1.multipart/list-during-complete` | List parts while a large completion is assembling | List is 404 or returns the pre-completion parts; never an empty 200 after the upload is consumed | List waits on the session lock and returns an empty 200 |
| `p1.multipart/list-during-abort` | Abort/list while a gated part upload holds the session | Abort 204; list is 404 or returns the pre-abort part; never an empty 200 | List waits behind abort and returns an empty 200 |
| `p1.multipart/parameter-errors` | Combined/invalid multipart parameters, invalid part bounds | Invalid/combined action parameters are 400 and leave the session usable | Action parameters ignored or session consumed by an invalid request |
| `p1.listing/pages` | Seed known keys; enumerate with page sizes 1 and 2 | Exact sorted union, no duplicates, proper truncation/counts | Drops a page-boundary key |
| `p1.listing/groups` | Keys `a/1`, `a/2`, `b/1`, `c`; delimiter `/`, small pages; prefix `a/` | Expected grouped prefixes/objects; prefixes consume page slots | Paginates before grouping |
| `p1.listing/tokens-restart` | Finish listing after restart without mutation; malformed/wrong-scope token; invalid max-keys | Same complete result for valid token; bad inputs 400 | Tokens depend on process-local cursor |
| `p1.listing/list-type` | Unsupported/empty `list-type`; valid `list-type=2` | Bad inputs 400; valid listing works | Ignores `list-type` |
| `p1.listing/max-keys` | Empty/zero/oversized/non-numeric `max-keys`; valid small page | Bad inputs 400; valid page honors `max-keys` | Treats an empty `max-keys` as omitted |
| `p1.listing/page-limits` | Page unions for limits 1, 2 and 1000 | Sorted union, correct truncation/counts | Drops or duplicates a page-boundary key |
| `p1.concurrency/coherent` | Bounded overlapping PUTs/GETs with distinct body/type pairs, then serialized final PUT | Every read is one coherent pair; final write wins | Body and metadata published separately |
| `p1.concurrency/no-lost-writes` | Concurrent different-key writes; wait for all acknowledgments; restart | Every acknowledged key survives with exact content | Commits a pre-upload manifest snapshot |
| `p1.concurrency/progress` | Gated partial PUT plus old-object GET and unrelated PUT/GET | Independent requests finish before releasing upload gate | Global lock held across body read |
| `p1.concurrency/churn-replace` | Bounded overlapping PUTs and GETs on one key, repeated | Every GET is a complete coherent version; no transport truncation | Deletes the old data file before concurrent readers open it |
| `p1.concurrency/churn-delete` | Bounded PUT/DELETE churn with concurrent GETs | Every GET is 404 or one complete coherent version; no truncation | Delete/overwrite races truncate a reader |
| `p2.overwrite/incomplete-disconnect` | Send prefix of declared body, half-close write side; collect failure/close | Old object/metadata unchanged; later PUT succeeds | Publishes short body despite read error |
| `p2.overwrite/incomplete-kill` | Kill with deliberately unfinished body; restart | Old object/metadata intact, subsequent write succeeds | Truncates published file before body completion |
| `p2.overwrite/publication-crash` | Bounded unacknowledged full-overwrite kill trials, different body/type pairs | Old or new coherent pair only, never 404 on existing key | Torn body/manifest publication |
| `p2.overwrite/acknowledged` | Fully receive successful PUT response, kill, restart | New version required, not old-or-new | Acknowledges before committing |
| `p2.multipart/cross-feature` | Complete over A, range/conditional GET, restart, list and CLI download | All surfaces describe exact completed bytes/metadata | Completion bypasses ordinary object metadata |
| `p2.multipart/crash-existing` | Bounded completion kill trials over A, with unacknowledged and acknowledged cases | Old/new pair before acknowledgment; new only after | Deletes A before new publication |
| `p2.multipart/crash-absent` | Same trials on a fresh key | 404/full new before acknowledgment; full new after | Exposes partially assembled bytes |
| `p2.isolation/identity` | Store `a` and `a/b`, same key in two buckets, Unicode keys | Independent exact readback/listing | Filesystem-prefix collision |
| `p2.isolation/escaping` | Store literal `%2F`, slash and XML-sensitive characters as distinct keys | Exactly-once URL decoding; exact XML round-trip | Double decoding or unescaped XML |
| `p2.isolation/traversal` | Raw/encoded `..` segments with neighboring-object and outside-dir sentinels | Reject with 400 or safe independent storage; no changed neighbors/outside writes | Path join escapes or aliases |
| `p2.perf/stream-roundtrip` | One streamed 128 MiB PUT/GET | Exact size/digest within total workload deadline | Truncates transfer |
| `p2.perf/range` | Reuse that object; fetch known range | Exact slice, 206 and correct headers | Reads wrong segment |
| `p2.perf/rss-budget` | Observe the same successful workload's server high-water RSS | Below frozen cap; workload must have completed correctly | `io.ReadAll` on large body |
| `p1.batch/copy-rename-delete` | Run an in-order copy/rename/delete chain, then delete a key and an absent key | 200 empty body; exact remaining keys, bytes, ETags, types and listing; copy timestamps not older | Applies operations out of order or to stale state |
| `p1.batch/preconditions` | IfMatch match/mismatch, absent Source, failure in last op, working-copy delete, wildcard | 412 on failure with the bucket snapshot unchanged; 200 and exact effects on success | Checks precondition outside the commit or leaves partial effects |
| `p1.batch/errors` | Malformed/empty/wrong-root XML, 33 ops, unknown op, missing fields, equal source/target, empty IfMatch, bad routing | 400 `InvalidArgument` with no mutation; object-path and combined-parameter batch 400; absent bucket 404; 32-op boundary 200 | Accepts invalid XML or applies part of a rejected batch |
| `p1.batch/independence` | Copy then overwrite/delete source; range, empty, multipart and fan-out copies; restart | Targets retain exact bytes, ETags, types and ranges; every surviving copy rereads after restart | Shared blob removed when a source is deleted or overwritten |
| `p1.batch/cross-feature` | Copied object under range, conditional, HEAD and listing; session across rename/delete; bucket delete guard | All protocol surfaces describe the copied version; sessions complete after rename/delete; empty bucket deletes | Copy bypasses range/conditional metadata or kills sessions |
| `p1.batch/atomic-visibility` | Writer swaps two keys with a three-rename batch while four readers list and GET | Every read is one complete version; both keys always present with distinct bodies/ETags; no temp key | Readers observe a half-applied swap |
| `p1.batch/concurrent-batches` | Concurrent renames, precondition conflict, fan-in/fan-out with reads, batch vs ordinary PUT | No 5xx; final state is one valid serial order; exactly one precondition winner; every batch 200 | Lost update or precondition evaluated without the commit lock |
| `p1.histories/objects` | Replay 80 generated put/get/head/delete/batch/bucket operations | Every response matches the documented state model; final state and listings agree | Partial rule implementation diverges from the model |
| `p1.histories/multipart` | Replay 50 generated multipart/ordinary operations across session slots | Part lists, completion validation, replacement and dead-session responses match the model | Multipart lifecycle diverges from the model |
| `p1.histories/listing` | Mutate both buckets, then exercise 16 paginated or invalid listings | Page items, counts, truncation, token shape, echoes and unions match the model | Pagination/grouping diverges from the model |
| `p1.histories/restart` | Replay 80 operations with three restarts and a pre-restart token reused after restart | All responses match the model across restarts; token continuation is exact | Process-local state is lost or tokens invalidated |
| `p1.histories/concurrent` | Run 12 concurrent operation groups with quiescent probes | Each group admits a serialization consistent with real-time order and every probe matches a candidate state | Lost writes or non-serializable mixed versions |
| `p2.batch/kill-during-swap` | Kill during repeated three-rename swap batches, restart each trial | Keys are a permutation of the two bodies, no temp key, coherent headers, store usable after recovery | Torn batch publication or partial rename visible |
| `p2.batch/kill-after-ack` | Acknowledge copy/rename/delete batch, kill immediately, restart | Every acknowledged effect survives; source keys gone and targets exact | Acknowledges before the atomic commit |
| `p2.batch/blob-lifetime` | Copy then delete/overwrite source, kill during an unrelated upload, fan-out and restart | Surviving copies keep exact bytes and ranges; overwritten keys are new versions; final listing exact | Blob freed while a copy still references it |
| `p2.faults/put-preserves-old` | Oversized PUT on a 32 MiB filesystem over an existing object | Failure is 5xx or a closed connection; old object and headers unchanged; server keeps serving; restart preserves state | Truncated body published as success |
| `p2.faults/space-released` | Repeat failed oversized PUTs, then two sequential 8 MiB PUTs; restart | No partial key appears; both 8 MiB writes succeed; restart preserves them | Failed writes leak staged temp space |
| `p2.faults/multipart-staging` | Fail an oversized part upload, then list parts, upload a real part and complete | No phantom part; existing part intact; completion exact; later large write succeeds | Part registered before its body is stored |

Do not expand compound cases into extra scored IDs without changing this
catalog and manifest together. The three performance cases share one workload,
not three large transfers. Missing setup/completion cannot earn RSS credit just
because a failed server consumed little memory.

### Fully specified multipart failure recipe

Use an existing bucket/key, object A with a distinct content type, and small,
different byte strings B, C and D. Compute ETags independently with Python MD5.

1. PUT A; assert 200 and save its GET/HEAD metadata.
2. Initiate upload U for the same key; upload part 1 = B and part 2 = C.
3. Replace part 2 with D; list parts and assert part 2 now has D's ETag/size.
4. Complete U with part 1's current ETag and C's stale ETag; require 400.
5. GET/HEAD must still describe A exactly, including its unchanged timestamp.
   Listing parts must still describe B and D.
6. Complete U with B's and D's current ETags; require 200.
7. GET must equal `B || D`; ETag is MD5 of that entire sequence, size is its
   length and content type is the initiation type. U now returns 404.

If step 2 or 3 cannot establish the setup, fail the case; do not claim step 5
passes merely because A was never touched. Run the same recipe against a
variant that begins replacing A before validating every part; it must fail.

### Gated upload and crash recipes

For progress testing, open a separate connection, declare a nontrivial
`Content-Length` and request `Expect: 100-continue`. Send headers, consume the
interim response, then send a prefix and deliberately withhold the remaining
bytes. Use the interim response to establish body-read entry before testing
progress, as required by section 3. It is a coordination aid, not proof of a
storage commit. A final rejection or missing interim response fails setup;
do not proceed and award progress credit against an upload that never began.

While that request remains unfinished, GET the old object and PUT/GET a distinct
key using separate connections. Collect their responses under the published
progress deadline **before** releasing/closing the upload. Clean up the stalled
socket even when another request fails. Do not use a client-side "started"
event as evidence that the server entered a handler.

For incomplete-disconnect, half-close the socket's write side after only a
prefix, then wait for the response or connection close under a deadline before
readback. No successful PUT response is allowed. For incomplete-kill, keep the
body unfinished, kill the server PID and wait for termination before restart.

For publication-crash trials, use fresh state per trial and only one publishing
request. Log bytes sent, whether a complete success response was received, kill
time, process exit and post-restart observations. An acknowledgment means the
client consumed the complete successful response before the kill barrier.
Uncertain delivery is unacknowledged, not failed. Arbitrary delays may sample
windows but cannot establish that a particular commit step was interrupted.
Zero useful overlap in a trial batch is an evaluator coverage warning, not
evidence that recovery was tested; apply the sensitivity gate from section 6.

### Work packages and stopping gates

The command interfaces below are **to be implemented**, not claims that these
files/tools already exist. Run Linux-dependent cases in the pinned evaluation
image with network disabled.

| Package | Files/scope | Gate before proceeding |
|---|---|---|
| A: contract and oracles | Candidate prompt, manifest, `tests/test_oracles.py` | Catalog has 12 families/51 cases; literal HTTP/XML/score fixtures agree with section 3; independent contract review resolves contradictions |
| B: P0 vertical slice | Seed CLI/HTTP/store; `tests/helpers.py`, `run.py`, `test_runner.py`; P0 cases | Seed builds; all eight P0 cases pass; server lifecycle, timeouts, result writing and cleanup have deliberate-failure coverage |
| C: deterministic reference | Reference storage recipe and deterministic P1 cases | Reference passes P0 plus deterministic protocol cases; stale-retry control is detected; no crash tests or harness rewrite yet |
| D: adversarial evaluator | Progress/crash/isolation/performance cases, private broken variants | All reference cases pass; repeated subset and negative-control gates pass; resource values measured/frozen; independent oracle/publication review resolved |
| E: harness integration | `bench.py`, Docker image, scoring regression tests | Correct fixed-denominator scoring, diagnostic-result rejection, candidate mount isolation and separate Locker artifacts; local plumbing run succeeds |
| F: calibration | Existing commands and generated calibration artifacts | Only now spend on the six screening runs; report uncertainty and actual budget usage |

Each package may require several small edits, but must not silently spill into
later packages. In particular, do not "fix" a reference failure by weakening an
assertion, copying hidden cases into the public smoke suite, or making the seed
the complete reference.

Use these stable validation entry points:

```bash
# From seed or reference, respectively:
go test ./...
go build -trimpath -o bin/locker ./cmd/locker

# From repository root; evaluator/helper/oracle unit tests:
python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'

# Runner accepts an absolute binary path and creates its own temporary data:
python3 tasks/locker/tests/run.py --binary /absolute/path/to/locker \
  --manifest tasks/locker/tests/manifest.json --seed 1 --output /tmp/locker-result.json

# Author-only diagnostic subset; not eligible for leaderboard ingestion:
python3 tasks/locker/tests/run.py --binary /absolute/path/to/locker \
  --manifest tasks/locker/tests/manifest.json --seed 1 \
  --family p1.concurrency --output /tmp/locker-diagnostic.json

# After harness integration:
python3 -m unittest discover -s tests
```

`--family` selects one complete family for authoring and marks the result
`diagnostic: true`; the harness must reject diagnostic results for scoring.
Full runs set `diagnostic: false`. Repeat diagnostic invocations for stability
work, keeping each result/log separate; never average away failing attempts.
Before declaring a package done, report changed paths, exact commands/outcomes,
remaining failures and any unresolved contract decision. Passing code generated
from the same mistaken assumption is not a substitute for the independent
review gates.
