# SpriteForge Lite — build task

You are building a single-page browser app in the current directory. The seed
already builds; it contains only a placeholder. Your job is to turn it into a
working sprite editor.

There is **no fixed time limit**; work until the app is complete. Do not ask
questions. If you cannot finish everything, finish P0 first, then P1, then P2.
Write `COMPLETION.md` at the end and stop. A watchdog stops only a hung run, not
a slow one.

A hidden Playwright suite will exercise the app. The contract below is
mandatory; missing IDs or API methods count as failures. The tests are not
shown, and they use randomised coordinates and colours. Do not try to detect or
special-case tests.

## Non-negotiable

- TypeScript + Vite. Keep the existing `package.json` scripts.
- `npm run build` must succeed.
- No new dependencies. No network. No external assets or fonts.
- No console errors or unhandled page errors during normal use.
- The app must work at 1280×800.
- Do not modify the hidden tests. Do not add a test runner that replaces them.
- Playwright, Chromium and `public-tests/` are available. Use them to test your
  own app; you may add tests under `tests-local/`. None of your tests are the
  acceptance criteria; the hidden suite is.
- You may use the chrome-devtools MCP if your client exposes it.

## Required test contract

### DOM

| Element | Selector | Behaviour |
|---|---|---|
| Canvas | `[data-testid="canvas"]` | 512×512 CSS px; 32×32 logical px; nearest-neighbour |
| Tools | `[data-testid="tool-pencil"]`, `tool-eraser`, `tool-line`, `tool-rect`, `tool-ellipse`, `tool-fill`, `tool-eyedropper` | Click selects; active tool has `data-active="true"` |
| Palette | `[data-testid="color-0"]` … `color-N` | Click selects; active colour has `data-active="true"` |
| Brush | `[data-testid="brush-1"]`, `[data-testid="brush-2"]` | Select 1px / 2px; active one has `data-active="true"` |
| Undo / redo | `[data-testid="undo"]`, `[data-testid="redo"]` | Click applies history |
| Layers | `[data-testid="layer-add"]`, `[data-testid="layer-delete"]`, `layer-list`, `layer-row-0`…, `layer-visible-0`…, `layer-opacity-0`… | Add, delete, toggle, opacity |
| Frames | `[data-testid="frame-add"]`, `[data-testid="frame-duplicate"]`, `[data-testid="frame-delete"]`, `frame-list`, `frame-0`… | Add, duplicate, delete, select |
| Transport | `[data-testid="play"]`, `[data-testid="pause"]`, `[data-testid="fps"]` | Play/pause; FPS input |
| Export | `[data-testid="export"]` | Downloads `spriteforge.png` |
| Status | `[data-testid="status"]` | Human-readable feedback |

Indexes are display indexes: `layer-row-0` is the top row, `frame-0` is the
first frame. Active layer/frame elements also carry `data-active="true"`.

### State API

Expose exactly this on `window`:

```ts
window.__spriteforge = {
  getState(): {
    width: 32;
    height: 32;
    fps: number;
    loop: boolean;
    palette: string[];              // lowercase #rrggbb
    activeFrame: number;
    activeLayer: number;            // index in frames[activeFrame].layers; 0 = bottom
    tool: string;
    brush: 1 | 2;
    frames: Array<{
      hold: number;
      layers: Array<{
        name: string;
        visible: boolean;
        opacity: number;            // 0..1
        pixels: number[];           // 1024 entries, row-major; -1 = transparent, else palette index
      }>;
    }>;
  };
  loadState(state): void;
  reset(): void;
};
```

`getState()` must return a deep copy. `loadState()` replaces the current
document and re-renders. `reset()` clears persistence and returns to the
default document.

### Persistence

- `localStorage` key: `spriteforge.project`.
- Value: JSON with `format: "spriteforge"`, `version: 1`, the same fields as
  `getState()`.
- Autosave after every edit. Restore on boot. Invalid data falls back to a
  fresh document.
- Tests will write a project, reload, and assert both `getState()` and the
  rendered canvas.

### Export

- Clicking `[data-testid="export"]` downloads `spriteforge.png`.
- The PNG is 32×32 and contains the composited visible layers of the active
  frame.

### Canvas, input and shortcuts

- Logical pixel `(x, y)` is at CSS offset `(x * 16 + 8, y * 16 + 8)`.
- `pixels` is row-major, 32×32, `-1` transparent, otherwise a palette index.
- Left click draws; right click erases.
- Pencil/eraser support brush sizes 1 and 2.
- Shortcuts fire only while the canvas area has focus:
  `B`, `E`, `L`, `R`, `O`, `G`, `I`, `[`, `]`, `Ctrl+Z`,
  `Ctrl+Shift+Z`, `Space`, `ArrowLeft`, `ArrowRight`.
- Do not intercept shortcuts while an input/select/textarea is focused.
- Out-of-bounds clicks are ignored.
- Undo on empty history and deleting the last layer/frame must not crash; show
  a status message instead.

### Layout

At 1280×800:

- canvas is 512×512 and fully visible;
- toolbar, palette, layers and frame strip are visible;
- no horizontal page scroll;
- no panel overlaps the canvas.

## Features

### P0 — must work

- App mounts; canvas exists at 512×512 CSS / 32×32 logical; no console or page
  errors.
- Default document: 1 layer, 1 frame, 16-colour palette, fps 8, loop on.
- Pencil draws a pixel; eraser clears it.
- Undo restores the previous pixel; redo reapplies it.

### P1 — the product

- Palette selection changes the drawn colour.
- Bucket fill fills a contiguous region.
- Brush size 1 vs 2.
- Line, rectangle and ellipse drag tools with preview.
- Layers: add, delete, visibility toggle, opacity 0–100%; live compositing.
- Frames: add, duplicate, delete, select; each frame has its own pixels.
- Playback: play/pause/stop/step at the configured FPS; loop toggle.
- Persistence: autosave/restore via `localStorage`.
- PNG export: 32×32 composited active frame.

### P2 — polish and edge cases

- Keyboard shortcuts and focus scoping.
- Draw at `(0,0)` and `(31,31)`; out-of-bounds clicks ignored.
- Undo on empty history; deleting the last layer/frame is refused gracefully.
- Palette add/remove/reorder preserves pixel indices (or clearly marks pixels
  using a removed colour as transparent).
- Onion skin of the previous frame with opacity.
- Status text updates for tool/frame/undo.
- No placeholder text, no dead buttons, no layout overflow.

## Out of scope

Accounts, collaboration, cloud sync, audio, image filters, mobile/touch,
additional export formats, i18n, theming.

## Completion

When done, write `COMPLETION.md`:

```
section: status (met/partial) — notes
```

Cover: canvas/drawing, palette, layers, frames/animation, persistence/export,
layout, test contract. Then stop. Do not keep polishing after the completion
file is written.
