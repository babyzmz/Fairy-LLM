import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: "http://127.0.0.1:1431",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run e2e:serve",
    url: "http://127.0.0.1:1431",
    reuseExistingServer: process.env.CI !== "true",
  },
});
