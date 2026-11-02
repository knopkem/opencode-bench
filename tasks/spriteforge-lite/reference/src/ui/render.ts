import { W, H } from "../model/constants";

/** Paint an RGBA composite buffer into a W×H offscreen view of `canvas`. */
export function paintComposite(canvas: HTMLCanvasElement, data: Uint8ClampedArray<ArrayBuffer>): void {
  const ctx = canvas.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const off = getOff();
  const octx = off.getContext("2d")!;
  octx.putImageData(new ImageData(data, W, H), 0, 0);
  ctx.drawImage(off, 0, 0, canvas.width, canvas.height);
}

let offCache: HTMLCanvasElement | null = null;
function getOff(): HTMLCanvasElement {
  if (!offCache) {
    offCache = document.createElement("canvas");
    offCache.width = W;
    offCache.height = H;
  }
  return offCache;
}
