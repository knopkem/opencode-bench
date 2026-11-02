import { W, H, MAX_FRAMES, MIN_LAYERS, MAX_LAYERS, MIN_FPS, MAX_FPS, MIN_HOLD, MAX_HOLD, TRANSPARENT } from "./constants";
import type { DocState, Frame, Layer } from "./document";
import { isValidHex } from "./palette";

interface SerializedLayer { name: string; visible: boolean; opacity: number; pixels: number[] }
interface SerializedFrame { hold: number; layers: SerializedLayer[] }
interface SerializedProject {
  format: "spriteforge";
  version: 1;
  width: number; height: number; fps: number; loop: boolean;
  palette: string[];
  activeFrame: number;
  frames: SerializedFrame[];
}

export function serializeProject(state: DocState): string {
  const p: SerializedProject = {
    format: "spriteforge",
    version: 1,
    width: state.width,
    height: state.height,
    fps: state.fps,
    loop: state.loop,
    palette: [...state.palette],
    activeFrame: state.activeFrame,
    frames: state.frames.map((f) => ({
      hold: f.hold,
      layers: f.layers.map((l) => ({
        name: l.name, visible: l.visible, opacity: l.opacity, pixels: Array.from(l.pixels),
      })),
    })),
  };
  return JSON.stringify(p);
}

export function deserializeProject(text: string): DocState {
  let raw: unknown;
  try { raw = JSON.parse(text); } catch { throw new Error("project is not valid JSON"); }
  const obj = raw as Partial<SerializedProject>;
  if (obj.format !== "spriteforge") throw new Error("not a spriteforge project");
  if (obj.version !== 1) throw new Error(`unsupported project version: ${obj.version}`);
  if (obj.width !== W || obj.height !== H) throw new Error(`canvas must be ${W}x${H}`);
  if (typeof obj.fps !== "number" || obj.fps < MIN_FPS || obj.fps > MAX_FPS) throw new Error(`fps out of range`);
  if (typeof obj.loop !== "boolean") throw new Error("loop flag missing");
  const palette = obj.palette;
  if (!Array.isArray(palette) || palette.length === 0) throw new Error("palette missing");
  for (const c of palette) {
    if (typeof c !== "string" || !isValidHex(c)) throw new Error(`invalid palette color: ${c}`);
  }
  const framesIn = obj.frames;
  if (!Array.isArray(framesIn) || framesIn.length === 0) throw new Error("no frames in project");
  if (framesIn.length > MAX_FRAMES) throw new Error(`too many frames (${framesIn.length})`);

  const frames: Frame[] = [];
  let nextId = 1;
  for (let fi = 0; fi < framesIn.length; fi++) {
    const f = framesIn[fi];
    if (typeof f.hold !== "number" || f.hold < MIN_HOLD || f.hold > MAX_HOLD) {
      throw new Error(`frame ${fi}: invalid hold`);
    }
    const ls = f.layers;
    if (!Array.isArray(ls) || ls.length < MIN_LAYERS || ls.length > MAX_LAYERS) {
      throw new Error(`frame ${fi}: layer count out of range`);
    }
    const layers: Layer[] = [];
    for (const sl of ls) {
      if (typeof sl.name !== "string" || !sl.name.trim()) throw new Error(`frame ${fi}: bad layer name`);
      if (typeof sl.visible !== "boolean") throw new Error(`frame ${fi}: bad visibility`);
      if (typeof sl.opacity !== "number" || sl.opacity < 0 || sl.opacity > 1) throw new Error(`frame ${fi}: bad opacity`);
      if (!Array.isArray(sl.pixels) || sl.pixels.length !== W * H) throw new Error(`frame ${fi}: pixels size`);
      const px = new Int16Array(W * H);
      for (let i = 0; i < W * H; i++) {
        const v = sl.pixels[i];
        if (v !== TRANSPARENT && (v < 0 || v >= palette.length)) {
          throw new Error(`frame ${fi}: pixel index ${v} outside palette`);
        }
        px[i] = v;
      }
      layers.push({ id: nextId++, name: sl.name.trim(), visible: sl.visible, opacity: sl.opacity, pixels: px });
    }
    frames.push({ hold: Math.round(f.hold), layers });
  }

  const activeFrame = typeof obj.activeFrame === "number" ? Math.min(Math.max(obj.activeFrame | 0, 0), frames.length - 1) : 0;

  return {
    width: W, height: H, fps: Math.round(obj.fps), loop: obj.loop,
    palette: palette.map((c) => c.toLowerCase()),
    frames, activeFrame,
    activeLayerId: frames[activeFrame].layers[0].id,
    nextLayerId: nextId,
  };
}
