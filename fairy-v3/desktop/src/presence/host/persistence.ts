import {
  availableMonitors,
  getCurrentWindow,
  PhysicalPosition,
  primaryMonitor,
} from "@tauri-apps/api/window";
import { z } from "zod";

export type ReducedMotionOverride = "system" | "reduce" | "full";

export interface RelativeMonitorPosition {
  x_ratio: number;
  y_ratio: number;
}

export interface PresenceSettings {
  positions: Record<string, RelativeMonitorPosition>;
  last_monitor_id: string | null;
  scale: number;
  quiet_mode: boolean;
  dismissed_notice_ids: string[];
  reduced_motion_override: ReducedMotionOverride;
}

export interface Point {
  x: number;
  y: number;
}

export interface Extent {
  width: number;
  height: number;
}

export interface MonitorFrame extends Point, Extent {
  id: string;
  scale_factor: number;
  is_primary: boolean;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface PresenceWindowPort {
  monitors(): Promise<MonitorFrame[]>;
  position(): Promise<Point>;
  size(): Promise<Extent>;
  setPosition(position: Point): Promise<void>;
  startDragging(): Promise<void>;
  onMoved(listener: (position: Point) => void): Promise<() => void>;
}

const STORAGE_KEY = "fairy.presence.settings.v1";
const settingsSchema = z
  .object({
    positions: z.record(
      z.string().min(1).max(200),
      z
        .object({
          x_ratio: z.number().min(0).max(1),
          y_ratio: z.number().min(0).max(1),
        })
        .strict(),
    ),
    last_monitor_id: z.string().min(1).max(200).nullable(),
    scale: z.number().min(0.75).max(1.5),
    quiet_mode: z.boolean(),
    dismissed_notice_ids: z.array(z.string().min(1).max(160)).max(128),
    reduced_motion_override: z.enum(["system", "reduce", "full"]),
  })
  .strict();

export function defaultPresenceSettings(): PresenceSettings {
  return {
    positions: {},
    last_monitor_id: null,
    scale: 1,
    quiet_mode: false,
    dismissed_notice_ids: [],
    reduced_motion_override: "system",
  };
}

export function loadPresenceSettings(storage: StorageLike): PresenceSettings {
  try {
    const stored = storage.getItem(STORAGE_KEY);
    if (stored === null) return defaultPresenceSettings();
    const parsed = settingsSchema.safeParse(JSON.parse(stored));
    return parsed.success ? parsed.data : defaultPresenceSettings();
  } catch {
    return defaultPresenceSettings();
  }
}

export function savePresenceSettings(
  storage: StorageLike,
  settings: PresenceSettings,
): void {
  const parsed = settingsSchema.safeParse(settings);
  if (!parsed.success) return;
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(parsed.data));
  } catch {
    // Presence settings are optional and never become project state.
  }
}

export function snapToMonitorEdges(
  position: Point,
  size: Extent,
  monitor: MonitorFrame,
  threshold = 20,
): Point {
  const maxX = Math.max(monitor.x, monitor.x + monitor.width - size.width);
  const maxY = Math.max(monitor.y, monitor.y + monitor.height - size.height);
  const clampedX = clamp(position.x, monitor.x, maxX);
  const clampedY = clamp(position.y, monitor.y, maxY);
  return {
    x:
      Math.abs(clampedX - monitor.x) <= threshold
        ? monitor.x
        : Math.abs(maxX - clampedX) <= threshold
          ? maxX
          : clampedX,
    y:
      Math.abs(clampedY - monitor.y) <= threshold
        ? monitor.y
        : Math.abs(maxY - clampedY) <= threshold
          ? maxY
          : clampedY,
  };
}

export function rememberMonitorPosition(
  settings: PresenceSettings,
  monitor: MonitorFrame,
  position: Point,
  size: Extent,
): PresenceSettings {
  const travelX = Math.max(1, monitor.width - size.width);
  const travelY = Math.max(1, monitor.height - size.height);
  return {
    ...settings,
    positions: {
      ...settings.positions,
      [monitor.id]: {
        x_ratio: clamp((position.x - monitor.x) / travelX, 0, 1),
        y_ratio: clamp((position.y - monitor.y) / travelY, 0, 1),
      },
    },
    last_monitor_id: monitor.id,
  };
}

