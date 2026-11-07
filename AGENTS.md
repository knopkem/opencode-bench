# AGENTS.md

This branch (`hard_task`) holds the **Locker** hard-task benchmark. SpriteForge
and its community leaderboard remain the `main`-branch suite; do not wire Locker
submissions or CI until the task is calibrated.

`HARD-TASK-V4-PLAN.md` is the implemented, frozen build record for the
`locker-v4` hardening (batch, faults, histories, mutants). The current
`locker-v5` catalog extends operational-failure recovery; the invariants below
are the v5 catalog.

## Repository shape

- `bench.py` is the whole harness: run orchestration, Docker mounts, scoring,
  CSV/report/leaderboard generation and model selection. It is stdlib-only.
- `tasks/locker/seed/` is copied into each run and is what the model sees.
  `tasks/locker/PROMPT.md` is copied into the workdir as `PROMPT.md`.
- `tasks/locker/reference/` is a complete Go implementation used only to
  validate the hidden suite. Never mount it into an agent workdir.
- `tasks/locker/tests/` is the hidden black-box Python suite, its trusted
  `manifest.json`, and `seed-baseline.json`, the frozen untouched-seed result
  used to normalise quality. Mount the directory only after the agent exits.
- `docker/locker.Dockerfile` is the lean Ubuntu + Go + Python + OpenCode image.
- `tests/test_bench.py` covers leaderboard metrics and Locker scoring.
- `runs/`, `results-locker.csv`, `report-locker.md`, `locker-leaderboard.html`
  and `docs/` are generated artifacts. Do not hand-edit per-run workdirs or
  generated reports; fix the source and regenerate.

## Commands

Run from the repo root:

- `./bench.py build` — build/rebuild the Locker agent/build/test image.
- `./bench.py check` — validate prompt, seed, manifest family/subcase counts,
  frozen resource limits, hidden runner files and image. Main preflight.
- `./bench.py run --model provider/model` — score one run.
  `--variant all --keep-going` runs every named variant, highest effort first.
  `--dry-run` prints Docker commands and writes no score.
- `./bench.py rescore --run RUN_ID` — rerun build + hidden tests on an existing
  `runs/<run_id>/workdir` with the current suite.
- `./bench.py backfill --all` — recompute effort/token metrics from
  `agent.log` and `opencode-data/opencode.db`.
- `./bench.py report` / `./bench.py leaderboard` — Locker-only views.
- `./bench.py preview --run RUN_ID` — run the built `bin/locker` server.
- `./bench.py submit` / `site` / `moderate` are SpriteForge-era tooling; do not
  use them to publish Locker results before calibration.

`docker info` must succeed for build/run/check/rescore; `check` and `models`
also need the `opencode` CLI. `report`, `leaderboard`, `backfill` and the unit
tests need neither Docker nor `opencode`.

Harness syntax check: `python3 -m py_compile bench.py`.
Metric/scoring regression tests: `python3 -m unittest discover -s tests`.

## Hidden-suite and scoring invariants

- `tasks/locker/tests/manifest.json` is the source of truth: 16 families,
  P0 3 / P1 7 / P2 6 families, and 73 fixed subcases P0 8 / P1 42 / P2 23.
- `EXPECTED = {"P0": 3, "P1": 7, "P2": 6}` in `bench.py` counts **families**.
  Changing the catalog requires updating the manifest, `EXPECTED`, prompt and
  docs together; earlier results become non-comparable.
- `tasks/locker/tests/seed-baseline.json` records the untouched seed's pass/fail
  for every subcase. P0 is only an eligibility gate and is never scored.
  Every seed-passing subcase is a regression gate: losing one makes the run
  ineligible. Quality is the mean credit over the P1/P2 families that still
  have headroom, where a family's credit is passed seed-failing subcases /
  fixed seed-failing subcases. A family the seed passes completely is a gate
  and is not scored. A no-op run therefore has quality 0.0, not the seed's
  inherited passes. Regenerate the baseline when the seed or suite changes.
- Eligibility = successful build, every P0 subcase passed, no seed-passing
  subcase regressed, quality ≥ 0.80.
- The runner writes `results.json` with schema
  `opencode-bench/locker-tests@1`, `suite_version: locker-v5`, the fixture seed
  and per-case durations/errors. `test_ok` is true only when all expected
  subcases pass.
- The hidden suite runs only against the built `bin/locker`, with
  `--network none`. It invokes the binary over HTTP and the CLI; it never
  imports packages or inspects the data format.
- Hidden tests and the reference are mounted only after the agent exits. Never
  copy either into the seed, `PROMPT.md`, the image, or an agent workdir.

## Seed / prompt / image coupling

