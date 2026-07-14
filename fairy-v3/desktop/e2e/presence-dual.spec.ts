import { expect, test, type Page } from "@playwright/test";
import { PNG } from "pngjs";

import type { PresenceProjectionState } from "../src/presence/domain/projection";
import type { PresenceInteractionSnapshot } from "../src/presence/domain/interaction";

const DISPLAY_SCALES = [1, 1.25, 1.5, 2] as const;

for (const scale of DISPLAY_SCALES) {
  test(`render surface stays transparent at ${scale * 100}% scale`, async ({
    browser,
  }, testInfo) => {
    const context = await browser.newContext({
      deviceScaleFactor: scale,
      viewport: { width: 640, height: 260 },
    });
    const page = await context.newPage();
    await page.goto("/?surface=pet-render");

    await expect(page.getByTestId("presence-render-surface")).toBeVisible();
    const canvas = page.locator("canvas.presence-webgl-canvas");
    await expect(canvas).toHaveAttribute("data-rendered", "true");
    await expect(page.getByRole("button")).toHaveCount(0);
    expect(await canvas.evaluate((element) => ({
      height: (element as HTMLCanvasElement).height,
      width: (element as HTMLCanvasElement).width,
    }))).toEqual({
      height: Math.round(260 * scale),
      width: Math.round(640 * scale),
    });
    const screenshot = await page.screenshot({
      path: testInfo.outputPath(`pet-render-liquid-${scale * 100}.png`),
      omitBackground: true,
    });
    const pixels = PNG.sync.read(screenshot);
    expect(visiblePngPixels(pixels)).toBeGreaterThan(250);
    expect(alphaAt(pixels, 0, 0)).toBe(0);
    expect(alphaAt(pixels, Math.round(96 * scale), Math.round(130 * scale))).toBeGreaterThan(0);
    expect(await overflow(page)).toEqual({ horizontal: 0, vertical: 0 });
    await context.close();
  });
}

test("input surface owns cards and controls without duplicating the renderer", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 420, height: 360 } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-input");
  await publishProjection(page, {
    activity: "working",
    work_state: "streaming",
    status_text: "Writing the reply",
    last_cursor: 12,
    last_event_id: "event-12",
    updated_at_ms: Date.now(),
    recent_activity_ms: [Date.now()],
    notice: null,
    reply: {
      id: "reply-12",
      text: "The reply card is isolated from the render-only surface.",
      kind: "scratch",
      streaming: true,
    },
    speaking: false,
  });

  await expect(page.getByRole("status")).toContainText("isolated from the render-only surface");
  await expect(page.getByRole("img", { name: "Fairy" })).toHaveCount(0);
  await expect(page.getByLabel("Fairy companion")).toHaveCount(0);
  expect(await overflow(page)).toEqual({ horizontal: 0, vertical: 0 });
  await page.screenshot({
    path: testInfo.outputPath("pet-input-reply.png"),
    omitBackground: true,
  });
  await context.close();
});

