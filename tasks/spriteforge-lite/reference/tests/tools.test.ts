import { describe, expect, it } from "vitest";
import { brushPixels, linePixels, rectPixels, ellipsePixels, floodFill, eyedrop } from "../src/model/tools";
import { W, TRANSPARENT } from "../src/model/constants";

const key = (p: { x: number; y: number }) => `${p.x},${p.y}`;
const has = (list: { x: number; y: number }[], x: number, y: number) => list.some((p) => p.x === x && p.y === y);

describe("brush", () => {
  it("size 1 draws one pixel, size 2 draws a 2x2 block anchored at click", () => {
    expect(brushPixels(3, 4, 1)).toEqual([{ x: 3, y: 4 }]);
    const b = brushPixels(3, 4, 2);
    expect(b.length).toBe(4);
    for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [1, 1]]) expect(has(b, 3 + dx, 4 + dy)).toBe(true);
  });

  it("size 2 clips at canvas edge", () => {
    const b = brushPixels(31, 31, 2);
    expect(b).toEqual([{ x: 31, y: 31 }]);
    expect(brushPixels(-1, 0, 2)).toEqual([]);
  });
});

describe("line", () => {
  it("horizontal line covers all endpoints inclusive", () => {
    const l = linePixels(0, 5, 7, 5);
    expect(l.length).toBe(8);
    for (let x = 0; x <= 7; x++) expect(has(l, x, 5)).toBe(true);
  });

  it("diagonal line hits both corners", () => {
    const l = linePixels(0, 0, 31, 31);
    expect(has(l, 0, 0)).toBe(true);
    expect(has(l, 31, 31)).toBe(true);
    expect(l.every((p) => p.x >= 0 && p.x < W && p.y >= 0 && p.y < W)).toBe(true);
    // steep slope should not skip pixels entirely along a column
    const d = linePixels(0, 0, 3, 10);
    expect(d.length).toBeGreaterThanOrEqual(10);
    expect(has(d, 0, 0) && has(d, 3, 10)).toBe(true);
  });

  it("single point when start==end", () => {
    expect(linePixels(4, 4, 4, 4)).toEqual([{ x: 4, y: 4 }]);
  });
});

describe("rect", () => {
  it("outline includes corners and no interior fill", () => {
    const r = rectPixels(2, 3, 9, 6);
    for (const c of [[2, 3], [9, 3], [2, 6], [9, 6]]) expect(has(r, c[0], c[1])).toBe(true);
    expect(has(r, 5, 4)).toBe(false); // interior empty
    // top edge complete
    for (let x = 2; x <= 9; x++) expect(has(r, x, 3)).toBe(true);
    // left edge complete
    for (let y = 3; y <= 6; y++) expect(has(r, 2, y)).toBe(true);
    // no duplicate positions
    const keys = new Set(r.map(key));
    expect(keys.size).toBe(r.length);
  });

  it("degenerate rect is a single pixel", () => {
    expect(rectPixels(5, 5, 5, 5)).toEqual([{ x: 5, y: 5 }]);
  });

  it("drag direction does not matter", () => {
    const a = new Set(rectPixels(2, 3, 9, 6).map(key));
    const b = new Set(rectPixels(9, 6, 2, 3).map(key));
    expect(a).toEqual(b);
  });
});

describe("ellipse", () => {
  it("outline passes through the four bounding-box edge midpoints", () => {
    const e = ellipsePixels(4, 4, 20, 12);
    // bbox: x 4..20 (w=16), y 4..12 (h=8) -> extremes at (4,8),(20,8),(12,4),(12,12)
    expect(has(e, 4, 8)).toBe(true);
    expect(has(e, 20, 8)).toBe(true);
    expect(has(e, 12, 4)).toBe(true);
    expect(has(e, 12, 12)).toBe(true);
    expect(has(e, 12, 8)).toBe(false); // center not drawn
    expect(e.every((p) => p.x >= 4 && p.x <= 20 && p.y >= 4 && p.y <= 12)).toBe(true);
    const keys = new Set(e.map(key));
    expect(keys.size).toBe(e.length);
  });

  it("degenerate ellipse (line-like) stays in bounds and non-empty", () => {
    const e = ellipsePixels(10, 10, 14, 10);
    expect(e.length).toBeGreaterThan(0);
    expect(e.every((p) => p.x >= 10 && p.x <= 14 && p.y === 10)).toBe(true);
  });

  it("single click draws one pixel", () => {
    expect(ellipsePixels(7, 7, 7, 7)).toEqual([{ x: 7, y: 7 }]);
  });

  it("drag direction does not matter", () => {
    const a = new Set(ellipsePixels(4, 4, 20, 12).map(key));
    const b = new Set(ellipsePixels(20, 12, 4, 4).map(key));
    expect(a).toEqual(b);
  });
});

describe("flood fill", () => {
  it("fills contiguous region of target color only", () => {
    const px = new Int16Array(W * W).fill(0);
    for (let x = 0; x < W; x++) px[5 * W + x] = 1; // full wall across row 5
    const out = floodFill(px, 0, 0, 3);
    for (const q of out) px[q.y * W + q.x] = 3;
    expect(px[0]).toBe(3);
    expect(px[4 * W + 4]).toBe(3);
    expect(px[5 * W + 5]).toBe(1);   // barrier untouched
    expect(px[6 * W + 6]).toBe(0);   // beyond barrier untouched
    expect(out.some((p) => p.x === 0 && p.y === 0)).toBe(true);
  });

  it("no-op when target equals fill value", () => {
    const px = new Int16Array(W * W).fill(2);
    expect(floodFill(px, 10, 10, 2)).toEqual([]);
  });

  it("does not wrap rows at canvas edge", () => {
    const px = new Int16Array(W * W).fill(0);
    for (let x = 0; x < W; x++) px[1 * W + x] = 9; // sealed second row
    const out2 = floodFill(px, 31, 0, 4);
    for (const q of out2) px[q.y * W + q.x] = 4;
    expect(px[W - 1]).toBe(4);          // clicked pixel filled
    expect(px[0]).toBe(4);              // whole row 0 reachable via adjacency
    expect(px[1 * W + 0]).toBe(9);      // row 1 col 0 must NOT be reached via index wrap
    expect(px[1 * W + 1]).toBe(9);
  });

  it("transparent regions are fillable as their own target", () => {
    const px = new Int16Array(W * W).fill(TRANSPARENT);
    px[0] = 1;
    const out = floodFill(px, 1, 0, 7);
    for (const p of out) px[p.y * W + p.x] = 7;
    expect(px[1]).toBe(7);
    expect(px[0]).toBe(1);
    expect(out.length).toBe(W * W - 1);
  });
});

describe("eyedropper", () => {
  it("reads palette index and transparent", () => {
    const px = new Int16Array(W * W).fill(TRANSPARENT);
    px[3 * W + 2] = 8;
    expect(eyedrop(px, 2, 3)).toBe(8);
    expect(eyedrop(px, 0, 0)).toBe(TRANSPARENT);
    expect(eyedrop(px, -1, 0)).toBe(TRANSPARENT);
  });
});
