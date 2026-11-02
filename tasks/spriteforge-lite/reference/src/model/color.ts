/** Color helpers: hex strings (#rrggbb) <-> RGB components. */

export function hexToRgb(hex: string): [number, number, number] {
  if (!/^#[0-9a-fA-F]{6}$/.test(hex)) throw new Error(`bad color: ${hex}`);
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

export function rgbToHex(r: number, g: number, b: number): string {
  const f = (n: number) => Math.round(clampByte(n)).toString(16).padStart(2, "0");
  return `#${f(r)}${f(g)}${f(b)}`;
}

function clampByte(n: number): number {
  return n < 0 ? 0 : n > 255 ? 255 : n;
}
