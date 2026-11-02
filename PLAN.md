# SpriteBench

**A build-and-play benchmark for local coding deployments.**

Status: v0 scaffold, 2026-11-02.

Implemented:

- one-command runner (`bench.py models|run|report|check|build`);
- agent/eval Docker image definition with Node, Chromium, Playwright and
  OpenCode;
- seed with `public-tests/` and a `test:e2e` script;
- hidden 18-test Playwright suite;
- instrumented reference (test IDs, state API, PNG export);
- reference validation: 18/18 hidden tests passed three times, 4/4 public
  smoke tests passed, `npm run build` clean.

The Dockerfile passes `docker build --check`. The full image build was not
completed on this machine: the Playwright base-image pull was too slow on this
network. The first real step is `./bench.py build` then `./bench.py check`.

The perf proxy (agent think/tool time, token and TTFT columns) is not wired in
v0; `wall_s`, quality, build and test results are.

The question this answers is not "which model is best?" It is:

> On this machine, with this engine, quant and context budget, which model
> builds a real browser app with the fewest issues in the least wall time?

The unit of comparison is a **deployment**: model + quantization + engine +
hardware + sampling settings. That is the only comparison that is meaningful
when one box is a 36 GB Mac running Splash and the other is a 16 GB 6800XT
running Strata.

The harness itself runs on a control Mac that does not host either model server.
The model endpoints are remote. That is deliberate: the control Mac has the
headroom for Docker, and the agent container can be isolated from the tests
without competing with Splash or Strata for memory.

## 0. What to keep from planner-bench — and what to change

`planner-bench` is useful as a planner-only probe, but it has two problems for
model selection:

1. **The tasks are familiar.** Hard algorithm/planning prompts are exactly the
   kind of thing models are trained on, so strong models cluster at 100% and
   the checklist stops discriminating.
2. **The tests reward coverage, not a working product.** A plan can name every
   edge case without the resulting app actually working. The rubric is the real
   discriminator, and it is manual.

SpriteBench replaces the plan-only task with a **build task**: a real app that
must boot, render, accept input, persist state and pass a hidden browser test
suite. The model cannot pass by describing the right answer; it has to ship it.

The existing `~/projects/spriteforge` app is the **reference implementation**
for v0. It builds, has 59 passing unit tests, and is visually usable. It is not
shown to the model. Its source is used to derive the seed and the hidden tests.

## 1. Task: SpriteForge Lite

A single-page browser sprite editor. The model starts from a seed repo and
works until it declares completion. Wall time is measured, but not capped for
scoring.

### Seed

`tasks/spriteforge-lite/seed/`:

```
package.json          # vite + typescript + vitest + @playwright/test
index.html            # mounts #app
src/main.ts           # placeholder: renders "TODO"
src/styles.css        # minimal reset only
tsconfig.json
AGENTS.md             # stack, commands, "no new dependencies"
public-tests/         # 5-6 smoke checks the agent can run and modify
playwright.config.ts  # points at public-tests/
node_modules/         # preinstalled by the harness; not part of the git tree
```

The seed builds and previews but has no editor. The model fills it in.

Constraints:

- TypeScript + Vite; `npm run build` must succeed.
- No new dependencies beyond the seed. No network. No external assets.
- The hidden acceptance suite is the source of truth.
- Self-testing is expected. The agent can run `npm run test:e2e`, write its own
  tests, use Playwright or the chrome-devtools MCP. Those checks are not
  scored; hidden tests still decide quality.

### Time budget

**No fixed scoring cap.** Let each model work until it writes `COMPLETION.md`
or exits. Wall time is the cost metric, so a model that takes 45 minutes is
allowed to win only if its quality justifies the wait.

Two practical watchdogs still apply:

- **Hard safety timeout:** 4 hours, to stop a runaway process. This is not a
  quality cap and should not be reported as one.
- **Inactivity timeout:** 30 minutes with no new tool call or output. This
  catches a hung server or agent, not a slow model.

A watchdog kill is a failed run, but partial test results are kept. Model load
and harness build/test time are recorded separately and excluded from
`wall_s`.

### Completion

The model writes `COMPLETION.md` with one line per section (`met` / `partial`)
and stops. The harness also accepts a clean process exit. A watchdog kill is a
failed run, but the partial test results are kept for diagnostics.

## 2. Test contract: make the prompt explicit enough to play

