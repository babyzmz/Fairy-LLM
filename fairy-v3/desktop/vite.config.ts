import babel from "@rolldown/plugin-babel";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const rootDirectory = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: rootDirectory,
  publicDir: fileURLToPath(new URL("../resources", import.meta.url)),
  plugins: [react(), babel({ presets: [reactCompilerPreset()] })],
  optimizeDeps: {
    // Native dependency trees can contain browser tooling examples with their own
    // imports. Only the desktop shell is a Vite entry; native scratch must never
    // participate in frontend dependency discovery.
    entries: ["index.html"],
  },
  server: {
    host: "127.0.0.1",
    port: 1430,
    // Vite 8 rejects every Windows path containing "~" before checking fs.allow.
    fs: { strict: !rootDirectory.includes("~") },
    watch: {
      ignored: [
        "**/native/**",
        "**/src-tauri/runtime/**",
        "**/src-tauri/target/**",
        "**/playwright-report/**",
        "**/test-results/**",
      ],
    },
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
