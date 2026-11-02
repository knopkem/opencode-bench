# Decisions

Append-only log of design decisions: what was decided + rationale.

- Pixels are stored per layer as palette slot indices (-1 = transparent), not raw hex colors — reordering/removing palette slots stays consistent by remapping pixel indices across all frames/layers.
- New layers begin fully transparent — a new document opens as a blank checkerboard instead of opaque black, matching animator expectations.
- Each frame owns its own copy of the active frame's layer list (add/duplicate copies structure) — sprite animation convention where frames share a structure but have independent pixels.
- Removing a palette color turns pixels using that slot transparent and shifts higher indices down — keeps every pixel's identity consistent after removal.
- Applying a preset preserves pixels whose slot index exists in the new palette and clears out-of-range ones — non-destructive preset switching on shared 16-color palettes.
- Line/Rectangle/Ellipse draw outlines only (no fill modes) — the spec lists shape tools with drag preview, and Bucket Fill already covers filling.
- Ellipse uses a parametric sweep plus explicit axis-extreme points — guarantees the four bounding-box edge midpoints land exactly, without Bresenham arc complexity.
- The 2px brush draws a 2×2 block anchored at the clicked pixel — predictable behavior and clean clipping at canvas edges.
- History stores full deep-cloned state snapshots per commit with unlimited depth — trivially correct for "unlimited undo/redo"; one commit per stroke/operation keeps step semantics natural.
- A new commit clears the redo stack, and undo restores the pre-edit snapshot — standard editor semantics animators expect.
- Stop pauses playback and returns to frame 1 — conventional transport behavior for animation editors.
- Step forward/back clamps at the ends while looping playback wraps and stops at the last frame when Loop is off — deterministic scrubbing next to wrap-around playback.
- Keyboard shortcuts are bound to the focusable canvas wrapper (tabindex=0), never window-level — guarantees shortcuts fire only when the canvas area has focus and cannot clobber browser defaults elsewhere.
- The project autosaves serialized JSON to localStorage after each commit and validates it on load (fresh document on corruption) — satisfies "project file" persistence since import/export UI is out of scope, with no console errors from storage failures.
- Playback is scheduled with a setTimeout chain computing duration = hold ÷ fps seconds — per-frame hold durations require variable timing that a fixed interval cannot express.
- Onion skin flattens the previous frame first, then composites it under the current frame at slider opacity — matches onion-skin conventions and keeps alpha math single-pass.
- Three built-in presets (Default 16, Neon, Pastel) plus free custom slots via native `<input type=color>` — exceeds the two-switchable-presets requirement with zero external asset loading.
- Limits: max 8 layers/frame, max 32 palette colors, 64 frames, fps 1–24, hold 1–24 — bounded serialization size while satisfying all minimums (≥4 layers).
- Opacity sliders push one history entry per change event — unlimited undo absorbs slider-drag spam without special-casing coalescing.
- No favicon link in index.html; missing navicon 404 is accepted as allowed by the spec — avoids any network fetch that could produce console errors.