The current `spriteforge/PLAN.md` is a good feature spec, but it does not give
the browser tests stable handles. Add a **mandatory test contract** to the
prompt. This is not test leakage: it is the app's public API, the same way a
real product has stable accessible names and test IDs.

### DOM contract

| Element | Selector | Required behaviour |
|---|---|---|
| App root | `#app` | Contains the editor |
| Canvas | `[data-testid="canvas"]` | 512×512 CSS pixels; 32×32 logical pixels; nearest-neighbour |
| Tools | `[data-testid="tool-pencil"]`, `tool-eraser`, `tool-line`, `tool-rect`, `tool-ellipse`, `tool-fill`, `tool-eyedropper` | Click selects; active one has `data-active="true"` |
| Palette | `[data-testid="color-0"]` … `color-N` | Click selects; active one has `data-active="true"` |
| Brush | `[data-testid="brush-1"]`, `brush-2` | Select 1px / 2px; active one has `data-active="true"` |
| Undo / redo | `[data-testid="undo"]`, `redo` | Click applies history |
| Layers | `[data-testid="layer-add"]`, `layer-delete`, `layer-list`, `layer-row-0`…, `layer-visible-0`…, `layer-opacity-0`… | Add, delete, toggle, opacity |
| Frames | `[data-testid="frame-add"]`, `[data-testid="frame-duplicate"]`, `frame-delete`, `frame-list`, `frame-0`… | Add, duplicate, delete, select |
| Transport | `[data-testid="play"]`, `pause`, `fps` | Play/pause; FPS input |
| Export | `[data-testid="export"]` | Downloads `spriteforge.png` |
| Status | `[data-testid="status"]` | Human-readable feedback |

Indexes are display indexes: `layer-row-0` is the top row, and `frame-0` is the
first frame. The state API uses the model's own bottom-to-top layer order; the
tests convert using the known count.

### State API

The app must expose a small, documented debug API:

```ts
window.__spriteforge = {
  getState(): {
    width: 32; height: 32;
    fps: number; loop: boolean;
    palette: string[];              // lowercase #rrggbb
    activeFrame: number;
    activeLayer: number;            // index in frames[activeFrame].layers, 0 = bottom
    tool: string;
    brush: 1 | 2;
    frames: Array<{
      hold: number;
      layers: Array<{
        name: string;
        visible: boolean;
        opacity: number;            // 0..1
        pixels: number[];           // 1024 entries, -1 = transparent, else palette index
      }>;
    }>;
  };
  loadState(state): void;
  reset(): void;
};
```

Why both DOM and API:

- DOM tests exercise the app like a user.
- `getState()` makes pixel and layer assertions deterministic and debuggable.
- `loadState()` lets tests set up multi-frame/layer scenarios without ten
  fragile UI clicks.
- A model can fake `getState()` but not the canvas pixels and the DOM
  interactions at the same time.

### Persistence

- `localStorage` key: `spriteforge.project`.
- Value: the same JSON shape as `getState()`, with `format: "spriteforge"` and
  `version: 1`.
- Autosave after every edit; restore on boot; fall back to a fresh document on
  invalid data.
- Tests will write a known project, reload, and assert the rendered pixel and
  state.

### Export

- `[data-testid="export"]` downloads a 32×32 PNG named `spriteforge.png`.
- The PNG must contain the composited visible layers of the active frame.
- The harness parses the PNG with `pngjs`; a screenshot is not accepted.

### Canvas and input

- Logical coordinate `(x, y)` maps to CSS `(x * 16 + 8, y * 16 + 8)`.
- `pixels` is row-major, 32×32, `-1` for transparent.
- Left click draws; right click erases.
- Pencil/eraser support brush 1 and 2.
- Keyboard shortcuts fire only when the canvas area has focus: `B`, `E`, `L`,
  `R`, `O`, `G`, `I`, `[`, `]`, `Ctrl+Z`, `Ctrl+Shift+Z`, `Space`, arrows.
- No console errors or unhandled page errors during normal use.

### Layout

At 1280×800:

- canvas is 512×512 and fully visible;
- toolbar, palette, layers and frame strip are visible;
- no horizontal page scroll;
- no panel overlaps the canvas.

## 3. Feature tiers

The task is scored, not all-or-nothing. P0 is fatal, P1 is major, P2 is minor.

### P0 — must work

