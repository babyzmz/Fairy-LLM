import type { Page } from "@playwright/test";
import { createHash } from "node:crypto";

export const PREVIEW_URL = "http://127.0.0.1:43125/";
const VOICE_WAV = pcmWav();
const VOICE_WAV_BASE64 = Buffer.from(VOICE_WAV).toString("base64");
const VOICE_WAV_HASH = createHash("sha256").update(VOICE_WAV).digest("hex");
const CAPTURE_PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
const CAPTURE_PNG_HASH = createHash("sha256")
  .update(Buffer.from(CAPTURE_PNG_BASE64, "base64"))
  .digest("hex");
const PDF_FIXTURE_BASE64 =
  "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQovVGl0bGUgKEZhaXJ5IFBERiBGaXh0dXJlKQo+PgplbmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdlcyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3VyY2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgMzAwIDIwMCBdCi9QYXJlbnQgMiAwIFIKPj4KZW5kb2JqCnhyZWYKMCA1CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwIG4gCjAwMDAwMDAwODEgMDAwMDAgbiAKMDAwMDAwMDE0MCAwMDAwMCBuIAowMDAwMDAwMTg5IDAwMDAwIG4gCnRyYWlsZXIKPDwKL1NpemUgNQovUm9vdCAzIDAgUgovSW5mbyAxIDAgUgo+PgpzdGFydHhyZWYKMjg4CiUlRU9GCg==";
const PDF_FIXTURE = Buffer.from(PDF_FIXTURE_BASE64, "base64");
const PDF_FIXTURE_HASH = createHash("sha256").update(PDF_FIXTURE).digest("hex");
const MODEL_BUFFER = Buffer.alloc(36);
[-1, -1, 0, 1, -1, 0, 0, 1, 0].forEach((value, index) =>
  MODEL_BUFFER.writeFloatLE(value, index * 4),
);
const MODEL_FIXTURE_TEXT = JSON.stringify({
  asset: { version: "2.0", generator: "Fairy fixture" },
  buffers: [
    {
      uri: `data:application/octet-stream;base64,${MODEL_BUFFER.toString("base64")}`,
      byteLength: 36,
    },
  ],
  bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 36 }],
  accessors: [
    {
      bufferView: 0,
      componentType: 5126,
      count: 3,
      type: "VEC3",
      min: [-1, -1, 0],
      max: [1, 1, 0],
    },
  ],
  materials: [
    {
      name: "Fairy mint",
      pbrMetallicRoughness: { baseColorFactor: [0.2, 0.8, 0.72, 1] },
    },
  ],
  meshes: [
    {
      name: "Triangle",
      primitives: [{ attributes: { POSITION: 0 }, material: 0 }],
    },
  ],
  nodes: [{ name: "Fairy Triangle", mesh: 0 }],
  scenes: [{ nodes: [0] }],
  scene: 0,
});
const MODEL_FIXTURE = Buffer.from(MODEL_FIXTURE_TEXT);
const MODEL_FIXTURE_HASH = createHash("sha256")
  .update(MODEL_FIXTURE)
  .digest("hex");

