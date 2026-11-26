# AGENTS.md

## Repository shape

- `bench.py` is the entire harness: one stdlib-only Python 3.10+ file for run
  orchestration, Docker mounts, scoring, CSV, reports and the leaderboard. There
  is no root `package.json`; `tests/test_bench.py` is a stdlib `unittest` suite
  for metric extraction and leaderboard scoring.
- `tasks/spriteforge-lite/seed/` is copied into each run and is what the model
  sees. The hidden Playwright suite and the reference implementation live only
  in the private evaluator-source repository and inside evaluator images; they
  must not be present in the public source tree.
- `docker/agent.Dockerfile` builds the public agent/build image. During agent
  runs, `docker/egress_proxy.py` is bind-mounted into a separate trusted sidecar:
  the agent has an internal-only network, while the sidecar allows exact
  provider/OpenCode host+port endpoints without terminating TLS. Do not replace
  this with proxy environment variables alone or a blanket `--network none`;
  the former is bypassable and the latter breaks authenticated cloud models.
- `docker/evaluator.Dockerfile` is the public recipe for adding a private suite
  to a separate evaluator image. `MIGRATION.md` records the split and release
  procedure.
- `submissions/` holds the public result schema, template and example;
  `.github/ISSUE_TEMPLATE/` holds the result form and
  `.github/workflows/community-leaderboard.yml` builds/deploys the public page.
- `runs/`, `results.csv`, `report.md`, `leaderboard.html` and `docs/` are
  generated artifacts. Do not hand-edit per-run workdirs or generated reports;
  fix the source (`bench.py`, `PROMPT.md`, seed, private evaluator suite,
  submissions, templates) and regenerate.

## Commands

Run from the repo root:

- `./bench.py build` — build/rebuild the public agent/build image.
- `./bench.py build-eval --tests-dir PRIVATE_SUITE_DIR` — build a separate
  evaluator image from a private suite checkout, with suite-version metadata.
  Evaluator releases are built from the private evaluator-source repository.
- `./bench.py check` — validate prompt, seed, evaluator manifest and tier
  counts, image separation and model config. This is the main preflight.
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

`docker info` must succeed for build/build-eval/run/check/rescore; `check`
and `models` also need the `opencode` CLI. `preview` only needs `node`;
`report`, `leaderboard`, `backfill` and the unit tests need neither Docker nor
`opencode`. The default image references are the public GHCR `edge` tags;
image options and environment overrides are for maintainers and local builds.
Harness syntax check: `python3 -m py_compile bench.py`.
Metric/scoring regression tests: `python3 -m unittest discover -s tests`.

## Hidden-test and scoring invariants

- Test titles **must** start with `[P0]`, `[P1]` or `[P2]`; `score_run()` parses
  the tier from that prefix. Unprefixed titles fall through to P2.
- `EXPECTED = {"P0": 5, "P1": 9, "P2": 6}` in `bench.py` must match the suite;
  `check` compares it with the evaluator manifest and Playwright listing.
  Adding or removing a hidden test means updating `EXPECTED`, evaluator suite
  metadata, and docs that state counts.
- The hidden suite runs only inside the evaluator image against the built
  `dist/`, served by `docker/serve.mjs` with no network. It does not use the
  seed's Vite dev server, and `npm run test:e2e` covers only the public smoke
  tests.
- The agent/build image and evaluator image are separate. The evaluator runs
  only after the agent exits and is never mounted into the agent container.
  Never copy the private suite or reference into the seed, `PROMPT.md`, the
  agent image, an agent workdir, or a public artifact.

## Community leaderboard invariants

- The public page is built from **open** issues labeled `benchmark-result` that
  do **not** have `hidden`. Issues are the source of truth; the workflow does
  not commit generated files. The workflow also runs on `opened` and adds
  `benchmark-result` when an unlabeled body validates, so submissions created
  outside the issue form are not silently dropped.
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
- `submit --gh` is idempotent per `run_id`: it replaces the result block in the
  existing open issue (preserving surrounding prose) instead of creating a
  duplicate. `--new` forces a new issue and `--issue N` targets one explicitly.