- `npm run build` succeeds; preview serves the app.
- App mounts; canvas 512×512; no console/page errors.
- Pencil draws a pixel; eraser clears it.
- Undo restores the previous pixel; redo reapplies it.
- Default document: 1 layer, 1 frame, 16-colour palette.

### P1 — the product

- Palette selection changes the drawn colour.
- Bucket fill fills a contiguous region.
- Brush size 1 vs 2.
- Line, rectangle and ellipse drag tools.
- Layers: add, delete, visibility, opacity; compositing is correct.
- Frames: add, duplicate, delete, select; each frame has its own pixels.
- Playback: play/pause/stop/step at the configured FPS; loop toggle.
- Persistence: autosave/restore via `localStorage`.
- PNG export: 32×32, composited active frame.

### P2 — polish and edge cases

- Keyboard shortcuts and focus scoping.
- Draw at `(0,0)` and `(31,31)`; out-of-bounds clicks ignored.
- Undo on an empty history does not crash.
- Deleting the last layer/frame is refused with a status message.
- Palette add/remove/reorder preserves pixel indices.
- Onion skin of the previous frame with opacity.
- Layout at 1280×800; no horizontal scroll.
- Status text updates for tool/frame/undo.
- No placeholder text, no dead buttons.

## 4. Hidden Playwright suite

`tasks/spriteforge-lite/tests/` is never copied into the model's workdir. The
harness mounts it at `/opt/spritebench/tests` only in the test container after
the agent stops.

The v0 suite has 18 tests. Each test is weighted by tier:

| Test | Tier | Weight |
|---|---|---|
| App mounts, default document, 512×512 canvas | P0 | 3 |
| Pencil draws and right-click erases | P0 | 3 |
| Undo restores and redo reapplies | P0 | 3 |
| No console/page errors | P0 | 3 |
| Palette selection draws the selected colour | P1 | 2 |
| Bucket fill fills the layer | P1 | 2 |
| Brush 1 vs 2 | P1 | 2 |
| Line / rect / ellipse drag | P1 | 2 |
| Layer add + visibility + opacity | P1 | 2 |
| Frame add / duplicate / select | P1 | 2 |
| Playback advances frames and pauses | P1 | 2 |
| Persistence across reload | P1 | 2 |
| PNG export contains the drawn pixel | P1 | 2 |
| Keyboard shortcuts + focus scoping | P2 | 1 |
| Edge pixels `(0,0)`, `(31,31)` | P2 | 1 |
| Empty undo / last layer/frame guards | P2 | 1 |
| Layout at 1280×800, no overflow | P2 | 1 |
| Status feedback | P2 | 1 |

v0 uses fixed coordinates so the reference is deterministic. The next
hardening step is a per-rep seed for draw coordinates, colours and frame order;
the contract already supports it.

### Randomisation (next hardening step)

The v0 suite is fixed. The next step is a per-rep seed for draw coordinates,
colours and frame order. The same seed is used for every arm in that rep, so
the comparison is fair; rep 2 uses a different seed to catch overfitting. The
model never sees the test file either way.

### Reference validation

Before running any model, run the suite against an instrumented copy of
`spriteforge`. It must pass 100%. If a test is flaky against the reference, fix
the test, not the model.

## 5. Harness

Clone the `planner-bench` shape:

```
spritebench/
  prompt/PROMPT.md
  prompt/SOURCE.sha256
  config/opencode.json
  config/arms.json
  tasks/spriteforge-lite/
    PROMPT.md
    seed/
    tests/                 # hidden
    reference/             # instrumented spriteforge; never visible to model
  tools/
    setup.sh
    bench.py               # check | run | grade | report | probe
    perf-proxy.py           # reused from planner-bench
  runs/
  results.csv
  report.md
```

### Run flow

1. Copy the seed into `runs/<task>/<arm>/r<rep>/workdir`.
2. Start the perf proxy; write a per-run OpenCode config with the arm pinned.
3. Start the timer; run `opencode run` with the task prompt (or `--agent`).
4. At exit or watchdog, kill the process group; record `watchdog`.
5. If needed, run `npm run build` once; record exit code and duration.
6. Start `vite preview` on a random port; wait for HTTP 200.
7. Copy hidden tests to `runs/.../e2e/`; run
   `npx playwright test --reporter=json` with `BASE_URL`.
