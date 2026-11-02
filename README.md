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
./bench.py run --model provider/model --variant xhigh   # if the model defines it
./bench.py status                # latest run, container and live engine status
./bench.py preview               # serve the latest built app
./bench.py rescore --run RUN_ID  # re-run build+tests after a suite fix
./bench.py report                # table plus failed test names
```

`run` with no `--model` asks for a provider, then shows that provider's models
as a numbered menu. `--provider NAME` (any provider) skips the first
prompt. `--goal` is one of `speed`, `quality`, `balanced` (default).
`--dry-run` prints the Docker commands and mounts without running anything.

Docker/OrbStack must be running (`docker info`); `--engine podman` is supported
for the same CLI shape.

## Configuration

The runner uses the OpenCode config that `opencode` already uses:

```bash
./bench.py models                         # global config
./bench.py run --model provider/model     # global config
./bench.py run --model ... --config ~/.config/opencode
./bench.py run --model ... --config /path/to/opencode.json
```

- `--config` is optional. It can be a config directory or a single file; the
  default comes from `opencode debug paths`.
- `--env KEY` passes an environment variable into the agent container. Variables
  referenced as `{env:KEY}` in the config are passed automatically when they
  exist on the host.
- `--variant` maps to `opencode run --variant`; `--agent` sets the agent;
  `--pure` disables external plugins. Thinking is controlled by the selected
  model entry in your OpenCode config (`body.thinking`, `reasoningEffort`,
  `variants`), not by this harness. If a model entry has `thinking: false` and
  no variants, `bench.py` cannot turn thinking on.
- API keys stay in your OpenCode config or environment; this repo stores none.

The agent container mounts the config directory read-only at `/config/opencode`
and copies it into a writable XDG config inside the container. The image
installs OpenCode 2.2.5 through the v2 installer; override it with
`./bench.py build --opencode-version X.Y.Z`.

## Isolation

- **agent container** — mounts only `workdir/` and the OpenCode config.
  Contains Chromium, Playwright, OpenCode and the seed's public smoke tests. It
  never mounts the hidden `tests/`, the reference app, the host home or the
  Docker socket.
- **build container** — mounts `workdir` only. No tests.
- **test container** — mounts `dist/` and hidden `tests/` read-only. Serves the
  build over local HTTP and runs Playwright with no network.

The control machine is not running the model server, so Docker/Chromium overhead
does not compete with inference. Model endpoints are whatever your OpenCode
config points at.

## What counts

`wall_s` is the total agent run, including the model's own browser testing.
A model that spends time debugging and still fails loses on both quality and
time. A model that debugs and passes trades time for quality; the report shows
the Pareto table instead of hiding it behind one weighted score.

Each run report records `variant`, `thinking` (`on`/`off`/`unknown`) and
`reasoning_effort`, so a no-thinking baseline is distinguishable from a
thinking run.

v0 records `wall_s` and the score. `agent_think_s` / `tool_s` and the token /
TTFT columns are reserved for the perf-proxy hook; they are not wired yet.

`COMPLETION.md` stops the run after a 60-second grace period. The watchdogs are
4 hours hard and 30 minutes without output; they are safety valves, not scoring
caps.

## Layout

```
bench.py                     orchestrator
docker/agent.Dockerfile      Playwright + Chromium + OpenCode 2 image
docker/entrypoint.sh         config copy + preinstalled node_modules
docker/serve.mjs             static server for the built app
tasks/spriteforge-lite/
  seed/                      starter repo with public-tests/
  tests/                     hidden Playwright suite + config
  reference/                 instrumented spriteforge; validation only
runs/                        per-run workdirs, logs, scores
results.csv                  one row per run
```

Status: the reference implementation is instrumented and the hidden suite
passes 18/18 three times, public smoke 4/4, and `npm run build` is clean. The
Dockerfile passes `docker build --check`; the OpenCode 2.2.5 v2 installer was
verified locally. The full image build was not completed here because the
Playwright base-image pull was too slow on this network. `./bench.py build`
and then `./bench.py check` are the first real checks.