- The seed is a naive Go v0: direct final-path writes, in-place metadata, no
  locking, ignored Range/conditional headers, 501 multipart, simple listing and
  read-all uploads. It must build and pass all P0 subcases while remaining
  ineligible. Its measured challenge baseline is recorded in `HARD-TASK-PLAN.md`
  and calibration notes; do not turn it into the reference.
- All graded behavior and the exact frozen limits belong in
  `tasks/locker/PROMPT.md`: 128 MiB stream fixture, 96 MiB server `VmHWM` cap,
  180 s large-workload deadline, 10 s progress deadline, 60 s request deadline,
  15 s startup deadline, 6 bounded crash trials, 32 batch operations,
  33,554,432-byte fault filesystem and 50,331,648-byte oversized write.
- Go standard library only. No new dependencies, no Node/Playwright, no
  network at build or test time.
- The image pins Ubuntu 24.04, OpenCode 2.2.5 and the distro Go toolchain.
  Changing the Go image requires rebuilding and rerunning the reference.

## Validating changes

- Reference: `cd tasks/locker/reference && go test ./... && go build -trimpath -o bin/locker ./cmd/locker`.
- Hidden suite against the reference:

  ```bash
  python3 tasks/locker/tests/run.py \
    --binary "$PWD/tasks/locker/reference/bin/locker" \
    --manifest tasks/locker/tests/manifest.json \
    --seed 20261106 --output /tmp/locker-result.json
  ```

  Expect 73/73 subcases. Add `--scratch /scratch` with the frozen 32 MiB tmpfs
  when `p2.faults` is included. `--family FAMILY` is author-only and marks the
  result diagnostic; it is never eligible for ingestion.
- Oracle unit tests:
  `python3 -m unittest discover -s tasks/locker/tests -p 'test_*.py'`.
- Evaluator sensitivity: `python3 tasks/locker/tests/negative_controls.py
  --binary tasks/locker/reference/bin/locker --scratch /scratch`. Each injected
  fault must make its intended invariant fail.
- Repeated crash/concurrency diagnostic runs are a release gate. A flaky run is
  an evaluator bug, not a model failure.
- Seed baseline: build/run the seed, require all eight P0 subcases, and require
  the hidden suite output to match `tasks/locker/tests/seed-baseline.json`
  subcase-for-subcase (15/73 today: P0 8, P1 2, P2 5). A no-op must score 0.0.
  The saturated `p2.overwrite` family is a gate, not a scored family.
- Run Linux-dependent cases in the pinned image with network disabled; the
  performance case reads `VmHWM` from `/proc/<pid>/status`.
- For harness changes: `./bench.py check`,
  `python3 -m unittest discover -s tests`, and a rescore against the reference.

## Effort metrics

- `ttft_s`, `think_s`, `output_s`, `tool_s` and `other_s` are sums across all
  assistant requests, not per-request values. TTFT covers queueing, prefill and
  any unstreamed reasoning.
- Token fields are cumulative across steps and include cache reads/writes.
  `total_tokens` can exceed the context window without compaction. Use
  `max_request_tokens` / `mean_request_tokens` for per-request size.
- `backfill` reconstructs effort metrics from `agent.log` and
  `opencode-data/opencode.db`; runs without a persisted DB have
  `metrics_source=log` and no timing reconstruction.
- Provider errors/retries are not model generation. They are excluded from
  think/output/tokens/tok/s and counted in `retries`/`retry_s`, but remain in
  wall time.
- `gen_tokens_per_s` is aggregate successful output+reasoning over
  think+output; the median is the robust display value.
- `self_test_s` is classified shell time for `go test`, `go build`,
  `scripts/smoke.py` and similar self-tests. `build_tool_s` separately covers
  `go build`/`go vet`. `wall_ex_self_test_s = wall_s - self_test_s`.

## Operational gotchas

- Parallel `run` processes are safe only across different providers/models.
  Same-model shells contend for the model server and inflate `wall_s`.
- Crash tests use `SIGKILL`; they check process-kill recovery, not power-loss
  durability or fsync ordering. Do not describe them as power-loss tests.
- Faults cases need `--tmpfs /scratch` (`run_tests` adds it); running them
  without a scratch filesystem exits 3 by design.
- `watchdog: exit` alone is not success. Interpret `outcome`, `COMPLETION.md`,
  `files_changed`, `results.json` and the family credits.
- The leaderboard speed baseline uses the fastest **eligible** Locker run and
  caps the speed ratio at 1.0. Failed runs must not set the baseline.
- Model selection, variants, thinking and API keys come from the user's local
  OpenCode config, not this repo. Do not add model lists or credentials here.