8. Collect console errors, page errors, screenshot at 1280×800.
9. Write `meta.json`, `test-results.json`, `perf.jsonl`, `results.csv`.

### Metrics

| Metric | Source |
|---|---|
| `wall_s` | harness timer around the agent run |
| `agent_think_s` | sum of model request durations |
| `tool_s` | `wall_s - agent_think_s` |
| `time_to_first_build` | first successful `npm run build` |
| `time_to_completion` | `COMPLETION.md` or exit |
| `watchdog` | hard safety or inactivity timeout fired |
| `build_ok`, `typecheck_ok` | harness commands |
| `p0_pass`, `p1_pass`, `p2_pass` | Playwright JSON |
| `quality` | weighted pass rate |
| `major_issues`, `minor_issues` | failed P1/P2 tests |
| `console_errors`, `page_errors` | browser events |
| `tokens_in/out/reasoning` | OpenCode JSON + proxy |
| `ttft_ms`, `prefill_tps`, `decode_tps` | proxy |
| `cached_tokens`, `draft_accept_pct` | proxy |
| `peak_ram/VRAM` | OS tooling, optional |
| `screenshot` | Playwright |

The proxy is the same one used by `planner-bench`; it already observes server
vs client timing and records reasoning tokens when the provider reports them.
Do not interpret `reasoning_tokens=0` as "no thinking" — check the raw stream.

### Agent self-testing is part of the task

No browser is an unrealistic handicap. A real coding agent can run the app,
open DevTools, write a Playwright check, see a console error and fix it. If the
benchmark removes that, it measures blind code generation, not product work.

So the agent container gets:

- Chromium and `@playwright/test` (or the `chrome-devtools` MCP, or both);
- a `public-tests/` suite in the seed — 5–6 smoke checks it can run and modify;
- its own local preview server;
- the same `data-testid` contract as the hidden suite.

The public suite is deliberately a small subset: page loads, canvas exists,
draw one pixel, undo, export. It gives the agent a feedback loop without
revealing the edge cases, random coordinates or P1/P2 coverage of the hidden
suite. The agent may also write its own tests; neither its tests nor the public
tests are scored.

The hidden suite remains outside the agent mount. The agent can test its own
app; it cannot read the acceptance tests.

**Time accounting.** Self-testing is real work, so it counts in `wall_s`. Do not
subtract it; that would reward a model for shipping blind. But record the
breakdown so you can see what the time means:

- `agent_think_s` — sum of model request durations from the proxy;
- `tool_s` — `wall_s - agent_think_s` (browser, build, edits, test runs);
- `time_to_first_build` — first successful `npm run build`;
- `time_to_completion` — `COMPLETION.md` or process exit.

A model that spends ten minutes debugging and still fails loses on both quality
and time. A model that spends ten minutes debugging and passes has a real
quality gain but a real time cost; the Pareto table shows it. That is the
tradeoff you want to see, not hide.

### Keeping the tests out of reach

Do not rely on hidden folders, `.gitignore`, obfuscated names or a polite
prompt. A model with shell access can run `find /`, `ls ..`, `cat ../...`,
inspect environment variables and read anything its user can read. If the tests
are on the same filesystem as the workdir, assume they are discoverable.

The rule is: **the tests must not exist in the agent's mount, and they must not
be readable by the agent's user.**

Recommended isolation, strongest first:

1. **Container or VM per run (preferred).**
   - The agent gets only the workdir mounted at `/work`.
   - Do not mount `spritebench/`, the user's home, the Docker socket, or the
     test directory.
   - The model server stays outside; the agent container reaches it through
     `host.docker.internal` (Mac/Windows) or the host gateway (Linux).
   - Hidden tests live on the host, or in a separate test container that is
     started only after the agent is dead.
   - Use an empty `/tmp`, a fresh home, no shell history.
   - On the Mac, Docker Desktop or a small Linux VM (Lima/UTM) works. On the
     PC, Docker/Podman works; the agent container needs no GPU because Strata
     runs on the host.
2. **Two-machine isolation (practical with your setup).**
   - Keep the hidden tests and the Playwright harness only on the *other*
     machine. The agent host gets the workdir and the model endpoint, nothing
     else. After the run, copy only `workdir/` to the test machine and evaluate
     there.
   - This is stronger than same-machine permissions and requires no container.
   - Remove SSH keys/agent forwarding from the agent host, or the model could
     reach the test machine. Do not expose the test directory over the network.
