import { afterEach, describe, expect, it, vi } from "vitest";

import type { PresenceInteractionSnapshot } from "../domain/interaction";
import { DEFAULT_FAIRY_MOTION_SNAPSHOT } from "../domain/motionState";
import {
  NativePresenceRendererHost,
  nativeFrameRateLimit,
  nativePresentationForSnapshot,
  nativeSurfaceKey,
  nativeVisualStateForSnapshot,
  type NativeGpuStatus,
} from "./NativePresenceRendererHost";
import type { PresenceRenderSnapshot } from "./presenceRenderer";

function snapshot(
  overrides: Partial<PresenceRenderSnapshot> = {},
): PresenceRenderSnapshot {
  return {
    motion: DEFAULT_FAIRY_MOTION_SNAPSHOT,
    interaction: null,
    input_capsule_visible: false,
    input_capsule_width: 280,
    input_capsule_height: 64,
    input_surface_visible: false,
    work_state: "idle",
    speaking: false,
    voice_level: 0,
    sleeping: false,
    reduced_motion: false,
    size_scale: 1,
    opacity: 0.92,
    particles_enabled: true,
    optics_mode: "enhanced",
    idle_for_ms: 0,
    target_frame_rate: 144,
    frame_rate_limit: 144,
    ...overrides,
  };
}

function interaction(
  overrides: Partial<PresenceInteractionSnapshot> = {},
): PresenceInteractionSnapshot {
  return {
    schema_version: 1,
    sequence: 1,
    sampled_at_ms: 16,
    phase: "aware",
    phase_started_at_ms: 0,
    reduced_motion: false,
    cursor: {
      point: { x: 20, y: 30 },
      direction: { x: 1, y: 0 },
      distance_px: 20,
      speed_px_s: 30,
      dwell_ms: 16,
      band: "active",
    },
    placement: {
      anchor: { x: 10, y: 10 },
      render_frame: { x: 0, y: 0, width: 640, height: 260 },
      input_compact_frame: { x: 0, y: 0, width: 616, height: 144 },
      input_expanded_frame: { x: 0, y: 0, width: 616, height: 360 },
      monitor_work_area: { x: 0, y: 0, width: 1920, height: 1040 },
      scale_factor: 1,
      expansion_direction: "left",
    },
    ...overrides,
  };
}

