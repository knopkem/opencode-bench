import { describe, expect, it } from "vitest";
import { newDocument } from "../src/model/document";
import { serializeProject, deserializeProject } from "../src/model/project";
import { MAX_FRAMES, TRANSPARENT, W } from "../src/model/constants";

describe("project serialization", () => {
  it("round-trips a document with pixels, palette, fps, loop, holds, layers", () => {
    const doc = newDocument();
    doc.setFps(16);
    doc.setLoop(false);
    const l = doc.activeLayer()!;
    l.pixels[0] = 3;
    l.pixels[W * W - 1] = TRANSPARENT;
    l.opacity = 0.25;
    l.name = "bg";
    doc.addFrame();
    doc.duplicateFrame();
    doc.state.frames[0].hold = 4;
    doc.addColor("#102030");
    doc.setActiveFrame(0);

    const text = serializeProject(doc.state);
    const back = deserializeProject(text);
    expect(back.fps).toBe(16);
    expect(back.loop).toBe(false);
    expect(back.palette.length).toBe(17);
    expect(back.palette[16]).toBe("#102030");
    expect(back.frames.length).toBe(3);
    expect(back.frames[0].hold).toBe(4);
    expect(back.frames[0].layers[0].pixels[0]).toBe(3);
    expect(back.frames[0].layers[0].pixels[W * W - 1]).toBe(TRANSPARENT);
    expect(back.frames[0].layers[0].opacity).toBeCloseTo(0.25);
    expect(back.frames[0].layers[0].name).toBe("bg");
    expect(back.activeFrame).toBe(0);
    // re-serialize is byte-identical (stable format)
    expect(serializeProject(back)).toBe(text);
  });

  it("rejects non-JSON and foreign formats", () => {
    expect(() => deserializeProject("nope")).toThrow();
    expect(() => deserializeProject(JSON.stringify({ format: "other" }))).toThrow();
    expect(() => deserializeProject(JSON.stringify({ format: "spriteforge", version: 99 }))).toThrow();
  });

  it("rejects out-of-range fps and frame count", () => {
    const base = JSON.parse(serializeProject(newDocument().state));
    expect(() => deserializeProject(JSON.stringify({ ...base, fps: 25 }))).toThrow();
    expect(() => deserializeProject(JSON.stringify({ ...base, fps: 0 }))).toThrow();
    const many = { ...base, frames: Array(base.frames.length + MAX_FRAMES).fill(base.frames[0]) };
    expect(() => deserializeProject(JSON.stringify(many))).toThrow();
  });

  it("rejects pixel indices outside the palette", () => {
    const state = newDocument().state;
    const bad = JSON.parse(serializeProject(state));
    bad.frames[0].layers[0].pixels[0] = 99;
    expect(() => deserializeProject(JSON.stringify(bad))).toThrow();
  });

  it("rejects empty palette / no frames / bad opacity", () => {
    const state = newDocument().state;
    const p = JSON.parse(serializeProject(state));
    p.palette = [];
    expect(() => deserializeProject(JSON.stringify(p))).toThrow();
    const q = JSON.parse(serializeProject(state));
    q.frames = [];
    expect(() => deserializeProject(JSON.stringify(q))).toThrow();
    const r = JSON.parse(serializeProject(state));
    r.frames[0].layers[0].opacity = 1.5;
    expect(() => deserializeProject(JSON.stringify(r))).toThrow();
    const s = JSON.parse(serializeProject(state));
    s.width = 64;
    expect(() => deserializeProject(JSON.stringify(s))).toThrow();
  });

  it("serialized project contains all layers of every frame", () => {
    const doc = newDocument();
    doc.addLayer("a");
    doc.duplicateFrame();
    const text = serializeProject(doc.state);
    const back = deserializeProject(text);
    expect(back.frames[1].layers.length).toBe(2);
    expect(back.frames[1].layers.map((l) => l.name)).toEqual(["Layer 1", "a"]);
    // deserialized layer ids are unique
    const ids = new Set(back.frames.flatMap((f) => f.layers.map((l) => l.id)));
    expect(ids.size).toBe(4);
  });
});
