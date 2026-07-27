import { expect, test, type Page } from "@playwright/test";
import { PNG } from "pngjs";

import { installWorkspaceFixture, PREVIEW_URL } from "./support/coreFixture";

test.beforeEach(async ({ page }) => {
  await installWorkspaceFixture(page);
});

test("minimum desktop window renders the durable workspace without overflow", async ({ page }) => {
  await page.setViewportSize({ width: 880, height: 680 });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Task Timeline" })).toBeVisible();
  await expect(page.getByText("Preview is ready")).toBeVisible();
  await expect(page.getByTitle("Task preview")).toHaveAttribute("src", PREVIEW_URL);
  await expect(page.getByTitle("Task preview")).toHaveAttribute("sandbox", "allow-forms allow-scripts");
  await page.getByRole("tab", { name: /Files/ }).click();
  await page.getByRole("button", { name: "main.ts" }).click();
  await expect(page.getByText("console.log('Fairy');")).toBeVisible();
  const properties = page.getByLabel("File properties");
  await expect(properties).toBeVisible();
  await expect(properties).toContainText("native");
  await properties.getByPlaceholder("Add a note").fill("Verify the entry point");
  await properties.getByRole("button", { name: "Add note" }).click();
  await expect(properties.getByText("Verify the entry point")).toBeVisible();
  expect(
    (await page.evaluate(() => window.__FAIRY_FIXTURE_CALLS__)).some((call) => call.method === "annotations.update"),
  ).toBe(true);
  await page.getByRole("tab", { name: "Preview" }).click();
  await expect(page.getByText("Developer diagnostic")).toHaveCount(0);
  await expect(page.getByLabel("Workspace status")).toContainText("standard");
  await expect(page.getByLabel("Workspace status")).toContainText("Core ready");

  const layout = await measureLayout(page);
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.documentHeight).toBeLessThanOrEqual(layout.viewportHeight);
  expect(layout.composerBottom).toBeLessThanOrEqual(layout.viewportHeight);
  expect(layout.contextBottom).toBeLessThanOrEqual(layout.composerTop);
});

test("Workspace inspector supports keyboard-only tabs and bounded resizing", async ({ page }) => {
  await page.setViewportSize({ width: 1180, height: 720 });
  await page.goto("/");

  const preview = page.getByRole("tab", { name: "Preview" });
  const files = page.getByRole("tab", { name: /Files/ });
  await preview.focus();
  await page.keyboard.press("ArrowRight");

  await expect(files).toBeFocused();
  await expect(files).toHaveAttribute("aria-selected", "true");
  await expect(files).toHaveAttribute("tabindex", "0");
  const panelId = await files.getAttribute("aria-controls");
  expect(panelId).not.toBeNull();
  await expect(page.locator(`#${panelId}`)).toHaveAttribute(
    "aria-labelledby",
    await files.getAttribute("id") as string,
  );

  const separator = page.getByRole("separator", {
    name: "Resize workspace inspector",
  });
  await expect(separator).toHaveAttribute("aria-valuemin", "360");
  const maximum = Number(await separator.getAttribute("aria-valuemax"));
  expect(maximum).toBeGreaterThanOrEqual(360);
  expect(maximum).toBeLessThanOrEqual(Math.round(1180 * 0.75));

  await separator.focus();
  await page.keyboard.press("Home");
  await expect(separator).toHaveAttribute("aria-valuenow", "360");
  await page.keyboard.press("End");
  await expect(separator).toHaveAttribute("aria-valuenow", String(maximum));
  await page.keyboard.press("ArrowRight");
  await expect(separator).toHaveAttribute(
    "aria-valuenow",
    String(Math.max(360, maximum - 24)),
  );

  const layout = await measureLayout(page);
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
});

test("Preview Browser opens the scoped Runtime and renders a controlled snapshot", async ({ page }) => {
  await page.setViewportSize({ width: 1180, height: 760 });
  await page.goto("/");

  await page.getByRole("button", { name: "Browser", exact: true }).click();
  await page.getByRole("button", { name: /Open the current Runtime/ }).click();

  await expect(page.getByLabel("Controlled browser")).toBeVisible();
  await expect(page.getByAltText("Current controlled browser page")).toBeVisible();
  const calls = await page.evaluate(() => window.__FAIRY_FIXTURE_CALLS__);
  expect(calls).toContainEqual(expect.objectContaining({
    method: "browser.sessions.start",
    params: expect.objectContaining({
      conversation_id: "0198f4de-0114-7000-8000-000000000002",
      task_id: "0198f4de-0114-7000-8000-000000000003",
      initial_url: PREVIEW_URL,
    }),
  }));
});

