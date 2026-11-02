## Mission

Build **SpriteForge**, a browser-based animated sprite studio: a single-page
app for creating, editing, previewing, and exporting small pixel-art
animations. It must be a complete, working product — buildable, tested, and
documented — not a demo.

## Non-negotiable constraints

- TypeScript + Vite
- `npm install && npm run build` produces a working app; `npm test` runs the
suite with Vitest; `npm run typecheck` passes; all three scripts exist
- No console errors during normal use (except missing navicon 404)
- You cannot ask questions. Make reasonable decisions and record each in
`DECISIONS.md` — append-only, one line per decision: *what was decided + a
one-sentence rationale*

## Feature specification

### 1. Canvas & drawing

- [ ] 32×32 pixel canvas rendered at 512×512 with nearest-neighbor upscaling
- [ ] Pencil tool: left-click draws, right-click erases; brush sizes 1px and
2px; keyboard `B`, `E`; `[` / `]` cycle brush size
- [ ] Line, rectangle, and ellipse tools with drag preview; keys `L`, `R`, `O`
- [ ] Bucket fill (`G`) and eyedropper (`I`)
- [ ] Unlimited undo/redo: `Ctrl+Z` / `Ctrl+Shift+Z`
- [ ] Shortcuts fire only when the canvas area has focus and do not clobber
browser defaults elsewhere
- [ ] all tools available in a tools palette

### 2. Palette

- [ ] Default 16-color palette; at least 2 switchable presets
- [ ] Palette editor: add the current color, remove, reorder
- [ ] Custom color via a native color picker; active color swatch always
visible; per-document palette is saved in the project file

### 3. Layers

- [ ] At least 4 layers; add, delete, rename, reorder (drag or buttons)
- [ ] Per-layer visibility toggle and opacity (0–100%) with live alpha
compositing
- [ ] Layers panel lists layers top-to-bottom in composite order

### 4. Frames & animation

- [ ] Up to 64 frames; filmstrip shows editable thumbnails (active frame
highlighted)
- [ ] Add, duplicate, delete, and reorder frames (drag or buttons)
- [ ] Per-frame hold duration (1–24 units of the playback rate);
playback FPS configurable 1–24; loop toggle
- [ ] Playback controls: play/pause, stop, step-forward, step-back; scrub by
clicking the filmstrip
- [ ] Onion skin of the previous frame with a configurable opacity slider

### 5. Import & export

- [ ] not needed

### 6. Documentation & completion

- [ ] `README.md`: build, run, test instructions; feature overview
- [ ] `USERGUIDE.md`: walks through every feature above by name, including
every keyboard shortcut
- [ ] `COMPLETION.md`: when you believe all criteria are met, one line per
section — `section: status (met/partial) — notes`. This is the
completion signal; nothing after it is expected

## Quality bar

- [ ] The pure model logic — document/frame/layer/palette state, compositing,
encoders, project serialization — is covered by unit tests (UI glue is
exempt); the suite is meaningful, not placeholder assertions
- [ ] `npm run typecheck`, `npm run build`, `npm test` all green
- [ ] The app is usable at 1280×800 with no layout breakage and no console
errors

## Out of scope — do not build

Collaboration or accounts, cloud sync, audio, image filters or effects
beyond the listed tools, mobile/touch support, additional export formats,
i18n, theming.


