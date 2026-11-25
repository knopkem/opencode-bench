import { expect, test, type Page } from "@playwright/test";

type State = {
  frames: Array<{
    layers: Array<{ pixels: number[] }>;
  }>;
  palette: string[];
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

async function canvasPixel(page: Page, x: number, y: number): Promise<number[]> {
  return page.evaluate(
    ({ x, y }) => {
      const canvas = document.querySelector<HTMLCanvasElement>('[data-testid="canvas"]');
      if (!canvas) throw new Error("canvas missing");
      const scale = canvas.width / 32;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("2d context missing");
      const px = Math.min(canvas.width - 1, Math.floor(x * scale + scale / 2));
      const py = Math.min(canvas.height - 1, Math.floor(y * scale + scale / 2));
      return Array.from(ctx.getImageData(px, py, 1, 1).data);
    },
    { x, y },
  );
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

test("draws the default palette colour", async ({ page }) => {
  expect((await state(page)).palette[0]).toBe("#000000");

  await clickPixel(page, 5, 5);
  expect(await pixel(page, 5, 5)).toBe(0);
  await expect.poll(() => canvasPixel(page, 5, 5)).toEqual([0, 0, 0, 255]);
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
