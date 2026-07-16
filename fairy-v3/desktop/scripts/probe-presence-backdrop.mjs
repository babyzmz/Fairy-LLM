import { chromium } from "playwright";

const port = Number(process.argv[2] ?? 9225);
const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
try {
  const pages = browser.contexts().flatMap((context) => context.pages());
  const renderPage = pages.find((page) => page.url().includes("surface=pet-render"));
  const inputPage = pages.find((page) => page.url().includes("surface=pet-input"));
  if (renderPage === undefined || inputPage === undefined) {
    throw new Error("Presence WebViews are unavailable");
  }
  const inputField = inputPage.locator('[data-testid="presence-input-field"]');
  if (await inputField.count() === 0) {
    await inputPage.locator(".presence-core-hit-target").click({ force: true });
  }
  await inputField.waitFor({ state: "attached", timeout: 10_000 });
  const readiness = await Promise.allSettled([
    renderPage.waitForFunction(
      () => document.querySelector(".presence-webgl-canvas")?.getAttribute("data-backdrop-status") === "ready",
      undefined,
      { timeout: 3_000 },
    ),
    inputPage.waitForFunction(
      () => document.querySelector(".presence-input-glass")?.getAttribute("data-backdrop-status") === "ready",
      undefined,
      { timeout: 3_000 },
    ),
  ]);

  const samples = [];
  for (let index = 0; index < 40; index += 1) {
    samples.push({
      render: await readCanvas(renderPage, ".presence-webgl-canvas"),
      input: await readCanvas(inputPage, ".presence-input-glass"),
    });
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  const summary = {
    readiness: readiness.map((result) => result.status),
    render: summarize(samples.map((sample) => sample.render)),
    input: summarize(samples.map((sample) => sample.input)),
  };
  process.stdout.write(`${JSON.stringify(summary)}\n`);
} finally {
  await browser.close();
}

async function readCanvas(page, selector) {
  return page.locator(selector).evaluate((canvas) => ({
    status: canvas.dataset.backdropStatus ?? null,
    error: canvas.dataset.backdropError ?? null,
    sequence: Number(canvas.dataset.backdropSequence ?? -1),
    age: Number(canvas.dataset.backdropAgeMs ?? -1),
    width: canvas.width,
    height: canvas.height,
  }));
}

function summarize(samples) {
  const sequences = samples.map((sample) => sample.sequence).filter(Number.isFinite);
  const ages = samples.map((sample) => sample.age).filter((value) => value >= 0).sort((a, b) => a - b);
  return {
    status: samples.at(-1)?.status ?? null,
    error: samples.at(-1)?.error ?? null,
    dimensions: [samples.at(-1)?.width ?? 0, samples.at(-1)?.height ?? 0],
    sequence_start: Math.min(...sequences),
    sequence_end: Math.max(...sequences),
    unique_frames: new Set(sequences).size,
    age_p50_ms: percentile(ages, 0.5),
    age_p95_ms: percentile(ages, 0.95),
    age_max_ms: ages.at(-1) ?? null,
  };
}

function percentile(values, percentileValue) {
  if (values.length === 0) return null;
  return values[Math.min(values.length - 1, Math.ceil(values.length * percentileValue) - 1)];
}
