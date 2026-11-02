import { MAX_LAYERS } from "./constants";

/** Default palette (PICO-8 style), 16 colors. */
export const DEFAULT_PALETTE: string[] = [
  "#000000", "#1a1c2d", "#2b2d40", "#414751", "#5e666f", "#7a868e",
  "#9aa3ad", "#c4ccd4", "#e0e4e7", "#f5f6f6", "#ffcd77", "#ff8877",
  "#dd5566", "#cc393f", "#29adff", "#2de2a2",
];

export interface PalettePreset {
  name: string;
  colors: string[];
}

export const PRESETS: PalettePreset[] = [
  { name: "Default (16)", colors: [...DEFAULT_PALETTE] },
  {
    name: "Neon",
    colors: [
      "#0d0d0d", "#1b1035", "#31145c", "#58177a", "#811f9c", "#b22bd4",
      "#2bffd4", "#19c2ee", "#0ea5e9", "#4ade80", "#a3e635", "#facc15",
      "#fb923c", "#ef4444", "#ec4899", "#f9a8d4",
    ],
  },
  {
    name: "Pastel",
    colors: [
      "#2f2f3a", "#464654", "#606072", "#8b8b9e", "#c8c8d6", "#eff1f5",
      "#ffc2cc", "#ffd9c0", "#fff3bf", "#cfe6b4", "#aad4cf", "#a9c5ea",
      "#c5b7e2", "#e2c7e8", "#f5d0fe", "#ffe5ec",
    ],
  },
];

export function isValidHex(hex: string): boolean {
  return /^#[0-9a-f]{6}$/.test(hex.toLowerCase());
}

export function validatePalette(palette: string[]): void {
  if (palette.length === 0) throw new Error("palette must have at least one color");
  for (const c of palette) {
    if (!isValidHex(c)) throw new Error(`invalid palette color: ${c}`);
  }
}

export const MAX_PALETTE = 32;
export const LAYER_LIMITS = { min: 1, max: MAX_LAYERS };
