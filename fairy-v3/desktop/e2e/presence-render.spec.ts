import { expect, test } from "@playwright/test";

for (const deviceScaleFactor of [1, 1.25, 1.5, 2]) {
  test(`transparent WebGL2 probe renders at ${deviceScaleFactor * 100}% scale`, async ({ browser }, testInfo) => {
    const context = await browser.newContext({
      deviceScaleFactor,
      viewport: { width: 640, height: 260 },
    });
    const page = await context.newPage();
    await page.goto("/?surface=presence-render");
    const surface = page.getByTestId("presence-render-surface");
    await expect(surface).toHaveAttribute("data-renderer", "liquid");
    const canvas = page.getByRole("img", { name: "Fairy WebGL renderer probe" });
    await expect(canvas).toHaveAttribute("data-rendered", "true");
    const centerAlpha = Number(await canvas.getAttribute("data-center-alpha"));
    expect(centerAlpha).toBeGreaterThan(0);
    expect(centerAlpha).toBeLessThan(255);
    expect(Number(await canvas.getAttribute("data-corner-alpha"))).toBe(0);
    await page.screenshot({
      path: testInfo.outputPath(`presence-render-probe-${deviceScaleFactor * 100}.png`),
      omitBackground: true,
    });
    await context.close();
  });
}
