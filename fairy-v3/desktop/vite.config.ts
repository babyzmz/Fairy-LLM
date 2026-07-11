import babel from "@rolldown/plugin-babel";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const rootDirectory = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: rootDirectory,
  publicDir: fileURLToPath(new URL("../resources", import.meta.url)),
  plugins: [react(), babel({ presets: [reactCompilerPreset()] })],
  build: {
    target: "es2022",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    css: true,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
