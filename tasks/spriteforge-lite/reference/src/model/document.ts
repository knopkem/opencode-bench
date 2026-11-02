import {
  W, H, MAX_FRAMES, MIN_LAYERS, MAX_LAYERS, MIN_FPS, MAX_FPS,
  MIN_HOLD, MAX_HOLD, TRANSPARENT, clamp, inBounds,
} from "./constants";
import { DEFAULT_PALETTE, validatePalette, isValidHex, MAX_PALETTE } from "./palette";

export interface Layer {
  id: number;
  name: string;
  visible: boolean;
  /** 0..1 */
  opacity: number;
  /** row-major W*H; TRANSPARENT (-1) = empty, else palette index */
  pixels: Int16Array;
}

export interface Frame {
  layers: Layer[];
  /** playback units, 1..MAX_HOLD */
  hold: number;
}

export interface DocState {
  width: number;
  height: number;
  fps: number;
  loop: boolean;
  palette: string[];
  frames: Frame[];
  activeFrame: number;
  activeLayerId: number;
  nextLayerId: number;
}

export function createEmptyPixels(): Int16Array {
  const px = new Int16Array(W * H);
  px.fill(TRANSPARENT);
  return px;
}

export function createEmptyLayer(id: number, name: string): Layer {
  return { id, name, visible: true, opacity: 1, pixels: createEmptyPixels() };
}

export function newDocument(palette?: string[]): DocumentModel {
  const doc = new DocumentModel();
  const p = palette ? [...palette] : [...DEFAULT_PALETTE];
  validatePalette(p);
  doc.state.palette = p;
  const layer = createEmptyLayer(doc.state.nextLayerId++, "Layer 1");
  doc.state.frames.push({ layers: [layer], hold: 1 });
  doc.state.activeLayerId = layer.id;
  return doc;
}

export class DocumentModel {
  state: DocState = {
    width: W, height: H, fps: 8, loop: true,
    palette: [], frames: [], activeFrame: 0, activeLayerId: -1, nextLayerId: 1,
  };

  snapshot(): DocState {
    return structuredClone(this.state);
  }

  restore(s: DocState): void {
    this.state = structuredClone(s);
    if (this.state.activeFrame >= this.state.frames.length) {
      this.state.activeFrame = this.state.frames.length - 1;
    }
    if (!this.activeLayer()) this.state.activeLayerId = this.activeFrame_().layers[0].id;
  }

  activeFrame_(): Frame {
    return this.state.frames[this.state.activeFrame];
  }

  activeLayer(): Layer | undefined {
    return this.activeFrame_().layers.find((l) => l.id === this.state.activeLayerId);
  }

  setFps(fps: number): void {
    this.state.fps = clamp(Math.round(fps), MIN_FPS, MAX_FPS);
  }

  setLoop(loop: boolean): void {
    this.state.loop = loop;
  }

  setActiveFrame(i: number): void {
    if (i < 0 || i >= this.state.frames.length) throw new Error(`frame out of range: ${i}`);
    this.state.activeFrame = i;
    if (!this.activeLayer()) this.state.activeLayerId = this.activeFrame_().layers[0].id;
  }

  setActiveLayer(id: number): void {
    if (!this.activeFrame_().layers.some((l) => l.id === id)) {
      throw new Error(`layer not in active frame: ${id}`);
    }
    this.state.activeLayerId = id;
  }

  setFrameHold(i: number, hold: number): void {
    const f = this.state.frames[i];
    if (!f) throw new Error(`frame out of range: ${i}`);
    f.hold = clamp(Math.round(hold), MIN_HOLD, MAX_HOLD);
  }

  addFrame(at?: number): Frame {
    if (this.state.frames.length >= MAX_FRAMES) throw new Error(`max ${MAX_FRAMES} frames`);
    const idx = at ?? this.state.frames.length;
    const clamped = clamp(idx, 0, this.state.frames.length);
    const f: Frame = { layers: [], hold: 1 };
    for (const l of this.activeFrame_().layers) {
      f.layers.push({ ...structuredClone(l), pixels: createEmptyPixels() });
    }
    this.state.frames.splice(clamped, 0, f);
    this.state.activeFrame = clamped;
    this.state.activeLayerId = f.layers[0].id;
    return f;
  }

  duplicateFrame(from?: number): Frame {
    const src = from ?? this.state.activeFrame;
    if (src < 0 || src >= this.state.frames.length) throw new Error("source frame missing");
    if (this.state.frames.length >= MAX_FRAMES) throw new Error(`max ${MAX_FRAMES} frames`);
    const copy = structuredClone(this.state.frames[src]);
    this.state.frames.splice(src + 1, 0, copy);
    this.state.activeFrame = src + 1;
    return copy;
  }

  deleteFrame(i: number): void {
    if (this.state.frames.length <= 1) throw new Error("cannot delete last frame");
    if (i < 0 || i >= this.state.frames.length) throw new Error(`frame out of range: ${i}`);
    this.state.frames.splice(i, 1);
    if (i <= this.state.activeFrame) this.state.activeFrame -= 1;
    this.state.activeFrame = clamp(this.state.activeFrame, 0, this.state.frames.length - 1);
    if (!this.activeLayer()) this.state.activeLayerId = this.activeFrame_().layers[0].id;
  }

  moveFrame(from: number, to: number): void {
    const n = this.state.frames.length;
    if (from < 0 || from >= n || to < 0 || to >= n) throw new Error("frame out of range");
    const [f] = this.state.frames.splice(from, 1);
    this.state.frames.splice(to, 0, f);
    this.state.activeFrame = from === this.state.activeFrame ? to : this.remapActive(from, to);
  }