3. **Separate Unix user with restrictive permissions.**
   - Create `spritebench-agent`; make `spritebench/` mode `700` owned by your
     main user; run OpenCode as the agent user.
   - This is weaker than a container: the agent shares the kernel and can read
     anything world-readable. Audit parent directory permissions first.
4. **macOS `sandbox-exec` profile.**
   - Deny `file-read*` on the benchmark directory, allow system paths and the
     workdir.
   - It is deprecated and not a hardened security boundary; use it only when a
     container/VM is not possible.
5. **Do not use the same directory with tests hidden by name.** That is
   concealment, not isolation.

Also:

- Do not put test paths in the prompt, environment, shell history, git history,
  npm cache or `package.json`.
- The agent **does** get Playwright/Chromium and a small public smoke suite, so
  it can test its own app. The hidden suite is still only in the test
  container. Do not confuse "no hidden tests" with "no browser".
- Keep the reference implementation outside the agent mount. The model should
  not be able to copy `spriteforge/` either.
- Disable or block `webfetch`/`websearch` if the benchmark is ever published.
  While it is private, network access is a smaller risk than filesystem access,
  but blocking egress is still cleaner.
- Watch `COMPLETION.md`; once it appears, give the agent a short grace period
  (for example 60 seconds) and stop the run. That is the task's own completion
  signal, not a scoring cap.

### Playwright reliability

The harness owns Playwright; the app does not. Pin the harness versions and do
not trust the model's `node_modules` or test scripts.

**Build stage, in a clean container without tests:**

1. Copy the workdir.
2. `npm ci --ignore-scripts --offline` from the seed lockfile. If the model
   added dependencies and they are not in the cache, the build fails — which is
   the contract.
3. `npm run typecheck` and `npm run build`.
4. Verify `dist/index.html` and the asset files exist.

**Serve stage:**

- Serve `dist/` with the harness's own static server on `127.0.0.1:<random>`;
  do not rely on the model's `npm run preview` or `npm run dev`.
- Wait for `GET /` to return 200 and for the HTML to contain `#app`.
- Use a fresh browser context per test, `serviceWorkers: 'block'`, and clear
  `localStorage` before each test. The app autosaves; state leaks otherwise.
- Wait for `[data-testid="canvas"]` and `window.__spriteforge` before acting.

**Test stage, in the Playwright container:**

- Mount `dist/` read-only and the hidden tests read-only. The app is served
  over HTTP; the browser cannot read the test files.
- Use `@playwright/test` pinned in the harness, Chromium pinned by
  `playwright install --with-deps chromium`, viewport 1280×800, DPR 1.
- `workers: 1`, per-test timeout 20 s, `expect` timeout 5 s. No retries for the
  score; optionally rerun a failure once to distinguish flake from a real
  failure.
- Capture `trace`, `video` and `screenshot` on failure. Also capture browser
  `console` and `pageerror` events.
- Distinguish **app failure** (assertion failed) from **harness failure**
  (browser missing, server did not start, Playwright crashed). Never score a
  harness failure as a model failure.

**Stability details:**

- Use `expect.poll` / `waitForFunction` for canvas and playback assertions.
- Read canvas pixels with a helper that accounts for backing size
  (`canvas.width / 32`), not hardcoded 16.
- Set range inputs with `evaluate` + `input` event; `fill()` is unreliable for
  `input[type=range]`.
- Handle downloads with `page.waitForEvent('download')`, save the file, parse
  it with `pngjs`. Do not accept a screenshot as a PNG export.
- Run the suite three times against the instrumented reference; it must pass
  100% every time before it is used on models.
- Run a one-test smoke first (page loads, canvas exists, state API exists). If
  smoke fails, stop and report `app_fail` or `harness_error` with a screenshot
  and the browser console.

### One command, no manual churn

The only interface should be:

```bash
./bench.py models
./bench.py run --model pc/ista-iq3_xxs --goal balanced
./bench.py report
```

`run` does the whole sequence:

1. Resolve the model from `config/models.json`.
2. Preflight the endpoint (`GET /v1/models`); fail with a clear message if
   Splash/Strata is not running.
3. Create `runs/<timestamp>-<model>/` and copy the seed into `workdir/`.
4. Generate a per-run OpenCode config containing only that provider/model.
5. Start the **agent container** with only `workdir/` and the config mounted.
   It has Node, Chromium and Playwright for its own testing, plus a public
   smoke suite in the seed. It has no hidden tests, no reference, no host home
   and no Docker socket.
