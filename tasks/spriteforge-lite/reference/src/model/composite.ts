import { W, H, TRANSPARENT } from "./constants";
import type { DocState } from "./document";
import { hexToRgb } from "./color";

interface Src { r: number; g: number; b: number; a: number }

/** Source-over accumulation over stacked per-pixel sources; returns RGBA bytes. */
function paintSources(sources: Src[]): [number, number, number, number] {
  let A = 0, cr = 0, cg = 0, cb = 0;
  for (const s of sources) {
    if (s.a <= 0) continue;
    const Anew = s.a + A * (1 - s.a);
    cr = (s.r * s.a + cr * A * (1 - s.a)) / Anew;
    cg = (s.g * s.a + cg * A * (1 - s.a)) / Anew;
    cb = (s.b * s.a + cb * A * (1 - s.a)) / Anew;
    A = Anew;
  }
  return [Math.round(cr), Math.round(cg), Math.round(cb), Math.round(A * 255)];
}

export function compositeFrame(state: DocState, frameIdx: number): Uint8ClampedArray<ArrayBuffer> {
  return compositeInner(state, frameIdx, null, 0);
}

export function compositeWithOnion(
  state: DocState, frameIdx: number, onionIdx: number | null, onionOpacity: number,
): Uint8ClampedArray<ArrayBuffer> {
  return compositeInner(state, frameIdx, onionIdx, onionOpacity);
}

function compositeInner(
  state: DocState, frameIdx: number, onionIdx: number | null, onionOpacity: number,
): Uint8ClampedArray<ArrayBuffer> {
  const out = new Uint8ClampedArray(W * H * 4);
  const cur = state.frames[frameIdx];
  const onion = onionIdx !== null && onionOpacity > 0 ? state.frames[onionIdx] : null;

  for (let i = 0; i < W * H; i++) {
    const sources: Src[] = [];
    if (onion) {
      // flatten the onion frame at this pixel, then attenuate by onion opacity
      const flat = paintSources(flattenPixel(onion.layers, i, state));
      if (flat[3] > 0) sources.push({ r: flat[0], g: flat[1], b: flat[2], a: (flat[3] / 255) * onionOpacity });
    }
    for (const l of cur.layers) {
      if (!l.visible || l.opacity <= 0) continue;
      const idx = l.pixels[i];
      if (idx === TRANSPARENT) continue;
      const [r, g, b] = hexToRgb(state.palette[idx]);
      sources.push({ r, g, b, a: l.opacity });
    }
    const [r, g, b, a] = paintSources(sources);
    out[i * 4] = r; out[i * 4 + 1] = g; out[i * 4 + 2] = b; out[i * 4 + 3] = a;
  }
  return out;
}

function flattenPixel(layers: { visible: boolean; opacity: number; pixels: Int16Array }[], i: number, state: DocState): Src[] {
  const srcs: Src[] = [];
  for (const l of layers) {
    if (!l.visible || l.opacity <= 0) continue;
    const idx = l.pixels[i];
    if (idx === TRANSPARENT) continue;
    const [r, g, b] = hexToRgb(state.palette[idx]);
    srcs.push({ r, g, b, a: l.opacity });
  }
  return srcs;
}