export async function installWorkspaceFixture(page: Page) {
  await installCoreFixture(page);
  await page.route(`${PREVIEW_URL}**`, async (route) => {
    if (route.request().url().endsWith("/fixture.pdf")) {
      await route.fulfill({
        contentType: "application/pdf",
        body: PDF_FIXTURE,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Expose-Headers":
            "Accept-Ranges, Content-Length, Content-Range",
          "Accept-Ranges": "bytes",
        },
      });
      return;
    }
    if (route.request().url().endsWith("/fixture.png")) {
      await route.fulfill({
        contentType: "image/png",
        body: Buffer.from(CAPTURE_PNG_BASE64, "base64"),
        headers: { "Access-Control-Allow-Origin": "*" },
      });
      return;
    }
    if (route.request().url().endsWith("/fixture.gltf")) {
      await route.fulfill({
        contentType: "model/gltf+json",
        body: MODEL_FIXTURE,
        headers: { "Access-Control-Allow-Origin": "*" },
      });
      return;
    }
    await route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html><body style="margin:0;font-family:Segoe UI;background:#f4f6f3;color:#18201d"><main style="padding:28px"><h1>Atlas preview</h1><p>Durable candidate version</p></main></body></html>`,
    });
  });
}

async function installCoreFixture(page: Page) {
  await page.addInitScript(
    ({
      previewUrl,
      voiceWavBase64,
      voiceWavHash,
      capturePngBase64,
      capturePngHash,
      pdfFixtureHash,
      pdfFixtureByteLength,
      modelFixtureText,
      modelFixtureHash,
      modelFixtureByteLength,
    }) => {
      const fixtureWindow = window as unknown as {
        isTauri: boolean;
        __FAIRY_BOOT_STARTED_AT__: number;
        __FAIRY_FIXTURE_CALLS__: Array<{
          method: string;
          params: Record<string, unknown>;
        }>;
        __FAIRY_PUSH_EVENT__: (message: string) => number;
      };
      fixtureWindow.isTauri = true;
      fixtureWindow.__FAIRY_FIXTURE_CALLS__ = [];
      Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: {
          getUserMedia: async () => ({
            getTracks: () => [{ stop() {} }],
          }),
        },
      });
      class FixtureMediaRecorder extends EventTarget {
        readonly mimeType = "audio/webm;codecs=opus";
        state = "inactive";

        constructor(_stream: unknown) {
          super();
        }

        start() {
          this.state = "recording";
        }

        stop() {
          if (this.state === "inactive") return;
          this.state = "inactive";
          const data = new Event("dataavailable");
          Object.defineProperty(data, "data", {
            value: new Blob(["fixture-recording"], { type: "audio/webm" }),
          });
          this.dispatchEvent(data);
          queueMicrotask(() => this.dispatchEvent(new Event("stop")));
        }
      }
      class FixtureAudio extends EventTarget {
        constructor(_url: string) {
          super();
        }

        play() {
          window.setTimeout(() => this.dispatchEvent(new Event("ended")), 20);
          return Promise.resolve();
        }

        pause() {}
      }
      Object.defineProperty(window, "MediaRecorder", {
        configurable: true,
        value: FixtureMediaRecorder,
      });
      Object.defineProperty(window, "Audio", {
        configurable: true,
        value: FixtureAudio,
      });
      const id = {
        project: "0198f4de-0114-7000-8000-000000000001",
        conversation: "0198f4de-0114-7000-8000-000000000002",
        task: "0198f4de-0114-7000-8000-000000000003",
        version: "0198f4de-0114-7000-8000-000000000004",
        event: "0198f4de-0114-7000-8000-000000000005",
        runtime: "0198f4de-0114-7000-8000-000000000006",
        preview: "0198f4de-0114-7000-8000-000000000007",
        scratchConversation: "0198f4de-0114-7000-8000-000000000010",
        scratchTask: "0198f4de-0114-7000-8000-000000000011",
        turn: "0198f4de-0114-7000-8000-000000000012",
        projectTurn: "0198f4de-0114-7000-8000-000000000014",
        message: "0198f4de-0114-7000-8000-000000000013",
        commandRun: "0198f4de-0114-7000-8000-000000000015",
        toolInvocation: "0198f4de-0114-7000-8000-000000000016",
        approval: "0198f4de-0114-7000-8000-000000000017",
        resumedMessage: "0198f4de-0114-7000-8000-000000000018",
        checkpoint: "0198f4de-0114-7000-8000-000000000019",
        scratchVersion: "0198f4de-0114-7000-8000-000000000021",
        fileSet: "0198f4de-0114-7000-8000-000000000022",
        renderJob: "0198f4de-0114-7000-8000-000000000023",
        presentation: "0198f4de-0114-7000-8000-000000000024",
        annotation: "0198f4de-0114-7000-8000-000000000025",
        selection: "0198f4de-0114-7000-8000-000000000026",
        pdfFileSet: "0198f4de-0114-7000-8000-000000000027",
        pdfRenderJob: "0198f4de-0114-7000-8000-000000000028",
        pdfPresentation: "0198f4de-0114-7000-8000-000000000029",
        schedule: "0198f4de-0114-7000-8000-000000000046",
        occurrence: "0198f4de-0114-7000-8000-000000000099",
      };
      const timestamp = "2026-07-11T00:00:00Z";
      const fixtureParams = new URLSearchParams(window.location.search);
      const companionActive = fixtureParams.get("companionActive");
      const companionAssistance = fixtureParams.get("companionAssistance");
      const companionCapture = fixtureParams.get("companionCapture");
      const companionSessionId = "0198f4de-0114-7000-8000-000000000030";
      let companionPresenceState:
        | "listening"
        | "standby"
        | "privacy_paused" =
        companionActive === "standby" || companionActive === "duration"
          ? "standby"
          : "listening";
      let companionStandbyReason: "inactivity" | "duration_limit" | null =
        companionActive === "duration"
          ? "duration_limit"
          : companionActive === "standby"
            ? "inactivity"
            : null;
      let companionRequestedProfile: "auto" | "game" | "focus" = "auto";
      let companionEffectiveActivity: "game" | "focus" = "game";
      let companionInteractionIntensity: "quiet" | "standard" | "active" =
        "standard";
      let companionProjectionSequence = 7;
      let companionAssistanceStatus =
        companionAssistance === "approval" ? "awaiting_approval" : null;
      let companionCaptureAvailable = companionCapture !== "unavailable";
      let companionMicrophoneStatus: "active" | "unavailable" =
        companionCapture === "unavailable" ? "unavailable" : "active";
      const initialTaskStatus =
        fixtureParams.get("taskStatus") === "previewing"
          ? "previewing"
          : "ready";
      const executionRecovery = fixtureParams.get("executionRecovery") === "1";
      const historySeed = fixtureParams.get("historySeed") === "1";
      let project = {
        id: id.project,
        workspace_id: id.project,
        name: "Atlas Console",
        residency: "local_only",
        active_version_id: id.version,
        active_preview_id: id.preview,
        revision: 1,
        pinned_at: null as string | null,
        archived_at: historySeed ? timestamp : null,
        deleted_at: null as string | null,
        purged_at: null as string | null,
        metadata_revision: 0,
        created_at: timestamp,
        updated_at: timestamp,
      };
      let conversation = {
        id: id.conversation,
        project_id: id.project,
        workspace_id: id.project,
        workspace_type: "project_chat",
        base_version_id: id.version,
        active_draft_version_id: id.version,
        active_task_id: id.task,
        active_preview_id: id.preview,
        title: "Project conversation",
        pinned_at: null,
        deleted_at: null,
        deleted_by_project_at: null as string | null,
        purged_at: null as string | null,
        revision: 0,
        created_at: timestamp,
        updated_at: timestamp,
      };
      let task = {
        id: id.task,
        project_id: id.project,
        workspace_id: id.project,
        conversation_id: id.conversation,
        user_request: "Tighten the project overview",
        operation_mode: "continue_current_chat_draft",
        base_version_id: id.version,
        execution_target: "local",
        target_version_id: id.version,
        memory_snapshot_id: null,
        memory_snapshot_hash: null,
        status: initialTaskStatus,
        display_title: "Tighten the project overview",
        pinned_at: null,
        metadata_revision: 0,
        created_at: timestamp,
        updated_at: timestamp,
      };
      let scratchConversation = {
        id: id.scratchConversation,
        project_id: null,
        workspace_id: id.scratchConversation,
        workspace_type: "chat_scratch",
        base_version_id: null,
        active_draft_version_id: id.scratchVersion,
        active_task_id: null,
        active_preview_id: null,
        title: "Scratch chat",
        pinned_at: null,
        deleted_at: historySeed ? timestamp : null,
        deleted_by_project_at: null as string | null,
        purged_at: null as string | null,
        revision: 0,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const scratchTask = {
        id: id.scratchTask,
        project_id: null,
        workspace_id: id.scratchConversation,
        conversation_id: id.scratchConversation,
        user_request: "Fixture chat request",
        operation_mode: "answer",
        base_version_id: null,
        execution_target: "local",
        target_version_id: id.scratchVersion,
        memory_snapshot_id: null,
        memory_snapshot_hash: null,
        status: "ready",
        display_title: "Fixture chat request",
        pinned_at: null,
        metadata_revision: 0,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const completedTurn = {
        id: id.turn,
        task_id: id.scratchTask,
        conversation_id: id.scratchConversation,
        profile_id: "openrouter-deepseek-v4-pro",
        status: "completed",
        idempotency_key: "e2e-turn",
        scope_digest: "e2e-scope",
        memory_snapshot_id: "0198f4de-0114-7000-8000-000000000014",
        memory_snapshot_hash: "e2e-memory",
        cancellation_revision: 0,
        usage: {},
        created_at: timestamp,
        updated_at: timestamp,
        started_at: timestamp,
        completed_at: timestamp,
        error_code: null,
      };
      const projectMessage = {
        id: "0198f4de-0114-7000-8000-000000000160",
        conversation_id: id.conversation,
        task_id: id.task,
        turn_id: id.projectTurn,
        sequence: 1,
        role: "user",
        visibility: "user",
        content: "Tighten the project boundary",
        created_at: timestamp,
      };
      const projectTraceId = "0198f4de-0114-7000-8000-000000000161";
      const projectTraceStep = (
        stepId: string,
        sequence: number,
        kind: string,
        publicSummary: string,
        overrides: Record<string, unknown> = {},
      ) => ({
        id: stepId,
        trace_id: projectTraceId,
        turn_id: id.projectTurn,
        sequence,
        parent_step_id: null,
        caused_by_step_id: null,
        kind,
        status: "succeeded",
        public_summary: publicSummary,
        public_detail: null,
        model_id: null,
        model_role: null,
        provider_attempt_id: null,
        command_run_id: null,
        artifact_refs: [],
        visibility: "user",
        revision: 1,
        created_at: timestamp,
        updated_at: timestamp,
        started_at: timestamp,
        completed_at: timestamp,
        duration_ms: 40,
        ...overrides,
      });
      const projectTraceSteps = [
        projectTraceStep(
          "0198f4de-0114-7000-8000-000000000162",
          1,
          "plan",
          "Project scope prepared",
        ),
        projectTraceStep(
          "0198f4de-0114-7000-8000-000000000163",
          2,
          "verification",
          "Preview is ready",
          {
            caused_by_step_id: "0198f4de-0114-7000-8000-000000000162",
            duration_ms: 120,
          },
        ),
        ...(executionRecovery
          ? [
              projectTraceStep(
                "0198f4de-0114-7000-8000-000000000164",
                3,
                "tool",
                "Sandbox command running",
                {
                  parent_step_id: "0198f4de-0114-7000-8000-000000000162",
                  caused_by_step_id: "0198f4de-0114-7000-8000-000000000162",
                  command_run_id: id.commandRun,
                  duration_ms: 300,
                },
              ),
              projectTraceStep(
                "0198f4de-0114-7000-8000-000000000165",
                4,
                "observation",
                "Runtime recovered after worker interruption",
                {
                  caused_by_step_id: "0198f4de-0114-7000-8000-000000000164",
                  duration_ms: 75,
                },
              ),
            ]
          : []),
      ];
      const projectTrace = {
        id: projectTraceId,
        turn_id: id.projectTurn,
        conversation_id: id.conversation,
        task_id: id.task,
        legacy: false,
        last_sequence: projectTraceSteps.length,
        revision: projectTraceSteps.length,
        created_at: timestamp,
        updated_at: timestamp,
        started_at: timestamp,
        completed_at: timestamp,
        steps: projectTraceSteps,
      };
      const scratchMessage = {
        id: id.message,
        conversation_id: id.scratchConversation,
        task_id: id.scratchTask,
        turn_id: id.turn,
        sequence: 1,
        role: "assistant",
        visibility: "user",
        content: "Scratch chat is durable",
        created_at: timestamp,
      };
      const toolMessage = {
        ...scratchMessage,
        id: "0198f4de-0114-7000-8000-000000000020",
        sequence: 2,
        role: "tool",
        visibility: "developer",
        content: "[TOOL_RESULT] fixture provider payload [/TOOL_RESULT]",
      };
      const resumedMessage = {
        ...scratchMessage,
        id: id.resumedMessage,
        sequence: 3,
        content: "Notification completed after approval",
      };
      const waitingTurn = {
        ...completedTurn,
        status: "waiting_for_tool",
        completed_at: null,
      };
      const pendingApproval = {
        id: id.approval,
        task_id: id.scratchTask,
        command_run_id: id.commandRun,
        changeset_id: null,
        tool_invocation_id: id.toolInvocation,
        requested_by: "assistant:system.notify",
        reason: "Allow Fairy to send a notification",
        decision: "pending",
        decided_by: null,
        decided_at: null,
        created_at: timestamp,
      };
      const version = {
        id: id.version,
        project_id: id.project,
        workspace_id: id.project,
        source_conversation_id: id.conversation,
        source_task_id: id.task,
        parent_version_id: null,
        project_root: "C:/Fairy/versions/atlas",
        visibility: "chat_draft",
        created_at: timestamp,
      };
      const runtime = {
        id: id.runtime,
        project_id: id.project,
        workspace_id: id.project,
        conversation_id: id.conversation,
        task_id: id.task,
        version_id: id.version,
        project_root: "C:/Fairy/versions/atlas",
        execution_target: "local",
        kind: "static_site",
        executor: "rust_local_worker",
        executor_handle: "preview-fixture",
        port: 43125,
        graph: {
          public_service_id: "web",
          services: [
            {
              service_id: "web",
              adapter: "static",
              cwd: ".",
              readiness_path: "/",
              depends_on: [],
            },
          ],
        },
        status: "running",
        health: "healthy",
        error_code: null,
        idempotency_key: "e2e-runtime",
        revision: 1,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const preview = {
        id: id.preview,
        project_id: id.project,
        workspace_id: id.project,
        conversation_id: id.conversation,
        task_id: id.task,
        version_id: id.version,
        runtime_id: id.runtime,
        project_root: "C:/Fairy/versions/atlas",
        execution_target: "local",
        url: previewUrl,
        visibility: "chat_draft",
        status: "ready",
        health: "healthy",
        error_code: null,
        idempotency_key: "e2e-preview",
        revision: 1,
        last_accessed_at: timestamp,
        created_at: timestamp,
        updated_at: timestamp,
      };
      const event = {
        id: id.event,
        cursor: 1,
        run_id: null,
        project_id: id.project,
        conversation_id: id.conversation,
        task_id: id.task,
        version_id: id.version,
        task_sequence: 1,
        event_type: "preview.ready",
        visibility: "user",
        message: "Preview is ready",
        payload: {},
        schema_version: 2,
        created_at: timestamp,
      };
      const events = [event];
      const scenarios = new URLSearchParams(window.location.search);
      if (scenarios.has("executionRecovery")) {
        events.push(
          {
            ...event,
            id: "0198f4de-0114-7000-8000-000000000020",
            cursor: 2,
            task_sequence: 2,
            event_type: "command.running",
            message: "Sandbox command running",
          },
          {
            ...event,
            id: "0198f4de-0114-7000-8000-000000000021",
            cursor: 3,
            task_sequence: 3,
            event_type: "runtime.recovered",
            message: "Runtime recovered after worker interruption",
          },
        );
      }
      let approvalScenario = false;
      let approvalVisible = false;
      let approvalDecision: "pending" | "approved" | "rejected" = "pending";
      const lineSidebarSeed = fixtureParams.get("lineSidebarSeed") === "1";
      const lineSidebarMessages = Array.from({ length: 30 }, (_, index) => {
        const sequence = index + 1;
        const pairIndex = Math.floor(index / 2) + 1;
        const role = index % 2 === 0 ? "user" : "assistant";
        return {
          ...scratchMessage,
          id: `0198f4de-0114-7000-8000-${String(300 + sequence).padStart(12, "0")}`,
          turn_id:
            `0198f4de-0114-7000-8000-${String(500 + pairIndex).padStart(12, "0")}`,
          sequence,
          role,
          content:
            role === "user"
              ? `Outline request ${String(sequence).padStart(2, "0")} — stable message anchor`
              : `Outline response ${String(sequence).padStart(2, "0")} — stable message anchor`,
        };
      });
      let messages = lineSidebarSeed
        ? lineSidebarMessages
        : [scratchMessage, toolMessage];
      let latestUserRequest = "";
      let scheduleSequence = 0;
      let schedules: Array<Record<string, unknown>> = [];
      const scheduleId = () =>
        scheduleSequence === 0
          ? id.schedule
          : `0198f4de-0114-7000-8000-${String(46 + scheduleSequence).padStart(12, "0")}`;
      const findSchedule = (scheduleIdValue: unknown) =>
        schedules.find((item) => item.id === scheduleIdValue);
      const projectBackgroundTasks = (currentConversationId: unknown) => {
        const tasks = schedules
          .filter((schedule) => schedule.status !== "cancelled")
          .map((schedule) => {
            const isCurrent = schedule.conversation_id === currentConversationId;
            const status = schedule.status === "active" ? "scheduled" : String(schedule.status);
            return {
              id: `schedule:${String(schedule.id)}`,
              kind: "schedule",
              conversation_id: schedule.conversation_id,
              conversation_title:
                schedule.conversation_id === id.scratchConversation
                  ? scratchConversation.title
                  : conversation.title,
              project_id: schedule.project_id,
              task_id: schedule.task_id,
              turn_id: null,
              workflow_run_id: null,
              schedule_id: schedule.id,
              occurrence_id: null,
              title: schedule.instruction,
              status,
              public_error: null,
              attention_code: schedule.attention_code,
              current_conversation: isCurrent,
              scheduled_for: null,
              next_fire_at: schedule.next_fire_at,
              schedule_revision: schedule.active_revision,
              turn_status: null,
              turn_cancellation_revision: null,
              workflow_budget_tier: null,
              created_at: schedule.created_at,
              updated_at: schedule.updated_at,
              can_pause: schedule.status === "active",
              can_resume: schedule.status === "paused",
              can_cancel: schedule.status === "active" || schedule.status === "paused",
              can_run_now: schedule.status === "active",
            };
          });
        return {
          current: tasks.filter((task) => task.current_conversation),
          other: tasks.filter((task) => !task.current_conversation),
          recent: [],
          nonterminal_count: tasks.length,
        };
      };
      fixtureWindow.__FAIRY_PUSH_EVENT__ = (message) => {
        const startedAt = performance.now();
        const cursor = (events.at(-1)?.cursor ?? 0) + 1;
        events.push({
          ...event,
          id: `0198f4de-0114-7000-8000-${String(100_000_000_000 + cursor)}`,
          cursor,
          run_id: id.commandRun,
          task_sequence: cursor,
          event_type: "turn.trace.step.completed",
          message,
          payload: {
            trace_step_id: `0198f4de-0114-7000-8001-${String(100_000_000_000 + cursor)}`,
            trace_id: projectTraceId,
            turn_id: id.projectTurn,
            sequence: 100 + cursor,
            parent_step_id: null,
            caused_by_step_id: "0198f4de-0114-7000-8000-000000000163",
            kind: "observation",
            status: "succeeded",
            public_summary: message,
            public_detail: null,
            model_role: null,
            artifact_refs: [],
            duration_ms: 1,
          },
          created_at: new Date().toISOString(),
        });
        return startedAt;
      };

      let permissions = {
        profile: "standard",
        capability_overrides: {} as Record<string, boolean>,
        revision: 0,
        updated_at: "2026-07-11T00:00:00Z",
      };
      let openRouterStatus = {
        configured: true,
        account_id: "openrouter-default" as string | null,
      };
      let modelSelection = {
        mode: "auto" as "auto" | "manual",
        model_id: null as string | null,
        allow_free_fallback: false,
        zero_data_retention: false,
        revision: 0,
        updated_at: "2026-07-11T00:00:00Z",
      };
      let memorySettings = {
        enabled: true,
        retention_days: 90,
        export_to_obsidian: false,
        sync_normalized_content: false,
        revision: 0,
        updated_at: "2026-07-11T00:00:00Z",
      };
      const modelCatalogFixture = () => ({
        account: {
          account_id: "openrouter-default",
          provider_kind: "openrouter",
          display_name: "OpenRouter",
          credential_status: "configured",
        },
        items: [
          modelEntry(
            "deepseek/deepseek-v4-pro",
            "DeepSeek V4 Pro",
            "primary",
            "chat",
            true,
          ),
          modelEntry("z-ai/glm-5.2", "GLM 5.2", "strongest", "chat", true),
          modelEntry(
            "moonshotai/kimi-k2.7-code",
            "Kimi K2.7 Code",
            "code",
            "chat",
            true,
          ),
          modelEntry(
            "google/gemini-3.1-flash-lite-image",
            "Gemini 3.1 Flash Lite Image",
            "image",
            "images",
            true,
          ),
          modelEntry(
            "google/lyria-3-pro-preview",
            "Lyria 3 Pro Preview",
            "music",
            "audio",
            true,
          ),
          modelEntry(
            "bytedance/seedance-2.0",
            "Seedance 2.0",
            "video",
            "videos",
            true,
          ),
          modelEntry(
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "Nemotron 3 Ultra",
            "free_general",
            "chat",
            false,
          ),
          modelEntry(
            "qwen/qwen3-coder:free",
            "Qwen3 Coder",
            "free_code",
            "chat",
            false,
          ),
        ],
        fetched_at: "2026-07-11T00:00:00Z",
        expires_at: "2026-07-11T06:00:00Z",
        stale: false,
        revision: 1,
        last_error_code: null,
      });
      const modelEntry = (
        modelId: string,
        displayName: string,
        category: string,
        endpointKind: "chat" | "images" | "audio" | "videos",
        paid: boolean,
      ) => ({
        model_id: modelId,
        display_name: displayName,
        category,
        endpoint_kind: endpointKind,
        description: displayName,
        paid,
        availability: "available",
        unavailable_reason: null,
        input_modalities: ["text"],
        output_modalities: [endpointKind === "chat" ? "text" : endpointKind],
        context_length: endpointKind === "chat" ? 131072 : null,
        max_output_tokens: endpointKind === "chat" ? 16384 : null,
        supports_tools: endpointKind === "chat",
        supports_structured_output: endpointKind === "chat" && paid,
        supports_streaming: endpointKind === "chat",
        supported_resolutions: [],
        supported_aspect_ratios: [],
        prices: [],
      });
      let desktopPreferences = {
        schema_version: 11,
        revision: 0,
        language: "system",
        launch_at_startup: false,
        minimize_to_tray: true,
        theme: "system",
        reduced_motion: false,
        compact_density: false,
        selected_profile_id: null as string | null,
        voice_replies_enabled: true,
        voice_volume_percent: 80,
        voice_rate_percent: 100,
        permission_cloud_profile: "standard",
        memory_enabled: true,
        memory_retention_days: 90,
        analytics_enabled: false,
        realtime_beta_enabled: true,
        realtime_backend: "cloud_live" as "auto" | "local_mini_cpm_o45" | "cloud_live",
        realtime_cloud_provider: "glm_realtime_flash" as const,
        realtime_allow_cloud_fallback: false,
        realtime_activity_profile: "auto" as const,
        realtime_interaction_intensity: "standard" as const,
        realtime_voice_output: "provider_native_voice" as const,
        realtime_game_audio_default: false,
        realtime_capture_mode: "selected_window" as const,
        realtime_excluded_applications: [],
        realtime_online_assistance_enabled: false,
        realtime_memory_enabled: true,
        realtime_presence_max_minutes: 240,
        realtime_cloud_daily_limit_minutes: 180,
        realtime_local_keep_warm_minutes: 10,
        pet_enabled: true,
        pet_always_on_top: true,
        pet_muted: false,
        pet_size_percent: 100,
        pet_opacity_percent: 92,
        pet_motion_enabled: true,
        pet_particles_enabled: true,
        pet_hover_enabled: true,
        pet_hover_dwell_ms: 250,
        pet_do_not_disturb: false,
        pet_remember_position: true,
        pet_renderer_mode: "auto",
        pet_anchor: null,
        ambient_dialogue_enabled: true,
        ambient_dialogue_voice_enabled: false,
        ambient_generated_dialogue_enabled: false,
        developer_mode: false,
      };
      let mcpServer = {
        server_id: "docs",
        display_name: "Document server",
        transport: "streamable_http",
        command: null,
        arguments: [],
        endpoint: "https://mcp.example.test/mcp",
        credential_configured: true,
        environment_names: [],
        enabled: false,
        status: "review_required",
        revision: 2,
        accepted_schema_digest: null,
        pending_schema_digest: "pending-docs-schema",
        accepted_tools: [],
        pending_tools: [
          {
            name: "search",
            title: "Search",
            description: "Search governed documents.",
            input_schema: {
              type: "object",
              properties: { query: { type: "string", maxLength: 200 } },
              required: ["query"],
              additionalProperties: false,
            },
            output_schema: null,
            imported_name: "mcp.docs.search",
            schema_digest: "docs-search-schema",
          },
        ],
        policies: [],
        last_error_code: null,
        created_at: timestamp,
        updated_at: timestamp,
      };
      let permissionConflictPending = scenarios.has("permissionConflict");
      let annotationDocument: Record<string, unknown> | null = null;
      const filePresentation = {
        job: {
          id: id.renderJob,
          workspace_id: id.project,
          version_id: id.version,
          file_set_id: id.fileSet,
          source_path: "src/main.ts",
          source_hash: "fixture-main",
          requested_mode: "native",
          renderer_pack_id: null,
          renderer_pack_version: null,
          cache_key: "fixture-presentation-cache",
          status: "ready",
          progress: 1,
          public_summary: "Native text presentation ready",
          error_code: null,
          created_at: timestamp,
          updated_at: timestamp,
        },
        presentation: {
          id: id.presentation,
          workspace_id: id.project,
          version_id: id.version,
          file_set_id: id.fileSet,
          source_path: "src/main.ts",
          source_hash: "fixture-main",
          renderer: "native.text",
          fidelity: "native",
          status: "ready",
          capabilities: ["select", "annotate"],
          assets: [],
          created_at: timestamp,
        },
      };
      const pdfPresentation = {
        job: {
          ...filePresentation.job,
          id: id.pdfRenderJob,
          file_set_id: id.pdfFileSet,
          source_path: "docs/sample.pdf",
          source_hash: pdfFixtureHash,
          cache_key: "fixture-pdf-presentation-cache",
          public_summary: "Native PDF presentation ready",
        },
        presentation: {
          ...filePresentation.presentation,
          id: id.pdfPresentation,
          file_set_id: id.pdfFileSet,
          source_path: "docs/sample.pdf",
          source_hash: pdfFixtureHash,
          renderer: "browser-native",
          capabilities: ["pages", "search", "zoom", "select", "annotate"],
        },
      };
      const modelPresentation = {
        job: {
          ...filePresentation.job,
          id: "0198f4de-0114-7000-8000-000000000042",
          file_set_id: "0198f4de-0114-7000-8000-000000000043",
          source_path: "models/triangle.gltf",
          source_hash: modelFixtureHash,
          cache_key: "fixture-model-presentation-cache",
          public_summary: "Native 3D presentation ready",
        },
        presentation: {
          ...filePresentation.presentation,
          id: "0198f4de-0114-7000-8000-000000000044",
          file_set_id: "0198f4de-0114-7000-8000-000000000043",
          source_path: "models/triangle.gltf",
          source_hash: modelFixtureHash,
          renderer: "browser-native",
          capabilities: ["orbit", "pan", "zoom", "inspect"],
        },
      };
      const results: Record<string, unknown> = {
        health: {
          status: "ok",
          service: "fairy-core",
          protocol: "core-service-v1",
        },
        "projects.list": { items: [project], next_cursor: null },
        "conversations.list": {
          items: [conversation, scratchConversation],
          next_cursor: null,
        },
        "tasks.list": { items: [task], next_cursor: null },
        "versions.list": { items: [version], next_cursor: null },
        "approvals.list": { items: [], next_cursor: null },
        "media.jobs.list": { items: [] },
        "asset_sets.list": {
          items: [
            {
              id: "0198f4de-0114-7000-8000-000000000031",
              workspace_id: id.project,
              version_id: id.version,
              kind: "image",
              title: "Generated hero",
              variants: [
                {
                  path: "media/generated.png",
                  content_hash: capturePngHash,
                  byte_length: 68,
                  media_type: "image/png",
                  role: "primary",
                  label: "Primary",
                },
              ],
              provenance: { provider: "fixture", model: "image-test" },
              generation_parameters: { width: 1, height: 1 },
              created_at: timestamp,
            },
          ],
        },
        "workspaces.get": {
          id: id.project,
          active_version_id: id.version,
          active_preview_id: id.preview,
          revision: 1,
          max_files: 200,
          max_bytes: 20 * 1024 * 1024,
          created_at: timestamp,
          updated_at: timestamp,
        },
        "workspaces.files.list": {
          workspace_id: id.project,
          version_id: id.version,
          generation: 1,
          source_hash: "fixture-workspace",
          items: [
            {
              path: "src/main.ts",
              byte_length: 22,
              content_hash: "fixture-main",
              kind: "source",
              language: "typescript",
            },
            {
              path: "docs/sample.pdf",
              byte_length: pdfFixtureByteLength,
              content_hash: pdfFixtureHash,
              kind: "binary",
              language: null,
            },
            {
              path: "media/generated.png",
              byte_length: 68,
              content_hash: capturePngHash,
              kind: "binary",
              language: null,
            },
            {
              path: "models/triangle.gltf",
              byte_length: modelFixtureByteLength,
              content_hash: modelFixtureHash,
              kind: "manifest",
              language: "json",
            },
          ],
        },
        "workspaces.files.read": {
          file: {
            path: "src/main.ts",
            byte_length: 22,
            content_hash: "fixture-main",
            kind: "source",
            language: "typescript",
          },
          media_type: "text/plain",
          text: "console.log('Fairy');",
          content_base64: null,
        },
        "files.present": filePresentation,
        "annotations.list": { document: null },
        "previews.resolve": { task, runtime, preview },
        "previews.activate": {
          outcome: "ready",
          context: { task, runtime, preview },
          adapter: "static",
          capacity: 3,
          active_count: 1,
          evicted_preview_id: null,
          public_reason: null,
        },
        "runtimes.health": {
          executor: {
            available: true,
            executor: "rust_local_worker",
            version: "0.1.0",
            error_code: null,
            diagnostics: [],
          },
          runtime,
          preview,
        },
        "capabilities.get": {
          profile: "standard",
          operations: {
            "model.generate": true,
            "workspace.create_scratch": true,
            "web.search": true,
            "run.sandboxed": false,
          },
          sandbox_healthy: true,
          command_metadata: [
            {
              name: "web.search",
              side_effect: "read",
              risk_level: "low",
              approval_policy: "never",
              profiles: ["observe", "standard", "autonomous"],
              requires_sandbox: false,
              idempotent: true,
              model_visible: true,
              description: "Search public web or news sources.",
              input_schema: { type: "object" },
              definition_digest: "web-search-v3",
              required_extensions: [],
              required_operations: [],
              source: "builtin",
            },
            {
              name: "run.sandboxed",
              side_effect: "execute",
              risk_level: "high",
              approval_policy: "never",
              profiles: ["autonomous"],
              requires_sandbox: true,
              idempotent: false,
              model_visible: true,
              description: "Run structured argv in FairySandbox.",
              input_schema: { type: "object" },
              definition_digest: "run-sandboxed-v3",
              required_extensions: [],
              required_operations: [],
              source: "builtin",
            },
          ],
          slash_commands: [
            {
              name: "new",
              description: "Start a durable conversation.",
              argument_hint: null,
              required_operation: "workspace.create_scratch",
              available: true,
            },
            {
              name: "project",
              description: "Switch to the Project workspace.",
              argument_hint: null,
              required_operation: null,
              available: true,
            },
            {
              name: "permission",
              description: "Change the execution profile.",
              argument_hint: "<observe|standard|autonomous>",
              required_operation: null,
              available: true,
            },
            {
              name: "stop",
              description: "Stop the active response.",
              argument_hint: null,
              required_operation: "model.generate",
              available: true,
            },
            {
              name: "clear",
              description: "Start a durable conversation.",
              argument_hint: null,
              required_operation: "workspace.create_scratch",
              available: true,
            },
            {
              name: "help",
              description: "Show available commands.",
              argument_hint: null,
              required_operation: null,
              available: true,
            },
          ],
          schema_version: 3,
        },
        "skills.list": {
          items: [
            {
              name: "Fairy Docs",
              version: "1.0.0",
              description: "Work with governed project documents.",
              tool_name: "skill.fairy-docs",
              required_capabilities: ["document.search"],
              compatible_mcp_servers: ["docs"],
              provenance: {
                source: "fairy://skills/docs",
                publisher: "Fairy Labs",
                license: "Apache-2.0",
              },
              content_sha256:
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              enabled: true,
              available: false,
            },
          ],
        },
        "extensions.catalog.list": {
          items: [
            {
              extension_id: "design-taste-frontend",
              kind: "skill",
              name: "Taste Skill",
              description: "Frontend design guidance.",
              publisher: "Leonxlnx",
              version: "2.0.0-experimental.1",
              source: "https://github.com/Leonxlnx/taste-skill",
              license: "MIT",
              experimental: true,
              installed: false,
            },
          ],
        },
        "knowledge.sources.list": { items: [] },
        "memory.proposals.list": { items: [] },
        "obsidian.health.get": {
          desktop_installed: true,
          cli_available: true,
          minimum_installer_version: "1.12.7",
          status: "ready",
          public_summary: "Obsidian Desktop and CLI are ready",
        },
        "providers.list": {
          items: [
            {
              id: "openrouter-deepseek-v4-pro",
              display_name: "DeepSeek V4 Pro",
              kind: "openai_compatible",
              base_url: "https://openrouter.ai/api/v1",
              model_id: "deepseek/deepseek-v4-pro",
              capabilities: ["text", "tools", "structured_output", "stt"],
              credential_required: true,
              credential_configured: true,
              enabled: true,
              timeout_seconds: 60,
              fallback_profile_id: null,
            },
            {
              id: "openrouter-glm-5-2",
              display_name: "GLM 5.2",
              kind: "openai_compatible",
              base_url: "https://openrouter.ai/api/v1",
              model_id: "z-ai/glm-5.2",
              capabilities: ["text", "tools", "structured_output"],
              credential_required: true,
              credential_configured: true,
              enabled: true,
              timeout_seconds: 180,
              fallback_profile_id: "openrouter-deepseek-v4-pro",
            },
            {
              id: "openrouter-kimi-k2-7-code",
              display_name: "Kimi K2.7 Code",
              kind: "openai_compatible",
              base_url: "https://openrouter.ai/api/v1",
              model_id: "moonshotai/kimi-k2.7-code",
              capabilities: ["text", "tools", "vision", "structured_output"],
              credential_required: true,
              credential_configured: true,
              enabled: true,
              timeout_seconds: 180,
              fallback_profile_id: null,
            },
            {
              id: "openrouter-nemotron-free",
              display_name: "Nemotron 3 Ultra",
              kind: "openai_compatible",
              base_url: "https://openrouter.ai/api/v1",
              model_id: "nvidia/nemotron-3-ultra-550b-a55b:free",
              capabilities: ["text", "tools"],
              credential_required: true,
              credential_configured: true,
              enabled: true,
              timeout_seconds: 180,
              fallback_profile_id: null,
            },
            {
              id: "openrouter-qwen3-coder-free",
              display_name: "Qwen3 Coder",
              kind: "openai_compatible",
              base_url: "https://openrouter.ai/api/v1",
              model_id: "qwen/qwen3-coder:free",
              capabilities: ["text", "tools"],
              credential_required: true,
              credential_configured: true,
              enabled: true,
              timeout_seconds: 180,
              fallback_profile_id: null,
            },
          ],
        },
        "providers.health": {
          items: [
            {
              profile_id: "openrouter-deepseek-v4-pro",
              status: "available",
              error_code: null,
              diagnostics: [],
            },
            ...[
              "openrouter-glm-5-2",
              "openrouter-kimi-k2-7-code",
              "openrouter-nemotron-free",
              "openrouter-qwen3-coder-free",
            ].map((profileId) => ({
              profile_id: profileId,
              status: "available",
              error_code: null,
              diagnostics: [],
            })),
          ],
        },
        "models.catalog.list": modelCatalogFixture(),
        "models.catalog.refresh": modelCatalogFixture(),
        "messages.list": {
          items: [scratchMessage, toolMessage],
          next_cursor: null,
        },
        "tasks.create": { task: scratchTask },
        "documents.import": {},
        "documents.list": {
          items: [
            {
              document: {
                id: "0198f4de-0114-7000-8000-000000000101",
                filename: "fixture-notes.md",
                media_type: "text/markdown",
                byte_length: 128,
              },
              revision: {},
            },
          ],
        },
        "documents.search": { items: [] },
        "documents.delete": {},
        "assistant.turns.create": { ...completedTurn, status: "created" },
        "assistant.turns.get": completedTurn,
        "assistant.turns.start": completedTurn,
        "assistant.turns.cancel": { ...completedTurn, status: "cancelled" },
        "assistant.turns.retry": { ...completedTurn, status: "created" },
        "assistant.turns.trace.list": {
          id: "0198f4de-0114-7000-8000-000000000150",
          turn_id: id.turn,
          conversation_id: id.scratchConversation,
          task_id: id.scratchTask,
          legacy: false,
          last_sequence: 4,
          revision: 4,
          created_at: timestamp,
          updated_at: timestamp,
          started_at: timestamp,
          completed_at: timestamp,
          evidence_sources: [
            {
              id: "0198f4de-0114-7000-8000-000000000156",
              source_kind: "web_document",
              public_label: "Fairy evidence guide",
              tool_name: "web.fetch",
              workspace_id: null,
              version_id: null,
              relative_path: null,
              line_start: null,
              line_end: null,
              safe_url: "https://example.com/fairy-evidence",
              observed_at: timestamp,
              expires_at: "2026-07-11T00:05:00Z",
              truncated: false,
            },
            {
              id: "0198f4de-0114-7000-8000-000000000157",
              source_kind: "project_file",
              public_label: "Workspace source",
              tool_name: "project.read",
              workspace_id: id.scratchConversation,
              version_id: "1298f4de-0114-7000-8000-000000000004",
              relative_path: "src/main.ts",
              line_start: 1,
              line_end: 1,
              safe_url: null,
              observed_at: timestamp,
              expires_at: null,
              truncated: false,
            },
          ],
          steps: [
            {
              id: "0198f4de-0114-7000-8000-000000000151",
              trace_id: "0198f4de-0114-7000-8000-000000000150",
              turn_id: id.turn,
              sequence: 1,
              parent_step_id: null,
              caused_by_step_id: null,
              kind: "route",
              status: "succeeded",
              public_summary: "Request routed for implementation",
              public_detail:
                "Kimi implements the request and Fairy verifies the result.",
              model_id: null,
              model_role: null,
              provider_attempt_id: null,
              command_run_id: null,
              artifact_refs: [],
              visibility: "user",
              revision: 1,
              created_at: timestamp,
              updated_at: timestamp,
              started_at: timestamp,
              completed_at: timestamp,
              duration_ms: 20,
            },
            {
              id: "0198f4de-0114-7000-8000-000000000152",
              trace_id: "0198f4de-0114-7000-8000-000000000150",
              turn_id: id.turn,
              sequence: 2,
              parent_step_id: "0198f4de-0114-7000-8000-000000000151",
              caused_by_step_id: "0198f4de-0114-7000-8000-000000000151",
              kind: "model",
              status: "succeeded",
              public_summary: "Implementation generated",
              public_detail: null,
              model_id: "moonshotai/kimi-k2.7-code",
              model_role: "primary",
              provider_attempt_id: "0198f4de-0114-7000-8000-000000000153",
              command_run_id: null,
              artifact_refs: [],
              visibility: "user",
              revision: 1,
              created_at: timestamp,
              updated_at: timestamp,
              started_at: timestamp,
              completed_at: timestamp,
              duration_ms: 420,
            },
            {
              id: "0198f4de-0114-7000-8000-000000000154",
              trace_id: "0198f4de-0114-7000-8000-000000000150",
              turn_id: id.turn,
              sequence: 3,
              parent_step_id: "0198f4de-0114-7000-8000-000000000152",
              caused_by_step_id: "0198f4de-0114-7000-8000-000000000152",
              kind: "tool",
              status: "succeeded",
              public_summary: "Workspace files verified",
              public_detail: null,
              model_id: null,
              model_role: null,
              provider_attempt_id: null,
              command_run_id: id.commandRun,
              artifact_refs: [],
              visibility: "user",
              revision: 1,
              created_at: timestamp,
              updated_at: timestamp,
              started_at: timestamp,
              completed_at: timestamp,
              duration_ms: 80,
            },
            {
              id: "0198f4de-0114-7000-8000-000000000155",
              trace_id: "0198f4de-0114-7000-8000-000000000150",
              turn_id: id.turn,
              sequence: 4,
              parent_step_id: null,
              caused_by_step_id: "0198f4de-0114-7000-8000-000000000154",
              kind: "response",
              status: "succeeded",
              public_summary: "Response ready",
              public_detail: null,
              model_id: null,
              model_role: null,
              provider_attempt_id: null,
              command_run_id: null,
              artifact_refs: [],
              visibility: "user",
              revision: 1,
              created_at: timestamp,
              updated_at: timestamp,
              started_at: timestamp,
              completed_at: timestamp,
              duration_ms: 30,
            },
          ],
        },
        "system.actions.execute": {
          run_id: id.commandRun,
          tool_name: "system.open_url",
          status: "succeeded",
          requires_approval: false,
          completed: true,
          replayed: false,
        },
        "conversations.create": scratchConversation,
        "voice.transcribe": {
          conversation_id: id.scratchConversation,
          profile_id: "openrouter-deepseek-v4-pro",
          text: "Fixture voice transcript",
          language: "en",
          segments: [],
        },
      };

      const activeProjects = () =>
        project.archived_at === null &&
        project.deleted_at === null &&
        project.purged_at === null
          ? [{ ...project }]
          : [];
      const activeConversations = () =>
        [conversation, scratchConversation].filter(
          (item) => item.deleted_at === null && item.purged_at === null,
        );
      const archivedProjects = () =>
        project.archived_at !== null &&
        project.deleted_at === null &&
        project.purged_at === null
          ? [
              {
                project: { ...project },
                thread_count: 1,
                archived_at: project.archived_at,
              },
            ]
          : [];
      const trashItems = () => {
        const items: Array<Record<string, unknown>> = [];
        if (project.deleted_at !== null && project.purged_at === null) {
          items.push({
            item_type: "project",
            item_id: project.id,
            title: project.name,
            source_project_id: null,
            source_project_title: null,
            thread_count: 1,
            deleted_at: project.deleted_at,
            estimated_bytes: 4096,
            can_restore: true,
            metadata_revision: project.metadata_revision,
          });
        }
        for (const item of [conversation, scratchConversation]) {
          if (
            item.deleted_at === null ||
            item.purged_at !== null ||
            item.deleted_by_project_at !== null
          ) {
            continue;
          }
          items.push({
            item_type:
              item.project_id === null
                ? "conversation"
                : "project_conversation",
            item_id: item.id,
            title: item.title,
            source_project_id: item.project_id,
            source_project_title:
              item.project_id === null ? null : project.name,
            thread_count: 0,
            deleted_at: item.deleted_at,
            estimated_bytes: item.project_id === null ? 2048 : 0,
            can_restore:
              item.project_id === null || project.deleted_at === null,
            metadata_revision: item.revision,
          });
        }
        return items;
      };
      const requireRevision = (actual: number, expected: unknown) => {
        if (actual !== Number(expected)) throw new Error("VERSION_CONFLICT");
      };
      const selectedConversation = (conversationId: unknown) => {
        if (conversationId === conversation.id) return conversation;
        if (conversationId === scratchConversation.id)
          return scratchConversation;
        throw new Error("Conversation is unavailable");
      };
      const storeConversation = (
        value: typeof conversation | typeof scratchConversation,
      ) => {
        if (value.id === conversation.id)
          conversation = value as typeof conversation;
        else scratchConversation = value as typeof scratchConversation;
        return { ...value };
      };
      const browserSessionId = "0198f4de-0114-7000-8000-000000000030";
      const browserTabId = "0198f4de-0114-7000-8000-000000000031";
      let browserActive = false;
      let browserUrl = "about:blank";
      let browserRevision = 1;
      let mainViewRequest = {
        schema_version: 1,
        sequence: 0,
        view: "workspace",
        settings_category: null as string | null,
        conversation_id: null as string | null,
      };
      const localReadiness = () => {
        const gib = 1_073_741_824;
        const variant =
          new URLSearchParams(window.location.search).get("localReadiness") ??
          "runtime-missing";
        const report = {
          schema_version: 1,
          profile: "auto",
          hardware_cached: false,
          hardware: {
            schema_version: 1,
            windows_supported: true,
            architecture_x64: true,
            avx2_available: true,
            system_total_bytes: 32 * gib,
            disk_available_bytes: 40 * gib,
            adapter: {
              name: "NVIDIA GeForce RTX 4090",
              vendor: "nvidia",
              vendor_id: 0x10de,
              dedicated_vram_bytes: 24 * gib,
              budget_bytes: 22 * gib,
              current_usage_bytes: 2 * gib,
              luid: "00000000:00000001",
            },
            cuda: {
              available: true,
              driver_api_version: 12_080,
              driver_compatible: true,
              device_count: 1,
              matched_device_ordinal: 0,
              adapter_luid_matches: true,
              error_code: null as string | null,
            },
            error_code: null as string | null,
          },
          model: {
            schema_version: 1,
            sequence: 4,
            phase: "runtime_missing",
            model_version: "4.5-q4-502eec5",
            manifest_digest: "a".repeat(64),
            current_file: null as string | null,
            received_bytes: 6_781_995_488,
            total_bytes: 6_781_995_488,
            error_code: "OMNI_RUNTIME_MISSING" as string | null,
          },
          model_shallow_present: true,
          model_install_required_bytes: 5 * gib,
          runtime: "missing",
          runtime_error_code: "OMNI_RUNTIME_MISSING" as string | null,
          capability: {
            schema_version: 1,
            static_eligible: true,
            local_beta_eligible: false,
            reason: "runtime_missing",
            available_budget_bytes: 20 * gib,
            required_budget_bytes: 15 * gib,
            warnings: [] as string[],
          },
        };
        if (variant === "unsupported") {
          report.hardware.adapter.dedicated_vram_bytes = 12 * gib;
          report.capability.static_eligible = false;
          report.capability.reason = "vram_below12gb";
        } else if (variant === "installable") {
          report.model.phase = "not_installed";
          report.model.received_bytes = 0;
          report.model.error_code = null;
          report.model_shallow_present = false;
          report.model_install_required_bytes = report.model.total_bytes + 7 * gib;
          report.capability.reason = "model_missing";
        } else if (variant === "downloading") {
          report.model.phase = "downloading";
          report.model.current_file = "MiniCPM-o-4_5-Q4_K_M.gguf";
          report.model.received_bytes = Math.round(report.model.total_bytes * 0.42);
          report.model.error_code = null;
          report.model_shallow_present = false;
          report.capability.reason = "model_missing";
        } else if (variant === "temporary") {
          report.model.phase = "ready";
          report.model.error_code = null;
          report.runtime = "passed";
          report.runtime_error_code = null;
          report.capability.reason = "insufficient_free_vram";
          report.capability.available_budget_bytes = 10 * gib;
        } else if (variant === "ready") {
          report.model.phase = "ready";
          report.model.error_code = null;
          report.runtime = "passed";
          report.runtime_error_code = null;
          report.capability.reason = "eligible";
          report.capability.local_beta_eligible = true;
        }
        return report;
      };
      const browserSession = (status: "active" | "stopped" = "active") => ({
        id: browserSessionId,
        project_id: id.project,
        conversation_id: id.conversation,
        task_id: id.task,
        execution_target: "local",
        profile_kind: "persistent",
        status,
        active_tab_id: status === "active" ? browserTabId : null,
        tabs: status === "active" ? [{
          id: browserTabId,
          session_id: browserSessionId,
          title: "Atlas preview",
          url: browserUrl,
          active: true,
          loading: false,
          revision: browserRevision,
        }] : [],
        revision: browserRevision,
        created_at: timestamp,
        updated_at: timestamp,
        error_code: null,
        public_error: null,
      });
      const companionSession = {
        id: companionSessionId,
        conversation_id: id.scratchConversation,
        device_id: "0198f4de-0114-7000-8000-000000000031",
        provider: "glm_realtime_flash",
        model_id: "glm-realtime-flash",
        voice_mode: "fairy",
        memory_mode: "none",
        microphone_consent: true,
        screen_consent: true,
        game_audio_consent: false,
        status: "active",
        audio_input_ms: 1_250,
        audio_output_ms: 400,
        video_frame_count: 8,
        interruption_count: 1,
        tool_call_count: 0,
        last_error_code: null,
        started_at: "2026-07-20T00:00:00Z",
        ended_at: null,
        revision: 2,
      };
      const companionWorkerStatus = () => {
        const durationLimit = companionStandbyReason === "duration_limit";
        return {
          running: companionActive !== null,
          session_id: companionActive === null ? null : companionSessionId,
          segment_id: companionActive === null ? null : "segment-fixture-1",
          context_epoch: companionActive === null ? null : 1,
          backend: companionActive === null ? null : "cloud_live",
          cloud_provider:
            companionActive === null ? null : "glm_realtime_flash",
          action_required: false,
          assistance: companionAssistanceStatus === null
            ? []
            : [{
                session_id: companionSessionId,
                segment_id: "segment-fixture-1",
                context_epoch: 1,
                request_id: "realtime-assistance-fixture-1",
                public_intent: "Find the current raid route",
                status: companionAssistanceStatus,
                error_code: null,
                public_summary: companionAssistanceStatus === "completed"
                  ? "The route is ready in the main chat."
                  : null,
              }],
          presence_projection: companionActive === null
            ? null
            : {
                session_id: companionSessionId,
                segment_id: "segment-fixture-1",
                context_epoch: 1,
                sequence: companionProjectionSequence,
                state: companionPresenceState,
                level: null,
                persona_digest: "a".repeat(64),
                requested_activity_profile: companionRequestedProfile,
                effective_activity: companionEffectiveActivity,
                interaction_intensity: companionInteractionIntensity,
                backend: "cloud_live",
                cloud_provider: "glm_realtime_flash",
                standby_reason: companionStandbyReason,
                wake_available:
                  companionPresenceState === "standby" && !durationLimit,
                duration_extension_required:
                  companionPresenceState === "standby" && durationLimit,
              },
          capture_scope: companionActive === null
            ? null
            : {
                mode: "selected_window",
                source_sequence: 1,
                source_available: companionCaptureAvailable,
                privacy_paused: companionPresenceState === "privacy_paused",
                sensitive_category: null,
                error_code: companionCaptureAvailable
                  ? null
                  : "CAPTURE_SOURCE_UNAVAILABLE",
              },
          media_channels: companionActive === null
            ? []
            : [
                {
                  channel: "microphone",
                  sequence: 1,
                  status: companionPresenceState === "privacy_paused"
                    ? "paused"
                    : companionMicrophoneStatus,
                  error_code: companionMicrophoneStatus === "unavailable"
                    ? "MICROPHONE_DEVICE_LOST"
                    : null,
                },
                {
                  channel: "selected_window",
                  sequence: 1,
                  status: companionPresenceState === "privacy_paused"
                    ? "paused"
                    : "active",
                  error_code: null,
                },
              ],
          audio_input_ms: companionSession.audio_input_ms,
          audio_output_ms: companionSession.audio_output_ms,
          video_frame_count: companionSession.video_frame_count,
          interruption_count: companionSession.interruption_count,
          tool_call_count: companionSession.tool_call_count,
        };
      };

      const tauriWindow = window as unknown as {
        __TAURI_INTERNALS__: {
          invoke(
            command: string,
            args: Record<string, unknown>,
          ): Promise<unknown>;
          transformCallback(callback: (payload: unknown) => void): number;
          unregisterCallback(callbackId: number): void;
        };
        __TAURI_EVENT_PLUGIN_INTERNALS__: {
          unregisterListener(event: string, eventId: number): void;
        };
      };
      let callbackId = 0;
      const callbacks = new Map<number, (payload: unknown) => void>();
      const eventListeners = new Map<string, number[]>();
      tauriWindow.__TAURI_EVENT_PLUGIN_INTERNALS__ = {
        unregisterListener(_event, eventId) {
          callbacks.delete(eventId);
        },
      };
      tauriWindow.__TAURI_INTERNALS__ = {
        transformCallback(callback) {
          callbackId += 1;
          callbacks.set(callbackId, callback);
          return callbackId;
        },
        unregisterCallback(id) {
          callbacks.delete(id);
        },
        async invoke(command, args) {
          if (command === "plugin:event|listen") {
            const event = String(args.event);
            const handler = Number(args.handler);
            eventListeners.set(event, [
              ...(eventListeners.get(event) ?? []),
              handler,
            ]);
            return handler;
          }
          if (command === "plugin:event|unlisten") {
            const event = String(args.event);
            const eventId = Number(args.eventId);
            eventListeners.set(
              event,
              (eventListeners.get(event) ?? []).filter((id) => id !== eventId),
            );
            callbacks.delete(eventId);
            return null;
          }
          if (command === "plugin:event|emit") return null;
          if (command === "list_capture_surfaces") {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "capture.list",
              params: {},
            });
            return [
              {
                kind: "display",
                source_id: "1",
                label: "Primary display",
                width: 1920,
                height: 1080,
                is_primary: true,
              },
              {
                kind: "window",
                source_id: "2",
                label: "Game window",
                width: 1,
                height: 1,
                is_primary: false,
              },
            ];
          }
          if (command === "capture_surface") {
            const captureRequest = args.request as Record<string, unknown>;
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "capture.surface",
              params: captureRequest,
            });
            return {
              kind: captureRequest.kind,
              source_id: captureRequest.source_id,
              source_label:
                captureRequest.kind === "window"
                  ? "Game window"
                  : "Primary display",
              media_type: "image/png",
              png_base64: capturePngBase64,
              width: captureRequest.kind === "window" ? 1 : 1920,
              height: captureRequest.kind === "window" ? 1 : 1080,
              byte_length: 68,
              content_hash: capturePngHash,
              captured_at_ms: 1_784_000_000_000,
            };
          }
          if (command === "provider_openrouter_status") {
            return openRouterStatus;
          }
          if (command === "provider_openrouter_configure") {
            openRouterStatus = {
              configured: true,
              account_id: "openrouter-default",
            };
            return openRouterStatus;
          }
          if (command === "provider_openrouter_delete") {
            openRouterStatus = { configured: false, account_id: null };
            return openRouterStatus;
          }
          if (command === "desktop_preferences_get") {
            const companionBackend =
              new URLSearchParams(window.location.search).get("companionBackend");
            if (companionBackend === "local") {
              return {
                ...desktopPreferences,
                realtime_backend: "local_mini_cpm_o45",
              };
            }
            return desktopPreferences;
          }
          if (command === "desktop_preferences_update") {
            const input = args.input as {
              expected_revision: number;
              preferences: typeof desktopPreferences;
            };
            if (input.expected_revision !== desktopPreferences.revision) {
              throw new Error("PREFERENCES_REVISION_CONFLICT");
            }
            desktopPreferences = {
              ...input.preferences,
              revision: desktopPreferences.revision + 1,
            };
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "desktop.preferences.update",
              params: input as unknown as Record<string, unknown>,
            });
            return desktopPreferences;
          }
          if (command === "realtime_local_readiness_get") {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "realtime.local.readiness.get",
              params: args.input as Record<string, unknown>,
            });
            return localReadiness();
          }
          if (command === "realtime_backend_resolution_preview") {
            const input = args.input as {
              cloud_microphone_upload_consent: boolean;
              cloud_screen_upload_consent: boolean;
            };
            const local =
              new URLSearchParams(window.location.search).get("companionBackend")
              === "local";
            const consented =
              input.cloud_microphone_upload_consent
              && input.cloud_screen_upload_consent;
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "realtime.backend.preview",
              params: input as unknown as Record<string, unknown>,
            });
            return {
              schema_version: 1,
              resolution_token: "a".repeat(64),
              available: local || consented,
              backend: local
                ? "local_mini_cpm_o45"
                : consented
                  ? "cloud_live"
                  : null,
              cloud_provider: !local && consented
                ? "glm_realtime_flash"
                : null,
              reason: local || consented
                ? null
                : "CLOUD_UPLOAD_CONSENT_REQUIRED",
              requires_cloud_upload_consent: !local,
              preference_revision: 1,
            };
          }
          if (command === "realtime_worker_status") {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "realtime.worker.status",
              params: {},
            });
            return companionWorkerStatus();
          }
          if (command === "realtime_worker_set_policy") {
            const input = args.input as {
              activity_profile: "auto" | "game" | "focus";
              interaction_intensity: "quiet" | "standard" | "active";
            };
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: command,
              params: input as unknown as Record<string, unknown>,
            });
            companionRequestedProfile = input.activity_profile;
            companionEffectiveActivity =
              input.activity_profile === "focus" ? "focus" : "game";
            companionInteractionIntensity = input.interaction_intensity;
            companionProjectionSequence += 1;
            return companionWorkerStatus();
          }
          if (
            command === "realtime_worker_retry_media"
            || command === "realtime_worker_replace_source"
          ) {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: command,
              params: args.input as Record<string, unknown>,
            });
            if (command === "realtime_worker_retry_media") {
              companionMicrophoneStatus = "active";
            } else {
              companionCaptureAvailable = true;
            }
            return companionWorkerStatus();
          }
          if (command === "realtime_worker_wake") {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: command,
              params: args.input as Record<string, unknown>,
            });
            companionPresenceState = "listening";
            companionStandbyReason = null;
            companionProjectionSequence += 1;
            return companionWorkerStatus();
          }
          if (command === "realtime_worker_extend") {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: command,
              params: args.input as Record<string, unknown>,
            });
            companionPresenceState = "listening";
            companionStandbyReason = null;
            companionProjectionSequence += 1;
            return companionWorkerStatus();
          }
          if (
            command === "realtime_worker_pause_privacy"
            || command === "realtime_worker_resume_privacy"
          ) {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: command,
              params: args.input as Record<string, unknown>,
            });
            companionPresenceState =
              command === "realtime_worker_pause_privacy"
                ? "privacy_paused"
                : "listening";
            companionStandbyReason = null;
            companionProjectionSequence += 1;
            return companionWorkerStatus();
          }
          if (command === "hide_companion_window") return null;
          if (command === "open_realtime_main_chat") {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: command,
              params: args.input as Record<string, unknown>,
            });
            mainViewRequest = {
              schema_version: 1,
              sequence: mainViewRequest.sequence + 1,
              view: "workspace",
              settings_category: null,
              conversation_id: id.scratchConversation,
            };
            return null;
          }
          if (command === "omni_model_status") {
            return localReadiness().model;
          }
          if (
            command === "omni_model_install_start" ||
            command === "omni_model_install_cancel" ||
            command === "omni_model_verify" ||
            command === "omni_model_remove"
          ) {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: command,
              params: (args.input ?? {}) as Record<string, unknown>,
            });
            return localReadiness().model;
          }
          if (command === "voice_session_start") {
            const input = args.input as Record<string, unknown>;
            const events = args.events as {
              onmessage(payload: Record<string, unknown>): void;
            };
            const audio = args.audio as {
              onmessage(payload: ArrayBuffer): void;
            };
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "voice.sessions.start",
              params: input,
            });
            const sessionId = "0198f4de-0114-7000-8000-000000000099";
            events.onmessage({
              type: "started",
              session_id: sessionId,
              sample_rate: 24_000,
              channels: 1,
              scope_digest: "a".repeat(64),
            });
            audio.onmessage(new Int16Array([0, 0]).buffer);
            events.onmessage({
              type: "completed",
              session_id: sessionId,
              pcm_bytes: 4,
            });
            return { id: sessionId };
          }
          if (command === "voice_session_cancel") return null;
          if (command === "main_view_request_get") {
            return { ...mainViewRequest };
          }
          if (command === "main_view_navigate") {
            const input = args.input as {
              view: "workspace" | "settings";
              settings_category: string | null;
              conversation_id: string | null;
            };
            mainViewRequest = {
              schema_version: 1,
              sequence: mainViewRequest.sequence + 1,
              view: input.view,
              settings_category:
                input.view === "settings" ? input.settings_category : null,
              conversation_id:
                input.view === "workspace" ? input.conversation_id : null,
            };
            return { ...mainViewRequest };
          }
          if (command === "open_settings_window") {
            fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
              method: "desktop.settings.open",
              params: { category: args.category ?? null },
            });
            return null;
          }
          if (command === "select_project_folder") {
            return "C:\\Projects\\fixture";
          }
          if (
            command !== "core_rpc"
            && command !== "settings_rpc"
            && command !== "companion_rpc"
          ) {
            throw new Error(`Unexpected Tauri command: ${command}`);
          }
          const request = args.request as {
            id: number;
            method: string;
            params: Record<string, unknown>;
          };
          fixtureWindow.__FAIRY_FIXTURE_CALLS__.push({
            method: request.method,
            params: request.params,
          });
          if (request.method === "realtime.sessions.list") {
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: {
                items: companionActive === null ? [] : [companionSession],
              },
            };
          }
          if (request.method === "realtime.assistance.get") {
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: {
                session_id: companionSessionId,
                request_id: "realtime-assistance-fixture-1",
                status: companionAssistanceStatus,
                revision: 3,
              },
            };
          }
          if (request.method === "realtime.assistance.cancel") {
            companionAssistanceStatus = "cancelled";
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: {
                session_id: companionSessionId,
                request_id: "realtime-assistance-fixture-1",
                status: "cancelled",
                revision: 4,
                error_code: null,
                spoken_summary: null,
              },
            };
          }
          if (
            request.method === "permissions.update" &&
            permissionConflictPending
          ) {
            permissionConflictPending = false;
            permissions = {
              profile: "observe",
              capability_overrides: { "web.search": false },
              revision: permissions.revision + 1,
              updated_at: "2026-07-11T00:00:01Z",
            };
            return {
              jsonrpc: "2.0",
              id: request.id,
              error: {
                code: -32000,
                message: "stale permission revision",
                data: { error_code: "VERSION_CONFLICT" },
              },
            };
          }
          if (request.method === "tasks.create") {
            await new Promise((resolve) => window.setTimeout(resolve, 180));
          }
          if (request.method === "assistant.schedules.create") {
            const now = new Date().toISOString();
            const created = {
              id: scheduleId(),
              conversation_id: String(request.params.conversation_id),
              task_id: null,
              project_id:
                request.params.conversation_id === id.scratchConversation ? null : id.project,
              workspace_id:
                request.params.conversation_id === id.scratchConversation
                  ? id.scratchConversation
                  : id.project,
              version_id:
                request.params.conversation_id === id.scratchConversation
                  ? id.scratchVersion
                  : id.version,
              instruction: String(request.params.instruction),
              operation_mode: request.params.operation_mode,
              trigger_kind: request.params.trigger_kind,
              trigger_rule: request.params.trigger_rule,
              timezone: request.params.timezone,
              next_fire_at: request.params.next_fire_at,
              execution_target: "local",
              profile_id: request.params.profile_id ?? null,
              model_selection: request.params.model_selection ?? null,
              permission_profile: permissions.profile,
              timeline_sequence: 10 + scheduleSequence,
              status: "active",
              active_revision: 1,
              consecutive_failures: 0,
              attention_code: null,
              created_at: now,
              updated_at: now,
              last_fire_at: null,
              paused_at: null,
              completed_at: null,
              cancelled_at: null,
            };
            scheduleSequence += 1;
            schedules = [...schedules, created];
            return { jsonrpc: "2.0", id: request.id, result: created };
          }
          if (request.method === "assistant.schedules.list") {
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: {
                items: schedules.filter(
                  (schedule) =>
                    request.params.conversation_id === null ||
                    request.params.conversation_id === undefined ||
                    schedule.conversation_id === request.params.conversation_id,
                ),
              },
            };
          }
          if (request.method === "assistant.schedules.get") {
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: findSchedule(request.params.schedule_id),
            };
          }
          if (request.method === "assistant.background_tasks.list") {
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: projectBackgroundTasks(request.params.current_conversation_id),
            };
          }
          if (
            request.method === "assistant.schedules.pause" ||
            request.method === "assistant.schedules.resume" ||
            request.method === "assistant.schedules.cancel" ||
            request.method === "assistant.schedules.update"
          ) {
            const index = schedules.findIndex(
              (schedule) => schedule.id === request.params.schedule_id,
            );
            const current = schedules[index];
            if (current === undefined) throw new Error("Unknown fixture schedule");
            const now = new Date().toISOString();
            const status =
              request.method === "assistant.schedules.pause"
                ? "paused"
                : request.method === "assistant.schedules.resume"
                  ? "active"
                  : request.method === "assistant.schedules.cancel"
                    ? "cancelled"
                    : current.status;
            const updated = {
              ...current,
              ...(request.method === "assistant.schedules.update"
                ? {
                    instruction: request.params.instruction,
                    operation_mode: request.params.operation_mode,
                    trigger_kind: request.params.trigger_kind,
                    trigger_rule: request.params.trigger_rule,
                    timezone: request.params.timezone,
                    next_fire_at: request.params.next_fire_at,
                  }
                : {}),
              status,
              active_revision: Number(current.active_revision) + 1,
              updated_at: now,
              paused_at: status === "paused" ? now : null,
              cancelled_at: status === "cancelled" ? now : null,
            };
            schedules = schedules.map((schedule, itemIndex) =>
              itemIndex === index ? updated : schedule,
            );
            return { jsonrpc: "2.0", id: request.id, result: updated };
          }
          if (request.method === "assistant.schedules.run_now") {
            const schedule = findSchedule(request.params.schedule_id);
            if (schedule === undefined) throw new Error("Unknown fixture schedule");
            const now = new Date().toISOString();
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: {
                id: id.occurrence,
                schedule_id: schedule.id,
                schedule_revision: schedule.active_revision,
                scheduled_for: now,
                status: "pending",
                coalesced_count: 0,
                turn_id: null,
                workflow_run_id: null,
                public_error: null,
                created_at: now,
                dispatched_at: null,
                completed_at: null,
              },
            };
          }
          if (request.method === "browser.health") {
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: {
                available: true,
                browser_name: "Microsoft Edge",
                browser_version: "fixture",
                error_code: null,
                diagnostic: null,
              },
            };
          }
          if (request.method === "browser.sessions.list") {
            const matchesScope = request.params.conversation_id === id.conversation
              && request.params.task_id === id.task;
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: { items: browserActive && matchesScope ? [browserSession()] : [] },
            };
          }
          if (request.method === "browser.sessions.start") {
            browserActive = true;
            browserUrl = String(request.params.initial_url ?? "about:blank");
            browserRevision += 1;
            return { jsonrpc: "2.0", id: request.id, result: browserSession() };
          }
          if (request.method === "browser.sessions.stop") {
            browserActive = false;
            browserRevision += 1;
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: browserSession("stopped"),
            };
          }
          if (request.method === "browser.snapshots.get") {
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: {
                session_id: browserSessionId,
                tab_id: browserTabId,
                page_revision: browserRevision,
                url: browserUrl,
                title: "Atlas preview",
                aria_snapshot: "- document:\n  - heading: Atlas preview",
                viewport_width: 1365,
                viewport_height: 768,
                screenshot_data_url: `data:image/png;base64,${capturePngBase64}`,
                captured_at: timestamp,
              },
            };
          }
          if (request.method === "models.selection.get") {
            return { jsonrpc: "2.0", id: request.id, result: modelSelection };
          }
          if (request.method === "models.selection.update") {
            modelSelection = {
              mode: request.params.mode as "auto" | "manual",
              model_id:
                (request.params.model_id as string | null | undefined) ?? null,
              allow_free_fallback: Boolean(request.params.allow_free_fallback),
              zero_data_retention: Boolean(request.params.zero_data_retention),
              revision: Number(request.params.expected_revision) + 1,
              updated_at: "2026-07-11T00:00:01Z",
            };
            return { jsonrpc: "2.0", id: request.id, result: modelSelection };
          }
          if (request.method === "memory.settings.get") {
            return { jsonrpc: "2.0", id: request.id, result: memorySettings };
          }
          if (request.method === "memory.settings.update") {
            requireRevision(
              memorySettings.revision,
              request.params.expected_revision,
            );
            memorySettings = {
              enabled: Boolean(request.params.enabled),
              retention_days: Number(request.params.retention_days),
              export_to_obsidian: Boolean(request.params.export_to_obsidian),
              sync_normalized_content: Boolean(
                request.params.sync_normalized_content,
              ),
              revision: memorySettings.revision + 1,
              updated_at: "2026-07-11T00:00:01Z",
            };
            return { jsonrpc: "2.0", id: request.id, result: memorySettings };
          }
          if (request.method === "projects.get") {
            if (request.params.project_id !== project.id)
              throw new Error("Project is unavailable");
            return { jsonrpc: "2.0", id: request.id, result: { ...project } };
          }
          if (request.method === "conversations.get") {
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: {
                ...selectedConversation(request.params.conversation_id),
              },
            };
          }
          if (request.method === "projects.update_metadata") {
            requireRevision(
              project.metadata_revision,
              request.params.expected_revision,
            );
            project = {
              ...project,
              name:
                request.params.name === null ||
                request.params.name === undefined
                  ? project.name
                  : String(request.params.name),
              pinned_at:
                request.params.pinned === undefined
                  ? project.pinned_at
                  : request.params.pinned
                    ? timestamp
                    : null,
              metadata_revision: project.metadata_revision + 1,
              updated_at: timestamp,
            };
            return { jsonrpc: "2.0", id: request.id, result: { ...project } };
          }
          if (request.method === "projects.archive") {
            requireRevision(
              project.metadata_revision,
              request.params.expected_revision,
            );
            project = {
              ...project,
              archived_at: timestamp,
              pinned_at: null,
              metadata_revision: project.metadata_revision + 1,
              updated_at: timestamp,
            };
            return { jsonrpc: "2.0", id: request.id, result: { ...project } };
          }
          if (request.method === "projects.archived.restore") {
            requireRevision(
              project.metadata_revision,
              request.params.expected_revision,
            );
            project = {
              ...project,
              archived_at: null,
              metadata_revision: project.metadata_revision + 1,
              updated_at: timestamp,
            };
            return { jsonrpc: "2.0", id: request.id, result: { ...project } };
          }
          if (
            request.method === "projects.delete" ||
            request.method === "projects.archived.delete"
          ) {
            requireRevision(
              project.metadata_revision,
              request.params.expected_revision,
            );
            project = {
              ...project,
              deleted_at: timestamp,
              pinned_at: null,
              metadata_revision: project.metadata_revision + 1,
              updated_at: timestamp,
            };
            if (conversation.deleted_at === null) {
              conversation = {
                ...conversation,
                deleted_at: timestamp,
                deleted_by_project_at: timestamp,
                pinned_at: null,
                revision: conversation.revision + 1,
                updated_at: timestamp,
              };
            }
            return { jsonrpc: "2.0", id: request.id, result: { ...project } };
          }
          if (request.method === "conversations.update") {
            const current = selectedConversation(
              request.params.conversation_id,
            );
            requireRevision(current.revision, request.params.expected_revision);
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: storeConversation({
                ...current,
                title:
                  request.params.title === null ||
                  request.params.title === undefined
                    ? current.title
                    : String(request.params.title),
                pinned_at:
                  request.params.pinned === undefined
                    ? current.pinned_at
                    : request.params.pinned
                      ? timestamp
                      : null,
                revision: current.revision + 1,
                updated_at: timestamp,
              }),
            };
          }
          if (request.method === "conversations.delete") {
            const current = selectedConversation(
              request.params.conversation_id,
            );
            requireRevision(current.revision, request.params.expected_revision);
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: storeConversation({
                ...current,
                deleted_at: timestamp,
                deleted_by_project_at: null,
                pinned_at: null,
                revision: current.revision + 1,
                updated_at: timestamp,
              }),
            };
          }
          if (request.method === "trash.items.restore") {
            if (request.params.item_type === "project") {
              requireRevision(
                project.metadata_revision,
                request.params.expected_revision,
              );
              project = {
                ...project,
                deleted_at: null,
                metadata_revision: project.metadata_revision + 1,
                updated_at: timestamp,
              };
              if (conversation.deleted_by_project_at !== null) {
                conversation = {
                  ...conversation,
                  deleted_at: null,
                  deleted_by_project_at: null,
                  revision: conversation.revision + 1,
                  updated_at: timestamp,
                };
              }
            } else {
              const current = selectedConversation(request.params.item_id);
              requireRevision(
                current.revision,
                request.params.expected_revision,
              );
              storeConversation({
                ...current,
                deleted_at: null,
                deleted_by_project_at: null,
                revision: current.revision + 1,
                updated_at: timestamp,
              });
            }
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: {
                item_type: request.params.item_type,
                item_id: request.params.item_id,
                status: "restored",
                released_bytes: 0,
              },
            };
          }
          if (request.method === "trash.items.purge") {
            if (request.params.item_type === "project") {
              requireRevision(
                project.metadata_revision,
                request.params.expected_revision,
              );
              project = {
                ...project,
                name: "Deleted project",
                purged_at: timestamp,
                metadata_revision: project.metadata_revision + 1,
                updated_at: timestamp,
              };
            } else {
              const current = selectedConversation(request.params.item_id);
              requireRevision(
                current.revision,
                request.params.expected_revision,
              );
              storeConversation({
                ...current,
                title: "Deleted chat",
                purged_at: timestamp,
                revision: current.revision + 1,
                updated_at: timestamp,
              });
            }
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: {
                item_type: request.params.item_type,
                item_id: request.params.item_id,
                status: "purged",
                released_bytes: 2048,
              },
            };
          }
          if (request.method === "trash.items.purge_all") {
            const items = trashItems();
            if (project.deleted_at !== null) {
              project = {
                ...project,
                name: "Deleted project",
                purged_at: timestamp,
                metadata_revision: project.metadata_revision + 1,
                updated_at: timestamp,
              };
            }
            for (const current of [conversation, scratchConversation]) {
              if (current.deleted_at !== null && current.purged_at === null) {
                storeConversation({
                  ...current,
                  title: "Deleted chat",
                  purged_at: timestamp,
                  revision: current.revision + 1,
                  updated_at: timestamp,
                });
              }
            }
            return {
              jsonrpc: "2.0",
              id: request.id,
              result: {
                purged_count: items.length,
                released_bytes: items.length * 2048,
              },
            };
          }
          const result =
            request.method === "projects.list"
              ? { items: activeProjects(), next_cursor: null }
              : request.method === "projects.archived.list"
                ? { items: archivedProjects(), next_cursor: null }
                : request.method === "trash.items.list"
                  ? { items: trashItems(), next_cursor: null }
                  : request.method === "conversations.list"
                    ? {
                        items: activeConversations().filter(
                          (item) =>
                            request.params.project_id === null ||
                            request.params.project_id === undefined ||
                            item.project_id === request.params.project_id,
                        ),
                        next_cursor: null,
                      }
                    : request.method === "tasks.list"
                      ? {
                          items: [{ ...task }, { ...scratchTask }],
                          next_cursor: null,
                        }
                      : request.method === "workspaces.get"
                        ? {
                            ...(results["workspaces.get"] as Record<
                              string,
                              unknown
                            >),
                            id: request.params.workspace_id,
                            active_version_id:
                              request.params.workspace_id ===
                              id.scratchConversation
                                ? id.scratchVersion
                                : id.version,
                            active_preview_id:
                              request.params.workspace_id ===
                              id.scratchConversation
                                ? null
                                : id.preview,
                          }
                        : request.method === "workspaces.files.list"
                          ? {
                              ...(results["workspaces.files.list"] as Record<
                                string,
                                unknown
                              >),
                              workspace_id: request.params.workspace_id,
                              version_id: request.params.version_id,
                            }
                          : request.method === "file_sets.resolve"
                            ? {
                                id: "0198f4de-0114-7000-8000-000000000043",
                                workspace_id: request.params.workspace_id,
                                version_id: request.params.version_id,
                                kind:
                                  request.params.path === "models/triangle.gltf"
                                    ? "gltf"
                                    : "single",
                                primary_path: request.params.path,
                                parser_version: "1.0.0",
                                manifest_hash: "a".repeat(64),
                                members: [
                                  {
                                    path: request.params.path,
                                    content_hash:
                                      request.params.path ===
                                      "models/triangle.gltf"
                                        ? modelFixtureHash
                                        : "a".repeat(64),
                                    byte_length:
                                      request.params.path ===
                                      "models/triangle.gltf"
                                        ? modelFixtureByteLength
                                        : 1,
                                    role: "primary",
                                  },
                                ],
                                missing_dependencies: [],
                                blocked_dependencies: [],
                              }
                            : request.method === "workspaces.files.read" &&
                                request.params.path === "models/triangle.gltf"
                              ? {
                                  file: {
                                    path: "models/triangle.gltf",
                                    byte_length: modelFixtureByteLength,
                                    content_hash: modelFixtureHash,
                                    kind: "manifest",
                                    language: "json",
                                  },
                                  media_type: "model/gltf+json",
                                  text: modelFixtureText,
                                  content_base64: null,
                                  stream_required: false,
                                }
                              : request.method === "workspaces.files.read" &&
                                  request.params.path === "docs/sample.pdf"
                                ? {
                                    file: {
                                      path: "docs/sample.pdf",
                                      byte_length: pdfFixtureByteLength,
                                      content_hash: pdfFixtureHash,
                                      kind: "binary",
                                      language: null,
                                    },
                                    media_type: "application/pdf",
                                    text: null,
                                    content_base64: null,
                                    stream_required: true,
                                  }
                                : request.method === "workspaces.files.read" &&
                                    request.params.path ===
                                      "media/generated.png"
                                  ? {
                                      file: {
                                        path: "media/generated.png",
                                        byte_length: 68,
                                        content_hash: capturePngHash,
                                        kind: "binary",
                                        language: null,
                                      },
                                      media_type: "image/png",
                                      text: null,
                                      content_base64: null,
                                      stream_required: true,
                                    }
                                  : request.method === "files.open_stream"
                                    ? {
                                        session_id:
                                          "0198f4de-0114-7000-8000-000000000030",
                                        workspace_id:
                                          request.params.workspace_id,
                                        version_id: request.params.version_id,
                                        path: request.params.path,
                                        content_hash:
                                          request.params.path ===
                                          "media/generated.png"
                                            ? capturePngHash
                                            : request.params.path ===
                                                "models/triangle.gltf"
                                              ? modelFixtureHash
                                              : pdfFixtureHash,
                                        byte_length:
                                          request.params.path ===
                                          "media/generated.png"
                                            ? 68
                                            : request.params.path ===
                                                "models/triangle.gltf"
                                              ? modelFixtureByteLength
                                              : pdfFixtureByteLength,
                                        media_type:
                                          request.params.path ===
                                          "media/generated.png"
                                            ? "image/png"
                                            : request.params.path ===
                                                "models/triangle.gltf"
                                              ? "model/gltf+json"
                                              : "application/pdf",
                                        url: `${previewUrl}${
                                          request.params.path ===
                                          "media/generated.png"
                                            ? "fixture.png"
                                            : request.params.path ===
                                                "models/triangle.gltf"
                                              ? "fixture.gltf"
                                              : "fixture.pdf"
                                        }`,
                                        expires_at: "2026-07-11T00:02:00Z",
                                      }
                                    : request.method === "files.present"
                                      ? request.params.path ===
                                        "docs/sample.pdf"
                                        ? pdfPresentation
                                        : request.params.path ===
                                            "models/triangle.gltf"
                                          ? modelPresentation
                                          : filePresentation
                                      : request.method === "annotations.list"
                                        ? { document: annotationDocument }
                                        : request.method ===
                                            "annotations.update"
                                          ? (() => {
                                              annotationDocument = {
                                                id: id.annotation,
                                                workspace_id:
                                                  request.params.workspace_id,
                                                version_id:
                                                  request.params.version_id,
                                                file_set_id:
                                                  request.params.file_set_id,
                                                source_hash:
                                                  request.params.source_hash,
                                                annotations:
                                                  request.params.annotations,
                                                revision:
                                                  Number(
                                                    request.params
                                                      .expected_revision,
                                                  ) + 1,
                                                created_at: timestamp,
                                                updated_at: timestamp,
                                              };
                                              return annotationDocument;
                                            })()
                                          : request.method ===
                                              "selections.create"
                                            ? {
                                                id: id.selection,
                                                workspace_id:
                                                  request.params.workspace_id,
                                                version_id:
                                                  request.params.version_id,
                                                file_set_id:
                                                  request.params.file_set_id,
                                                source_path:
                                                  request.params.source_path,
                                                source_hash:
                                                  request.params.source_hash,
                                                viewer_kind:
                                                  request.params.viewer_kind,
                                                locator_kind:
                                                  request.params.locator_kind,
                                                locator: request.params.locator,
                                                created_at: timestamp,
                                              }
                                            : request.method ===
                                                "previews.resolve"
                                              ? request.params.task_id ===
                                                id.scratchTask
                                                ? null
                                                : {
                                                    task: { ...task },
                                                    runtime: { ...runtime },
                                                    preview: { ...preview },
                                                  }
                                              : request.method ===
                                                  "voice.synthesize"
                                                ? {
                                                    task_id:
                                                      request.params.task_id,
                                                    turn_id:
                                                      request.params.turn_id,
                                                    message_id:
                                                      request.params.message_id,
                                                    profile_id:
                                                      request.params.profile_id,
                                                    start_offset:
                                                      request.params
                                                        .start_offset,
                                                    end_offset:
                                                      request.params.end_offset,
                                                    media_type: "audio/wav",
                                                    audio_base64:
                                                      voiceWavBase64,
                                                    sample_rate: 24_000,
                                                    channels: 1,
                                                    frames: 2,
                                                    content_hash: voiceWavHash,
                                                  }
                                                : request.method ===
                                                    "tasks.create"
                                                  ? (() => {
                                                      const userRequest =
                                                        String(
                                                          request.params
                                                            .user_request ?? "",
                                                        );
                                                      latestUserRequest =
                                                        userRequest;
                                                      approvalScenario =
                                                        userRequest ===
                                                        "Request a governed notification";
                                                      approvalVisible = false;
                                                      approvalDecision =
                                                        "pending";
                                                      messages = [
                                                        scratchMessage,
                                                        toolMessage,
                                                      ];
                                                      return {
                                                        task: {
                                                          ...scratchTask,
                                                          user_request:
                                                            userRequest,
                                                        },
                                                      };
                                                    })()
                                                  : request.method ===
                                                      "assistant.turns.create"
                                                    ? (() => {
                                                        messages = [
                                                          ...messages,
                                                          {
                                                            ...scratchMessage,
                                                            id: "0198f4de-0114-7000-8000-000000000030",
                                                            task_id:
                                                              id.scratchTask,
                                                            turn_id: id.turn,
                                                            sequence: 3,
                                                            role: "user",
                                                            content:
                                                              latestUserRequest,
                                                          },
                                                        ];
                                                        return {
                                                          ...completedTurn,
                                                          status: "created",
                                                          completed_at: null,
                                                        };
                                                      })()
                                                    : request.method ===
                                                        "assistant.turns.start"
                                                      ? (() => {
                                                          if (
                                                            !approvalScenario
                                                          ) {
                                                            messages = [
                                                              ...messages,
                                                              {
                                                                ...resumedMessage,
                                                                id: "0198f4de-0114-7000-8000-000000000031",
                                                                sequence: 4,
                                                                content:
                                                                  "Fixture streamed response completed",
                                                              },
                                                            ];
                                                            return completedTurn;
                                                          }
                                                          if (
                                                            approvalDecision ===
                                                            "pending"
                                                          ) {
                                                            approvalVisible = true;
                                                            return waitingTurn;
                                                          }
                                                          if (
                                                            !messages.some(
                                                              (message) =>
                                                                message.id ===
                                                                resumedMessage.id,
                                                            )
                                                          ) {
                                                            messages = [
                                                              ...messages,
                                                              resumedMessage,
                                                            ];
                                                          }
                                                          return completedTurn;
                                                        })()
                                                      : request.method ===
                                                          "assistant.turns.trace.list"
                                                        ? request.params
                                                            .turn_id ===
                                                          id.projectTurn
                                                          ? projectTrace
                                                          : results[
                                                              request.method
                                                            ]
                                                        : request.method ===
                                                            "messages.list"
                                                          ? {
                                                              items:
                                                                request.params
                                                                  .conversation_id ===
                                                                id.conversation
                                                                  ? [
                                                                      projectMessage,
                                                                    ]
                                                                  : messages,
                                                              next_cursor: null,
                                                            }
                                                          : request.method ===
                                                              "approvals.list"
                                                            ? {
                                                                items:
                                                                  approvalVisible &&
                                                                  request.params
                                                                    .task_id ===
                                                                    id.scratchTask
                                                                    ? [
                                                                        {
                                                                          ...pendingApproval,
                                                                          decision:
                                                                            approvalDecision,
                                                                          decided_by:
                                                                            approvalDecision ===
                                                                            "pending"
                                                                              ? null
                                                                              : "user",
                                                                          decided_at:
                                                                            approvalDecision ===
                                                                            "pending"
                                                                              ? null
                                                                              : timestamp,
                                                                        },
                                                                      ]
                                                                    : [],
                                                                next_cursor:
                                                                  null,
                                                              }
                                                            : request.method ===
                                                                "approvals.decide"
                                                              ? (() => {
                                                                  if (
                                                                    request
                                                                      .params
                                                                      .approval_id !==
                                                                    id.approval
                                                                  ) {
                                                                    throw new Error(
                                                                      "Approval is unavailable",
                                                                    );
                                                                  }
                                                                  if (
                                                                    "decided_by" in
                                                                    request.params
                                                                  ) {
                                                                    throw new Error(
                                                                      "Renderer cannot choose decided_by",
                                                                    );
                                                                  }
                                                                  approvalDecision =
                                                                    request
                                                                      .params
                                                                      .approved
                                                                      ? "approved"
                                                                      : "rejected";
                                                                  approvalVisible = false;
                                                                  if (
                                                                    approvalDecision ===
                                                                      "approved" &&
                                                                    !messages.some(
                                                                      (
                                                                        message,
                                                                      ) =>
                                                                        message.id ===
                                                                        resumedMessage.id,
                                                                    )
                                                                  ) {
                                                                    messages = [
                                                                      ...messages,
                                                                      resumedMessage,
                                                                    ];
                                                                  }
                                                                  const cursor =
                                                                    (events.at(
                                                                      -1,
                                                                    )?.cursor ??
                                                                      0) + 1;
                                                                  events.push({
                                                                    ...event,
                                                                    id: `0198f4de-0114-7000-8000-${String(100_000_000_000 + cursor)}`,
                                                                    cursor,
                                                                    run_id:
                                                                      id.commandRun,
                                                                    project_id:
                                                                      null,
                                                                    conversation_id:
                                                                      id.scratchConversation,
                                                                    task_id:
                                                                      id.scratchTask,
                                                                    version_id:
                                                                      null,
                                                                    task_sequence:
                                                                      cursor,
                                                                    event_type:
                                                                      "approval.decided",
                                                                    message: `Approval ${approvalDecision}`,
                                                                    payload: {
                                                                      approval_id:
                                                                        id.approval,
                                                                      decision:
                                                                        approvalDecision,
                                                                    },
                                                                  });
                                                                  return {
                                                                    approval: {
                                                                      ...pendingApproval,
                                                                      decision:
                                                                        approvalDecision,
                                                                      decided_by:
                                                                        "user",
                                                                      decided_at:
                                                                        timestamp,
                                                                    },
                                                                    changeset:
                                                                      null,
                                                                  };
                                                                })()
                                                              : request.method ===
                                                                  "tasks.review"
                                                                ? (() => {
                                                                    if (
                                                                      request
                                                                        .params
                                                                        .task_id !==
                                                                      id.task
                                                                    ) {
                                                                      throw new Error(
                                                                        "Task is unavailable",
                                                                      );
                                                                    }
                                                                    task = {
                                                                      ...task,
                                                                      status:
                                                                        "ready",
                                                                    };
                                                                    const cursor =
                                                                      (events.at(
                                                                        -1,
                                                                      )
                                                                        ?.cursor ??
                                                                        0) + 1;
                                                                    events.push(
                                                                      {
                                                                        ...event,
                                                                        id: `0198f4de-0114-7000-8000-${String(100_000_000_000 + cursor)}`,
                                                                        cursor,
                                                                        task_sequence:
                                                                          cursor,
                                                                        event_type:
                                                                          "task.reviewed",
                                                                        message:
                                                                          "Review complete",
                                                                        payload:
                                                                          {
                                                                            status:
                                                                              "ready",
                                                                            checkpoint_id:
                                                                              id.checkpoint,
                                                                          },
                                                                      },
                                                                    );
                                                                    return {
                                                                      id: id.checkpoint,
                                                                      task_id:
                                                                        id.task,
                                                                      version_id:
                                                                        id.version,
                                                                      changed_files:
                                                                        [
                                                                          "README.md",
                                                                        ],
                                                                      command_run_ids:
                                                                        [
                                                                          id.commandRun,
                                                                        ],
                                                                      preview_artifact_id:
                                                                        null,
                                                                      created_at:
                                                                        timestamp,
                                                                    };
                                                                  })()
                                                                : request.method ===
                                                                    "versions.accept"
                                                                  ? (() => {
                                                                      task = {
                                                                        ...task,
                                                                        status:
                                                                          "accepted",
                                                                      };
                                                                      project =
                                                                        {
                                                                          ...project,
                                                                          active_version_id:
                                                                            id.version,
                                                                          revision:
                                                                            project.revision +
                                                                            1,
                                                                        };
                                                                      return project;
                                                                    })()
                                                                  : request.method ===
                                                                      "permissions.get"
                                                                    ? permissions
                                                                    : request.method ===
                                                                        "permissions.update"
                                                                      ? (() => {
                                                                          if (
                                                                            request
                                                                              .params
                                                                              .expected_revision !==
                                                                            permissions.revision
                                                                          ) {
                                                                            throw new Error(
                                                                              "VERSION_CONFLICT",
                                                                            );
                                                                          }
                                                                          permissions =
                                                                            {
                                                                              profile:
                                                                                String(
                                                                                  request
                                                                                    .params
                                                                                    .profile,
                                                                                ),
                                                                              capability_overrides:
                                                                                (request
                                                                                  .params
                                                                                  .capability_overrides as Record<
                                                                                  string,
                                                                                  boolean
                                                                                >) ??
                                                                                {},
                                                                              revision:
                                                                                permissions.revision +
                                                                                1,
                                                                              updated_at:
                                                                                "2026-07-11T00:00:01Z",
                                                                            };
                                                                          return permissions;
                                                                        })()
                                                                      : request.method ===
                                                                          "capabilities.get"
                                                                        ? (() => {
                                                                            const base =
                                                                              results[
                                                                                "capabilities.get"
                                                                              ] as {
                                                                                slash_commands: Array<{
                                                                                  required_operation:
                                                                                    | string
                                                                                    | null;
                                                                                  available: boolean;
                                                                                }>;
                                                                                [
                                                                                  key: string
                                                                                ]: unknown;
                                                                              };
                                                                            const operations: Record<
                                                                              string,
                                                                              boolean
                                                                            > =
                                                                              {
                                                                                "model.generate": true,
                                                                                "workspace.create_scratch":
                                                                                  permissions.profile !==
                                                                                  "observe",
                                                                                "web.search":
                                                                                  permissions
                                                                                    .capability_overrides[
                                                                                    "web.search"
                                                                                  ] !==
                                                                                  false,
                                                                                "run.sandboxed":
                                                                                  permissions.profile ===
                                                                                    "autonomous" &&
                                                                                  !scenarios.has(
                                                                                    "sandboxUnavailable",
                                                                                  ) &&
                                                                                  permissions
                                                                                    .capability_overrides[
                                                                                    "run.sandboxed"
                                                                                  ] !==
                                                                                    false,
                                                                              };
                                                                            return {
                                                                              ...base,
                                                                              profile:
                                                                                permissions.profile,
                                                                              operations,
                                                                              sandbox_healthy:
                                                                                !scenarios.has(
                                                                                  "sandboxUnavailable",
                                                                                ),
                                                                              slash_commands:
                                                                                base.slash_commands.map(
                                                                                  (
                                                                                    command,
                                                                                  ) => ({
                                                                                    ...command,
                                                                                    available:
                                                                                      command.required_operation ===
                                                                                        null ||
                                                                                      operations[
                                                                                        command
                                                                                          .required_operation
                                                                                      ] ===
                                                                                        true,
                                                                                  }),
                                                                                ),
                                                                            };
                                                                          })()
                                                                        : request.method ===
                                                                            "mcp.servers.list"
                                                                          ? {
                                                                              items:
                                                                                mcpServer ===
                                                                                null
                                                                                  ? []
                                                                                  : [
                                                                                      mcpServer,
                                                                                    ],
                                                                            }
                                                                          : request.method ===
                                                                              "mcp.servers.accept"
                                                                            ? (() => {
                                                                                if (
                                                                                  mcpServer ===
                                                                                    null ||
                                                                                  request
                                                                                    .params
                                                                                    .server_id !==
                                                                                    mcpServer.server_id ||
                                                                                  request
                                                                                    .params
                                                                                    .expected_revision !==
                                                                                    mcpServer.revision ||
                                                                                  request
                                                                                    .params
                                                                                    .schema_digest !==
                                                                                    mcpServer.pending_schema_digest
                                                                                ) {
                                                                                  throw new Error(
                                                                                    "MCP acceptance revision mismatch",
                                                                                  );
                                                                                }
                                                                                mcpServer =
                                                                                  {
                                                                                    ...mcpServer,
                                                                                    enabled:
                                                                                      Boolean(
                                                                                        request
                                                                                          .params
                                                                                          .enabled,
                                                                                      ),
                                                                                    status:
                                                                                      request
                                                                                        .params
                                                                                        .enabled
                                                                                        ? "ready"
                                                                                        : "disabled",
                                                                                    revision:
                                                                                      mcpServer.revision +
                                                                                      1,
                                                                                    accepted_schema_digest:
                                                                                      mcpServer.pending_schema_digest,
                                                                                    accepted_tools:
                                                                                      mcpServer.pending_tools,
                                                                                    policies:
                                                                                      request
                                                                                        .params
                                                                                        .tools,
                                                                                    updated_at:
                                                                                      "2026-07-11T00:00:01Z",
                                                                                  };
                                                                                return mcpServer;
                                                                              })()
                                                                            : request.method ===
                                                                                "events.state"
                                                                              ? {
                                                                                  ledger_id:
                                                                                    "0198f4de-0114-7000-8000-000000000045",
                                                                                  oldest_cursor:
                                                                                    events.at(
                                                                                      0,
                                                                                    )
                                                                                      ?.cursor ??
                                                                                    0,
                                                                                  latest_cursor:
                                                                                    events.at(
                                                                                      -1,
                                                                                    )
                                                                                      ?.cursor ??
                                                                                    0,
                                                                                }
                                                                              : request.method ===
                                                                                  "events.list"
                                                                                ? (() => {
                                                                                    const cursor =
                                                                                      Number(
                                                                                        request
                                                                                          .params
                                                                                          .cursor ??
                                                                                          0,
                                                                                      );
                                                                                    const limit =
                                                                                      Number(
                                                                                        request
                                                                                          .params
                                                                                          .limit ??
                                                                                          500,
                                                                                      );
                                                                                    const items =
                                                                                      events
                                                                                        .filter(
                                                                                          (
                                                                                            item,
                                                                                          ) =>
                                                                                            item.cursor >
                                                                                            cursor,
                                                                                        )
                                                                                        .slice(
                                                                                          0,
                                                                                          limit,
                                                                                        );
                                                                                    return {
                                                                                      items,
                                                                                      next_cursor:
                                                                                        items.at(
                                                                                          -1,
                                                                                        )
                                                                                          ?.cursor ??
                                                                                        cursor,
                                                                                    };
                                                                                  })()
                                                                                : request.method ===
                                                                                    "events.subscribe"
                                                                                  ? (() => {
                                                                                      const cursor =
                                                                                        Number(
                                                                                          request
                                                                                            .params
                                                                                            .cursor ??
                                                                                            0,
                                                                                        );
                                                                                      const items =
                                                                                        events.filter(
                                                                                          (
                                                                                            item,
                                                                                          ) =>
                                                                                            item.cursor >
                                                                                            cursor,
                                                                                        );
                                                                                      return {
                                                                                        items,
                                                                                        next_cursor:
                                                                                          items.at(
                                                                                            -1,
                                                                                          )
                                                                                            ?.cursor ??
                                                                                          cursor,
                                                                                      };
                                                                                    })()
                                                                                  : results[
                                                                                      request
                                                                                        .method
                                                                                    ];
          if (result === undefined) {
            throw new Error(`Unexpected Core method: ${request.method}`);
          }
          return { jsonrpc: "2.0", id: request.id, result };
        },
      };
      fixtureWindow.__FAIRY_BOOT_STARTED_AT__ = performance.now();
    },
    {
      previewUrl: PREVIEW_URL,
      voiceWavBase64: VOICE_WAV_BASE64,
      voiceWavHash: VOICE_WAV_HASH,
      capturePngBase64: CAPTURE_PNG_BASE64,
      capturePngHash: CAPTURE_PNG_HASH,
      pdfFixtureHash: PDF_FIXTURE_HASH,
      pdfFixtureByteLength: PDF_FIXTURE.length,
      modelFixtureText: MODEL_FIXTURE_TEXT,
      modelFixtureHash: MODEL_FIXTURE_HASH,
      modelFixtureByteLength: MODEL_FIXTURE.length,
    },
  );
}

function pcmWav(): Uint8Array {
  const samples = new Uint8Array([0, 0, 1, 0]);
  const bodyLength = 4 + 8 + 16 + 8 + samples.length;
  const bytes = new Uint8Array(8 + bodyLength);
  const view = new DataView(bytes.buffer);
  writeAscii(bytes, 0, "RIFF");
  view.setUint32(4, bodyLength, true);
  writeAscii(bytes, 8, "WAVE");
  writeAscii(bytes, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 24_000, true);
  view.setUint32(28, 48_000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(bytes, 36, "data");
  view.setUint32(40, samples.length, true);
  bytes.set(samples, 44);
  return bytes;
}

function writeAscii(bytes: Uint8Array, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    bytes[offset + index] = value.charCodeAt(index);
  }
}
