import { expect, type Page } from "@playwright/test";

export type LayerState = {
  name: string;
  visible: boolean;
  opacity: number;
  pixels: number[];
};

export type FrameState = {
  hold: number;
  layers: LayerState[];
};

export type State = {
  width: number;
  height: number;
  fps: number;
  loop: boolean;
  palette: string[];
  activeFrame: number;
  activeLayer: number;
  tool: string;
  brush: 1 | 2;
  frames: FrameState[];
};

export async function getState(page: Page): Promise<State> {
  const value = await page.evaluate(() => (window as any).__spriteforge?.getState());
  expect(value, "window.__spriteforge.getState() is missing").toBeTruthy();
  return value as State;
}

export async function reset(page: Page): Promise<void> {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.waitForSelector('[data-testid="canvas"]');
  await page.waitForFunction(() => Boolean((window as any).__spriteforge));
}

export async function canvasBox(page: Page) {
  const box = await page.getByTestId("canvas").boundingBox();
  expect(box, "canvas is not visible").toBeTruthy();
  return box!;
}

export async function clickPixel(
  page: Page,
  x: number,
  y: number,
  button: "left" | "right" = "left",
): Promise<void> {
  const box = await canvasBox(page);
  await page.mouse.click(box.x + x * 16 + 8, box.y + y * 16 + 8, { button });
  await page.waitForTimeout(30);
}

export async function dragPixels(
  page: Page,
  from: { x: number; y: number },
  to: { x: number; y: number },
): Promise<void> {
  const box = await canvasBox(page);
  await page.mouse.move(box.x + from.x * 16 + 8, box.y + from.y * 16 + 8);
  await page.mouse.down();
  await page.mouse.move(box.x + to.x * 16 + 8, box.y + to.y * 16 + 8, { steps: 5 });
  await page.mouse.up();
  await page.waitForTimeout(30);
}

export async function pixel(page: Page, x: number, y: number): Promise<number> {
  const state = await getState(page);
  return state.frames[state.activeFrame].layers[state.activeLayer].pixels[y * 32 + x];
}

export async function canvasPixel(
  page: Page,
  x: number,
  y: number,
): Promise<[number, number, number, number]> {
  const data = await page.evaluate(
    ({ x, y }) => {
      const canvas = document.querySelector<HTMLCanvasElement>('[data-testid="canvas"]');
      if (!canvas) throw new Error("canvas missing");
      const scale = canvas.width / 32;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("2d context missing");
      const px = Math.min(canvas.width - 1, Math.floor(x * scale + scale / 2));
      const py = Math.min(canvas.height - 1, Math.floor(y * scale + scale / 2));
      return Array.from(ctx.getImageData(px, py, 1, 1).data) as [number, number, number, number];
    },
    { x, y },
  );
  return data;
}

export async function setRange(page: Page, testId: string, value: string): Promise<void> {
  await page.getByTestId(testId).evaluate((element, next) => {
    const input = element as HTMLInputElement;
    input.value = next;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, value);
  await page.waitForTimeout(30);
}

export function hexToRgb(hex: string): [number, number, number] {
  const value = hex.replace("#", "");
  return [
    Number.parseInt(value.slice(0, 2), 16),
    Number.parseInt(value.slice(2, 4), 16),
    Number.parseInt(value.slice(4, 6), 16),
  ];
}
