import babel from "@rolldown/plugin-babel";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const rootDirectory = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: rootDirectory,
  publicDir: fileURLToPath(new URL("../resources", import.meta.url)),
  plugins: [react(), babel({ presets: [reactCompilerPreset()] })],
  server: {
    host: "127.0.0.1",
    port: 1430,
    // Vite 8 rejects every Windows path containing "~" before checking fs.allow.
    fs: { strict: !rootDirectory.includes("~") },
  },
  build: {
    manifest: true,
    target: "es2022",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    css: true,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
