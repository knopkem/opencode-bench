import { expect, test } from "@playwright/test";
import { canvasPixel, clickPixel, getState, pixel, reset } from "./helpers";

test.describe("[P0] core", () => {
  test.beforeEach(async ({ page }) => {
    await reset(page);
  });

  test("[P0] app mounts with the default document and a 512x512 canvas", async ({ page }) => {
    const state = await getState(page);
    expect(state.width).toBe(32);
    expect(state.height).toBe(32);
    expect(state.fps).toBe(8);
    expect(state.loop).toBe(true);
    expect(state.palette.length).toBe(16);
    expect(state.frames.length).toBe(1);
    expect(state.frames[0].layers.length).toBe(1);

    const box = await page.getByTestId("canvas").boundingBox();
    expect(box).toBeTruthy();
    expect(Math.round(box!.width)).toBe(512);
    expect(Math.round(box!.height)).toBe(512);
  });

  test("[P0] pencil draws and right-click erases a pixel", async ({ page }) => {
    await clickPixel(page, 5, 5);
    expect(await pixel(page, 5, 5)).toBe(0);
    expect(await canvasPixel(page, 5, 5)).toEqual([0, 0, 0, 255]);

    await clickPixel(page, 5, 5, "right");
    expect(await pixel(page, 5, 5)).toBe(-1);
    expect((await canvasPixel(page, 5, 5))[3]).toBe(0);
  });

  test("[P0] undo restores and redo reapplies", async ({ page }) => {
    await clickPixel(page, 6, 6);
    await page.getByTestId("undo").click();
    expect(await pixel(page, 6, 6)).toBe(-1);

    await page.getByTestId("redo").click();
    expect(await pixel(page, 6, 6)).toBe(0);
  });

  test("[P0] normal use has no console or page errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(String(error)));

    await page.reload();
    await page.waitForSelector('[data-testid="canvas"]');
    await clickPixel(page, 3, 3);
    await page.getByTestId("undo").click();
    await page.waitForTimeout(300);

    expect(errors.filter((error) => !error.includes("favicon"))).toEqual([]);
  });
});
