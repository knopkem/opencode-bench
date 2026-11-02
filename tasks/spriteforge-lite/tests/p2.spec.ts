import { expect, test } from "@playwright/test";
import {
  canvasBox,
  canvasPixel,
  clickPixel,
  getState,
  hexToRgb,
  pixel,
  reset,
  setRange,
} from "./helpers";

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

  test("[P2] onion opacity blends the previous frame at 50%", async ({ page }) => {
    const base = await getState(page);
    let previousSlot = 0;
    let brightest = -1;
    for (let index = 0; index < base.palette.length; index++) {
      const [r, g, b] = hexToRgb(base.palette[index]);
      if (r + g + b > brightest) {
        brightest = r + g + b;
        previousSlot = index;
      }
    }
    const previousPixels = new Array(32 * 32).fill(previousSlot);
    const currentPixels = new Array(32 * 32).fill(-1);
    await page.evaluate(
      ({ baseState, previous, current }) => {
        (window as any).__spriteforge.loadState({
          ...baseState,
          activeFrame: 1,
          activeLayer: 0,
          frames: [
            {
              hold: 1,
              layers: [
                { name: "Previous", visible: true, opacity: 1, pixels: previous },
              ],
            },
            {
              hold: 1,
              layers: [{ name: "Current", visible: true, opacity: 1, pixels: current }],
            },
          ],
        });
      },
      { baseState: base, previous: previousPixels, current: currentPixels },
    );

    const slider = page.getByTestId("onion-opacity");
    await expect(slider).toHaveValue("50");

    const readAt = async (value: string) => {
      await setRange(page, "onion-opacity", value);
      return canvasPixel(page, 5, 5);
    };

    const off = await readAt("0");
    const full = await readAt("100");
    const half = await readAt("50");
    const previousColor = hexToRgb(base.palette[previousSlot]);

    expect([off[0], off[1], off[2], off[3]]).not.toEqual(full);
    for (let channel = 0; channel < 3; channel++) {
      expect(Math.abs(full[channel] - previousColor[channel])).toBeLessThanOrEqual(2);
    }
    expect(full[3]).toBe(255);

    if (off[3] === 0) {
      // Transparent canvas: the onion colour stays, alpha carries the 50%.
      expect(half[3]).toBeGreaterThan(100);
      expect(half[3]).toBeLessThan(160);
      for (let channel = 0; channel < 3; channel++) {
        expect(Math.abs(half[channel] - previousColor[channel])).toBeLessThanOrEqual(2);
      }
    } else {
      // Checkerboard painted into the canvas: the pixel is a 50/50 blend.
      expect(half[3]).toBe(255);
      for (let channel = 0; channel < 3; channel++) {
        const midpoint = Math.round((off[channel] + full[channel]) / 2);
        expect(Math.abs(half[channel] - midpoint)).toBeLessThanOrEqual(2);
      }
    }
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
