import { expect, test, type Page } from "@playwright/test";

type State = {
  frames: Array<{
    layers: Array<{ pixels: number[] }>;
  }>;
  activeFrame: number;
  activeLayer: number;
};

async function state(page: Page): Promise<State> {
  const value = await page.evaluate(() => (window as any).__spriteforge?.getState());
  expect(value, "window.__spriteforge.getState() is missing").toBeTruthy();
  return value as State;
}

async function pixel(page: Page, x: number, y: number): Promise<number> {
  const current = await state(page);
  return current.frames[current.activeFrame].layers[current.activeLayer].pixels[y * 32 + x];
}

async function clickPixel(page: Page, x: number, y: number): Promise<void> {
  const box = await page.getByTestId("canvas").boundingBox();
  expect(box).toBeTruthy();
  await page.mouse.click(box!.x + x * 16 + 8, box!.y + y * 16 + 8);
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.waitForSelector('[data-testid="canvas"]');
});

test("page loads with a 512x512 canvas", async ({ page }) => {
  const box = await page.getByTestId("canvas").boundingBox();
  expect(box).toBeTruthy();
  expect(Math.round(box!.width)).toBe(512);
  expect(Math.round(box!.height)).toBe(512);
});

test("draws and undoes a pixel", async ({ page }) => {
  await clickPixel(page, 5, 5);
  expect(await pixel(page, 5, 5)).toBe(0);

  await page.getByTestId("undo").click();
  expect(await pixel(page, 5, 5)).toBe(-1);
});

test("selects a palette colour", async ({ page }) => {
  await page.getByTestId("color-2").click();
  await clickPixel(page, 6, 6);
  expect(await pixel(page, 6, 6)).toBe(2);
});

test("exports a PNG", async ({ page }) => {
  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("export").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("spriteforge.png");
});