function healthyStatus(
  overrides: Partial<NativeGpuStatus> = {},
): NativeGpuStatus {
  return {
    backend: "windows_host_backdrop_d3d11_composition",
    optics_source: "host_backdrop",
    lifecycle: "running",
    zero_copy_capture: false,
    pixel_ipc: false,
    hdr_capture: false,
    target_frame_rate: 144,
    effective_frame_rate: 144,
    display_refresh_rate_hz: 144,
    capture_frame_rate_limit: 0,
    frames_presented: 2,
    capture_fps_avg: 144,
    frame_interval_p1_fps: 120,
    source_frames_received: 0,
    source_capture_fps_avg: 0,
    source_frame_interval_p1_fps: 0,
    callback_to_present_p95_ms: 0.3,
    present_p95_ms: 0.2,
    surface_width: 640,
    surface_height: 260,
    target_x: 0,
    target_y: 0,
    monitor_x: 0,
    monitor_y: 0,
    monitor_width: 1920,
    monitor_height: 1080,
    monitor_handle: "0x0000000000000001",
    monitor_device_name: "\\\\.\\DISPLAY1",
    monitor_friendly_name: "Test display",
    adapter_name: "Test adapter",
    adapter_index: 0,
    output_device_name: "\\\\.\\DISPLAY1",
    output_index: 0,
    hdr_color_space: "0",
    capture_item_width: 1920,
    capture_item_height: 1080,
    capture_window_handle: null,
    capture_source_stage: "monitor_capture_started",
    capture_source_hresult: null,
    started_at_ms: 1,
    last_presented_at_ms: 2,
    presentation_revision: 0,
    visual_sequence: 0,
    fallback_reason: null,
    error_code: null,
    ...overrides,
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("NativePresenceRendererHost", () => {
  it("maps only bounded presentation scalars and never sends captured pixels", async () => {
    const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
    const host = new NativePresenceRendererHost({
      invokeCommand: async (command, args) => {
        calls.push({ command, args });
        return healthyStatus();
      },
      watchdogIntervalMs: 60_000,
    });

    await expect(host.start(snapshot({
      input_capsule_visible: true,
      interaction: interaction(),
      motion: {
        ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
        state: "responding",
        activity: "response",
      },
      work_state: "streaming",
      voice_level: 0.7,
    }))).resolves.toBe(true);

    const start = calls.find((call) => call.command === "pet_native_gpu_start");
    expect(start?.args).toEqual({
      request: expect.objectContaining({
        target_frame_rate: 144,
        visual_sequence: 1,
        capsule_visible: true,
        expansion_direction: "left",
        visual_state: "responding",
        opacity: 0.92,
        voice_level: 0,
        reduced_motion: false,
        particles_enabled: true,
        frame_rate_limit: 144,
        shape_droplet: 0,
        shape_bridge: 0,
        shape_capsule: 0,
        returning: false,
      }),
    });
    expect(JSON.stringify(start?.args)).not.toMatch(
      /rgba|pixel|data_url|backdrop|capture_frame/i,
    );

    await host.dispose();
  });

  it("coalesces live scalar updates and hot-swaps when the selected target changes", async () => {
    const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
    const host = new NativePresenceRendererHost({
      invokeCommand: async (command, args) => {
        calls.push({ command, args });
        const target = command === "pet_native_gpu_start"
          ? Number((args?.request as Record<string, unknown>)?.target_frame_rate ?? 144)
          : 144;
        return healthyStatus({ target_frame_rate: target });
      },
      watchdogIntervalMs: 60_000,
    });

    await host.start(snapshot());
    host.setSnapshot(snapshot({ opacity: 0.7 }));
    host.setSnapshot(snapshot({
      opacity: 0.8,
      motion: {
        ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
        state: "thinking",
        activity: "tool",
      },
      work_state: "tool",
    }));
    await vi.waitFor(() => {
      expect(calls.filter((call) => call.command === "pet_native_gpu_update")).toHaveLength(1);
    });
    expect(calls.find((call) => call.command === "pet_native_gpu_update")?.args).toEqual({
      request: expect.objectContaining({
        opacity: 0.8,
        visual_sequence: 2,
        visual_state: "tool",
      }),
    });

    await host.start(snapshot({ target_frame_rate: 300, frame_rate_limit: 300 }));
    expect(calls.map((call) => call.command)).toEqual([
      "pet_native_gpu_start",
      "pet_native_gpu_update",
      "pet_native_gpu_rebind",
    ]);
    expect(calls.at(-1)?.args).toEqual({
      request: expect.objectContaining({
        target_frame_rate: 300,
        frame_rate_limit: 300,
        visual_sequence: 3,
      }),
    });

    await host.dispose();
  });

  it("stops the native surface and reports fallback when a live update fails", async () => {
    const health = vi.fn();
    const states: string[] = [];
    const commands: string[] = [];
    const host = new NativePresenceRendererHost({
      invokeCommand: async (command) => {
        commands.push(command);
        if (command === "pet_native_gpu_update") throw new Error("renderer lost");
        return healthyStatus();
      },
      onHealth: health,
      onStateChange: (state) => states.push(state),
      watchdogIntervalMs: 60_000,
    });

    await host.start(snapshot());
    host.setSnapshot(snapshot({
      motion: {
        ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
        state: "thinking",
        activity: "model",
      },
      work_state: "analyzing",
    }));
    await vi.waitFor(() => expect(host.currentState()).toBe("fallback"));

    expect(commands).toEqual([
      "pet_native_gpu_start",
      "pet_native_gpu_update",
      "pet_native_gpu_stop",
    ]);
    expect(states).toContain("fallback");
    expect(health).toHaveBeenLastCalledWith({
      requested_mode: "auto",
      mode: "native",
      actual_backend: "none",
      optics_source: "none",
      status: "failed",
      error_code: "NATIVE_GPU_UPDATE_FAILED",
      fallback_reason: "NATIVE_GPU_UPDATE_FAILED",
      monitor_refresh_hz: 0,
      effective_fps: 0,
    });

    await host.dispose();
  });

  it("recovers a transient DXGI startup failure without restarting the app", async () => {
    let starts = 0;
    const states: string[] = [];
    const host = new NativePresenceRendererHost({
      invokeCommand: async (command) => {
        if (command === "pet_native_gpu_start") {
          starts += 1;
          if (starts === 1) throw new Error("DXGI_ERROR_UNSUPPORTED");
        }
        return healthyStatus();
      },
      onStateChange: (state) => states.push(state),
      recoveryDelaysMs: [1],
      watchdogIntervalMs: 60_000,
    });

    await expect(host.start(snapshot())).resolves.toBe(false);
    await vi.waitFor(() => expect(host.currentState()).toBe("running"));
    expect(starts).toBe(2);
    expect(states).toContain("fallback");

    await host.dispose();
  });

  it("restarts once when the monitor or DPI surface identity changes", async () => {
    const commands: string[] = [];
    const host = new NativePresenceRendererHost({
      invokeCommand: async (command) => {
        commands.push(command);
        return healthyStatus();
      },
      watchdogIntervalMs: 60_000,
    });
    const initial = snapshot({ interaction: interaction() });
    await host.start(initial);

    host.setSnapshot(snapshot({
      interaction: interaction({
        placement: {
          ...interaction().placement,
          render_frame: { x: 400, y: 120, width: 640, height: 260 },
        },
      }),
    }));
    await Promise.resolve();
    expect(commands.filter((command) => command === "pet_native_gpu_start")).toHaveLength(1);
    expect(commands.filter((command) => command === "pet_native_gpu_stop")).toHaveLength(0);

    host.setSnapshot(snapshot({
      interaction: interaction({
        placement: {
          ...interaction().placement,
          render_frame: { x: 2_100, y: 120, width: 800, height: 325 },
          monitor_work_area: { x: 1_920, y: 0, width: 2_560, height: 1_400 },
          scale_factor: 1.25,
        },
      }),
    }));
    await vi.waitFor(() => {
      expect(commands.filter((command) => command === "pet_native_gpu_rebind")).toHaveLength(1);
    });
    expect(commands).not.toContain("pet_native_gpu_stop");

    await host.dispose();
  });

  it("keeps a healthy monitor capture stable across watchdog polls", async () => {
    vi.useFakeTimers();
    const commands: string[] = [];
    const host = new NativePresenceRendererHost({
      invokeCommand: async (command) => {
        commands.push(command);
        return healthyStatus({ capture_source_stage: "monitor_capture_started" });
      },
      watchdogIntervalMs: 250,
    });

    await host.start(snapshot({ interaction: interaction() }));
    await vi.advanceTimersByTimeAsync(250);
    await vi.runAllTicks();
    await vi.advanceTimersByTimeAsync(1);

    expect(commands.filter((command) => command === "pet_native_gpu_start"))
      .toHaveLength(1);
    expect(commands.filter((command) => command === "pet_native_gpu_rebind"))
      .toHaveLength(0);
    expect(commands).not.toContain("pet_native_gpu_stop");

    await host.dispose();
  });

  it("reconciles the latest surface after an in-flight hot-swap", async () => {
    const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
    let releaseFirstRebind!: () => void;
    const firstRebindBlocked = new Promise<void>((resolve) => {
      releaseFirstRebind = resolve;
    });
    let rebinds = 0;
    const host = new NativePresenceRendererHost({
      invokeCommand: async (command, args) => {
        calls.push({ command, args });
        if (command === "pet_native_gpu_rebind") {
          rebinds += 1;
          if (rebinds === 1) await firstRebindBlocked;
        }
        return healthyStatus();
      },
      watchdogIntervalMs: 60_000,
    });
    await host.start(snapshot({ interaction: interaction() }));

    host.setSnapshot(snapshot({
      interaction: interaction({
        placement: {
          ...interaction().placement,
          render_frame: { x: 2_100, y: 120, width: 800, height: 325 },
          monitor_work_area: { x: 1_920, y: 0, width: 2_560, height: 1_400 },
          scale_factor: 1.25,
        },
      }),
    }));
    await vi.waitFor(() => expect(rebinds).toBe(1));

    host.setSnapshot(snapshot({
      interaction: interaction({
        placement: {
          ...interaction().placement,
          render_frame: { x: -1_280, y: 80, width: 960, height: 390 },
          monitor_work_area: { x: -1_280, y: 0, width: 1_280, height: 1_024 },
          scale_factor: 1.5,
        },
      }),
    }));
    releaseFirstRebind();

    await vi.waitFor(() => expect(rebinds).toBe(2));
    expect(calls.filter((call) => call.command === "pet_native_gpu_stop")).toHaveLength(0);

    await host.dispose();
  });

  it("does not rebind the monitor capture when a same-monitor drag settles", async () => {
    const commands: string[] = [];
    const host = new NativePresenceRendererHost({
      invokeCommand: async (command) => {
        commands.push(command);
        return healthyStatus();
      },
      watchdogIntervalMs: 60_000,
    });
    const current = snapshot({ interaction: interaction() });
    await host.start(current);

    host.refreshNativeSurface(current);
    await Promise.resolve();
    expect(commands.filter((command) => command === "pet_native_gpu_rebind"))
      .toHaveLength(0);
    expect(commands).not.toContain("pet_native_gpu_stop");

    await host.dispose();
  });

  it("suspends without discarding intent and resumes from the latest snapshot", async () => {
    const commands: string[] = [];
    const states: string[] = [];
    const host = new NativePresenceRendererHost({
      invokeCommand: async (command) => {
        commands.push(command);
        return healthyStatus();
      },
      onStateChange: (state) => states.push(state),
      watchdogIntervalMs: 60_000,
    });
    await host.start(snapshot({ interaction: interaction() }));
    const crossMonitorSnapshot = snapshot({
      interaction: interaction({
        placement: {
          ...interaction().placement,
          render_frame: { x: 2_100, y: 120, width: 800, height: 325 },
          monitor_work_area: { x: 1_920, y: 0, width: 2_560, height: 1_400 },
          scale_factor: 1.25,
        },
      }),
      motion: {
        ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
        state: "responding",
        activity: "response",
      },
      work_state: "streaming",
    });
    const suspending = host.suspend();
    host.setSnapshot(crossMonitorSnapshot);
    await suspending;
    expect(host.currentState()).toBe("suspended");
    await Promise.resolve();
    expect(commands.filter((command) => command === "pet_native_gpu_start")).toHaveLength(1);

    await expect(host.resume(crossMonitorSnapshot)).resolves.toBe(true);
    expect(commands).toEqual([
      "pet_native_gpu_start",
      "pet_native_gpu_stop",
      "pet_native_gpu_start",
    ]);
    expect(states).toContain("suspended");

    await host.dispose();
  });
});

describe("native presentation projection", () => {
  it("maps visible work, speaking, sleep and hover states deterministically", () => {
    expect(nativeVisualStateForSnapshot(snapshot({
      motion: {
        ...DEFAULT_FAIRY_MOTION_SNAPSHOT,
        state: "thinking",
        activity: "model",
      },
    }))).toBe("thinking");
    expect(nativeVisualStateForSnapshot(snapshot({
      motion: { ...DEFAULT_FAIRY_MOTION_SNAPSHOT, state: "error" },
      work_state: "tool",
      speaking: true,
      voice_level: 0.6,
    }))).toBe("error");
    expect(nativeVisualStateForSnapshot(snapshot({
      motion: { ...DEFAULT_FAIRY_MOTION_SNAPSHOT, state: "sleeping" },
      sleeping: true,
    }))).toBe("sleeping");
    expect(nativeVisualStateForSnapshot(snapshot({
      motion: { ...DEFAULT_FAIRY_MOTION_SNAPSHOT, state: "aware" },
      interaction: interaction(),
    }))).toBe("aware");
  });

  it("keeps stable input off the render surface and reserves the bridge for transitions", () => {
    const stable = nativePresentationForSnapshot(snapshot({
      input_capsule_visible: true,
      interaction: interaction({ phase: "interactive" }),
    }));
    expect(stable).toEqual(expect.objectContaining({
      shape_droplet: 0,
      shape_bridge: 0,
      shape_capsule: 0,
      returning: false,
    }));
    const stretching = nativePresentationForSnapshot(snapshot({
      interaction: interaction({ phase: "stretching" }),
    }));
    expect(stretching).toEqual(expect.objectContaining({
      shape_droplet: 1,
      shape_bridge: 1,
      shape_capsule: 0,
    }));
    const returning = nativePresentationForSnapshot(snapshot({
      input_capsule_visible: true,
      interaction: interaction({ phase: "returning" }),
    }));
    expect(returning.returning).toBe(true);
  });

  it("forwards accessibility modes to the native material without changing geometry", () => {
    const baseline = nativePresentationForSnapshot(snapshot({
      input_capsule_visible: true,
      interaction: interaction({ phase: "interactive" }),
    }));
    const adapted = nativePresentationForSnapshot(snapshot({
      input_capsule_visible: true,
      interaction: interaction({ phase: "interactive" }),
      reduced_transparency: true,
      increased_contrast: true,
    }));

    expect(adapted).toEqual(expect.objectContaining({
      reduced_transparency: true,
      increased_contrast: true,
      core_x: baseline.core_x,
      core_y: baseline.core_y,
      capsule_x: baseline.capsule_x,
      capsule_y: baseline.capsule_y,
    }));
  });

  it("projects the physical core anchor and dynamic lower capsule into logical coordinates", () => {
    const projected = nativePresentationForSnapshot(snapshot({
      input_capsule_visible: true,
      input_capsule_width: 360,
      input_capsule_height: 84,
      input_surface_visible: true,
      interaction: interaction({
        placement: {
          ...interaction().placement,
          anchor: { x: 1_816, y: 676 },
          render_frame: { x: 1_000, y: 544, width: 960, height: 390 },
          scale_factor: 1.5,
          expansion_direction: "left",
        },
      }),
    }));
    expect(projected).toEqual(expect.objectContaining({
      core_x: 544,
      core_y: 88,
      capsule_x: 436,
      capsule_y: 220,
      capsule_half_width: 172,
      input_surface_visible: true,
      input_surface_height: 84,
    }));
  });

  it("keeps live optics at the selected rate unless the system applies a power cap", () => {
    expect(nativeFrameRateLimit(snapshot({ target_frame_rate: 60 }))).toBe(60);
    expect(nativeFrameRateLimit(snapshot({
      target_frame_rate: 300,
      frame_rate_limit: 300,
    }))).toBe(300);
    expect(nativeFrameRateLimit(snapshot({ idle_for_ms: 15_000 }))).toBe(144);
    expect(nativeFrameRateLimit(snapshot({ reduced_motion: true }))).toBe(144);
    expect(nativeFrameRateLimit(snapshot({ frame_rate_limit: 15 }))).toBe(15);
    expect(nativePresentationForSnapshot(snapshot({
      motion: { ...DEFAULT_FAIRY_MOTION_SNAPSHOT, state: "speaking" },
      speaking: true,
      voice_level: 2,
      opacity: 0,
    }))).toEqual(expect.objectContaining({
      visual_state: "speaking",
      voice_level: 1,
      opacity: 0.2,
    }));
  });

  it("keys a native surface by monitor, DPI and dimensions rather than normal movement", () => {
    const initial = snapshot({ interaction: interaction() });
    const moved = snapshot({
      interaction: interaction({
        placement: {
          ...interaction().placement,
          render_frame: { x: 900, y: 400, width: 640, height: 260 },
        },
      }),
    });
    expect(nativeSurfaceKey(initial)).toBe(nativeSurfaceKey(moved));
    expect(nativeSurfaceKey(snapshot())).toBeNull();
  });
});