test("narrow workspace remains usable and reduced motion disables repeated HUD motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 640, height: 700 });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Task Timeline" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Preview" })).toBeVisible();

  const acceptButton = page.getByRole("button", { name: "Use this version" });
  await acceptButton.scrollIntoViewIfNeeded();
  await expect(acceptButton).toBeVisible();

  const layout = await measureLayout(page);
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.composerBottom).toBeLessThanOrEqual(layout.viewportHeight);

  const decisionBottom = await acceptButton.evaluate((element) => element.getBoundingClientRect().bottom);
  expect(decisionBottom).toBeLessThanOrEqual(layout.composerTop);

  const motion = await page.locator(".scan-line").evaluate((element) => {
    const style = getComputedStyle(element);
    const duration = style.animationDuration;
    return {
      durationMs: duration.endsWith("ms") ? Number.parseFloat(duration) : Number.parseFloat(duration) * 1_000,
      iterations: style.animationIterationCount,
    };
  });
  expect(motion.durationMs).toBeLessThanOrEqual(0.001);
  expect(motion.iterations).toBe("1");
});

test("PDF files use the isolated native viewer with bounded controls", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 760 });
  await page.goto("/");
  await page.getByRole("tab", { name: /Files/ }).click();
  await page.getByRole("button", { name: "sample.pdf" }).click();

  const viewer = page.getByLabel("PDF viewer: docs/sample.pdf");
  await expect(viewer).toBeVisible();
  await expect(viewer.getByText("1 / 1")).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Previous PDF page" })).toBeDisabled();
  await expect(viewer.getByRole("button", { name: "Next PDF page" })).toBeDisabled();
  const canvas = viewer.getByRole("img", { name: "docs/sample.pdf, page 1" });
  await expect(canvas).toBeVisible();
  expect(
    await canvas.evaluate((element) => ({
      width: (element as HTMLCanvasElement).width,
      height: (element as HTMLCanvasElement).height,
    })),
  ).toEqual({ width: 300, height: 200 });
});

test("generated images expose native inspection and durable provenance", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 760 });
  await page.goto("/");
  await page.getByRole("tab", { name: /Files/ }).click();
  await page.getByRole("button", { name: "generated.png" }).click();

  await expect(page.getByRole("img", { name: "generated.png" })).toBeVisible();
  await expect(page.getByText("1 x 1")).toBeVisible();
  const properties = page.getByLabel("File properties");
  await expect(properties).toContainText("Generated hero");
  await expect(properties).toContainText("image-test");
});

test("glTF models render nonblank pixels and preserve scene-node selections", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await page.getByRole("tab", { name: /Files/ }).click();
  await page.getByRole("button", { name: "triangle.gltf" }).click();

  const viewer = page.getByLabel("3D model viewer: models/triangle.gltf");
  await expect(viewer).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Fairy_Triangle" })).toBeVisible();
  const canvas = page.getByLabel("3D viewport: models/triangle.gltf");
  await expect(canvas).toBeVisible();
  await expect(viewer.getByText(/1 meshes.+1 triangles/)).toBeVisible();
  const pixels = PNG.sync.read(await canvas.screenshot());
  let coloredPixels = 0;
  for (let index = 0; index < pixels.data.length; index += 4) {
    if (pixels.data[index + 1] > pixels.data[index] + 20 && pixels.data[index + 1] > pixels.data[index + 2]) {
      coloredPixels += 1;
    }
  }
  expect(coloredPixels).toBeGreaterThan(20);

  await viewer.getByRole("button", { name: "Fairy_Triangle" }).click();
  const properties = page.getByLabel("File properties");
  await expect(properties).toContainText("Fairy_Triangle");
  await properties.getByRole("button", { name: "Save selection" }).click();
  const calls = await page.evaluate(() => window.__FAIRY_FIXTURE_CALLS__);
  expect(
    calls.some(
      (call) =>
        call.method === "selections.create" &&
        call.params.locator_kind === "scene_node" &&
        (call.params.locator as { node_path?: string }).node_path === "0",
    ),
  ).toBe(true);
});

test("governed Review promotes a previewing Task before Version acceptance", async ({ page }) => {
  await page.goto("/?taskStatus=previewing");

  const accept = page.getByRole("button", { name: "Use this version" });
  await expect(accept).toBeDisabled();
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await expect(accept).toBeEnabled();
  await accept.click();

  const calls = await page.evaluate(
    () =>
      (
        window as unknown as {
          __FAIRY_FIXTURE_CALLS__: Array<{ method: string }>;
        }
      ).__FAIRY_FIXTURE_CALLS__,
  );
  expect(calls.some((call) => call.method === "tasks.review")).toBe(true);
  expect(calls.some((call) => call.method === "versions.accept")).toBe(true);
});

async function measureLayout(page: Page) {
  return page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight,
    documentWidth: document.documentElement.scrollWidth,
    documentHeight: document.documentElement.scrollHeight,
    contextBottom: document.querySelector(".context-bar")?.getBoundingClientRect().bottom ?? 0,
    composerTop: document.querySelector(".chat-composer")?.getBoundingClientRect().top ?? 0,
    composerBottom: document.querySelector(".chat-composer")?.getBoundingClientRect().bottom ?? 0,
  }));
}
