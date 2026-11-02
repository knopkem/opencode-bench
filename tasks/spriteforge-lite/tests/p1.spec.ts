import { expect, test } from "@playwright/test";
import fs from "node:fs";
import { PNG } from "pngjs";
import {
  canvasPixel,
  clickPixel,
  dragPixels,
  getState,
  hexToRgb,
  pixel,
  reset,
  setRange,
} from "./helpers";

function activeRow(state: Awaited<ReturnType<typeof getState>>): number {
  return state.frames[state.activeFrame].layers.length - 1 - state.activeLayer;
}

test.describe("[P1] product", () => {
  test.beforeEach(async ({ page }) => {
    await reset(page);
  });

  test("[P1] palette selection controls the drawn colour", async ({ page }) => {
    await page.getByTestId("color-3").click();
    await clickPixel(page, 4, 4);
    expect(await pixel(page, 4, 4)).toBe(3);

    const state = await getState(page);
    const [r, g, b] = hexToRgb(state.palette[3]);
    expect(await canvasPixel(page, 4, 4)).toEqual([r, g, b, 255]);
  });

  test("[P1] bucket fill fills the layer", async ({ page }) => {
    await page.getByTestId("tool-fill").click();
    await clickPixel(page, 10, 10);
    const state = await getState(page);
    const pixels = state.frames[state.activeFrame].layers[state.activeLayer].pixels;
    expect(pixels).toHaveLength(1024);
    expect(pixels.every((value) => value === 0)).toBe(true);
  });

  test("[P1] brush size 2 paints a 2x2 block", async ({ page }) => {
    await page.getByTestId("brush-2").click();
    await clickPixel(page, 10, 10);
    const state = await getState(page);
    const pixels = state.frames[state.activeFrame].layers[state.activeLayer].pixels;
    const painted = pixels.filter((value) => value === 0).length;
    expect(painted).toBeGreaterThanOrEqual(4);
  });

  test("[P1] line, rectangle and ellipse drag tools draw", async ({ page }) => {
    await page.getByTestId("tool-line").click();
    await dragPixels(page, { x: 2, y: 2 }, { x: 5, y: 2 });
    for (let x = 2; x <= 5; x++) expect(await pixel(page, x, 2)).toBe(0);

    await page.getByTestId("undo").click();
    await page.getByTestId("tool-rect").click();
    await dragPixels(page, { x: 2, y: 4 }, { x: 5, y: 7 });
    expect(await pixel(page, 2, 4)).toBe(0);
    expect(await pixel(page, 5, 7)).toBe(0);

    await page.getByTestId("undo").click();
    await page.getByTestId("tool-ellipse").click();
    await dragPixels(page, { x: 2, y: 10 }, { x: 7, y: 15 });
    const state = await getState(page);
    const pixels = state.frames[state.activeFrame].layers[state.activeLayer].pixels;
    expect(pixels.filter((value) => value === 0).length).toBeGreaterThan(0);
  });

  test("[P1] layers add, toggle visibility and opacity composite", async ({ page }) => {
    await page.getByTestId("layer-add").click();
    let state = await getState(page);
    expect(state.frames[state.activeFrame].layers).toHaveLength(2);
    const row = activeRow(state);

    await clickPixel(page, 3, 3);
    expect((await canvasPixel(page, 3, 3))[3]).toBe(255);

    await page.getByTestId(`layer-visible-${row}`).click();
    expect((await canvasPixel(page, 3, 3))[3]).toBe(0);
    await page.getByTestId(`layer-visible-${row}`).click();

    await setRange(page, `layer-opacity-${row}`, "50");
    state = await getState(page);
    expect(state.frames[state.activeFrame].layers[state.activeLayer].opacity).toBeCloseTo(0.5, 1);
    const alpha = (await canvasPixel(page, 3, 3))[3];
    expect(alpha).toBeGreaterThan(90);
    expect(alpha).toBeLessThan(170);
  });

  test("[P1] frames add, select and duplicate with independent pixels", async ({ page }) => {
    await page.getByTestId("frame-add").click();
    let state = await getState(page);
    expect(state.frames.length).toBe(2);
    expect(state.activeFrame).toBe(1);

    await clickPixel(page, 1, 1);
    expect(await pixel(page, 1, 1)).toBe(0);

    await page.getByTestId("frame-0").click();
    state = await getState(page);
    expect(state.activeFrame).toBe(0);
    expect(await pixel(page, 1, 1)).toBe(-1);

    await page.getByTestId("frame-1").click();
    expect(await pixel(page, 1, 1)).toBe(0);

    await page.getByTestId("frame-duplicate").click();
    state = await getState(page);
    expect(state.frames.length).toBe(3);
  });

  test("[P1] playback advances frames and pause stops it", async ({ page }) => {
    await page.getByTestId("frame-add").click();
    await page.getByTestId("frame-add").click();
    await setRange(page, "fps", "24");
    const before = (await getState(page)).activeFrame;

    await page.getByTestId("play").click();
    await expect
      .poll(async () => (await getState(page)).activeFrame, { timeout: 3_000 })
      .not.toBe(before);

    await page.getByTestId("pause").click();
    const paused = (await getState(page)).activeFrame;
    await page.waitForTimeout(250);
    expect((await getState(page)).activeFrame).toBe(paused);
  });

  test("[P1] edits persist across reload", async ({ page }) => {
    await clickPixel(page, 7, 7);
    await page.waitForTimeout(100);
    await page.reload();
    await page.waitForSelector('[data-testid="canvas"]');
    await page.waitForFunction(() => Boolean((window as any).__spriteforge));
    expect(await pixel(page, 7, 7)).toBe(0);
  });

  test("[P1] export produces a 32x32 PNG with the drawn pixel", async ({ page }) => {
    await clickPixel(page, 7, 7);
    const downloadPromise = page.waitForEvent("download");
    await page.getByTestId("export").click();
    const download = await downloadPromise;
    const file = await download.path();
    expect(file).toBeTruthy();

    const png = PNG.sync.read(fs.readFileSync(file!));
    expect(png.width).toBe(32);
    expect(png.height).toBe(32);
    const index = (7 * 32 + 7) * 4;
    expect([...png.data.subarray(index, index + 4)]).toEqual([0, 0, 0, 255]);
  });
});
