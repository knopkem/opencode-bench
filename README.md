# opencode-bench — Locker

This branch (`hard_task`) is the **Locker** hard-task benchmark: a local Go
object store with an HTTP API, CLI, multipart state transitions, range and
conditional requests, concurrent publication and process-kill recovery.

SpriteForge and its community leaderboard remain the `main`-branch suite.
Locker has its own results file and leaderboard, and no community submission
workflow until the task is calibrated.

## What the model does

Each run copies `tasks/locker/seed/` into a fresh workdir, adds
`tasks/locker/PROMPT.md` as `PROMPT.md`, and gives the model one task: implement
the documented storage and protocol guarantees in the existing Go service.
The seed is a working v0 that passes P0; it is deliberately naive (direct
writes, in-place metadata, no locking, no Range/conditional handling, 501
multipart, simple listing, read-all uploads).

The model may restructure the storage layer but must keep the CLI/HTTP contract.
The hidden suite is black-box and is mounted only after the agent exits.

## Quick start

```bash
./bench.py build                 # build the Locker image once
./bench.py check                 # prompt, seed, manifest, limits, image
./bench.py models --provider NAME
./bench.py run --model provider/model
./bench.py run --model provider/model --variant all --keep-going
./bench.py run --model provider/model --timeout 1080 --idle-timeout 300 --reps 2
./bench.py status
./bench.py preview --run RUN_ID  # run the built bin/locker server
./bench.py leaderboard           # serve + open locker-leaderboard.html
./bench.py leaderboard --write   # write the file only
./bench.py rescore --run RUN_ID  # rebuild + rerun the hidden suite
./bench.py backfill --all        # recompute effort/token metrics
./bench.py report                # table plus failing subcases
python3 -m unittest discover -s tests
```

Docker/OrbStack must be running (`docker info`). `--engine podman` uses the same
CLI shape.

## Budget and grading

The prompt asks for roughly 12 minutes of work, with an 18-minute hard cap and
a 5-minute idle timeout. The model gets the same seed, toolchain and time
budget every run.

- P0 is a regression gate and contributes no quality points.
- P1 and P2 are nine equally weighted families. Family credit is passed
  required subcases divided by the fixed manifest total.
- Quality is the mean of the nine P1/P2 family credits.
- Eligible = successful build, every P0 subcase passed, quality ≥ 0.80.
- All P0 + all P1 + no P2 credit scores `5/9 ≈ 0.556`.

The hidden manifest is `tasks/locker/tests/manifest.json`: 12 families,
51 subcases (P0 8 / P1 30 / P2 13), suite `locker-v3`, fixture seed
`20261106`.

## Resource limits

| Limit | Value |
|---|---|
| Streaming fixture | 134,217,728 bytes (128 MiB) |
| Server high-water RSS cap | 100,663,296 bytes (96 MiB), Linux `VmHWM` |
| Large PUT + GET + range deadline | 180 s |
| Progress deadline during a stalled upload | 10 s |
| Individual request deadline | 60 s |
| Startup/readiness deadline | 15 s |
| Bounded crash trials | 6 |

The RSS figure is about the server process, not the Python test client.

## Artifacts

Generated and ignored by git:

- `runs/<run_id>/` — workdir, `agent.log`, `build.log`, `test.log`,
  `results.json`, `score.json`, `report.md`
- `results-locker.csv`
- `report-locker.md`
- `locker-leaderboard.html`

`runs/` may also contain SpriteForge-era runs from `main`; the Locker CSV only
contains runs scored by this branch's harness.

## Hidden suite

```bash
# Reference validation, from the repo root:
cd tasks/locker/reference
go test ./...
go build -trimpath -o bin/locker ./cmd/locker
cd ../../..
python3 tasks/locker/tests/run.py \
  --binary "$PWD/tasks/locker/reference/bin/locker" \
  --manifest tasks/locker/tests/manifest.json \
  --seed 20261106 --output /tmp/locker-result.json

# Author-only diagnostic (result is marked diagnostic and is not ingestible):
python3 tasks/locker/tests/run.py --binary ... \
  --manifest tasks/locker/tests/manifest.json \
  --family p1.concurrency --output /tmp/locker-diagnostic.json

# Oracle tests and injected-fault sensitivity controls:
python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'
python3 tasks/locker/tests/negative_controls.py \
  --binary tasks/locker/reference/bin/locker
```

The reference and hidden tests are never mounted into an agent workdir.

## Model selection

`./bench.py models` prints a provider summary from the local `opencode models`.
Use `--provider NAME` to list one provider and `--all` to print every model
reference. `run` with no `--model` asks for a provider, then shows a numbered
menu. Variants, thinking levels and API keys come from the local OpenCode
config, not this repo.

## Repository map

```text
bench.py                         harness and scoring
docker/locker.Dockerfile         Ubuntu 24.04 + Go + Python + OpenCode
tasks/locker/PROMPT.md           candidate-visible contract
tasks/locker/seed/               candidate-visible naive v0
tasks/locker/reference/          author-only complete implementation
tasks/locker/tests/              hidden suite, manifest, oracles, controls
tasks/locker/CALIBRATION.md      author validation evidence and pending calibration
tests/test_bench.py              harness metric/scoring regression tests
```

See `AGENTS.md` for invariants and validation gates. See `HARD-TASK-PLAN.md`
for the design rationale and calibration plan.
