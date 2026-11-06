# Locker implementation and evaluator validation

Status: `locker-v4` is implemented and frozen (see `## locker-v4` below); the
earlier sections are the v3 history. Paid model calibration is still pending.
This file is not visible to candidates.

## Frozen inputs

- Suite `locker-v3`, fixture seed `20261106`.
- 12 families, 51 subcases: P0 8, P1 30, P2 13.
- Limits: 134,217,728-byte stream fixture; 100,663,296-byte server `VmHWM`
  cap; 180 s large-workload deadline; 10 s progress deadline; 60 s request
  deadline; 15 s startup deadline; 6 bounded crash trials.
- Image: Ubuntu 24.04, distro Go 1.22.2, Python 3.12.3, OpenCode 2.2.5.

## Reference

Commands used:

```bash
cd tasks/locker/reference
go test ./...
go build -trimpath -o bin/locker ./cmd/locker
python3 tasks/locker/tests/run.py \
  --binary "$PWD/tasks/locker/reference/bin/locker" \
  --manifest tasks/locker/tests/manifest.json \
  --seed 20261106 --output /tmp/locker-result.json
```

Results:

- macOS authoring run: 51/51 subcases, exit 0.
- Pinned Linux image: 51/51 on full v3 runs (plus earlier v2/v1 runs).
- Diagnostic stress, 50 repetitions each:
  - `p1.concurrency`: original 3-case family 50/50; hardened 5-case family 10/10.
  - `p2.overwrite`: 50/50 runs passed all 4 subcases.
  - `p2.multipart`: 50/50 runs passed all 3 subcases.
- Evaluator unit tests: 8/8 (`test_oracles.py`).
- Injected-fault controls (`negative_controls.py`) all detected:
  - split body/metadata publication -> `p1.concurrency/coherent` fails;
  - lost unrelated write -> `p1.concurrency/no-lost-writes` fails;
  - global lock held across body input -> `p1.concurrency/progress` fails;
  - object replaced before multipart validation -> `p1.multipart/stale-retry`
    fails.

The process-kill cases use `SIGKILL`. They check process-kill recovery, not
power-loss durability or fsync ordering.

## Seed baseline

Built from `tasks/locker/seed` and run against the frozen suite in the pinned
Linux image:

| Tier | Family | Passed |
|---|---|---:|
| P0 | cli | 3/3 |
| P0 | http | 3/3 |
| P0 | restart | 2/2 |
| P1 | range | 0/4 |
| P1 | conditional | 0/4 |
| P1 | multipart | 0/11 |
| P1 | listing | 0/6 |
| P1 | concurrency | 1–2/5 (race-dependent) |
| P2 | overwrite | 4/4 |
| P2 | multipart | 0/3 |
| P2 | isolation | 0/3 |
| P2 | perf | 1/3 |

All P0 subcases pass. Across five seed runs, concurrency scored 1/5 or 2/5
depending on the write race, so total subcases were 14–15/51 and quality was
`0.170–0.193`. The unchanged seed is ineligible in every run. The overwrite
family is measured rather than assumed to fail; the naive direct-write seed
happens to preserve the old version in the sampled kill windows.

## Hardening after the first local model run

The first local run (`strata/swift-1.5-iq3_xxs`, medium) passed the original
39-case suite 39/39. Code inspection and targeted probes found three real
weaknesses that the original suite did not exercise:

- a completed/aborted multipart session could be mutated by a concurrent
  part upload or completion;
- a GET could observe old metadata and then race with deletion of the old data
  file, producing a truncated 200 response under PUT/DELETE churn;
- invalid/combined multipart action parameters and some listing parameters
  were accepted or ignored.

The v2 suite added ten subcases: `abort-during-part`, `concurrent-complete`,
`concurrent-replace`, `abort-complete-race`, `parameter-errors`,
`list-type`, `max-keys`, `page-limits`, `churn-replace` and `churn-delete`.
The binary produced by the first run scored 40/49 on v2 (P0 8/8; P1 range
4/4, conditional 4/4, multipart 4/9, listing 4/6, concurrency 3/5; P2 13/13),
quality ≈ 0.857.

A fresh model run on the v2 prompt/suite adapted to those races and scored
48/49: it failed only `POST ...?uploadId=...&partNumber=...` (returned 200
where combined action parameters must be 400). The v3 suite adds
`list-during-complete` and `list-during-abort`; the v2 run's binary returns an
empty 200 part list after the upload is consumed in both cases instead of 404.
On v3 that binary scores 48/51 (quality ≈0.970), still eligible.

