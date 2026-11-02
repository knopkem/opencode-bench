import { W, H, TRANSPARENT, inBounds } from "./constants";

export interface Pos { x: number; y: number }

/** Pencil stroke footprint: size 1 = single pixel; size 2 = 2x2 block anchored at (x,y). */
export function brushPixels(x: number, y: number, size: 1 | 2): Pos[] {
  const out: Pos[] = [];
  if (!inBounds(x, y)) return out;
  for (let dy = 0; dy < size; dy++) {
    for (let dx = 0; dx < size; dx++) {
      const px = x + dx, py = y + dy;
      if (inBounds(px, py)) out.push({ x: px, y: py });
    }
  }
  return out;
}

/** Bresenham line, inclusive endpoints. */
export function linePixels(x0: number, y0: number, x1: number, y1: number): Pos[] {
  const out: Pos[] = [];
  let x = x0, y = y0;
  const dx = Math.abs(x1 - x0), dy = Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let err = dx - dy;
  while (true) {
    if (inBounds(x, y)) out.push({ x, y });
    if (x === x1 && y === y1) break;
    const e2 = 2 * err;
    if (e2 > -dy) { err -= dy; x += sx; }
    if (e2 < dx) { err += dx; y += sy; }
  }
  return out;
}

/** Rectangle outline of the normalized bounds defined by two corner points. */
export function rectPixels(x0: number, y0: number, x1: number, y1: number): Pos[] {
  const a = Math.min(x0, x1), b = Math.max(x0, x1);
  const c = Math.min(y0, y1), d = Math.max(y0, y1);
  const seen = new Set<number>();
  const out: Pos[] = [];
  const add = (x: number, y: number) => {
    if (inBounds(x, y) && !seen.has(y * W + x)) { seen.add(y * W + x); out.push({ x, y }); }
  };
  for (let x = a; x <= b; x++) { add(x, c); add(x, d); }
  for (let y = c; y <= d; y++) { add(a, y); add(b, y); }
  return out;
}

/** Ellipse outline via midpoint algorithm on the normalized bounding box. */
export function ellipsePixels(x0: number, y0: number, x1: number, y1: number): Pos[] {
  const left = Math.min(x0, x1), right = Math.max(x0, x1);
  const top = Math.min(y0, y1), bottom = Math.max(y0, y1);
  const w = right - left, h = bottom - top;
  const seen = new Set<number>();
  const out: Pos[] = [];
  const cx = (left + right) / 2, cy = (top + bottom) / 2;
  const add = (px: number, py: number) => {
    const gx = Math.round(cx + px), gy = Math.round(cy + py);
    if (inBounds(gx, gy) && !seen.has(gy * W + gx)) { seen.add(gy * W + gx); out.push({ x: gx, y: gy }); }
  };
  if (w === 0 && h === 0) return [{ x: left, y: top }].filter((p) => inBounds(p.x, p.y));
  if (w === 0 || h === 0) return linePixels(left, top, right, bottom);
  const rx = w / 2, ry = h / 2;
  add(-rx, 0); add(rx, 0); add(0, -ry); add(0, ry); // axis extremes are always on the outline
  // parametric sweep with enough steps to cover the outline on a 32x32 canvas
  const steps = Math.ceil(Math.PI * (rx + ry));
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * 2 * Math.PI;
    add(rx * Math.cos(t), -ry * Math.sin(t));
  }
  return out;
}

/** Flood fill: BFS replacing `target` with `value`, 4-connected. Returns changed positions. */
export function floodFill(
  pixels: Int16Array, x: number, y: number, value: number,
): Pos[] {
  const target = pixels[y * W + x];
  if (target === value) return [];
  const visited = new Uint8Array(W * H);
  const stack = [y * W + x];
  visited[y * W + x] = 1;
  const out: Pos[] = [];
  while (stack.length) {
    const idx = stack.pop()!;
    const cx = idx % W, cy = (idx / W) | 0;
    out.push({ x: cx, y: cy });
    const neigh = [idx - 1, idx + 1, idx - W, idx + W];
    for (const ni of neigh) {
      if (ni < 0 || ni >= W * H) continue;
      if (ni === idx + 1 && idx % W === W - 1) continue; // row wrap guard
      if (ni === idx - 1 && idx % W === 0) continue;
      if (!visited[ni] && pixels[ni] === target) {
        visited[ni] = 1;
        stack.push(ni);
      }
    }
  }
  return out;
}

/** Eyedropper: read palette index at pixel (-1 transparent). */
export function eyedrop(pixels: Int16Array, x: number, y: number): number {
  if (!inBounds(x, y)) return TRANSPARENT;
  return pixels[y * W + x];
}
