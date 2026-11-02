# SpriteForge Lite — agent notes

The full task and the mandatory test contract are in `/work/PROMPT.md`. Read it
first.

## Stack

- TypeScript + Vite. Do not add dependencies.
- `npm run build` must succeed. `npm run typecheck` is part of the build.
- `npm run test:e2e` runs the public smoke tests in `public-tests/`.
- Chromium and Playwright are installed. You may add your own checks under
  `tests-local/` or use the chrome-devtools MCP.

## Rules

- Do not ask questions. Make reasonable decisions and record them in
  `DECISIONS.md`.
- Do not look for hidden acceptance tests; they are not in this container.
- The public tests are a subset. Passing them is necessary, not sufficient.
- When done, write `COMPLETION.md` and stop.

## Contract

`data-testid` names and the `window.__spriteforge` API are specified in
`PROMPT.md`. They are mandatory; the hidden browser tests use them.

## Useful commands

```bash
npm run build
npm run test:e2e
npm run dev
```
