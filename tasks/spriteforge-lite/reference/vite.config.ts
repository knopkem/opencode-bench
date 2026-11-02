/// <reference types="vitest" />
import { defineConfig } from "vite";

export default defineConfig({
  build: { outDir: "dist", sourcemap: false },
  test: { globals: true, environment: "node" },
});
