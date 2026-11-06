# AGENTS.md

## Repository shape

- `bench.py` is the entire harness: one stdlib-only Python 3.10+ file for run
  orchestration, Docker mounts, scoring, CSV, reports and the leaderboard. There
  is no root `package.json`; `tests/test_bench.py` is a stdlib `unittest` suite
  for metric extraction and leaderboard scoring.
- `tasks/spriteforge-lite/seed/` is copied into each run and is what the model
  sees. `tasks/spriteforge-lite/tests/` is the hidden Playwright suite.
  `tasks/spriteforge-lite/reference/` is a working implementation used only to
  validate the suite; never expose it to the model.
- `submissions/` holds the public result schema, template and example;
  `.github/ISSUE_TEMPLATE/` holds the result form and
  `.github/workflows/community-leaderboard.yml` builds/deploys the public page.
- `runs/`, `results.csv`, `report.md`, `leaderboard.html` and `docs/` are
  generated artifacts. Do not hand-edit per-run workdirs or generated reports;
  fix the source (`bench.py`, `PROMPT.md`, seed, hidden tests, submissions,
  templates) and regenerate.

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
- `./bench.py submit --run RUN_ID --print` — turn a run into the community
  result JSON block. `--gh` creates the labeled issue; `--check FILE` validates
  offline; `--template` prints the skeleton.
- `./bench.py site --issues issues.json --output docs` — build the public page
  from `gh issue list` JSON. Writes `docs/index.html` and
  `docs/data/results.json`; no network or Docker.
- `./bench.py moderate --issue N --hide|--show` — add or remove the `hidden`
  label on a community result.
- `./bench.py preview --run RUN_ID` — serve a built app on `127.0.0.1:4173`.

`docker info` must succeed for build/run/check/rescore; `check` and `models`
also need the `opencode` CLI. `preview` only needs `node`; `report`,
`leaderboard`, `backfill` and the unit tests need neither Docker nor `opencode`.
Harness syntax check: `python3 -m py_compile bench.py`.
Metric/scoring regression tests: `python3 -m unittest discover -s tests`.

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

## Community leaderboard invariants

- The public page is built from **open** issues labeled `benchmark-result` that
  do **not** have `hidden`. Issues are the source of truth; the workflow does
  not commit generated files.
- The submission format is exactly one fenced `json` block with
  `"schema": "opencode-bench/result@1"`. `submissions/schema.json` documents it;
  `validate_submission()` is the authoritative check.
- Never trust submitted `quality`, `eligible`, `test_ok` or hidden-test totals.
  Recompute/compare them. The current suite is `EXPECTED = {"P0": 5, "P1": 9,
  "P2": 6}`; changing the suite requires updating the schema/template/example
  and old submissions become non-comparable.
- CI owns `submitter`, `issue`, `issue_url`, `submitted_at` and
  `evidence.level`. `verified` is a manual maintainer badge, not a replay.
  Any issue edit clears `verified`; maintainers re-add it after review.
- `hidden` is the reversible moderation switch. `needs-attention` is automatic
  for validation failures. Do not auto-remove `hidden`.
- `bench.py site` / `submit` are stdlib-only and must not require Docker or the
  OpenCode CLI. The workflow uses `gh issue list` JSON and never executes issue
  content as code.
- The Pages workflow needs `contents: read`, `issues: write`, `pages: write`
  and `id-token: write`. Do not add `contents: write` or bot commits.
- One-time repo setup: Pages source “GitHub Actions”; labels `benchmark-result`,
  `verified`, `hidden`, `needs-attention`.

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
  was not persisted (`metrics_source=log`). After changing metric extraction or
  `METRICS_VERSION`, run `./bench.py backfill --all` and regenerate the
  leaderboard/report.
- Provider errors/retries are **not** model generation. Messages with
  `finish=error`/`error` are excluded from `think_s`, `output_s`, request tokens
  and `gen_tokens_per_s`; they are counted in `retries`/`retry_s` and remain in
  `wall_s`/`other_s`. Do not let a zero-token retry depress tok/s.
- `gen_tokens_per_s` is aggregate successful output+reasoning over
  think+output. `gen_tokens_per_s_median` is the per-step median and is the
  robust display value when a provider stalls.
- `self_test_s` is the classified shell/browser self-test execution time, a
  subset of `tool_s`; `wall_ex_self_test_s = wall_s - self_test_s`. The
  leaderboard speed baseline uses the fastest **eligible** run and caps the
  speed ratio at 1.0. Do not reintroduce `min(wall_s)` across all rows: a fast
  failed run previously flattened every score.

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
- For community/site changes, run
  `./bench.py submit --check submissions/examples/issue-1.json`,
  `./bench.py site --issues tests/fixtures/issues.json --output /tmp/site`,
  `./bench.py site --issues tests/fixtures/issues-invalid.json --output
  /tmp/site-bad --errors /tmp/errors.json`, `python3 -m unittest discover -s
  tests`, and `actionlint .github/workflows/community-leaderboard.yml`.
- The public page is generated into `docs/` and is **not committed**. Do not
  hand-edit it; fix `bench.py`, the issue template/schema, or the workflow and
  rebuild.

## Operational gotchas

- Parallel `run` processes are safe only across **different** providers/models.
  Same-model shells contend for the model server and inflate `wall_s`.
- "Latest" is ambiguous during parallel runs: `preview` and `rescore` take
  `--run RUN_ID`; `status` does not, so use `score.json` or the leaderboard for
  a specific run.
- Model selection, variants, thinking and API keys come from the user's local
  OpenCode config (`opencode models`, `opencode debug paths`), not this repo.
  Do not add model lists or credentials here.