export function restoreMonitorPosition(
  settings: PresenceSettings,
  monitors: readonly MonitorFrame[],
  size: Extent,
): { monitor: MonitorFrame; position: Point } {
  if (monitors.length === 0) throw new Error("No monitor is available");
  const monitor =
    monitors.find((candidate) => candidate.id === settings.last_monitor_id) ??
    monitors.find((candidate) => candidate.is_primary) ??
    monitors[0];
  const remembered = settings.positions[monitor.id];
  const position =
    remembered === undefined
      ? {
          x: monitor.x + Math.max(0, monitor.width - size.width - 32),
          y: monitor.y + Math.max(0, monitor.height - size.height - 32),
        }
      : {
          x:
            monitor.x +
            Math.round(remembered.x_ratio * Math.max(0, monitor.width - size.width)),
          y:
            monitor.y +
            Math.round(remembered.y_ratio * Math.max(0, monitor.height - size.height)),
        };
  return { monitor, position: snapToMonitorEdges(position, size, monitor) };
}

export function monitorForPosition(
  monitors: readonly MonitorFrame[],
  position: Point,
  size: Extent,
): MonitorFrame | null {
  if (monitors.length === 0) return null;
  const center = { x: position.x + size.width / 2, y: position.y + size.height / 2 };
  return (
    monitors.find(
      (monitor) =>
        center.x >= monitor.x &&
        center.x < monitor.x + monitor.width &&
        center.y >= monitor.y &&
        center.y < monitor.y + monitor.height,
    ) ??
    [...monitors].sort(
      (left, right) => distanceToMonitor(center, left) - distanceToMonitor(center, right),
    )[0]
  );
}

export function resolveReducedMotion(
  override: ReducedMotionOverride,
  systemPrefersReducedMotion: boolean,
): boolean {
  if (override === "reduce") return true;
  if (override === "full") return false;
  return systemPrefersReducedMotion;
}

export function createTauriPresenceWindowPort(): PresenceWindowPort {
  const current = getCurrentWindow();
  return {
    async monitors() {
      const [available, primary] = await Promise.all([
        availableMonitors(),
        primaryMonitor(),
      ]);
      return available.map((monitor) => {
        const workArea = monitor.workArea;
        return {
          id: monitorId(monitor.name, workArea.position, workArea.size),
          x: workArea.position.x,
          y: workArea.position.y,
          width: workArea.size.width,
          height: workArea.size.height,
          scale_factor: monitor.scaleFactor,
          is_primary:
            primary !== null &&
            primary.position.x === monitor.position.x &&
            primary.position.y === monitor.position.y &&
            primary.size.width === monitor.size.width &&
            primary.size.height === monitor.size.height,
        };
      });
    },
    async position() {
      const value = await current.outerPosition();
      return { x: value.x, y: value.y };
    },
    async size() {
      const value = await current.outerSize();
      return { width: value.width, height: value.height };
    },
    async setPosition(position) {
      await current.setPosition(
        new PhysicalPosition(Math.round(position.x), Math.round(position.y)),
      );
    },
    async startDragging() {
      await current.startDragging();
    },
    async onMoved(listener) {
      return current.onMoved(({ payload }) => listener({ x: payload.x, y: payload.y }));
    },
  };
}

export function createDefaultPresenceWindowPort(): PresenceWindowPort {
  if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
    return createTauriPresenceWindowPort();
  }
  return createBrowserPresenceWindowPort();
}

function createBrowserPresenceWindowPort(): PresenceWindowPort {
  const frame = (): MonitorFrame => ({
    id: "browser-preview",
    x: 0,
    y: 0,
    width: Math.max(1, window.screen?.availWidth ?? window.innerWidth),
    height: Math.max(1, window.screen?.availHeight ?? window.innerHeight),
    scale_factor: window.devicePixelRatio || 1,
    is_primary: true,
  });
  return {
    async monitors() {
      return [frame()];
    },
    async position() {
      return { x: window.screenX || 0, y: window.screenY || 0 };
    },
    async size() {
      return {
        width: Math.max(1, window.outerWidth || window.innerWidth),
        height: Math.max(1, window.outerHeight || window.innerHeight),
      };
    },
    async setPosition() {
      // Browser previews cannot move their containing window.
    },
    async startDragging() {
      // Native dragging exists only in the capability-scoped Tauri window.
    },
    async onMoved() {
      return () => undefined;
    },
  };
}

function monitorId(
  name: string | null,
  position: { x: number; y: number },
  size: { width: number; height: number },
): string {
  return `${name ?? "monitor"}:${position.x}:${position.y}:${size.width}:${size.height}`;
}

function distanceToMonitor(point: Point, monitor: MonitorFrame): number {
  const x = clamp(point.x, monitor.x, monitor.x + monitor.width);
  const y = clamp(point.y, monitor.y, monitor.y + monitor.height);
  return Math.hypot(point.x - x, point.y - y);
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
