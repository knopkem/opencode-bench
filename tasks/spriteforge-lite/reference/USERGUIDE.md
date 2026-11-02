# SpriteForge User Guide

Open the app (`npm run dev`, then visit http://localhost:5173, or
`npm run build && npm run preview`). Everything lives on one screen:
tools on the left, canvas in the middle, palette/layers/playback on the right,
filmstrip across the bottom.

**Keyboard shortcuts only work when the canvas area has focus.** Click once on
the canvas (or Tab to it) before using tool keys. A blue border around the
canvas indicates it has focus. Shortcuts never fire while you are typing in
inputs elsewhere.

## Canvas & drawing

The canvas is 32×32 pixels displayed at 512×512 (nearest-neighbor, crisp
pixels). Transparency is shown as a checkerboard.

All tools appear in the Tools palette on the left; click a button or press its
key:

| Tool | Key | Behavior |
|---|---|---|
| Pencil | `B` | Left-click paints pixels; drag to paint a continuous stroke. Right-click erases. |
| Eraser | `E` | Paints transparency instead of color. Right-click also erases with any draw tool. |
| Line | `L` | Drag from start to end; a live preview follows the cursor; release commits. Right-drag erases the line. |
| Rectangle | `R` | Drag defines the bounding box; outline drawn with live preview. Right-drag erases. |
| Ellipse | `O` | Drag defines the bounding box; ellipse outline with live preview. Right-drag erases. |
| Bucket Fill | `G` | Click fills the contiguous region of the clicked pixel's color with the active palette slot. Right-click fills that region with transparency. |
| Eyedropper | `I` | Click a pixel to make its palette slot the active color. |

Brush size: press `[` or `]` to cycle between **1px** and **2px** (2px draws a
2×2 block anchored at the clicked pixel). The current size shows under the
tool buttons; "1px"/"2px" buttons do the same thing.

Undo / redo are unlimited: `Ctrl+Z` (undo), `Ctrl+Shift+Z` (redo, also
`Cmd+Shift+Z` on macOS). Toolbar buttons "Undo Ctrl+Z" / "Redo Ctrl+Shift+Z"
mirror them. Each stroke, fill, shape, layer/palette/frame operation, setting
change, scrub-free frame edit, etc. is one undo step.

## Palette

Right panel, top. The big swatch labeled **active** always shows the currently
selected palette slot (slot number + hex).

- **Select a slot**: click any swatch (highlighted with an amber outline).
- **Native color picker**: the small color square opens your OS color picker.
  - **Apply to slot** recolors the selected slot with the picked color.
  - **Add color** appends the picked color as a new palette slot (max 32) and
    selects it.
- **Remove** deletes the selected slot. Pixels using it become transparent;
  higher slots shift down to keep indices consistent.
- **◀ / ▶** reorder the selected slot within the palette; every pixel using
  affected slots is remapped automatically.
- **Preset dropdown**: switch between *Default (16)*, *Neon*, and *Pastel*.
  Applying a preset keeps pixels whose slot numbers still exist in the new
  palette and clears out-of-range ones. The per-document palette is saved in
  the project file.

## Layers

Middle-right panel. Rows are listed **top-to-bottom in composite order** (the
first row renders above all others). The active layer has a blue border —
click any row to make it active (drawing targets it).

- **+ Add** adds a new empty layer (max 8 per frame); **Delete** removes the
  active layer (at least one layer must remain).
- **▲ / ▼** move the active layer up/down in composite order. You can also
  **drag rows** onto each other to reorder.
- **Rename**: double-click the name, type, press Enter (or blur).
- **Visibility checkbox** toggles the layer on/off.
- **Opacity slider (0–100%)** blends live into the composited canvas.

## Frames & animation

Bottom strip: thumbnails of every frame, numbered `frame·hold`, the active
frame outlined in amber.

- **+ Add frame** inserts an empty frame after the active one (structure copied
  from active layers), **Duplicate** copies the active frame, **Delete frame**
  removes the active frame. **◀ Move frame / Move frame ▶** reorder; you can
  also **drag thumbnails** onto each other. Max 64 frames.
- **Scrub**: click any thumbnail to jump to that frame.
- **Hold:** the "Hold" box sets the active frame's hold duration (1–24 units
  of the playback rate). A frame displays for `hold ÷ fps` seconds.
- **Transport** buttons: **⏹ Stop** (pause + return to frame 1), **← Step** /
  **→ Step** move one frame (keyboard `ArrowLeft` / `ArrowRight`),
  **▶ Play / Pause** starts/stops playback (`Space`). Playback advances at
  `fps` (configurable 1–24 in the right panel) honoring per-frame holds; with
  **Loop** checked it wraps around, otherwise it stops on the last frame.
- **Onion skin (prev frame)** slider (0–100%): draws the previous frame
  flattened underneath the current one at the chosen opacity, so you can align
  animation. Setting it to 0 disables it.

## Persistence

Every edit autosaves the complete project (pixels, layers, palette, fps, loop,
holds) as JSON to localStorage and restores it when you reopen the page. If
stored data is invalid, the app safely starts a fresh document.

## Keyboard reference

| Key | Action |
|---|---|
| `B` / `E` | Pencil / Eraser tool |
| `L` / `R` / `O` | Line / Rectangle / Ellipse tool |
| `G` / `I` | Bucket Fill / Eyedropper tool |
| `[` / `]` | Cycle brush size 1px ↔ 2px |
| `Ctrl+Z` | Undo (unlimited) |
| `Ctrl+Shift+Z` | Redo (unlimited) |
| `Space` | Play / Pause |
| `ArrowLeft` / `ArrowRight` | Step back / forward one frame |

All keys require canvas focus (click the canvas first).
