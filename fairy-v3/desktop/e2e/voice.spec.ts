import { expect, test, type Page } from "@playwright/test";

import { installWorkspaceFixture } from "./support/coreFixture";

test.beforeEach(async ({ page }) => {
  await installWorkspaceFixture(page);
  await page.setViewportSize({ width: 880, height: 680 });
  await page.goto("/");
  await page
    .getByLabel("History navigation")
    .getByRole("button", { name: "Scratch chat", exact: true })
    .click();
});

test("records audio through Core and inserts the transcript into the composer", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Start recording" }).click();
  await expect(page.getByRole("button", { name: "Stop recording" })).toBeVisible();
  await page.getByRole("button", { name: "Stop recording" }).click();

  await expect(page.getByLabel("Message Fairy")).toHaveValue("Fixture voice transcript");
  const request = await voiceCall(page, "voice.transcribe");
  expect(request.params).toMatchObject({
    conversation_id: "0198f4de-0114-7000-8000-000000000010",
    profile_id: "openrouter-deepseek-v4-pro",
    media_type: "audio/webm",
  });
  expect(request.params.audio_base64).toBe("Zml4dHVyZS1yZWNvcmRpbmc=");
});

test("streams a durable assistant message through a native voice session", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Speak message" }).click();

  await expect
    .poll(async () => (await voiceCalls(page, "voice.sessions.start")).length)
    .toBe(1);
  const [request] = await voiceCalls(page, "voice.sessions.start");
  expect(request.params).toMatchObject({
    task_id: "0198f4de-0114-7000-8000-000000000011",
    turn_id: "0198f4de-0114-7000-8000-000000000012",
    message_id: "0198f4de-0114-7000-8000-000000000013",
    start_offset: 0,
    end_offset: 23,
  });
  expect(request.params).not.toHaveProperty("text");
  expect(request.params).not.toHaveProperty("profile_id");
});

test("shows a denied microphone state without sending audio", async ({ page }) => {
  await page.evaluate(() => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => {
          throw new DOMException("denied", "NotAllowedError");
        },
      },
    });
  });

  await page.getByRole("button", { name: "Start recording" }).click();

  await expect(page.getByRole("alert")).toContainText("Microphone permission denied");
  expect(await voiceCalls(page, "voice.transcribe")).toHaveLength(0);
});

interface FixtureCall {
  method: string;
  params: Record<string, unknown>;
}

test("host realtime focus cancels ordinary recording and releases controls after stop", async ({ page }) => {
  await page.getByRole("button", { name: "Start recording" }).click();
  await expect(page.getByRole("button", { name: "Stop recording" })).toBeVisible();
  const focus = (active: boolean) => page.evaluate((value) => {
    (window as unknown as { __FAIRY_FIXTURE_SET_AUDIO_FOCUS__(active: boolean): void })
      .__FAIRY_FIXTURE_SET_AUDIO_FOCUS__(value);
  }, active);
  await focus(true);
  await expect(page.getByRole("button", { name: "Stop recording" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start recording" })).toBeDisabled();
  expect(await voiceCalls(page, "voice.transcribe")).toHaveLength(0);
  await focus(false);
  await expect(page.getByRole("button", { name: "Start recording" })).toBeEnabled();
  await expect(page.getByLabel("Message Fairy")).toHaveValue("");
});

async function voiceCall(page: Page, method: string): Promise<FixtureCall> {
  await expect.poll(async () => (await voiceCalls(page, method)).length).toBeGreaterThan(0);
  const result = (await voiceCalls(page, method))[0];
  if (result === undefined) throw new Error(`Missing fixture call: ${method}`);
  return result;
}

async function voiceCalls(page: Page, method: string): Promise<FixtureCall[]> {
  return page.evaluate((targetMethod) => {
    const fixtureWindow = window as unknown as {
      __FAIRY_FIXTURE_CALLS__: FixtureCall[];
    };
    return fixtureWindow.__FAIRY_FIXTURE_CALLS__.filter(
      (call) => call.method === targetMethod,
    );
  }, method);
}
