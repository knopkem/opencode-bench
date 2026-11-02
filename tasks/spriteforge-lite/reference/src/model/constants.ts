export const W = 32;
export const H = 32;
export const MAX_FRAMES = 64;
export const MIN_LAYERS = 1;
export const MAX_LAYERS = 8;
export const MIN_FPS = 1;
export const MAX_FPS = 24;
export const MIN_HOLD = 1;
export const MAX_HOLD = 24;
export const TRANSPARENT = -1;

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

export function inBounds(x: number, y: number): boolean {
  return x >= 0 && y >= 0 && x < W && y < H;
}