test("pet input reuses one streaming turn and keeps voice and approval isolated", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 420, height: 360 } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-input");
  await installPresenceRequestCapture(page);
  await publishInteraction(page, interactionSnapshot("right", 96));

  const input = page.getByLabel("Quick message to Fairy");
  await expect(input).toBeVisible();
  await input.fill("Stream exactly one reply");
  await input.press("Enter");
  await expect.poll(async () => {
    const requests = await capturedPresenceRequests(page);
    return requests.find((request) => request.kind === "chat.send") ?? null;
  }).toMatchObject({ kind: "chat.send", text: "Stream exactly one reply" });
  const sendRequest = (await capturedPresenceRequests(page)).find(
    (request) => request.kind === "chat.send",
  );
  const submissionId = (sendRequest as { submission_id?: string } | null)?.submission_id;
  expect(submissionId).toEqual(expect.any(String));
  await expect(page.getByRole("status")).toContainText("Sending to Fairy");

  await publishSubmission(page, {
    submission_id: submissionId ?? "missing",
    status: "accepted",
    failure: null,
  });
  await publishProjection(page, {
    activity: "working",
    work_state: "streaming",
    status_text: "Writing the reply",
    last_cursor: 31,
    last_event_id: "event-31",
    updated_at_ms: Date.now(),
    recent_activity_ms: [Date.now()],
    notice: null,
    reply: {
      id: "reply-31",
      text: "One durable stream is visible here.",
      kind: "scratch",
      streaming: true,
    },
    speaking: true,
  });

  await expect(page.getByText("One durable stream is visible here.")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Stop reading" })).toBeVisible();
  await page.getByRole("button", { name: "Stop reading" }).click();
  await page.getByRole("button", { name: "Stop reply" }).click();
  await expect.poll(() => capturedPresenceRequests(page)).toContainEqual({
    kind: "voice.stop",
  });
  await expect.poll(() => capturedPresenceRequests(page)).toContainEqual({
    kind: "chat.cancel",
    submission_id: submissionId,
  });
  await page.screenshot({
    path: testInfo.outputPath("pet-input-streaming-reply.png"),
    omitBackground: true,
  });

  await publishProjection(page, {
    activity: "ready",
    work_state: "ready",
    status_text: "Ready for review",
    last_cursor: 32,
    last_event_id: "event-32",
    updated_at_ms: Date.now(),
    recent_activity_ms: [Date.now()],
    notice: null,
    reply: {
      id: "reply-31",
      text: "One durable stream is visible here.",
      kind: "scratch",
      streaming: false,
    },
    speaking: false,
  });
  await expect(page.getByText("One durable stream is visible here.")).toBeVisible();
  await expect(page.getByText("One durable stream is visible here.")).not.toBeVisible({
    timeout: 6_000,
  });

  await publishProjection(page, {
    activity: "needs_attention",
    work_state: "awaiting_confirmation",
    status_text: "Waiting for your decision",
    last_cursor: 33,
    last_event_id: "event-33",
    updated_at_ms: Date.now(),
    recent_activity_ms: [Date.now()],
    notice: {
      id: "approval-33",
      tone: "critical",
      text: "An approval needs your decision",
    },
    reply: null,
    speaking: false,
  });
  await expect(page.getByRole("button", { name: "Review in Fairy" })).toBeVisible();
  await expect(page.getByRole("button", { name: /approve/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /reject/i })).toHaveCount(0);
  await context.close();
});

test("hover input stays passive until the 520ms interaction gate", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 372, height: 72 } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-input");
  const surface = page.getByTestId("presence-input-surface");

  await publishInteraction(
    page,
    interactionSnapshot("right", 96, "input_reveal", 300, 30, 300),
  );
  await expect(surface).toHaveAttribute("data-layout", "compact");
  await expect(surface).toHaveAttribute("data-content-visible", "false");
  await expect(surface).toHaveAttribute("data-interactive", "false");
  const input = page.getByLabel("Quick message to Fairy");
  await expect(input).toHaveCount(1);
  await expect(input).not.toBeFocused();

  await publishInteraction(
    page,
    interactionSnapshot("right", 96, "input_reveal", 430, 31, 300),
  );
  await expect(surface).toHaveAttribute("data-content-visible", "true");
  await expect(surface).toHaveAttribute("data-interactive", "false");
  await expect(page.locator(".presence-panel")).toHaveAttribute("inert", "");
  await expect(input).not.toBeFocused();

  await publishInteraction(
    page,
    interactionSnapshot("right", 96, "interactive", 520, 32, 520),
  );
  await expect(surface).toHaveAttribute("data-interactive", "true");
  await expect(page.locator(".presence-panel")).not.toHaveAttribute("inert", "");
  await expect(input).not.toBeFocused();
  await input.click();
  await expect(input).toBeFocused();
  const grip = page.getByRole("button", { name: "Move Fairy" });
  await expect(grip).toBeVisible();
  const bounds = await grip.boundingBox();
  expect(bounds).not.toBeNull();
  await page.mouse.move((bounds?.x ?? 0) + 5, (bounds?.y ?? 0) + 5);
  await page.mouse.down();
  await expect(surface).toHaveAttribute("data-moving", "true");
  await page.mouse.move((bounds?.x ?? 0) + 24, (bounds?.y ?? 0) + 14);
  await page.mouse.up();
  await expect(surface).toHaveAttribute("data-moving", "false");
  expect(await overflow(page)).toEqual({ horizontal: 0, vertical: 0 });
  await page.screenshot({
    path: testInfo.outputPath("pet-input-grip.png"),
    omitBackground: true,
  });
  await context.close();
});

