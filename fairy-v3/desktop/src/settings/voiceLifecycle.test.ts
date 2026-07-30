import { describe, expect, it } from "vitest";

import {
  mergeVoiceLifecycleSnapshot,
  type VoiceWorkerHealth,
  type VoiceWorkerLifecycleSnapshot,
  voiceStatusFromLifecycle,
} from "./client";

describe("voice lifecycle projection", () => {
  it("rejects stale snapshots and applies a newer authoritative sequence", () => {
    const current = health(7, "ready");
    const stale = snapshot(6, "failed");
    const playing = snapshot(8, "playing");

    expect(mergeVoiceLifecycleSnapshot(current, stale)).toBe(current);
    expect(mergeVoiceLifecycleSnapshot(current, playing)).toMatchObject({
      sequence: 8,
      lifecycle_state: "playing",
      active_consumer_count: 1,
      status: "ready",
    });
  });

  it("maps lifecycle transitions to the bounded pet status vocabulary", () => {
    expect(voiceStatusFromLifecycle(snapshot(1, "starting_worker"))).toBe("warming");
    expect(voiceStatusFromLifecycle(snapshot(2, "checking_runtime"))).toBe("warming");
    expect(voiceStatusFromLifecycle(snapshot(3, "playing"))).toBe("ready");
    expect(voiceStatusFromLifecycle(snapshot(4, "stopped"))).toBe("idle");
    expect(voiceStatusFromLifecycle(snapshot(5, "failed"))).toBe("error");
  });
});

function snapshot(
  sequence: number,
  lifecycleState: VoiceWorkerLifecycleSnapshot["lifecycle_state"],
): VoiceWorkerLifecycleSnapshot {
  return {
    sequence,
    lifecycle_state: lifecycleState,
    active_consumer_count: lifecycleState === "playing" ? 1 : 0,
    queued_playback_count: 0,
    error_code: lifecycleState === "failed" ? "VOICE_WORKER_INTERRUPTED" : null,
    started_at_unix_ms: 1,
    transitioned_at_unix_ms: sequence,
  };
}

function health(
  sequence: number,
  lifecycleState: VoiceWorkerHealth["lifecycle_state"],
): VoiceWorkerHealth {
  return {
    ...snapshot(sequence, lifecycleState),
    status: "ready",
    model_repository: "fixture",
    model_installed: true,
    model_ready: true,
    model_digest: "a".repeat(64),
    prompt_ready: true,
    cuda_available: true,
    tensorrt_available: true,
    onnx_cuda_available: true,
    backend: "tensorrt",
    device_name: "Fixture GPU",
    sample_rate: 24_000,
    error_code: null,
  };
}