  private remapActive(from: number, to: number): number {
    const a = this.state.activeFrame;
    if (from < to && a > from && a <= to) return a - 1;
    if (from > to && a >= to && a < from) return a + 1;
    return a;
  }

  addLayer(name?: string): Layer {
    const fr = this.activeFrame_();
    if (fr.layers.length >= MAX_LAYERS) throw new Error(`max ${MAX_LAYERS} layers`);
    const layer = createEmptyLayer(this.state.nextLayerId++, name ?? `Layer ${fr.layers.length + 1}`);
    fr.layers.push(layer);
    this.state.activeLayerId = layer.id;
    return layer;
  }

  deleteLayer(id: number): void {
    const fr = this.activeFrame_();
    if (fr.layers.length <= MIN_LAYERS) throw new Error("cannot delete last layer");
    const idx = fr.layers.findIndex((l) => l.id === id);
    if (idx === -1) throw new Error(`layer not in active frame: ${id}`);
    fr.layers.splice(idx, 1);
    if (this.state.activeLayerId === id) {
      this.state.activeLayerId = fr.layers[Math.min(idx, fr.layers.length - 1)].id;
    }
  }

  renameLayer(id: number, name: string): void {
    const l = this.activeFrame_().layers.find((x) => x.id === id);
    if (!l) throw new Error(`layer not in active frame: ${id}`);
    const trimmed = name.trim();
    if (!trimmed) throw new Error("layer name must be non-empty");
    l.name = trimmed.slice(0, 40);
  }

  moveLayer(fromIdx: number, toIdx: number): void {
    const fr = this.activeFrame_();
    const n = fr.layers.length;
    if (fromIdx < 0 || fromIdx >= n || toIdx < 0 || toIdx >= n) throw new Error("layer out of range");
    const [l] = fr.layers.splice(fromIdx, 1);
    fr.layers.splice(toIdx, 0, l);
  }

  setLayerVisible(id: number, visible: boolean): void {
    const l = this.activeFrame_().layers.find((x) => x.id === id);
    if (!l) throw new Error(`layer not in active frame: ${id}`);
    l.visible = visible;
  }

  setLayerOpacity(id: number, opacity: number): void {
    const l = this.activeFrame_().layers.find((x) => x.id === id);
    if (!l) throw new Error(`layer not in active frame: ${id}`);
    l.opacity = clamp(opacity, 0, 1);
  }

  fillLayer(layer: Layer, value: number): void {
    layer.pixels.fill(value);
  }

  drawPixels(layer: Layer, positions: Iterable<{ x: number; y: number }>, value: number): void {
    for (const p of positions) {
      if (inBounds(p.x, p.y)) layer.pixels[p.y * W + p.x] = value;
    }
  }

  // ---- palette ops (with pixel remap across all frames/layers) ----

  private remapAll(mapFn: (idx: number) => number): void {
    for (const f of this.state.frames) {
      for (const l of f.layers) {
        const px = l.pixels;
        for (let i = 0; i < px.length; i++) px[i] = mapFn(px[i]);
      }
    }
  }

  addColor(hex: string): number {
    if (!isValidHex(hex)) throw new Error(`invalid color: ${hex}`);
    if (this.state.palette.length >= MAX_PALETTE) throw new Error(`max ${MAX_PALETTE} colors`);
    const idx = this.state.palette.length;
    this.state.palette.push(hex.toLowerCase());
    return idx;
  }

  removeColor(i: number): void {
    const n = this.state.palette.length;
    if (i < 0 || i >= n) throw new Error(`palette index out of range: ${i}`);
    if (n <= 1) throw new Error("cannot remove last palette color");
    this.state.palette.splice(i, 1);
    this.remapAll((idx) => {
      if (idx === TRANSPARENT) return TRANSPARENT;
      if (idx === i) return TRANSPARENT;   // pixels using removed color become transparent
      return idx > i ? idx - 1 : idx;
    });
    if (this.state.palette.length === 0) validatePalette(this.state.palette);
  }

  moveColor(from: number, to: number): void {
    const p = this.state.palette;
    const n = p.length;
    if (from < 0 || from >= n || to < 0 || to >= n) throw new Error("palette index out of range");
    if (from === to) return;
    const [c] = p.splice(from, 1);
    p.splice(to, 0, c);
    if (from < to) {
      this.remapAll((idx) => {
        if (idx === TRANSPARENT) return TRANSPARENT;
        if (idx === from) return to;
        if (idx > from && idx <= to) return idx - 1;
        return idx;
      });
    } else {
      this.remapAll((idx) => {
        if (idx === TRANSPARENT) return TRANSPARENT;
        return idx >= to && idx < from ? idx + 1 : idx === from ? to : idx;
      });
    }
  }

  /** Change the hex value at a slot without touching indices. */
  setColor(i: number, hex: string): void {
    if (!isValidHex(hex)) throw new Error(`invalid color: ${hex}`);
    if (i < 0 || i >= this.state.palette.length) throw new Error(`palette index out of range: ${i}`);
    this.state.palette[i] = hex.toLowerCase();
  }

  applyPreset(colors: string[]): void {
    validatePalette(colors);
    const next = colors.map((c) => c.toLowerCase());
    // Keep pixels whose palette index still exists in the new palette.
    this.remapAll((idx) => (idx === TRANSPARENT || idx < next.length ? idx : TRANSPARENT));
    this.state.palette = next;
  }
}