6. Wait for `COMPLETION.md` (60 s grace), process exit, or watchdog.
7. Copy `workdir/` to an eval directory.
8. Run the **build container** without tests: `npm ci --ignore-scripts
   --offline`, `npm run typecheck`, `npm run build`.
9. Run the **test container** with `dist/` and the hidden tests mounted
   read-only; serve `dist/` and run Playwright.
10. Parse results, write `score.json` and `report.md`, append `results.csv`,
    and print the score.

Typical output:

```
pc/ista-iq3_xxs                              rep 1
  build   ok        typecheck ok
  P0      6/6       P1 8/9       P2 5/7
  quality 0.92      minor 2      major 1
  wall    18m 42s
  tokens  (proxy hook; not wired in v0)
  report  runs/2026-11-02T14-05-22-pc-ista-iq3_xxs/report.md
```

`./bench.py models` prints the configured list; `./bench.py run` with no
`--model` shows the same list as a numbered menu. `--reps 2` repeats and
aggregates. `--all` runs every configured model (use with care; no scoring cap).
`--dry-run` prints the Docker commands and mounts without executing.

### Model config

`config/models.json` is the only thing to edit when adding a model:

```json
{
  "models": {
    "mac/qwen3.8-27b": {
      "machine": "mac-splash",
      "provider": "splash",
      "base_url": "http://192.168.10.112:8000/v1",
      "model": "unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M",
      "context": 32768,
      "thinking": "medium"
    },
    "pc/ista-iq3_xxs": {
      "machine": "pc-strata",
      "provider": "strata",
      "base_url": "http://192.168.10.136:8080/v1",
      "model": "ista-flash-next-iq3_xxs",
      "context": 32768,
      "thinking": "medium"
    }
  }
}
```

API keys come from environment variables (`SPLASH_API_KEY`,
`STRATA_API_KEY`, or `BENCH_API_KEY` as a fallback), never from the JSON.
The harness writes a temporary `opencode.json` per run and mounts it read-only.

### What Docker hides — and what it does not

Dockerizing the **tests** does not hide them from an agent running on the host.
The agent must be the isolated process:

- agent container: mounts `workdir/` and the per-run config only. It contains
  Chromium/Playwright and the seed's `public-tests/`, but never the hidden
  `tests/`;
- build container: mounts `workdir/` only;
- test container: mounts `dist/` and hidden `tests/` read-only.

The host keeps the tests. The agent container never mounts the host home, the
benchmark directory, the reference app or the Docker socket. That is why the
agent cannot read the tests even though it can run `find /` inside its
container.

### Commands for development

```bash
./bench.py check
./bench.py models
./bench.py run --model mac/qwen3.8-27b --dry-run
./bench.py run --model mac/qwen3.8-27b
./bench.py report
```

The low-level `check` command validates the prompt hash, seed build, reference
suite and Docker images. It is for development; the normal workflow is the three
commands at the top.

## 6. Scoring and decision

```
fatal   = P0 failures
major   = P1 failures
minor   = P2 failures
quality = (3*P0_pass + 2*P1_pass + 1*P2_pass) / (3*P0_total + 2*P1_total + 1*P2_total)
```

A run is **eligible** only if:

- `fatal == 0`
- `quality >= 0.80`
- the app builds and previews

Do not hide the quality/time tradeoff behind one arbitrary weight. Pick a goal:

- `--goal speed`: among eligible runs, lowest median `wall_s` wins; quality is
  only a gate.
- `--goal quality`: highest `quality` wins; `wall_s` is the tie-break.
- `--goal balanced`: highest `quality` among runs within 20% of the fastest
  eligible time; if none qualify, fall back to the fastest eligible run.

All three modes still require `fatal == 0` and `quality >= 0.80`. Report the
full Pareto table, not just the winner:

| Arm | Quality | Time | Minor | Major | Fatal | Verdict |
|---|---:|---:|---:|---:|---:|---|
| PC / Swift IQ3_XXS | 0.92 | 14m | 2 | 0 | 0 | eligible |
| PC / ISTA IQ3_XXS | 0.95 | 19m | 1 | 0 | 0 | eligible |
| Mac / Qwen3.8-27B | 0.88 | 11m | 3 | 0 | 0 | eligible |
| Mac / Flash-Next Q2_0 | 0.90 | 17m | 2 | 0 | 0 | tight memory |

