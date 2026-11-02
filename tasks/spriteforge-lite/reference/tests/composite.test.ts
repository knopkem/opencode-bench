import { describe, expect, it } from "vitest";
import { newDocument } from "../src/model/document";
import { compositeFrame, compositeWithOnion } from "../src/model/composite";
import { W, TRANSPARENT } from "../src/model/constants";

function setPixel(doc: ReturnType<typeof newDocument>, layerIdx: number, x: number, y: number, v: number) {
  doc.state.frames[doc.state.activeFrame].layers[layerIdx].pixels[y * W + x] = v;
}
function rgba(buf: Uint8ClampedArray, x: number, y: number) {
  const i = (y * W + x) * 4;
  return [buf[i], buf[i + 1], buf[i + 2], buf[i + 3]];
}

describe("compositing", () => {
  it("empty document composites to fully transparent", () => {
    const doc = newDocument();
    // start pixels are palette index 0 (opaque black), clear them
    doc.fillLayer(doc.activeLayer()!, TRANSPARENT);
    const buf = compositeFrame(doc.state, 0);
    expect(rgba(buf, 0, 0)[3]).toBe(0);
  });

  it("single opaque layer produces exact palette color", () => {
    const doc = newDocument();
    doc.fillLayer(doc.activeLayer()!, TRANSPARENT);
    doc.setColor(5, "#ff0000");
    setPixel(doc, 0, 1, 1, 5);
    const buf = compositeFrame(doc.state, 0);
    expect(rgba(buf, 1, 1)).toEqual([255, 0, 0, 255]);
    expect(rgba(buf, 2, 2)[3]).toBe(0);
  });

  it("layer opacity yields proportional alpha over transparency", () => {
    const doc = newDocument();
    doc.fillLayer(doc.activeLayer()!, TRANSPARENT);
    setPixel(doc, 0, 0, 0, 0);
    doc.setLayerOpacity(doc.activeLayer()!.id, 0.5);
    const buf = compositeFrame(doc.state, 0);
    expect(rgba(buf, 0, 0)[3]).toBeCloseTo(128, 0);
    expect(rgba(buf, 0, 0).slice(0, 3)).toEqual([0, 0, 0]); // color preserved
  });

  it("invisible layers are skipped", () => {
    const doc = newDocument();
    doc.fillLayer(doc.activeLayer()!, TRANSPARENT);
    setPixel(doc, 0, 0, 0, 3);
    doc.setLayerVisible(doc.activeLayer()!.id, false);
    const buf = compositeFrame(doc.state, 0);
    expect(rgba(buf, 0, 0)[3]).toBe(0);
  });

  it("two layers blend source-over with live alpha", () => {
    const doc = newDocument();
    const bottom = doc.activeLayer()!;
    doc.fillLayer(bottom, TRANSPARENT);
    doc.setColor(0, "#0000ff"); // blue
    setPixel(doc, 0, 0, 0, 0);
    const top = doc.addLayer("top");
    doc.fillLayer(top, TRANSPARENT);
    doc.setColor(1, "#00ff00"); // green at index 1
    setPixel(doc, 1, 0, 0, 1);
    doc.setLayerOpacity(top.id, 0.5);
    const buf = compositeFrame(doc.state, 0);
    const [r, g, b, a] = rgba(buf, 0, 0);
    expect(a).toBe(255);                       // fully opaque result
    expect(b).toBeCloseTo(128, 0);             // half green blends away half of blue
    expect(g).toBeCloseTo(128, 0);
    expect(r).toBe(0);
  });

  it("opacity 0 layer contributes nothing", () => {
    const doc = newDocument();
    const l = doc.activeLayer()!;
    doc.fillLayer(l, TRANSPARENT);
    setPixel(doc, 0, 0, 0, 0);
    doc.setLayerOpacity(l.id, 0);
    expect(rgba(compositeFrame(doc.state, 0), 0, 0)[3]).toBe(0);
  });

  it("onion skin adds previous frame underneath current at configured opacity", () => {
    const doc = newDocument();
    const l = doc.activeLayer()!;
    doc.fillLayer(l, TRANSPARENT);
    doc.setColor(9, "#ffffff");
    setPixel(doc, 0, 0, 0, 9);            // frame 0: white at (0,0)
    doc.duplicateFrame();                 // frame 1 copy -> clear it
    doc.fillLayer(doc.activeLayer()!, TRANSPARENT);
    doc.setColor(10, "#000000");
    setPixel(doc, 0, 1, 1, 10);           // frame 1: black at (1,1) only

    const plain = compositeFrame(doc.state, 1);
    expect(rgba(plain, 0, 0)[3]).toBe(0);

    const onion = compositeWithOnion(doc.state, 1, 0, 0.4);
    const [r, g, b, a] = rgba(onion, 0, 0);
    expect([r, g, b]).toEqual([255, 255, 255]);
    expect(a).toBeCloseTo(102, 0);        // 255 * 0.4 ≈ 102
    expect(rgba(onion, 1, 1)).toEqual([0, 0, 0, 255]); // current frame on top unaffected
  });

  it("onion opacity 0 equals plain composite", () => {
    const doc = newDocument();
    const l = doc.activeLayer()!;
    doc.fillLayer(l, TRANSPARENT);
    setPixel(doc, 0, 0, 0, 2);
    doc.addFrame();
    const a = compositeFrame(doc.state, 1);
    const b = compositeWithOnion(doc.state, 1, 0, 0);
    expect(Array.from(a)).toEqual(Array.from(b));
  });

  it("onion of first frame (no previous) is safe", () => {
    const doc = newDocument();
    const buf = compositeWithOnion(doc.state, 0, null, 0.5);
    expect(buf.length).toBe(W * W * 4);
  });
});