A fresh v3 model run scored **50/51** (quality 0.99). It fixed the session-list
races; its only failure was combined multipart parameter validation
(`GET ...?uploadId=...&partNumber=...` returned 200 instead of 400). However,
the run hit the **18-minute hard wall cap** (since raised to 60 minutes) (`outcome: timeout`, no
`COMPLETION.md`) after 33 steps. The task is therefore score-saturated for this
model and at the budget boundary: further hardening should add a genuinely
different interaction dimension or change the timeout/eligibility policy, not
more parser gotchas. The current eligibility rule remains build + all P0 +
quality ≥0.80, so this run is marked eligible despite the timeout.


The churn cases are stress tests: four bounded attempts per case. Five
candidate runs each failed at least one churn case; this is measured detection,
not a claim that every interleaving is deterministic. The reference passes all
attempts and the full suite.

## Harness

- `./bench.py check` validates the prompt, seed, manifest family/subcase
  counts, frozen limits, hidden runner files and the image.
- A rescore against the reference produces quality 1.00, 51/51 subcases,
  `eligible: true`, and writes the Locker-only CSV/report/leaderboard artifacts.
- `python3 -m unittest discover -s tests` passes.

## Pending

- Six paid screening runs (two frontier models and one mid-tier model, two
  reps each) with matched seeds.
- Actual cost/pricing capture. The harness records wall/idle caps and marks
  unknown cost explicitly (`cost_known`), but does not enforce a dollar or token
  ceiling; the provider bill can exceed a target.
- Independent human/model review of the wire contract and oracle oracles.
- Any decision to publish a frontier leaderboard or community submissions.

## locker-v4

Catalog changed from 51 to 69 subcases; v3 results are not comparable.

### WP1 Contract

commands run:

```bash
python3 -m py_compile bench.py
python3 -m unittest discover -s tests
python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'
python3 -c "import json;json.load(open('tasks/locker/tests/manifest.json'))"
./bench.py check
cd tasks/locker/seed && go build -trimpath -o bin/locker ./cmd/locker && python3 scripts/smoke.py
```

Reference full suite (pinned image, `--network none`, built Linux binary):
51/69 passed; the 51 existing cases pass and the 18 new placeholder cases fail
with `case not implemented yet`. Seed smoke: 7 public checks fail, including
exactly the two new `batch copy` and `batch copy visible` checks (the seed
returns 405 for `POST /b?batch`).

deviations from HARD-TASK-V4-PLAN.md: `AGENTS.md` intro updated so it no longer
claims the v3 invariants apply; `README.md` budget line also changed from 12 to
20 minutes; `HARD-TASK-PLAN.md` "Implementation status" line folded into the
existing v4 paragraph. No code/semantics deviations.

prompt clarifications: none; all §4.1 texts applied verbatim.

open questions: none.

### WP2 Reference

commands run:

```bash
cd tasks/locker/reference && go vet ./... && go test ./...
docker run ... opencode-bench-locker:latest go build -trimpath -o /out/locker ./cmd/locker
docker run ... python3 -B .../run.py --binary /bin-under-test/locker \
  --manifest .../manifest.json --seed 20261106 --output /out/ref.json
```

results: `go vet` and `go test` pass (`protocol`, `server`, `store`). Linux build
succeeds. Full suite against the new binary: existing 51/51 pass, new
placeholder cases 0/18 fail with `case not implemented yet`, as expected between
packages. Manual HTTP probe: batch copy/delete returns 200 and the copy is
readable; failed `IfMatch` returns 412; `batch` on an object path returns 400.

deviations from HARD-TASK-V4-PLAN.md: the nine `LOCKER_FAULT` switches from
§5.6 are implemented with the mutant campaign in WP7 rather than WP2; WP2's gate
does not exercise them. Added `internal/store/batch_test.go` and
`internal/protocol/batch_test.go` covering working-copy order, manifest-byte
stability on 412, rename chains, absent-key delete, copy/source independence,
`errWriter` attribution, parser limits and every malformed shape.

prompt clarifications: none.

open questions: none.

### WP3 State model

commands run:

```bash
python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'
```

results: 19/19 oracle and state-model unit tests pass. New
`tasks/locker/tests/state_model.py` and `test_state_model.py` cover range
clipping/suffix/416 (including empty objects), conditional precedence, grouped
pagination, multipart replacement/stale completion/abort, batch working-copy
order and rollback, structural errors, and the live-upload bucket-delete guard.
The model was written from `PROMPT.md` and reuses `oracles.py` for range,
listing and conditional decisions.