- `hidden` is the reversible moderation switch. `needs-attention` is automatic
  for validation failures. Do not auto-remove `hidden`.
- `bench.py site` / `submit` are stdlib-only and must not require Docker or the
  OpenCode CLI. The workflow uses `gh issue list` JSON and never executes issue
  content as code.
- The Pages workflow needs `contents: read`, `actions: read`, `issues: write`,
  `pages: write` and `id-token: write`. Do not add `contents: write` or bot
  commits. `actions: read` exists only so the deploy job can poll for the
  uploaded `github-pages` artifact; the artifact lookup in `deploy-pages` is
  eventually consistent, so `deploy` waits for it and retries once.
- One-time repo setup: Pages source “GitHub Actions”; labels `benchmark-result`,
  `verified`, `hidden`, `needs-attention`.

## Effort metrics

- `ttft_s`, `think_s`, `output_s`, `tool_s` and `other_s` are **sums across all
  assistant requests**, not per-request values. `ttft_s / steps` is the average
  time to first token; shares are relative to `wall_s`. TTFT covers queueing,
  prefill and any unstreamed reasoning before the first content block; do not
  present it as a pure prefill benchmark.
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
- The hidden P0/P1 canvas-pixel checks expect the default pencil colour to be
  pure black. `PROMPT.md` pins `palette[0]` to `#000000` and the public smoke
  test asserts the rendered pixel, so the stated contract, the public suite and
  the hidden suite stay aligned.
- `docker/agent.Dockerfile` copies the seed's `package.json` +
  `package-lock.json` and runs `npm ci --ignore-scripts` at image-build time.
  Changing seed dependencies or the lockfile requires rebuilding the agent
  image; runtime only copies a missing `node_modules` from the image.
- `.dockerignore` must exclude `.private-eval/`, all historical evaluator
  paths, run directories and generated result files from every public image
  build context. `docker/evaluator.Dockerfile` uses a named `tests` build
  context supplied by the private evaluator repository.
- The seed pins `@playwright/test` `1.64.1`, matching the
  `mcr.microsoft.com/playwright:v1.64.1-noble` base image. Update both together
  if the Playwright version changes.

## Validating changes

- For prompt/seed changes, run the public syntax/unit checks below plus
  `./bench.py check` with validated agent and evaluator images. Hidden-suite
  validation happens only in the private evaluator-source repository: build the
  evaluator image from the private suite, then run it against the private
  reference. The exact commands are in that repository's reference README.
- The documented validation bar is 20/20 hidden tests, three consecutive runs,
  plus 5/5 public smoke tests. Treat a suite that is flaky on the reference as
  a test bug, not a model failure.
- `watchdog: exit` alone is not success. Interpret finished runs with `outcome`
  (`complete`, `gave_up`, `incomplete`, `timeout`, `stalled`, `error`),
  `COMPLETION.md` and `files_changed`.
- For community/site changes, run
  `./bench.py submit --check submissions/examples/issue-1.json`,
  `./bench.py site --issues tests/fixtures/issues.json --output
  /private/var/folders/2w/hgzb68wd3c37r_g6dqdm10940000gn/T/opencode/site`,
  `./bench.py site --issues tests/fixtures/issues-invalid.json --output
  /private/var/folders/2w/hgzb68wd3c37r_g6dqdm10940000gn/T/opencode/site-bad
  --errors /private/var/folders/2w/hgzb68wd3c37r_g6dqdm10940000gn/T/opencode/errors.json`,
  `python3 -m unittest discover -s tests`, and
  `actionlint .github/workflows/*.yml`.
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
  Do not add model lists or credentials here. Agent egress is resolved separately
  for each selected provider from its effective `baseURL` and models.dev metadata;
  `--allow-host` is the explicit escape hatch for auxiliary API/authentication
  endpoints, not an unrestricted-network switch.
