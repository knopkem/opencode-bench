# AGENTS.md

## Repository shape

- `bench.py` is the entire harness: one stdlib-only Python 3.10+ file for run
  orchestration, Docker mounts, scoring, CSV, reports and the leaderboard. There
  is no root `package.json` and no automated test suite for the harness itself.
- `tasks/spriteforge-lite/seed/` is copied into each run and is what the model
  sees. `tasks/spriteforge-lite/tests/` is the hidden Playwright suite.
  `tasks/spriteforge-lite/reference/` is a working implementation used only to
  validate the suite; never expose it to the model.
- `runs/`, `results.csv`, `report.md` and `leaderboard.html` are generated
  artifacts. Do not hand-edit per-run workdirs or generated reports; fix the
  source (`bench.py`, `PROMPT.md`, seed, hidden tests) and regenerate.

## Commands

Run from the repo root:

- `./bench.py build` — build/rebuild the single agent/build/test image.
- `./bench.py check` — validate prompt, seed, hidden-test tier counts, model
  config and image. This is the main preflight.
- `./bench.py run --model provider/model` — score one run.
  `--variant all --keep-going` runs every named variant, highest effort first.
  `--dry-run` prints the Docker commands and discards the run; it writes no
  score or report.
- `./bench.py rescore --run RUN_ID` — rerun build + hidden tests on an existing
  `runs/<run_id>/workdir` with the current suite.
- `./bench.py backfill --all` — recompute effort/token metrics from existing
  `agent.log` and `opencode-data/opencode.db`; `--run RUN_ID` for one run,
  `--dry-run` to preview. Needs neither Docker nor `opencode`.
- `./bench.py report` / `./bench.py leaderboard` — all-run views. The default
  `leaderboard` command serves and opens the page; `--write` only writes
  `leaderboard.html`.
- `./bench.py preview --run RUN_ID` — serve a built app on `127.0.0.1:4173`.

`docker info` must succeed for build/run/check/rescore; `check` and `models`
also need the `opencode` CLI. `preview` only needs `node`; `report`,
`leaderboard` and `backfill` need neither Docker nor `opencode`. Harness syntax
check: `python3 -m py_compile bench.py`.

## Hidden-test and scoring invariants

- Test titles **must** start with `[P0]`, `[P1]` or `[P2]`; `score_run()` parses
  the tier from that prefix. Unprefixed titles fall through to P2.
- `EXPECTED = {"P0": 5, "P1": 9, "P2": 6}` in `bench.py` must match the suite;
  `check` fails otherwise and scoring caps each tier at these totals. Adding or
  removing a hidden test means updating `EXPECTED` (and docs that state counts).
- The hidden suite runs only against the built `dist/`, served by
  `docker/serve.mjs` with no network. It does not use the seed's Vite dev
  server, and `npm run test:e2e` covers only the public smoke tests.
- Hidden tests and the reference are mounted only after the agent exits. Never
  copy either into the seed, `PROMPT.md`, the image, or an agent workdir.

## Effort metrics

- `ttft_s`, `think_s`, `output_s`, `tool_s` and `other_s` are **sums across all
  assistant requests**, not per-request values. `ttft_s / steps` is the average
  time to first token; shares are relative to `wall_s`.
- Token fields are cumulative across assistant steps and include cache reads and
  writes. `total_tokens` can therefore exceed the model's context window without
  any compaction. Compaction depends on one request's context, not the run total.
  Use `max_request_tokens` / `mean_request_tokens` for per-request context size.
- `backfill` recomputes these fields from `agent.log` and
  `opencode-data/opencode.db`; it cannot reconstruct timing for runs whose DB
  was not persisted (`metrics_source=log`).

## Seed / prompt / image coupling

- Root `PROMPT.md` is copied into every run as `PROMPT.md`; the seed has its own
  `AGENTS.md`. The task contract in `PROMPT.md`, the hidden tests, and the seed
  `data-testid`/state API must stay in sync.
- The Dockerfile copies the seed's `package.json` + `package-lock.json` and runs
  `npm ci --ignore-scripts` at image-build time. Changing seed dependencies or
  the lockfile requires `./bench.py build`; runtime only copies a missing
  `node_modules` from the image.
- The seed pins `@playwright/test` `1.64.1`, matching the
  `mcr.microsoft.com/playwright:v1.64.1-noble` base image. Update both together
  if the Playwright version changes.

## Validating changes

- For prompt/seed/test changes, run `./bench.py check`, then run the hidden suite
  against the reference. The exact local command is in
  `tasks/spriteforge-lite/reference/README.md` (build the reference, then run
  the hidden config with `APP_DIR`, `APP_PORT`, `SERVE_SCRIPT`, `RESULT_FILE`
  and `NODE_PATH`). Append `--grep '\[P0\]'` (or another tier) to run a subset.
- The documented validation bar is 20/20 hidden tests, three consecutive runs,
  plus 4/4 public smoke tests. Treat a suite that is flaky on the reference as
  a test bug, not a model failure.
- `watchdog: exit` alone is not success. Interpret finished runs with `outcome`
  (`complete`, `gave_up`, `incomplete`, `timeout`, `stalled`, `error`),
  `COMPLETION.md` and `files_changed`.

## Operational gotchas

- Parallel `run` processes are safe only across **different** providers/models.
  Same-model shells contend for the model server and inflate `wall_s`.
- "Latest" is ambiguous during parallel runs: `preview` and `rescore` take
  `--run RUN_ID`; `status` does not, so use `score.json` or the leaderboard for
  a specific run.
- Model selection, variants, thinking and API keys come from the user's local
  OpenCode config (`opencode models`, `opencode debug paths`), not this repo.
  Do not add model lists or credentials here.