deviations from HARD-TASK-V4-PLAN.md: none.

prompt clarifications: none.

open questions: none.

### WP4 Batch cases

commands run:

```bash
# build reference for Linux, then 10x each family in the pinned image
for fam in p1.batch p2.batch; do
  for i in $(seq 1 10); do
    docker run ... run.py --binary /bin-under-test/locker --family "$fam" \
      --output "/out/$fam-$i.json"
  done
done
```

results: `p1.batch` 7/7 and `p2.batch` 3/3 in all 10 consecutive diagnostic
runs each (20/20 runs, zero flakes). Seed binary: `p1.batch` 0/7 and
`p2.batch` 0/3; every case fails with 405 `MethodNotAllowed` from
`POST /b?batch`, so no assertion is vacuous.

deviations from HARD-TASK-V4-PLAN.md: added a shared `expect_present` assertion
helper and a `_run_parallel` barrier helper; atomic-visibility uses the plan's
dedicated writer plus four readers with their own loop rather than the generic
helper. No semantic deviations.

prompt clarifications: none.

open questions: none.

### WP5 Faults

commands run:

```bash
# 10x in the pinned image with the frozen tmpfs
docker run ... --tmpfs /scratch:rw,size=33554432 ... run.py \
  --scratch /scratch --family p2.faults --output /out/faults-$i.json
# full run without --tmpfs/--scratch
docker run ... run.py --binary ... --output /out/results.json
./bench.py check
python3 -m unittest discover -s tests
python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'
```

results: `p2.faults` 3/3 in all 10 runs. A full run without `--scratch` exits
3, writes `results.json`, and marks all three faults cases
`infrastructure: no scratch filesystem` while every other family runs.
`./bench.py check` passes the tmpfs preflight; unit tests 13/13 and 19/19.
Seed binary on the fault family: 0/3 — `put-preserves-old` publishes a torn
body, `space-released` leaves the failed 48 MiB write on the final path so the
next 8 MiB PUT is 500 ENOSPC, and `multipart-staging` is 501.

deviations from HARD-TASK-V4-PLAN.md: the tmpfs probe uses `args.engine` instead
of the literal `docker`; the faults family receives its infrastructure error via
`run_suite(scratch_error=...)` so it can mark the three cases without starting a
server.

prompt clarifications: none.

open questions: none.

### WP6 Histories

commands run:

```bash
python3 tasks/locker/tests/history_sweep.py --binary <reference> --seeds 200
# 10x in the pinned image
docker run ... run.py --binary /bin-under-test/locker --family p1.histories \
  --output /out/hist-$i.json
```

results: `p1.histories` 5/5 in 10/10 Docker runs, 0.7–0.8 s of case time per
suite (well under 20 s). 200-seed sweep: 1000/1000 histories passed, zero
model/reference disagreements. Seed binary: 0/5 (objects/listing/restart/
concurrent diverge on batch/range/conditional/listing semantics; multipart is
501).

deviations from HARD-TASK-V4-PLAN.md: `history_check.py` is self-contained for
batch/completion XML instead of importing `test_runner` (avoids a circular
import). Every generator prepends the two `create_bucket` ops; for `listing`
that leaves 22 mutation ops + 16 listing ops inside the fixed 40, preserving the
first-24/last-16 phase split. HEAD error responses skip the error-XML body check
as the prompt requires. The sweep tool starts a fresh server per case.

prompt clarifications: none; the prompt already states whole-second HTTP dates,
HEAD bodies, and the batch/listing rules the model encodes.

open questions: none.

### WP7 Mutants

commands run:

```bash
docker run ... bash -lc 'go vet ./... && go test ./... && go build ...'
docker run ... --tmpfs /scratch:rw,size=33554432 ... \
  python3 -B negative_controls.py --binary /bin-under-test/locker \
  --scratch /scratch --repeat 10
docker run ... run.py --binary ... --scratch /scratch --output /out/results.json
```

results: all 13 controls detected naturally 10/10; both amplified variants
(`ack-before-publish-amp`, `metadata-before-blob-amp`) 10/10; 0 missed, 0 errors,
0 skipped; exit 0. `go vet`/`go test` pass with the switches present and unset.
Full hidden suite remains 69/69 (`test_ok`, non-diagnostic).