test("render settings switch modes through the safe companion-only channel", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 640, height: 260 } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-render");
  await expect(page.getByTestId("presence-renderer")).toHaveAttribute(
    "data-renderer",
    "liquid",
  );
  await page.evaluate(() => {
    const channel = new BroadcastChannel("fairy.presence.render-settings.v1");
    channel.postMessage({
      kind: "render-settings.snapshot",
      settings: {
        schema_version: 1,
        mode: "compatibility",
        size_scale: 0.75,
        opacity: 0.4,
        motion_enabled: false,
        particles_enabled: false,
      },
    });
    window.setTimeout(() => channel.close(), 100);
  });
  await expect(page.getByTestId("presence-renderer")).toHaveAttribute(
    "data-renderer",
    "compatibility",
  );
  await expect(page.getByTestId("presence-render-surface")).toHaveAttribute(
    "data-reduced-motion",
    "true",
  );
  await page.evaluate(() => {
    const channel = new BroadcastChannel("fairy.presence.render-settings.v1");
    channel.postMessage({
      kind: "render-settings.snapshot",
      settings: {
        schema_version: 1,
        mode: "liquid",
        size_scale: 1.5,
        opacity: 1,
        motion_enabled: true,
        particles_enabled: false,
      },
    });
    window.setTimeout(() => channel.close(), 100);
  });
  await expect(page.getByTestId("presence-renderer")).toHaveAttribute(
    "data-renderer",
    "liquid",
  );
  await advanceInteraction(page, "right", 96);
  const screenshot = await page.screenshot({
    path: testInfo.outputPath("pet-render-size-150.png"),
    omitBackground: true,
  });
  const pixels = PNG.sync.read(screenshot);
  expect(alphaAt(pixels, 0, 130)).toBeLessThanOrEqual(4);
  expect(alphaAt(pixels, 639, 130)).toBeLessThanOrEqual(4);
  expect(alphaAt(pixels, 112, 130)).toBeGreaterThan(20);
  await context.close();
});

test("public work and speaking states drive the render surface", async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 640, height: 260 } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-render");
  const surface = page.getByTestId("presence-render-surface");

  await publishProjection(page, {
    activity: "working",
    work_state: "tool",
    status_text: "Working in the project",
    last_cursor: 20,
    last_event_id: "event-20",
    updated_at_ms: Date.now(),
    recent_activity_ms: [Date.now()],
    notice: null,
    reply: null,
    speaking: false,
  });
  await expect(surface).toHaveAttribute("data-work-state", "tool");
  await expect(surface).toHaveAttribute("data-speaking", "false");

  await publishProjection(page, {
    activity: "working",
    work_state: "streaming",
    status_text: "Writing the reply",
    last_cursor: 21,
    last_event_id: "event-21",
    updated_at_ms: Date.now(),
    recent_activity_ms: [Date.now()],
    notice: null,
    reply: null,
    speaking: true,
  });
  await expect(surface).toHaveAttribute("data-work-state", "streaming");
  await expect(surface).toHaveAttribute("data-speaking", "true");
  await page.screenshot({
    path: testInfo.outputPath("pet-render-speaking.png"),
    omitBackground: true,
  });
  await context.close();
});

for (const expansionDirection of ["right", "left"] as const) {
  test(`Liquid Glass material forms a continuous ${expansionDirection} capsule`, async ({
    browser,
  }, testInfo) => {
    const context = await browser.newContext({ viewport: { width: 640, height: 260 } });
    const page = await context.newPage();
    await page.goto("/?surface=pet-render");
    await expect(page.getByTestId("presence-renderer")).toHaveAttribute(
      "data-renderer-health",
      "running",
    );
    const anchorX = expansionDirection === "right" ? 96 : 544;
    await advanceInteraction(page, expansionDirection, anchorX);
    await expect(page.getByTestId("presence-render-surface")).toHaveAttribute(
      "data-interaction-phase",
      "interactive",
    );

    const screenshot = await page.screenshot({
      path: testInfo.outputPath(`pet-render-liquid-${expansionDirection}.png`),
      omitBackground: true,
    });
    const pixels = PNG.sync.read(screenshot);
    const direction = expansionDirection === "right" ? 1 : -1;
    const glassCenter = alphaAt(pixels, anchorX + direction * 42, 130);
    const glassEdge = maximumAlpha(pixels, anchorX, 196, 202);
    expect(glassCenter).toBeGreaterThanOrEqual(15);
    expect(glassCenter).toBeLessThanOrEqual(31);
    expect(glassEdge).toBeGreaterThanOrEqual(54);
    expect(glassEdge).toBeLessThanOrEqual(90);
    expect(alphaAt(pixels, anchorX + direction * 126, 130)).toBeGreaterThan(4);
    expect(alphaAt(pixels, anchorX + direction * 220, 130)).toBeGreaterThan(4);
    expect(alphaAt(pixels, 0, 0)).toBe(0);
    await context.close();
  });
}

