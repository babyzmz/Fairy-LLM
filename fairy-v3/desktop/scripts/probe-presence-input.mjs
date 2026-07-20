import { chromium } from "playwright";

const args = parseArguments(process.argv.slice(2));
const port = Number(args.port);
const action = args.action ?? "measure";
const screenshotPrefix = args["screenshot-prefix"] ?? null;

if (!Number.isInteger(port) || port < 1 || port > 65_535) {
  throw new Error("--port must be a valid TCP port");
}
if (!new Set(["close", "measure", "menu", "open"]).has(action)) {
  throw new Error("--action must be close, measure, menu, or open");
}

const browser = await connectWithRetry(`http://127.0.0.1:${port}`, 30_000);
try {
  const page = await findInputPage(browser, 30_000);
  if (action === "menu") {
    await waitForPresentationCommit(page, browser);
    await page.locator(".presence-core-hit-target").click({ button: "right", force: true });
    const menu = page.getByRole("menu", { name: "Fairy menu" });
    await menu.waitFor({ state: "visible", timeout: 10_000 });
    const state = {
      visible: true,
      items: await menu.locator("button").allTextContents(),
    };
    await page.keyboard.press("Escape");
    process.stdout.write(`${JSON.stringify(state)}\n`);
  } else {
    await ensureInputOpen(page);
    const presentation = await waitForPresentationCommit(page, browser);
    if (action === "close") {
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => {
      const surface = document.querySelector('[data-testid="presence-input-surface"]');
      return surface instanceof HTMLElement && surface.dataset.layout === "hidden";
    });
    process.stdout.write(`${JSON.stringify({ input_open: false, layout: "hidden" })}\n`);
    } else if (action === "open") {
    if (screenshotPrefix !== null) {
      await page.waitForTimeout(350);
      await page.screenshot({
        path: `${screenshotPrefix}-input.png`,
        omitBackground: true,
      });
      const renderPage = await findSurfacePage(browser, "pet-render");
      await renderPage?.screenshot({
        path: `${screenshotPrefix}-render.png`,
        omitBackground: true,
      });
    }
    const state = await page.evaluate(() => {
      const surface = document.querySelector('[data-testid="presence-input-surface"]');
      return {
        input_open: document.querySelector('[data-testid="presence-input-field"]') !== null,
        layout: surface instanceof HTMLElement ? surface.dataset.layout ?? null : null,
        interaction_phase:
          surface instanceof HTMLElement ? surface.dataset.interactionPhase ?? null : null,
        optical_canvas_count: document.querySelectorAll("canvas").length,
      };
    });
    process.stdout.write(`${JSON.stringify({
      ...state,
      presentation,
      render: await readRenderSurface(browser),
    })}\n`);
    } else {
    const result = await measureInput(page);
    const focusPoints = [];
    for (const point of inputHitPoints(result.shell)) {
      await page.mouse.click(point.x, point.y);
      const focused = await page.evaluate(
        () => document.activeElement === document.querySelector(".presence-input textarea"),
      );
      focusPoints.push({ name: point.name, focused });
    }
    process.stdout.write(`${JSON.stringify({
      ...result,
      focus_points: focusPoints,
      presentation,
    })}\n`);
    }
  }
} finally {
  await browser.close();
}

async function ensureInputOpen(page) {
  const field = page.locator('[data-testid="presence-input-field"]');
  if (await field.count() === 0) {
    await page.locator(".presence-core-hit-target").click({ force: true });
  }
  await field.waitFor({ state: "attached", timeout: 30_000 });
  await page.waitForFunction(() => {
    const surface = document.querySelector('[data-testid="presence-input-surface"]');
    return surface instanceof HTMLElement && surface.dataset.layout === "compact";
  });
}

async function measureInput(page) {
  return page.evaluate(() => {
    const rect = (value) => ({
      left: value.left,
      top: value.top,
      right: value.right,
      bottom: value.bottom,
      width: value.width,
      height: value.height,
    });
    const shell = document.querySelector('[data-testid="presence-input-field"]');
    const input = document.querySelector(".presence-input textarea");
    const coreTarget = document.querySelector(".presence-core-hit-target");
    const surface = document.querySelector('[data-testid="presence-input-surface"]');
    if (
      !(shell instanceof HTMLElement)
      || !(input instanceof HTMLTextAreaElement)
      || !(coreTarget instanceof HTMLElement)
    ) {
      throw new Error("Presence input geometry is unavailable");
    }
    const shellRect = shell.getBoundingClientRect();
    const inputRect = input.getBoundingClientRect();
    const edgeDelta = {
      left: Math.abs(shellRect.left - inputRect.left),
      top: Math.abs(shellRect.top - inputRect.top),
      right: Math.abs(shellRect.right - inputRect.right),
      bottom: Math.abs(shellRect.bottom - inputRect.bottom),
    };
    return {
      layout: surface instanceof HTMLElement ? surface.dataset.layout ?? null : null,
      interaction_ready:
        surface instanceof HTMLElement ? surface.dataset.interactionReady ?? null : null,
      interaction_phase:
        surface instanceof HTMLElement ? surface.dataset.interactionPhase ?? null : null,
      device_pixel_ratio: window.devicePixelRatio,
      shell: rect(shellRect),
      textarea: rect(inputRect),
      core_target: rect(coreTarget.getBoundingClientRect()),
      edge_delta_css: edgeDelta,
      edge_delta_physical: {
        left: edgeDelta.left * window.devicePixelRatio,
        top: edgeDelta.top * window.devicePixelRatio,
        right: edgeDelta.right * window.devicePixelRatio,
        bottom: edgeDelta.bottom * window.devicePixelRatio,
      },
    };
  });
}

