import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/support/globalSetup.ts",
  projects: [
    {
      name: "functional",
      grepInvert: /@performance/u,
    },
    {
      name: "performance",
      dependencies: ["functional"],
      fullyParallel: false,
      grep: /@performance/u,
    },
  ],
  use: {
    baseURL: "http://127.0.0.1:1431",
    trace: "retain-on-failure",
  },
});
