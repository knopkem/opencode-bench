# opencode-bench

[Live community leaderboard](https://knopkem.github.io/opencode-bench/) ·
[Submit a result](https://github.com/knopkem/opencode-bench/issues/new?template=benchmark-result.yml)

Build-and-play benchmark for OpenCode models.

One command selects a model from **your existing `opencode models` list**, runs
it in an isolated agent container, builds the resulting app in a test-free
container, runs the hidden Playwright suite in a separate container, and writes
a score and report.

There is no model list, endpoint list or machine list in this repo. The local
OpenCode config is the source of truth.

## What the model does

Each run gives the model a fresh copy of a small TypeScript + Vite seed app and
one task: build **SpriteForge Lite**, a working single-page sprite editor. The
seed mounts a placeholder; the model must replace it with a real app and leave
`npm run build` and `npm run typecheck` green.

The app must provide a 32×32 logical canvas shown at 512×512, pencil and eraser
with continuous drag, line/rectangle/ellipse/fill/eyedropper tools, palette and
brush selection, undo/redo, layers with visibility and opacity, frames with
playback and onion skin, `localStorage` persistence, PNG export, keyboard
shortcuts, and the `window.__spriteforge` state API. It must use the required
`data-testid` selectors and write `COMPLETION.md` when finished.

There is no fixed time limit, only watchdogs for hung runs. The model may run
the public smoke tests and add its own tests under `tests-local/`, but cannot
add dependencies, use the network, or modify the hidden suite. The hidden
20-test Playwright suite (P0: 5, P1: 9, P2: 6) is mounted only after the agent
exits and is never visible to the model.

## Quick start

```bash
./bench.py build                 # build the agent/eval image once
./bench.py check                 # config, seed, tests, image
./bench.py models                # provider summary from `opencode models`
./bench.py models --provider NAME
./bench.py run --model provider/model
./bench.py run --model provider/model --variant high   # if defined
./bench.py run --model provider/model --variant all --keep-going
./bench.py status                # newest run, container, live engine status
./bench.py preview               # serve the newest built app
./bench.py preview --run RUN_ID  # serve a specific run
./bench.py leaderboard           # serve + open the all-runs leaderboard
./bench.py leaderboard --write   # write leaderboard.html only
./bench.py rescore --run RUN_ID  # re-run build+tests on an existing workdir
./bench.py backfill --all        # recompute effort/token metrics on existing runs
./bench.py report                # table plus failed test names
./bench.py submit --run RUN_ID --print  # JSON block for the issue form
./bench.py submit --run RUN_ID --gh     # create the labeled issue
./bench.py site --issues issues.json --output docs  # build the public page
./bench.py moderate --issue N --hide    # hide a community result
python3 -m unittest discover -s tests   # metric/scoring regression tests
```

Docker/OrbStack must be running (`docker info`). `--engine podman` uses the same
CLI shape.

## Selecting a model

`./bench.py models` prints a provider summary from `opencode models`:
provider, not a list of 459 models. Use:

```bash
./bench.py models --provider NAME
./bench.py models --all
```

`run` with no `--model` asks for a provider, then lists that provider's models
as a numbered menu. `--provider NAME` skips the provider prompt.

If the selected model defines variants and `--variant` was not given, the
runner asks which variant to use:

```text
Variants for splash/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF:Q2_0:
   0. default (on)
   1. high (on high)
   2. medium (on medium)
   3. low (on low)
   4. none (off none)
variant>
```

`0`/empty uses the model's configured default. Variants are ordered from highest
reasoning effort to lowest. `--variant high` skips the prompt. In a
non-interactive shell it uses the default and prints the variants.

## Overnight matrix

`--variant all` runs every **named** variant defined for the selected model,
then exits:

```bash
./bench.py run \
  --model splash/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF:Q2_0 \
  --variant all \
  --keep-going
```

- Variants run from highest reasoning effort to lowest, for example
  `high → medium → low → none`.
- `--keep-going` continues to the next variant after a failed run. Without it,
  the first failure stops the matrix.
- The model's configured default is not one of the named variants. Run it
  separately with `--variant default` if you want that baseline too.
- `--reps N` repeats each variant N times.
- Run IDs include the variant, so results do not collide.
- For a real overnight session, run it under `tmux` or `nohup`.

## Parallel runs (multiple shells)

Each `bench.py run` process is safe to run alongside other shells:

- `results.csv` reads and writes are protected by a cross-process lock.
- `leaderboard.html` is written atomically, so readers never see a partial page.
- Run directories are claimed atomically; a same-second collision gets a numeric
  suffix instead of overwriting another run.
- Agent container names include a random suffix.

The intended pattern is one shell per provider/model. Variants inside each shell
still run sequentially, so two shells on different providers can run their
`--variant all` sequences side by side:

```bash
# shell 1
./bench.py run --model strata/swift-1.5-iq3_xxs --variant all --keep-going

# shell 2
./bench.py run --model splash/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF:Q2_0 \
  --variant all --keep-going

# any shell, after or during
./bench.py leaderboard
```

Do not run two shells against the same provider/model at the same time: they
will contend for the same model server and inflate `wall_s`. The file safety is
there for independent providers, not for parallelising one provider’s variants.

### “Latest” is ambiguous with parallel runs

- `./bench.py preview` serves the newest `dist/` by modification time. When more
  than one run is finishing around the same time, pass the run explicitly:
  `./bench.py preview --run RUN_ID`.
- `./bench.py status` reports the newest run by `agent.log` mtime and has no
  `--run` option. For any other run, read
  `runs/<run_id>/score.json`, or use the leaderboard/report, which cover all
  completed runs.
- `./bench.py report` and `./bench.py leaderboard` include every completed run,
  so they are the reliable cross-run views.
- Run IDs are printed when a run starts and are also the directory names in
  `runs/`. A same-second collision gets a `-2`, `-3`, … suffix; use the exact ID
  from the console or `results.csv`.
- To see active agent containers:
  `docker ps --filter name=opencode-bench --format '{{.Names}} {{.Status}}'`.

## Configuration

The runner uses the OpenCode config that `opencode` already uses:

```bash
./bench.py run --model provider/model
./bench.py run --model provider/model --config ~/.config/opencode
./bench.py run --model provider/model --config /path/to/opencode.json
```

- `--config` is optional. It can be a config directory or a single file; the
  default comes from `opencode debug paths`.
- `--env KEY` passes an environment variable into the agent container. Variables
  referenced as `{env:KEY}` in the config are passed automatically when they
  exist on the host.
- `--variant` is passed to OpenCode 2 in the model reference
  (`provider/model#variant`); `--agent` sets the agent; `--pure` disables
  external plugins.
- Thinking is controlled by the selected model entry in your OpenCode config
  (`body.thinking`, `reasoningEffort`, `variants`), not by this harness. If a
  model entry has `thinking: false` and no variants, `bench.py` cannot turn
  thinking on.
- API keys stay in your OpenCode config or environment; this repo stores none.

The agent container mounts the config directory read-only at `/config/opencode`
and copies it into a writable XDG config. The image installs OpenCode 2.2.5
through the v2 installer; override with
`./bench.py build --opencode-version X.Y.Z`. The Playwright base image is
`mcr.microsoft.com/playwright:v1.64.1-noble`; override with
`./bench.py build --playwright-image ...`.

## Reports and artifacts

Each run writes:

```
runs/<run_id>/
  workdir/              generated app
  opencode-data/        OpenCode session DB (compaction count)
  agent.log             OpenCode JSON events
  build.log             typecheck/build output
  test.log              Playwright output
  playwright.json       machine-readable test results
  score.json            all metrics
  report.md             per-run report
```

`./bench.py report` prints:

- model, thinking/variant, quality, wall time, wall excluding self-test,
  self-test share, retries, compactions, minor issues and eligibility;
- a `Failures` section with the failed test names;
- a pointer to `results.csv` and the per-run artifacts.

Each run records `variant`, `thinking` (`on`/`off`/`unknown`),
`reasoning_effort` and the number of OpenCode compactions. The OpenCode data
directory is persisted under `runs/<run_id>/opencode-data`, so the compaction
count survives after the agent container exits.

`results.csv` keeps `wall_s` as numeric seconds and adds `variant`, `thinking`,
`reasoning_effort`, `compactions`, `self_test_s`, `wall_ex_self_test_s`,
`retries`, `retry_s` and `gen_tokens_per_s_median`. Console/report displays
format wall time as `HH:MM:SS`. An optional free-text `hardware` note can be
attached with `./bench.py run --hardware "..."`.

Each run also records `steps` (assistant steps in `agent.log`),
`files_changed` (files added, removed or rewritten relative to the seed, ignoring
build artefacts) and `outcome`, which separates a clean finish from a silent
early exit:

| outcome | meaning |
|---|---|
| `complete` | `COMPLETION.md` was written |
| `gave_up` | exited on its own without `COMPLETION.md` and changed no files |
| `incomplete` | exited on its own without `COMPLETION.md`, but did change files |
| `timeout` | the hard timeout fired |
| `stalled` | the idle watchdog fired |
| `error` | the agent process exited non-zero |

`watchdog: exit` alone is not a success signal: an agent that stops after two
steps looks identical to one that finished.

### Effort and token metrics

Each run also breaks wall time into four source buckets plus overhead, using the
timestamps in `opencode-data/opencode.db`:

| field | meaning |
|---|---|
| `ttft_s` | sum over all assistant requests of request start → first model token (prefill/queue); divide by `steps` for the per-request average |
| `think_s` | explicit reasoning before the first output/tool block |
| `output_s` | model writing code, text and tool-call arguments |
| `tool_s` | shell/build/test/Playwright/file-write execution |
| `self_test_s` | shell test/Playwright/browser-test execution (subset of `tool_s`) |
| `build_tool_s` | build/typecheck command execution (subset of `tool_s`) |
| `wall_ex_self_test_s` | `wall_s - self_test_s`; the “work” wall basis |
| `retries` / `retry_s` | provider errors/retries; counted in wall and `other_s`, excluded from think/output and tok/s |
| `other_s` | startup, gaps, compaction calls, provider retry time and completion grace |
| `max_request_tokens` | largest per-request prompt size (input + cache reads/writes) |
| `mean_request_tokens` | mean per-request prompt size |

Tokens come from `agent.log` (`step_finish.part.tokens`) plus compaction rows in
the session DB: `input_tokens`, `output_tokens`, `reasoning_tokens`,
`cache_read_tokens`, `cache_write_tokens`, `compaction_tokens` and
`total_tokens`. These token fields are **cumulative across assistant steps** and
include cache reads/writes, so `total_tokens` can exceed the model's context
window even when no compaction occurred. The context limit applies to one
request, not to the run total. `max_request_tokens` and `mean_request_tokens`
report the per-request prompt size (input + cache reads/writes), which is the
relevant context-pressure metric. Derived fields include `think_share`,
`tool_share`, `self_test_share`, `gen_tokens_per_s`,
`gen_tokens_per_s_median`, `tokens_per_step` and `cache_hit_rate`.

`gen_tokens_per_s` is aggregate `(output + reasoning) / (think + output)` over
successful attempts only. `gen_tokens_per_s_median` is the median per-step rate
and is the more robust number when a provider stalls or retries. The
think/output boundary is heuristic, but tok/s uses their sum, so it is stable;
the per-step median is included as a second check. Both rates are
hardware-dependent.

Reasoning-token counts are provider-reported. A run can show a large `think_s`
with `reasoning_tokens = 0`; in that case the **time split is authoritative** and
the token count only means the provider did not report reasoning usage. The
leaderboard uses time-based shares for cross-provider comparisons and labels the
token breakdown as reported. `gen_tokens_per_s` is hardware-dependent for local
models.

Provider errors/retries are reported separately: they count in `wall_s` and
`other_s`, but their zero-token attempts are excluded from `think_s`,
`output_s` and `gen_tokens_per_s`. This prevents a stalled 359-second retry from
making a healthy run look 25% slower.

`./bench.py backfill --all` recomputes these fields from existing artifacts
(`agent.log` and `opencode.db`) without Docker or the OpenCode CLI. It updates
`score.json`, `report.md`, `results.csv` and the leaderboard. Use
`--run RUN_ID` for one run or `--dry-run` to preview.

`./bench.py preview` serves the newest built `dist/` on
`http://127.0.0.1:4173/`. With parallel runs, pass `--run RUN_ID` to choose a
specific run. `./bench.py status` shows the newest run by `agent.log` mtime,
container, live engine `/status` when the provider exposes one, and score; it
does not take a run ID.

## Community leaderboard

The public page is built entirely from open GitHub issues labeled
`benchmark-result`. It is hosted on GitHub Pages and regenerated by
`.github/workflows/community-leaderboard.yml` whenever a result issue is opened,
edited, labeled, unlabeled or closed, plus a daily reconciliation run.

### Submit a result

```bash
./bench.py run --model provider/model
./bench.py submit --run RUN_ID --print   # paste into the issue form
# or create the issue directly:
./bench.py submit --run RUN_ID --gh
```

The issue form is at
[New result](https://github.com/knopkem/opencode-bench/issues/new?template=benchmark-result.yml).
The canonical JSON format, LLM instructions and a filled example are in
[`submissions/TEMPLATE.md`](submissions/TEMPLATE.md) and
[`submissions/examples/issue-1.json`](submissions/examples/issue-1.json).

`submit` reads `score.json`, strips local paths, includes the M1 metrics
(self-test time/runs, retries, work wall, median tok/s), computes a score hash and
validates the block before posting. Hardware is a single optional free-text
line; there is no structured hardware capture.

### Validation and evidence

CI re-parses every issue, recomputes quality/eligibility from the P0/P1/P2
counts, checks the current hidden-test totals, wall/self-test consistency and
duplicate run IDs. Invalid issues are excluded and get a single bot comment with
the exact errors plus `needs-attention`.

- **self-reported** — validated and listed immediately, amber badge.
- **verified** — a maintainer added the `verified` label after review; green
  “maintainer-checked” badge. This is not an independent CI replay.
- Any edit clears `verified`; a maintainer re-adds it after review.

### Moderation

The page lists open issues with `benchmark-result` and without `hidden`.

```bash
./bench.py moderate --issue 12 --hide
./bench.py moderate --issue 12 --show
```

`hidden` is reversible. Removing `benchmark-result`, closing the issue or
deleting a test issue also removes it; the daily job reconciles deleted issues.

### Caveats

Wall time is hardware-dependent; the table shows a work wall (excluding
self-test execution) and a wall-basis selector, but it does not normalise across
machines. tok/s excludes failed/retried attempts and shows both the aggregate
and the per-step median. Results are self-reported unless marked verified.

The local leaderboard remains private: the public page reads issues only, not
the local `results.csv`.

## Leaderboard

`./bench.py leaderboard` renders **all runs** from `results.csv` into
`leaderboard.html`, regardless of which shell started them. It is rebuilt
automatically after every scored or rescored run and is the recommended
cross-run view when several shells are active.

```bash
./bench.py leaderboard              # serve http://127.0.0.1:4174/?live=1 and open it
./bench.py leaderboard --write      # write leaderboard.html only
./bench.py leaderboard --port 8080  # serve and open on another port
```

The default command serves the page and opens it in the browser. It refreshes
every 30 seconds. Use `--write` for scripting or when you only need the file.
It reads a locked snapshot of `results.csv`, so it is safe to leave running
while parallel shells finish. The server only serves the leaderboard page, not
the project tree, so hidden tests and reference code are not exposed.

Runs are scored and ranked by:

```
score = 80% quality + 15% speed + 5% compactions
speed = fastest eligible wall / run wall, capped at 1.0
compactions = 1 / (1 + count)
```

Only **eligible** runs set the speed baseline; a fast failed run cannot flatten
everyone else's scores. Quality carries the most weight, then wall time, then
compactions. Runs that fail eligibility stay on the board but keep only 75% of
their score, so they rank below quality runs instead of being hidden. Ties break
on quality, wall time, compactions and run ID. The page also shows the raw
quality, P0/P1/P2 counts, `HH:MM:SS` wall time, compaction count and eligibility
for each run.

The **Wall / work** cell shows total wall and a sub-line with wall excluding
self-test execution, self-test time/runs and provider retries. The **Wall
basis** selector switches the score between total wall and wall excluding
self-test; each basis has its own eligible baseline and speed cap. This makes
the verification trade-off visible without silently changing the benchmark.

Each row has one **Effort** column: a stacked bar (TTFT, thinking, model output,
tool execution, other), coloured percentages underneath, then peak request
tokens and median generation tok/s. The colour legend is above the table; hover
the effort cell for the full breakdown with per-bucket time and peak/mean request
tokens, or click it for the larger detail dialog with self-test/build/retry time,
step distribution, cache hit rate and cost. A collapsible **Model medians** table
below the main table aggregates tokens, work wall, self-test share and tok/s by
model/variant. Missing metrics render as `—`. The token breakdown is
provider-reported, so a thinking phase with zero reasoning tokens is annotated
rather than shown as no thinking.

The leaderboard has **Provider**, **Reasoning** and **Wall basis** drop-downs. It
opens sorted by **Score**, highest first. Click any sortable column header to
sort by it, and click again to reverse the direction. `Started` sorts newest
first and `Wall` sorts fastest first by default. Rank numbers follow the current
filtered and sorted view. Filter, sort and wall-basis choices are remembered in
the browser, so the 30-second live refresh keeps them.

## Scoring

```
fatal   = P0 failures
major   = P1 failures
minor   = P2 failures
quality = weighted P0/P1/P2 pass rate
```

Eligible requires no P0 failures, a successful build, and quality ≥ 0.80.
`--goal` selects the winner rule:

- `speed` — lowest median wall time among eligible runs;
- `quality` — highest quality, then wall time;
- `balanced` — highest quality within 20% of the fastest eligible time.

The report shows the Pareto table rather than collapsing quality and time into
one arbitrary score.

`wall_s` includes the model's own browser testing. A model that debugs and
fails loses on both quality and time; one that debugs and passes trades time for
quality. The leaderboard reports self-test time and offers a wall basis that
excludes it, because a thorough model can otherwise lose to a lucky model that
skipped verification. Provider errors/retries remain in total wall but are
excluded from tok/s and shown separately.

`COMPLETION.md` stops the run after a 60-second grace period. Watchdogs are
4 hours hard and 30 minutes without output. They are safety valves, not scoring
caps.

The effort breakdown (`ttft_s`, `think_s`, `output_s`, `tool_s`), request-token
and cumulative token metrics, and throughput are derived from `agent.log` and
the persisted OpenCode session DB. The older perf-proxy idea is optional; it is
not required for these metrics.
`wall_s`, quality, build and test results remain the scoring inputs.

## Isolation

- **agent container** — mounts only `workdir/` and the OpenCode config.
  Contains Chromium, Playwright, OpenCode and the seed's public smoke tests. It
  never mounts the hidden `tests/`, the reference app, the host home or the
  Docker socket.
- **build container** — mounts `workdir` only. No tests.
- **test container** — mounts `dist/` and hidden `tests/` read-only. Serves the
  build over local HTTP and runs Playwright with no network.

The agent runs as an isolated process; Dockerizing only the tests would not hide
them. The image exposes Playwright Chromium as `google-chrome` with
`--no-sandbox`, so a config that enables `chrome-devtools` MCP has a browser to
launch.

## Layout

```
bench.py                     orchestrator
tests/test_bench.py          metric/scoring regression tests
submissions/                 public result schema, template and example
.github/ISSUE_TEMPLATE/      benchmark-result issue form
.github/workflows/           community leaderboard build + Pages deploy
docs/                        generated public page (gitignored)
docker/agent.Dockerfile      Playwright + Chromium + OpenCode 2 image
docker/entrypoint.sh         config copy + preinstalled node_modules
docker/serve.mjs             static server for the built app
PROMPT.md                    build task and test contract
tasks/spriteforge-lite/
  seed/                      starter repo with public-tests/
  tests/                     hidden Playwright suite + config
  reference/                 instrumented spriteforge; validation only
runs/                        per-run workdirs, logs, scores
results.csv                  one row per run
leaderboard.html             generated all-runs leaderboard
```

## Status

- The reference implementation is instrumented with test IDs, state API, PNG
  export and play/pause controls.
- Hidden suite: 20/20 against the reference; public smoke: 4/4.
- The image is built (`opencode-bench:latest`), and `docker build --check` passes.
- OpenCode 2.2.5 v2 installer verified locally.
- The current hidden suite covers canvas/drawing, continuous pencil/eraser
  drags, palette, layers, frames, playback, persistence, PNG export, onion
  opacity, keyboard/focus behaviour, layout and edge cases.
- `./bench.py leaderboard` serves and opens a ranked all-runs page on
  `127.0.0.1:4174` for live overnight monitoring; `--write` only writes the
  HTML file.
- The community page is built from labeled issues by
  `.github/workflows/community-leaderboard.yml` and deployed with GitHub Pages.
  Enable Pages with source “GitHub Actions” and create the labels
  `benchmark-result`, `verified`, `hidden` and `needs-attention` once.