function inputHitPoints(rect) {
  const inset = Math.max(4, Math.min(10, rect.width / 8, rect.height / 8));
  return [
    { name: "center", x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 },
    { name: "top_left", x: rect.left + inset, y: rect.top + inset },
    { name: "top_right", x: rect.right - inset, y: rect.top + inset },
    { name: "bottom_left", x: rect.left + inset, y: rect.bottom - inset },
    { name: "bottom_right", x: rect.right - inset, y: rect.bottom - inset },
  ];
}

async function connectWithRetry(endpoint, timeoutMs) {
  const startedAt = Date.now();
  let lastError;
  while (Date.now() - startedAt < timeoutMs) {
    try {
      return await chromium.connectOverCDP(endpoint);
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error(`WebView2 CDP did not become ready: ${String(lastError)}`);
}

async function findInputPage(browser, timeoutMs) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    for (const context of browser.contexts()) {
      for (const page of context.pages()) {
        const surface = await page
          .evaluate(() => document.documentElement.dataset.surface ?? null)
          .catch(() => null);
        if (page.url().includes("surface=pet-input") || surface === "pet-input") return page;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("pet-input WebView was not exposed through CDP");
}

async function readRenderSurface(browser) {
  const page = await findSurfacePage(browser, "pet-render");
  if (page === null) return null;
  await page.waitForTimeout(350);
  return page.evaluate(() => {
    const root = document.querySelector('[data-testid="presence-render-surface"]');
    const renderer = document.querySelector('[data-testid="presence-renderer"]');
    const canvas = document.querySelector("canvas.presence-webgl-canvas");
    return {
      renderer: renderer instanceof HTMLElement ? renderer.dataset.renderer ?? null : null,
      health: renderer instanceof HTMLElement
        ? renderer.dataset.rendererHealth ?? null
        : null,
      input_capsule_visible: root instanceof HTMLElement
        ? root.dataset.inputCapsuleVisible === "true"
        : false,
      input_presentation: root instanceof HTMLElement
        ? {
            session_id: Number(root.dataset.inputPresentationSession ?? 0),
            revision: Number(root.dataset.inputPresentationRevision ?? 0),
          }
        : null,
      shape: canvas instanceof HTMLCanvasElement
        ? {
            droplet: canvas.dataset.shapeDroplet ?? null,
            bridge: canvas.dataset.shapeBridge ?? null,
            capsule: canvas.dataset.shapeCapsule ?? null,
          }
        : null,
    };
  });
}

async function waitForPresentationCommit(inputPage, browser) {
  const renderPage = await findSurfacePage(browser, "pet-render");
  if (renderPage === null) throw new Error("pet-render WebView was not exposed through CDP");
  const deadline = Date.now() + 10_000;
  let last = null;
  while (Date.now() < deadline) {
    const input = await inputPage.evaluate(() => {
      const root = document.documentElement;
      return {
        status: root.dataset.inputPresentationStatus ?? null,
        session_id: Number(root.dataset.inputPresentationSession ?? 0),
        revision: Number(root.dataset.inputPresentationRevision ?? 0),
      };
    });
    const render = await renderPage.evaluate(() => {
      const root = document.querySelector('[data-testid="presence-render-surface"]');
      return root instanceof HTMLElement
        ? {
            session_id: Number(root.dataset.inputPresentationSession ?? 0),
            revision: Number(root.dataset.inputPresentationRevision ?? 0),
          }
        : null;
    });
    last = { input, render };
    if (
      input.status === "committed"
      && input.session_id > 0
      && input.revision > 0
      && render !== null
      && render.session_id === input.session_id
      && render.revision === input.revision
    ) {
      return last;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Input/Renderer presentation did not converge: ${JSON.stringify(last)}`);
}

async function findSurfacePage(browser, expectedSurface) {
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      const surface = await page
        .evaluate(() => document.documentElement.dataset.surface ?? null)
        .catch(() => null);
      if (surface === expectedSurface) return page;
    }
  }
  return null;
}

function parseArguments(values) {
  const result = {};
  for (let index = 0; index < values.length; index += 2) {
    const name = values[index];
    const value = values[index + 1];
    if (!name?.startsWith("--") || value === undefined) {
      throw new Error(`Invalid argument near ${name ?? "end of input"}`);
    }
    result[name.slice(2)] = value;
  }
  return result;
}
