# spritebench

Build-and-play benchmark for OpenCode models.

One command selects a model from **your existing `opencode models` list**, runs
it in an isolated agent container, builds the resulting app in a test-free
container, runs the hidden Playwright suite in a separate container, and writes
a score and report.

There is no model list, endpoint list or machine list in this repo. The local
OpenCode config is the source of truth.

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
./bench.py leaderboard --serve   # serve the all-runs leaderboard
./bench.py rescore --run RUN_ID  # re-run build+tests on an existing workdir
./bench.py report                # table plus failed test names
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
./bench.py leaderboard --serve --open
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
  `docker ps --filter name=spritebench --format '{{.Names}} {{.Status}}'`.

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

- model, thinking/variant, quality, wall time, compactions, minor issues and
  eligibility;
- a `Failures` section with the failed test names;
- a pointer to `results.csv` and the per-run artifacts.

Each run records `variant`, `thinking` (`on`/`off`/`unknown`),
`reasoning_effort` and the number of OpenCode compactions. The OpenCode data
directory is persisted under `runs/<run_id>/opencode-data`, so the compaction
count survives after the agent container exits.

`results.csv` keeps `wall_s` as numeric seconds and adds `variant`, `thinking`,
`reasoning_effort` and `compactions`. Console/report displays format wall time
as `HH:MM:SS`.

`./bench.py preview` serves the newest built `dist/` on
`http://127.0.0.1:4173/`. With parallel runs, pass `--run RUN_ID` to choose a
specific run. `./bench.py status` shows the newest run by `agent.log` mtime,
container, live engine `/status` when the provider exposes one, and score; it
does not take a run ID.

## Leaderboard

`./bench.py leaderboard` renders **all runs** from `results.csv` into
`leaderboard.html`, regardless of which shell started them. It is rebuilt
automatically after every scored or rescored run and is the recommended
cross-run view when several shells are active.

```bash
./bench.py leaderboard              # write leaderboard.html
./bench.py leaderboard --open       # write it and open it in the browser
./bench.py leaderboard --serve      # serve http://127.0.0.1:4174/?live=1
```

The served page refreshes every 30 seconds; add `--open` to launch the browser.
It reads a locked snapshot of `results.csv`, so it is safe to leave running
while parallel shells finish. The server only serves the leaderboard page, not
the project tree, so hidden tests and reference code are not exposed.

Runs are scored and ranked by:

```
score = 80% quality + 15% speed + 5% compactions
speed = fastest wall / run wall
compactions = 1 / (1 + count)
```

Quality carries the most weight, then wall time, then compactions. Ties break
on quality, wall time, compactions and run ID. The page also shows the raw
quality, P0/P1/P2 counts, `HH:MM:SS` wall time, compaction count and eligibility
for each run.

The leaderboard has **Provider** and **Reasoning** filter drop-downs. It opens
sorted by **Score**, highest first. Click any sortable column header to sort by
it, and click again to reverse the direction. `Started` sorts newest first and
`Wall` sorts fastest first by default. Rank numbers follow the current filtered
and sorted view. Filter and sort choices are remembered in the browser, so the
30-second live refresh keeps them.

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
quality.

`COMPLETION.md` stops the run after a 60-second grace period. Watchdogs are
4 hours hard and 30 minutes without output. They are safety valves, not scoring
caps.

The perf-proxy hook (`agent_think_s`, `tool_s`, token/TTFT, prefill/decode) is
not wired yet. `wall_s`, quality, build and test results are.

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
- The image is built (`spritebench:latest`), and `docker build --check` passes.
- OpenCode 2.2.5 v2 installer verified locally.
- The current hidden suite covers canvas/drawing, continuous pencil/eraser
  drags, palette, layers, frames, playback, persistence, PNG export, onion
  opacity, keyboard/focus behaviour, layout and edge cases.
- `./bench.py leaderboard` builds a ranked all-runs page and can serve it on
  `127.0.0.1:4174` for live overnight monitoring.
