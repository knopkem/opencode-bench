# OpenCode Bench

**How well can an AI coding model build a real app?**

OpenCode Bench gives a model a starter project, asks it to build a sprite editor,
and tests the result in a browser. It then reports app quality, speed, token use,
and other useful metrics.

[View the community leaderboard](https://knopkem.github.io/opencode-bench/) ·
[Submit a result](https://github.com/knopkem/opencode-bench/issues/new?template=benchmark-result.yml)

## Quick start

### 1. Install the prerequisites

- **Python 3.10+**
- **Docker** or **Podman**, running locally
- **OpenCode**, with at least one model configured
- **Node.js**, only if you want to preview a generated app

Check that Docker and OpenCode are ready:

```bash
docker info
opencode models
```

### 2. Check your setup

```bash
./bench.py check
```

This downloads the prebuilt benchmark images if they are missing. You do not need
to pull or build them yourself.

### 3. Run the benchmark

```bash
./bench.py run
```

Choose a provider and model when prompted. To skip the prompts, use a model from
`opencode models`:

```bash
./bench.py run --model provider/model
```

The benchmark builds the app and runs its browser tests automatically. Results
are saved under `runs/<run_id>/`; the run ID is printed in the terminal.

## View the results

```bash
./bench.py report                 # Summary of all runs
./bench.py leaderboard            # Open the local leaderboard
./bench.py preview --run RUN_ID   # Open the generated app
./bench.py status                 # Check the newest run
```

The leaderboard is the easiest way to compare models. The report includes failed
test names, while each run directory contains its logs, scores, and generated app.

## Run tests

### Test a model

Running the benchmark is the full test:

```bash
./bench.py run --model provider/model
```

The model works in an isolated container. Its network is restricted to the
selected model provider and OpenCode infrastructure through a separate forwarding
proxy. TLS is not intercepted, so existing API keys and authenticated cloud-model
connections continue to work. Custom provider `baseURL` values and local IP/ports
are discovered from OpenCode configuration. If a provider uses an additional API
or authentication host, approve it explicitly:

```bash
./bench.py run --model provider/model --allow-host auth.example.com:443
```

The option is repeatable and accepts URLs, hostnames, IP addresses, and explicit
ports. It does not disable the network policy. For a provider on the host, use a
container-reachable address such as `host.docker.internal`, not `127.0.0.1`.
Requests denied by the proxy are recorded in `egress.log`; attempts to reach
evaluation sources make the run ineligible without discarding its test-quality
results. Direct connections have no route outside the internal network.

After the model finishes, OpenCode Bench:

1. checks that the app compiles;
2. serves the built app locally;
3. runs 20 Playwright browser tests;
4. writes the score and report.

To build and test an existing run again without asking the model to redo its work:

```bash
./bench.py rescore --run RUN_ID
```

### Test the benchmark itself

These regression tests check scoring and metrics. They need neither Docker nor
OpenCode:

```bash
python3 -m unittest discover -s tests
```

## Useful options

List the models available through your OpenCode configuration:

```bash
./bench.py models
./bench.py models --provider NAME
```

Run a specific reasoning variant, or every variant defined for a model:

```bash
./bench.py run --model provider/model --variant high
./bench.py run --model provider/model --variant all --keep-going
```

Repeat each run three times:

```bash
./bench.py run --model provider/model --reps 3
```

Use a different OpenCode configuration:

```bash
./bench.py run --model provider/model --config ~/.config/opencode
```

For every available option:

```bash
./bench.py --help
./bench.py run --help
```

## Understanding the score

The browser tests are grouped by importance:

- **P0:** essential functionality
- **P1:** core editor features
- **P2:** polish and edge cases

A run is eligible when it builds successfully, has no P0 failures, reaches at
least 80% weighted quality, and has no recorded evaluation-source integrity
violation. The local leaderboard combines quality, speed, and
the number of context compactions. Full quality matters more than a fast but
broken app.

Each run also records wall time, tokens, retries, and an outcome such as
`complete`, `incomplete`, or `timeout`. See `runs/<run_id>/score.json` for the
raw metrics and `runs/<run_id>/report.md` for a readable summary.

## Share a result

Create or update a GitHub issue for a run with the
[GitHub CLI](https://cli.github.com/):

```bash
./bench.py submit --run RUN_ID --gh
```

Or print the result and paste it into the
[submission form](https://github.com/knopkem/opencode-bench/issues/new?template=benchmark-result.yml):

```bash
./bench.py submit --run RUN_ID --print
```

Submissions are checked before appearing on the community leaderboard. They are
self-reported unless a maintainer marks them as verified.

## What does the model build?

The task is **SpriteForge Lite**, a small pixel-art editor with drawing tools,
layers, animation frames, undo/redo, local saving, and PNG export. The model
starts with the same TypeScript and Vite starter project every time.

The public task description is in [PROMPT.md](PROMPT.md). The acceptance tests
run separately and are not available to the model while it works.

## Troubleshooting

- **`docker info` fails:** start Docker or Podman and try again.
- **No models are listed:** configure a provider in OpenCode first, then run
  `opencode models`.
- **An image cannot be downloaded:** check your network connection and access to
  `ghcr.io`, then run `./bench.py check` again.
- **Several runs are active:** use `--run RUN_ID` with `preview` and `rescore` so
  you select the right result. Avoid parallel runs on the same model provider,
  since they can distort timing.

## For maintainers

Build the public agent image after changing its Dockerfile or dependencies:

```bash
./bench.py build
```

The evaluator image is built separately from the private test suite:

```bash
./bench.py build-eval --tests-dir PRIVATE_SUITE_DIR
```

See [MIGRATION.md](MIGRATION.md) for the image-release process and
[AGENTS.md](AGENTS.md) for repository conventions and validation requirements.
