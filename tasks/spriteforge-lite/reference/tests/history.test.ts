import { describe, expect, it } from "vitest";
import { History } from "../src/model/history";
import { newDocument } from "../src/model/document";
import { TRANSPARENT } from "../src/model/constants";

describe("history", () => {
  it("undo restores state before an edit; redo re-applies it", () => {
    const doc = newDocument();
    const h = new History();
    h.push(doc.snapshot());          // pre-edit snapshot
    doc.activeLayer()!.pixels[0] = 6;
    const back = h.undo(doc.snapshot());
    expect(back!.frames[0].layers[0].pixels[0]).toBe(TRANSPARENT);
    doc.restore(back!);
    const fwd = h.redo(doc.snapshot());
    expect(fwd!.frames[0].layers[0].pixels[0]).toBe(6);
    doc.restore(fwd!);
    expect(doc.activeLayer()!.pixels[0]).toBe(6);
  });

  it("a new edit clears the redo stack", () => {
    const doc = newDocument();
    const h = new History();
    h.push(doc.snapshot());
    doc.activeLayer()!.pixels[0] = 1;
    h.undo(doc.snapshot());
    h.push(doc.snapshot());         // fresh edit after undo
    expect(h.canRedo()).toBe(false);
    expect(h.canUndo()).toBe(true);
  });

  it("unlimited depth: many edits undo one by one", () => {
    const doc = newDocument();
    const h = new History();
    for (let i = 1; i <= 50; i++) {
      h.push(doc.snapshot());
      doc.activeLayer()!.pixels[i - 1] = i;
    }
    let s = doc.snapshot();
    for (let i = 50; i >= 1; i--) {
      s = h.undo(s)!;
      // after undoing step i, pixel i-1 reverted to 0 and earlier edits remain
      expect(s.frames[0].layers[0].pixels[i - 1]).toBe(TRANSPARENT);
      if (i > 1) expect(s.frames[0].layers[0].pixels[i - 2]).toBe(i - 1);
      doc.restore(s);
    }
    expect(h.canUndo()).toBe(false);
    expect(doc.activeLayer()!.pixels[0]).toBe(TRANSPARENT);
  });

  it("undo on empty history returns null", () => {
    const h = new History();
    const doc = newDocument();
    expect(h.undo(doc.snapshot())).toBeNull();
    expect(h.redo(doc.snapshot())).toBeNull();
  });

  it("snapshots are deep copies, not aliases", () => {
    const doc = newDocument();
    const h = new History();
    h.push(doc.snapshot());
    doc.addFrame();
    doc.duplicateFrame();
    const back = h.undo(doc.snapshot())!;
    expect(back.frames.length).toBe(1);
    doc.restore(back);
    expect(doc.state.frames.length).toBe(1);
  });
});