deviations from HARD-TASK-V4-PLAN.md: `metadata-before-blob` is implemented in
`PutObject` (where the blob rename happens) rather than in `commitObject`, with
the same detectors. `stale-snapshot-clone` yields once between the two lock
windows to widen the natural schedule. `ack-before-publish` sends
`Content-Length: 0` with the early 200 so a client can observe the ack before
publication. The control runner always measures an amplified variant when one is
defined (not only after a natural miss) so `NEGATIVE_CONTROLS.md` can report both
rates. `Open` now rejects unknown `LOCKER_FAULT` names, as the plan assumed, and
a Go test covers the accepted list. Fault cases turn transport errors on required
small writes into `CaseFailure` so `leak-temp-on-error` is a clean detection.

prompt clarifications: none.

open questions: none.

### WP8 Release

Reference commit: `78ead33` (WP7 fault-switch commit; the reference did not
change during WP8). Image: `opencode-bench-locker:latest`, ID
`sha256:dd5a3f4123ffbc1f50ab174280ef5ff11230e36a39aba77a3299a9c58683515b`.

commands run:

```bash
python3 -m py_compile bench.py tasks/locker/tests/*.py
python3 -m unittest discover -s tests
python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'
./bench.py check
# five timed full runs in the pinned image with --tmpfs /scratch
# 30 diagnostic runs each of p1.batch, p2.batch, p2.faults, p1.histories,
#   p1.concurrency, p2.overwrite, p2.multipart
# negative_controls.py --scratch /scratch --repeat 10
# history_sweep.py --seeds 200
# full seed run with --tmpfs /scratch
./bench.py rescore --run 20261106T150936Z-locker-strata-swift-1.5-iq3_xxs-medium-r1
./bench.py run --model deepseek/deepseek-flash --dry-run
```

results:

- Static: `py_compile` OK; harness tests 13/13; oracle/state-model tests 19/19;
  `./bench.py check` passes (16 families, 69 subcases, new limits, tmpfs probe).
- Five timed full reference runs: 69/69 each, `diagnostic: false`,
  `suite_version: locker-v4`; wall times 19.81, 19.14, 21.50, 19.57, 18.45 s;
  median **19.57 s** (limit 90 s).
- Flake gate: 30/30 clean for each of the seven families (210/210 diagnostic
  runs, zero failures).
- Mutants: 13/13 controls detected naturally 10/10; both amplified variants
  10/10; see `NEGATIVE_CONTROLS.md`.
- Sweep: 200 seeds × 5 cases = 1000/1000 histories, zero disagreements.
- Seed baseline: build OK, 8/8 P0, 15/69 total, quality **0.1333**, ineligible.
  Per family: P1 range 0/4, conditional 0/4, multipart 0/11, listing 0/6,
  concurrency 2/5; P2 overwrite 4/4, multipart 0/3, isolation 0/3, perf 1/3,
  batch 0/7, histories 0/5, faults 0/3. The three new families and every batch
  case fail as designed.
- Harness end-to-end: `./bench.py rescore` on a v3-era Locker workdir completed
  and produced a v4 score of 45/69, quality 0.69, not eligible; `./bench.py run
  --dry-run` prints `--tmpfs /scratch:rw,size=33554432` and `--scratch /scratch`.
- Independent review (fresh agent): list (B) — model batch rules without a
  prompt sentence — is **EMPTY** after the clarification below. List (A) is
  limited to behaviours intentionally outside a pure no-I/O state model:
  HTTP/XML shape and malformed XML, `Last-Modified`/commit-time metadata,
  version identity and observation isolation, crash/restart persistence,
  concurrent serialization/5xx/deadlock, and operational-failure/ENOSPC. Each
  is graded outside `state_model.py` by the batch, history, concurrency, crash
  or fault families.

prompt clarifications: the batch malformed-input bullet now also says "or an
`IfMatch` that is neither `*` nor one quoted token returns 400
`InvalidArgument`". This is required by `p1.batch/errors` (empty and bare
`IfMatch` bodies) and is mirrored into `HARD-TASK-V4-PLAN.md` §4.1(h). It is the
only prompt change made during implementation; §4.1(a)–(j) were otherwise
applied verbatim.

deviations from HARD-TASK-V4-PLAN.md: the flake-gate family list names
`p2.crash`, which does not exist in the v4 catalog; the gate was run over
`p2.overwrite` and `p2.multipart` (the two crash families) in its place.

open questions: none. Suite is frozen; the only remaining measurement is the
user's rerun of the saturating local model.
