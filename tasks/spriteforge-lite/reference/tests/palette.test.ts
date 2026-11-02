import { describe, expect, it } from "vitest";
import { DEFAULT_PALETTE, PRESETS, isValidHex } from "../src/model/palette";
import { DocumentModel, newDocument } from "../src/model/document";
import { W, TRANSPARENT } from "../src/model/constants";

describe("palette", () => {
  it("default palette has 16 valid colors", () => {
    expect(DEFAULT_PALETTE.length).toBe(16);
    for (const c of DEFAULT_PALETTE) expect(isValidHex(c)).toBe(true);
  });

  it("has at least 2 presets besides default, each 16 colors", () => {
    expect(PRESETS.length).toBeGreaterThanOrEqual(3);
    for (const p of PRESETS) {
      expect(p.colors.length).toBe(16);
      for (const c of p.colors) expect(isValidHex(c)).toBe(true);
    }
    expect(PRESETS.map((p) => p.name)).toEqual(["Default (16)", "Neon", "Pastel"]);
  });

  it("addColor appends and returns new index", () => {
    const doc = newDocument();
    const idx = doc.addColor("#abcdef");
    expect(idx).toBe(16);
    expect(doc.state.palette[idx]).toBe("#abcdef");
  });

  it("removeColor shifts indices and clears pixels using removed slot", () => {
    const doc = newDocument();
    const layer = doc.activeLayer()!;
    layer.pixels[0] = 5;   // uses color to be removed
    layer.pixels[1] = 7;   // above removal -> shifts down
    layer.pixels[2] = 4;   // below removal -> unchanged
    doc.removeColor(5);
    expect(layer.pixels[0]).toBe(TRANSPARENT);
    expect(layer.pixels[1]).toBe(6);
    expect(layer.pixels[2]).toBe(4);
    expect(doc.state.palette.length).toBe(15);
  });

  it("moveColor forward remaps pixel indices consistently", () => {
    const doc = newDocument();
    const layer = doc.activeLayer()!;
    layer.pixels[0] = 0;
    layer.pixels[1] = 1;
    layer.pixels[2] = 2;
    doc.moveColor(0, 2); // [A,B,C,...] -> [B,C,A,...]
    expect(doc.state.palette.slice(0, 3)).toEqual([DEFAULT_PALETTE[1], DEFAULT_PALETTE[2], DEFAULT_PALETTE[0]]);
    expect(layer.pixels[0]).toBe(2); // old idx 0 moved to slot 2
    expect(layer.pixels[1]).toBe(0); // old idx 1 shifted down
    expect(layer.pixels[2]).toBe(1); // old idx 2 shifted down
  });

  it("moveColor backward remaps pixel indices consistently", () => {
    const doc = newDocument();
    const layer = doc.activeLayer()!;
    layer.pixels[0] = 2;
    layer.pixels[1] = 0;
    doc.moveColor(2, 0); // [A,B,C] -> [C,A,B]
    expect(layer.pixels[0]).toBe(0); // old 2 moved to 0
    expect(layer.pixels[1]).toBe(1); // old 0 shifted up
  });

  it("setColor changes hex value only, not indices", () => {
    const doc = newDocument();
    const layer = doc.activeLayer()!;
    layer.pixels[0] = 3;
    doc.setColor(3, "#123456");
    expect(doc.state.palette[3]).toBe("#123456");
    expect(layer.pixels[0]).toBe(3);
  });

  it("applyPreset keeps pixels with valid indices and clears out-of-range ones", () => {
    const doc = newDocument();
    const layer = doc.activeLayer()!;
    layer.pixels[0] = 10;
    layer.pixels[1] = 15;
    doc.addColor("#ffffff"); // index 16
    layer.pixels[2] = 16;
    doc.applyPreset(PRESETS[1].colors);
    expect(doc.state.palette).toEqual(PRESETS[1].colors.map((c) => c.toLowerCase()));
    expect(layer.pixels[0]).toBe(10);
    expect(layer.pixels[1]).toBe(15);
    expect(layer.pixels[2]).toBe(TRANSPARENT);
  });

  it("rejects invalid colors and last-color removal", () => {
    const doc = newDocument();
    expect(() => doc.addColor("rebeccapurple")).toThrow();
    expect(() => doc.addColor("#fff")).toThrow();
    const tiny = new DocumentModel();
    tiny.state.palette = ["#000000"];
    tiny.state.frames.push({ layers: [], hold: 1 });
    expect(() => tiny.removeColor(0)).toThrow();
  });

  it("canvas is 32x32", () => {
    const doc = newDocument();
    expect(doc.state.width).toBe(W);
    expect(doc.state.height).toBe(W);
    expect(doc.activeLayer()!.pixels.length).toBe(W * W);
  });
});