The decision is allowed to be "both are fine; use the faster one for the
implementer and the higher-quality one for the planner." That is the point.

## 7. Experiment matrix

### Mac 36 GB + Splash

- `qwen3.8-27b` — UD-Q4_K_M; the safe baseline.
- `qwen3.8-flash-next` — ISTA Q2_0; tight memory, run with everything else closed.
- `qwen3.6-35b-a3b` — UD-Q4_K_M; fast fallback.
- `swift-1.5-27b` — existing arm, if available.

### PC 6800XT 16 GB + Strata

- `qwen3.6-35b-a3b` — UD-Q4_K_M; likely the practical speed winner.
- `swift-1.5-flash-next` — IQ3_XXS; current setup.
- `ista-flash-next` — IQ3_XXS; the main unknown.
- `ista-flash-next` — IQ2_XS; faster, lower quality.
- Gyro is probably out on RDNA2; check support before downloading.

Fixed settings per run:

- context 32K (the task does not need 262K; using the full window would
  conflate memory pressure with app quality);
- KV cache q8_0;
- temperature 0.6, top_p 0.95, top_k 20, min_p 0.0;
- thinking effort `medium` (record it; do not compare xhigh vs none by accident);
- same OpenCode agent prompt, same tools, same watchdog settings.

Start with **one rep per arm** (pilot). Promote the top two per machine to a
second rep. If the top two are within 10% on time and 5% on quality, run a
third. With no scoring cap the calendar time depends on the models; the pilot
is the point where you decide whether the matrix is worth expanding.

## 8. Avoiding a rigged benchmark

The planner-bench critique applies here too. Mitigations:

- **Build, don't recite.** The app must boot and pass browser tests; prose is
  worthless.
- **Hidden, randomised tests.** The prompt gives the contract, not the cases.
  Coordinates, colours and frame order vary by rep.
- **Behaviour, not code.** Tests click, drag, read canvas pixels, reload the
  page and parse a PNG. They do not grep for function names.
- **Custom app.** SpriteForge is not a standard benchmark task; the reference
  is local and not public.
- **Tiered scoring.** P0 prevents a beautiful broken app; P2 separates "works"
  from "works well".
- **Held-out second task.** After v0, add a small CRUD/notes app with forms and
  persistence. A model that only knows canvas code will not automatically win.
- **Reference validation.** The suite must pass 100% on the instrumented
  reference before it is used to judge models.
- **Calibration.** If all arms pass everything, the task is too easy; add P2 or
  tighten the quality bar. If all fail P0, the task is too hard; reduce scope.

## 9. Implementation phases

### Phase 0 — instrument the reference

- Add `data-testid` attributes to the existing `spriteforge` UI.
- Add `window.__spriteforge` with `getState/loadState/reset`.
- Add the PNG export button. Export is required in v0.
- Write the Playwright suite; make it pass 100% on the reference.
- Freeze the reference and its test results.

### Phase 1 — seed and prompt

- Strip the app to a placeholder seed that still builds.
- Write `PROMPT.md` with the contract and P0/P1/P2.
- Add `check` validation: prompt hash, seed builds, tests pass on reference.
- Run one pilot arm on the PC (fastest box) to calibrate task scope and check
  that the watchdog is not firing during normal work.

### Phase 2 — matrix

- Run the pilot matrix (one rep per arm).
- Promote the top two per machine; run the second rep.
- Grade, report, inspect screenshots, write verdict.

### Phase 3 — extend

- Add `sprite-fix`: a broken seed + bug report, 10–15 minutes.
- Add a second app (notes/kanban) to test generalisation.
- Optionally add a VLM visual rubric; keep it secondary.

## 10. Open decisions

1. **Require the model's own unit tests?** Probably not for scoring. It spends
   time and can be gamed; hidden browser tests are enough.
2. **Visual rubric?** One screenshot per run and a 0–2 human/VLM score is
   cheap, but it reintroduces subjectivity. Use it as a note, not the verdict.
3. **Run both machines in parallel?** Yes for wall time; keep one request per
   machine and record thermal state.
4. **Watchdog values.** 4 hours hard, 30 minutes inactivity. Adjust only if a
   legitimate slow model trips them.
