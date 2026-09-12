import { chromium } from "@playwright/test";
import { createServer } from "vite";

export default async function globalSetup() {
  const server = await createServer({
    server: {
      host: "127.0.0.1",
      port: 1431,
      strictPort: true,
    },
  });
  try {
    await server.listen();
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();
      await page.goto("http://127.0.0.1:1431");
      await page.evaluate(async (modulePath) => {
        await import(modulePath);
      }, "/src/settings/SettingsApp.tsx");
    } finally {
      await browser.close();
    }
  } catch (error) {
    // A failed globalSetup has no returned teardown callback.
    await server.close();
    throw error;
  }

  return async () => {
    await server.close();
  };
}
