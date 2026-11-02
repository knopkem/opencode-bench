# spritebench

Build-and-play benchmark for local coding deployments.

One command selects a model, runs OpenCode in an isolated agent container,
builds the resulting app in a test-free container, runs the hidden Playwright
suite in a separate container, and writes a score and report.

## Quick start

```bash
./bench.py build                 # build the agent/eval image once
./bench.py check                 # config, seed, tests, image
./bench.py models                # list configured deployments
./bench.py run --model pc/swift-iq2-xs
./bench.py report
```

Status: the reference implementation is instrumented and the hidden suite
passes 18/18 three times, public smoke 4/4, and `npm run build` is clean. The
Dockerfile passes `docker build --check`, but the full image build was not
completed here: the Playwright base-image pull was too slow on this network.
`./bench.py build` and then `./bench.py check` are the first real checks.

`run` with no `--model` shows a numbered menu. `--goal` is one of `speed`,
`quality`, `balanced` (default). `--dry-run` prints the Docker commands and
mounts without running anything. Docker/OrbStack must be running (`docker
info`); `--engine podman` is supported for the same CLI shape.

## Configuration

Edit `config/models.json`. API keys come from environment variables, never from
the JSON:

```bash
export SPLASH_API_KEY=...
export STRATA_API_KEY=...
```

The harness generates a per-run `opencode.json`, so adding a model is only a
config edit.

## Isolation

- **agent container** — mounts only `runs/<id>/workdir` and the generated
  config. Contains Node, Chromium, Playwright and OpenCode, plus the seed's
  public smoke tests. It never mounts the hidden `tests/`, the reference app,
  the host home or the Docker socket.
- **build container** — mounts `workdir` only. No tests.
- **test container** — mounts `dist/` and hidden `tests/` read-only. Serves the
  build over local HTTP and runs Playwright with no network.

The control Mac is not running Splash or Strata, so Docker/Chromium overhead
does not compete with the model servers. Model endpoints are remote.

## What counts

`wall_s` is the total agent run, including the model's own browser testing.
A model that spends time debugging and still fails loses on both quality and
time. A model that debugs and passes trades time for quality; the report shows
the Pareto table instead of hiding it behind one weighted score.

v0 records `wall_s` and the score. The `agent_think_s` / `tool_s` split and the
token/timing columns are reserved for the perf-proxy hook; they are not wired
yet, so those CSV cells stay empty.

`COMPLETION.md` stops the run after a 60-second grace period. The watchdogs are
4 hours hard and 30 minutes without output; they are safety valves, not scoring
caps.

## Layout

```
bench.py                     orchestrator
config/models.json           deployments: endpoint + model + limits
docker/agent.Dockerfile      Playwright + OpenCode agent/eval image
docker/entrypoint.sh         copy preinstalled node_modules into /work
docker/serve.mjs             static server for the built app
tasks/spriteforge-lite/
  seed/                      starter repo with public-tests/
  tests/                     hidden Playwright suite + config
  reference/                 instrumented spriteforge; validation only
runs/                        per-run workdirs, logs, scores
results.csv                  one row per run
```

The hidden suite is validated against the instrumented reference: 18/18 passed
three times, and the public smoke suite passed 4/4.
