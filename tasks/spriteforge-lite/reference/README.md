# Reference implementation

This is `spriteforge` from `~/projects/spriteforge`, instrumented with the
benchmark's public test contract:

- `data-testid` handles for the canvas, tools, palette, brush, layers, frames,
  transport, export and status;
- `window.__spriteforge.getState()`, `loadState()` and `reset()`;
- a 32×32 PNG export button;
- separate play/pause controls.

It is **not** shown to the model. It exists to validate the hidden Playwright
suite.

## Validation

On 2026-11-02, against this build:

- public smoke suite: 4/4 passed;
- hidden suite: 20/20 passed three times in a row;
- `npm run build`: passed.

The hidden suite also caught a real bug in the reference: clicking a layer's
visibility checkbox re-rendered the layer list during `pointerdown`, so the
checkbox's `change` event never fired. The reference now ignores row-level
pointer handling when the target is an input.

## Rebuild and run the hidden suite locally

```bash
cd tasks/spriteforge-lite/reference
npm ci --ignore-scripts
npm run build

cd ../seed
APP_DIR="$(cd ../reference/dist && pwd)" \
APP_PORT=4173 \
SERVE_SCRIPT="$(cd ../../../docker && pwd)/serve.mjs" \
RESULT_FILE=/tmp/opencode-bench-reference.json \
NODE_PATH="$(pwd)/node_modules" \
npx playwright test --config ../tests/playwright.config.ts
```

The hidden suite expects the app at `APP_DIR`; the Docker runner supplies
`/app` and `/opt/opencode-bench/tests` instead.
