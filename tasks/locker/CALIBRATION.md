# Locker implementation and evaluator validation

Status: authoring/evaluator validation complete; paid model calibration is
pending. This file is not visible to candidates.

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
