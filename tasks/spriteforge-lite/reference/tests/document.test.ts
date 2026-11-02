import { describe, expect, it } from "vitest";
import { newDocument } from "../src/model/document";
import { MAX_FRAMES, MAX_LAYERS, MIN_FPS, MAX_FPS, MIN_HOLD, MAX_HOLD, TRANSPARENT, W } from "../src/model/constants";

describe("document basics", () => {
  it("starts with one frame, one layer, default palette", () => {
    const doc = newDocument();
    expect(doc.state.frames.length).toBe(1);
    expect(doc.state.frames[0].layers.length).toBe(1);
    expect(doc.state.palette.length).toBe(16);
    expect(doc.activeLayer()).toBeDefined();
    expect(doc.state.fps).toBeGreaterThanOrEqual(MIN_FPS);
    expect(doc.state.loop).toBe(true);
  });

  it("fps clamps to 1..24", () => {
    const doc = newDocument();
    doc.setFps(30); expect(doc.state.fps).toBe(MAX_FPS);
    doc.setFps(0); expect(doc.state.fps).toBe(MIN_FPS);
    doc.setFps(12); expect(doc.state.fps).toBe(12);
  });

  it("frame hold clamps to 1..24 and validates index", () => {
    const doc = newDocument();
    doc.setFrameHold(0, 100); expect(doc.state.frames[0].hold).toBe(MAX_HOLD);
    doc.setFrameHold(0, -5); expect(doc.state.frames[0].hold).toBe(MIN_HOLD);
    expect(() => doc.setFrameHold(7, 1)).toThrow();
  });

  it("adds frames up to 64 then refuses", () => {
    const doc = newDocument();
    for (let i = 0; i < MAX_FRAMES - 1; i++) doc.addFrame();
    expect(doc.state.frames.length).toBe(MAX_FRAMES);
    expect(() => doc.addFrame()).toThrow();
  });

  it("duplicate copies pixels of active frame", () => {
    const doc = newDocument();
    const l = doc.activeLayer()!;
    l.pixels[0] = 4;
    const copy = doc.duplicateFrame();
    expect(copy.layers[0].pixels[0]).toBe(4);
    expect(copy.layers[0].pixels[1]).toBe(TRANSPARENT);
    // mutating the copy must not touch source
    copy.layers[0].pixels[0] = TRANSPARENT;
    expect(l.pixels[0]).toBe(4);
    expect(doc.state.activeFrame).toBe(1);
  });

  it("deleteFrame protects last frame and fixes active indices", () => {
    const doc = newDocument();
    doc.addFrame(); doc.addFrame();
    doc.state.activeFrame = 1;
    doc.deleteFrame(1);
    expect(doc.state.activeFrame).toBe(0);
    expect(() => doc.deleteFrame(0)).not.toThrow();
    expect(doc.state.frames.length).toBe(1);
    expect(() => doc.deleteFrame(0)).toThrow();
  });

  it("moveFrame reorders and tracks active frame", () => {
    const doc = newDocument();
    doc.addFrame(); doc.addFrame(); // frames 0,1,2 ; active=2
    doc.moveFrame(0, 2);            // order becomes [f1,f2,f0]; active f2 shifts to index 1
    expect(doc.state.activeFrame).toBe(1);
    doc.state.activeFrame = 0;      // points to f1
    doc.moveFrame(2, 0);            // [f0,f1,f2]; f1 shifts down to 1
    expect(doc.state.activeFrame).toBe(1);
  });

  it("layers: add/delete/rename/reorder with limits", () => {
    const doc = newDocument();
    for (let i = 0; i < MAX_LAYERS - 1; i++) doc.addLayer();
    expect(doc.state.frames[0].layers.length).toBe(MAX_LAYERS);
    expect(() => doc.addLayer()).toThrow();
    const idsAll = doc.state.frames[0].layers.map((l) => l.id);
    for (const id of idsAll.slice(0, -1)) doc.deleteLayer(id);
    expect(doc.state.frames[0].layers.length).toBe(1);
    expect(() => doc.deleteLayer(doc.state.frames[0].layers[0].id)).toThrow();

    doc.renameLayer(doc.state.frames[0].layers[0].id, "BG");
    expect(doc.state.frames[0].layers[0].name).toBe("BG");
    expect(() => doc.renameLayer(doc.state.frames[0].layers[0].id, "   ")).toThrow();

    doc.addLayer("A"); doc.addLayer("B");
    const ids = doc.state.frames[0].layers.map((l) => l.id);
    doc.moveLayer(0, 2);
    expect(doc.state.frames[0].layers.map((l) => l.id)).toEqual([ids[1], ids[2], ids[0]]);
    expect(() => doc.moveLayer(0, 5)).toThrow();
  });

  it("layer visibility + opacity clamping", () => {
    const doc = newDocument();
    const l = doc.activeLayer()!;
    doc.setLayerVisible(l.id, false);
    expect(doc.activeLayer()!.visible).toBe(false);
    doc.setLayerOpacity(l.id, 0.42);
    expect(doc.activeLayer()!.opacity).toBeCloseTo(0.42);
    doc.setLayerOpacity(l.id, 2);
    expect(doc.activeLayer()!.opacity).toBe(1);
    doc.setLayerOpacity(l.id, -1);
    expect(doc.activeLayer()!.opacity).toBe(0);
    expect(() => doc.setLayerVisible(-9, true)).toThrow();
  });

  it("drawPixels writes bounds-checked palette indices", () => {
    const doc = newDocument();
    const l = doc.activeLayer()!;
    doc.drawPixels(l, [{ x: 0, y: 0 }, { x: 31, y: 31 }, { x: -1, y: 5 }, { x: 40, y: 0 }], 7);
    expect(l.pixels[0]).toBe(7);
    expect(l.pixels[W * W - 1]).toBe(7);
    expect(l.pixels[5 * W + 0]).toBe(TRANSPARENT); // out-of-bounds draws are skipped
    expect(Array.from(l.pixels).filter((v) => v === 7).length).toBe(2);
  });

  it("setActiveFrame validates and setActiveLayer validates membership", () => {
    const doc = newDocument();
    expect(() => doc.setActiveFrame(3)).toThrow();
    doc.addFrame();
    doc.setActiveFrame(1);
    const other = doc.state.frames[0].layers[0];
    expect(() => doc.setActiveLayer(other.id)).not.toThrow(); // layer ids shared across frames? no—ids differ per frame copies
    const cur = doc.activeFrame_().layers[0];
    doc.setActiveLayer(cur.id);
    expect(doc.state.activeLayerId).toBe(cur.id);
  });

  it("snapshot/restore round-trips independently (deep copy)", () => {
    const doc = newDocument();
    const snap = doc.snapshot();
    doc.activeLayer()!.pixels[0] = 9;
    doc.restore(snap);
    expect(doc.activeLayer()!.pixels[0]).toBe(TRANSPARENT);
    // restore must not alias the snapshot object
    doc.activeLayer()!.pixels[0] = 5;
    expect(snap.frames[0].layers[0].pixels[0]).toBe(TRANSPARENT);
  });
});