test("WebGL recovery locks to Canvas after repeated context loss", async ({
  browser,
}) => {
  const context = await browser.newContext({ viewport: { width: 640, height: 260 } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-render");
  const renderer = page.getByTestId("presence-renderer");
  const canvas = page.locator("canvas.presence-webgl-canvas");
  await expect(renderer).toHaveAttribute("data-renderer", "liquid");
  await expect(renderer).toHaveAttribute("data-renderer-health", "running");

  const extensionAvailable = await canvas.evaluate((element) => {
    const context = (element as HTMLCanvasElement).getContext("webgl2");
    const extension = context?.getExtension("WEBGL_lose_context") ?? null;
    if (extension === null) return false;
    const scope = window as typeof window & {
      __fairyContextExtension?: WEBGL_lose_context;
    };
    scope.__fairyContextExtension = extension;
    extension.loseContext();
    return true;
  });
  expect(extensionAvailable).toBe(true);
  await expect(renderer).toHaveAttribute("data-renderer", "compatibility");
  await expect(renderer).toHaveAttribute("data-renderer-health", "fallback");
  await expect(renderer).toHaveAttribute("data-error-code", "WEBGL_CONTEXT_LOST");
  await expect(page.locator("canvas.presence-compatibility-canvas")).toHaveAttribute(
    "data-rendered",
    "true",
  );

  await page.evaluate(() => {
    const scope = window as typeof window & {
      __fairyContextExtension?: WEBGL_lose_context;
    };
    scope.__fairyContextExtension?.restoreContext();
  });
  await expect(renderer).toHaveAttribute("data-renderer", "liquid");
  await expect(renderer).toHaveAttribute("data-renderer-health", "running");
  await expect(canvas).toHaveAttribute("data-rendered", "true");

  await canvas.evaluate(() => {
    const scope = window as typeof window & {
      __fairyContextExtension?: WEBGL_lose_context;
    };
    scope.__fairyContextExtension?.loseContext();
  });
  await expect(renderer).toHaveAttribute("data-renderer", "compatibility");
  await page.evaluate(() => {
    const scope = window as typeof window & {
      __fairyContextExtension?: WEBGL_lose_context;
    };
    scope.__fairyContextExtension?.restoreContext();
  });
  await page.waitForTimeout(300);
  await expect(renderer).toHaveAttribute("data-renderer", "compatibility");
  await context.close();
});

test("runtime policy and renderer metrics remain bounded and non-visible", async ({
  browser,
}) => {
  const context = await browser.newContext({ viewport: { width: 640, height: 260 } });
  const page = await context.newPage();
  await page.goto("/?surface=pet-render");
  const surface = page.getByTestId("presence-render-surface");
  const canvas = page.locator("canvas.presence-webgl-canvas");
  await page.evaluate(() => {
    const channel = new BroadcastChannel("fairy.presence.runtime-policy.v1");
    channel.postMessage({
      kind: "presence.runtime-policy",
      policy: {
        schema_version: 1,
        frame_rate_limit: 15,
        power_saver: true,
        foreground_fullscreen: false,
      },
    });
    window.setTimeout(() => channel.close(), 100);
  });
  await expect(surface).toHaveAttribute("data-frame-rate-limit", "15");
  await expect(surface).toHaveAttribute("data-power-saver", "true");
  await expect(canvas).toHaveAttribute("data-timing-samples", /\d+/, {
    timeout: 6_000,
  });
  const cpuP95 = Number(await canvas.getAttribute("data-cpu-frame-p95-ms"));
  const gpuMetric = await canvas.getAttribute("data-gpu-frame-p95-ms");
  expect(cpuP95).toBeLessThan(8);
  if (gpuMetric !== "unavailable") expect(Number(gpuMetric)).toBeLessThan(8);
  await expect(surface).not.toContainText(/frame|heap|gpu/i);
  await context.close();
});

async function publishProjection(page: Page, projection: PresenceProjectionState) {
  await page.evaluate((value) => {
    const channel = new BroadcastChannel("fairy.presence.v2");
    const publish = () => channel.postMessage({ kind: "presence.projection", projection: value });
    publish();
    window.setTimeout(publish, 30);
    window.setTimeout(publish, 80);
    window.setTimeout(() => channel.close(), 120);
  }, projection);
}

async function publishSubmission(
  page: Page,
  update: {
    submission_id: string;
    status: "accepted" | "failed" | "cancelled";
    failure: "offline" | "busy" | "unavailable" | null;
  },
) {
  await page.evaluate((value) => {
    const channel = new BroadcastChannel("fairy.presence.v2");
    const publish = () => channel.postMessage({ kind: "presence.submission", update: value });
    publish();
    window.setTimeout(publish, 30);
    window.setTimeout(publish, 80);
    window.setTimeout(() => channel.close(), 120);
  }, update);
}

async function installPresenceRequestCapture(page: Page) {
  await page.evaluate(() => {
    const scope = window as typeof window & {
      __fairyPresenceRequests?: Array<Record<string, unknown>>;
      __fairyPresenceRequestChannel?: BroadcastChannel;
    };
    scope.__fairyPresenceRequests = [];
    scope.__fairyPresenceRequestChannel = new BroadcastChannel("fairy.presence.v2");
    scope.__fairyPresenceRequestChannel.onmessage = (event) => {
      if (event.data?.kind === "presence.request") {
        scope.__fairyPresenceRequests?.push(event.data.request);
      }
    };
  });
}

async function capturedPresenceRequests(
  page: Page,
): Promise<Array<Record<string, unknown>>> {
  return page.evaluate(() => {
    const scope = window as typeof window & {
      __fairyPresenceRequests?: Array<Record<string, unknown>>;
    };
    return scope.__fairyPresenceRequests ?? [];
  });
}

async function publishInteraction(
  page: Page,
  snapshot: PresenceInteractionSnapshot,
) {
  await page.evaluate((value) => {
    const channel = new BroadcastChannel("fairy.presence.interaction.v1");
    const publish = () => channel.postMessage({ kind: "presence.interaction", snapshot: value });
    publish();
    window.setTimeout(publish, 30);
    window.setTimeout(publish, 80);
    window.setTimeout(() => channel.close(), 120);
  }, snapshot);
}

function interactionSnapshot(
  expansion_direction: "left" | "right",
  anchorX: number,
  phase: PresenceInteractionSnapshot["phase"] = "interactive",
  sampledAt = 520,
  sequence = 25,
  phaseStartedAt = sampledAt,
): PresenceInteractionSnapshot {
  return {
    schema_version: 1,
    sequence,
    sampled_at_ms: sampledAt,
    phase,
    phase_started_at_ms: phaseStartedAt,
    reduced_motion: false,
    cursor: {
      point: { x: anchorX + (expansion_direction === "right" ? 40 : -40), y: 130 },
      direction: { x: expansion_direction === "right" ? 1 : -1, y: 0 },
      distance_px: 40,
      speed_px_s: 80,
      dwell_ms: 520,
      band: "active",
    },
    placement: {
      anchor: { x: anchorX, y: 130 },
      render_frame: { x: 0, y: 0, width: 640, height: 260 },
      input_compact_frame: {
        x: expansion_direction === "right" ? 268 : 0,
        y: 94,
        width: 372,
        height: 72,
      },
      input_expanded_frame: {
        x: expansion_direction === "right" ? 220 : 0,
        y: -64,
        width: 420,
        height: 360,
      },
      monitor_work_area: { x: 0, y: 0, width: 1920, height: 1040 },
      scale_factor: 1,
      expansion_direction,
    },
  };
}

async function advanceInteraction(
  page: Page,
  direction: "left" | "right",
  anchorX: number,
) {
  const stages: ReadonlyArray<{
    phase: PresenceInteractionSnapshot["phase"];
    sampledAt: number;
    waitAfter: number;
  }> = [
    { phase: "aware", sampledAt: 0, waitAfter: 100 },
    { phase: "droplet", sampledAt: 100, waitAfter: 80 },
    { phase: "stretching", sampledAt: 180, waitAfter: 120 },
    { phase: "input_reveal", sampledAt: 300, waitAfter: 220 },
    { phase: "interactive", sampledAt: 520, waitAfter: 0 },
  ];
  for (const [index, stage] of stages.entries()) {
    await publishInteraction(
      page,
      interactionSnapshot(direction, anchorX, stage.phase, stage.sampledAt, index + 1),
    );
    if (stage.waitAfter > 0) await page.waitForTimeout(stage.waitAfter);
  }
}

function visiblePngPixels(image: PNG): number {
  let visible = 0;
  for (let index = 3; index < image.data.length; index += 4) {
    if (image.data[index] > 4) visible += 1;
  }
  return visible;
}

function alphaAt(image: PNG, x: number, y: number): number {
  return image.data[(y * image.width + x) * 4 + 3] ?? 0;
}

function maximumAlpha(
  image: PNG,
  x: number,
  startY: number,
  endY: number,
): number {
  let maximum = 0;
  for (let y = startY; y <= endY; y += 1) {
    maximum = Math.max(maximum, alphaAt(image, x, y));
  }
  return maximum;
}

async function overflow(page: Page) {
  return page.evaluate(() => ({
    horizontal: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
    vertical: Math.max(0, document.documentElement.scrollHeight - window.innerHeight),
  }));
}
