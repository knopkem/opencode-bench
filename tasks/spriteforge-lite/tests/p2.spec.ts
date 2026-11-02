import { expect, test } from "@playwright/test";
import { canvasBox, clickPixel, getState, pixel, reset } from "./helpers";

test.describe("[P2] polish and edge cases", () => {
  test.beforeEach(async ({ page }) => {
    await reset(page);
  });

  test("[P2] keyboard shortcuts work and do not fire from inputs", async ({ page }) => {
    const canvas = await canvasBox(page);
    await page.mouse.click(canvas.x + 20, canvas.y + 20);

    await page.keyboard.press("e");
    expect((await getState(page)).tool).toBe("eraser");
    await page.keyboard.press("b");
    expect((await getState(page)).tool).toBe("pencil");
    await page.keyboard.press("g");
    expect((await getState(page)).tool).toBe("fill");

    const brush = (await getState(page)).brush;
    await page.keyboard.press("]");
    expect((await getState(page)).brush).not.toBe(brush);

    await page.getByTestId("fps").click();
    const toolBefore = (await getState(page)).tool;
    await page.keyboard.press("b");
    expect((await getState(page)).tool).toBe(toolBefore);
  });

  test("[P2] draws at the extreme pixels and ignores outside clicks", async ({ page }) => {
    await clickPixel(page, 0, 0);
    await clickPixel(page, 31, 31);
    expect(await pixel(page, 0, 0)).toBe(0);
    expect(await pixel(page, 31, 31)).toBe(0);

    const before = await getState(page);
    const box = await canvasBox(page);
    await page.mouse.click(box.x - 8, box.y + 8);
    await page.waitForTimeout(50);
    const after = await getState(page);
    expect(after.frames[after.activeFrame].layers[after.activeLayer].pixels).toEqual(
      before.frames[before.activeFrame].layers[before.activeLayer].pixels,
    );
  });

  test("[P2] empty undo and last-layer/frame deletion are safe", async ({ page }) => {
    await page.getByTestId("undo").click();
    expect((await getState(page)).frames.length).toBe(1);

    await page.getByTestId("layer-delete").click();
    let state = await getState(page);
    expect(state.frames[state.activeFrame].layers.length).toBe(1);

    await page.getByTestId("frame-delete").click();
    state = await getState(page);
    expect(state.frames.length).toBe(1);
  });

  test("[P2] layout has no horizontal overflow at 1280x800", async ({ page }) => {
    const dimensions = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      scrollHeight: document.documentElement.scrollHeight,
    }));
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.innerWidth);

    for (const id of ["canvas", "brush-1", "layer-add", "frame-add", "export", "status"]) {
      await expect(page.getByTestId(id)).toBeVisible();
    }
  });

  test("[P2] status reports the active tool", async ({ page }) => {
    await page.getByTestId("tool-pencil").click();
    await expect(page.getByTestId("status")).toContainText(/pencil/i);
  });
});
