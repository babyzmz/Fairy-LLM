import { describe, expect, it, vi } from "vitest";

import {
  automaticTrashMaintenanceFailed,
  markTrashMaintenanceSucceeded,
  runAutomaticTrashMaintenance,
} from "./trashMaintenance";

describe("trash maintenance", () => {
  it("purges eligible content at most once per day", async () => {
    const storage = new MemoryStorage();
    const purgeAll = vi.fn(async () => ({}));
    const now = Date.parse("2026-07-17T10:00:00Z");

    const first = await runAutomaticTrashMaintenance(
      { trash_auto_purge_30_days: true },
      { purgeAll },
      storage,
      now,
    );
    const second = await runAutomaticTrashMaintenance(
      { trash_auto_purge_30_days: true },
      { purgeAll },
      storage,
      now + 60_000,
    );

    expect(first).toEqual({ attempted: true, failed: false });
    expect(second).toEqual({ attempted: false, failed: false });
    expect(purgeAll).toHaveBeenCalledOnce();
    expect(purgeAll).toHaveBeenCalledWith({
      user_confirmed: true,
      deleted_before: "2026-06-17T10:00:00.000Z",
      maintenance: true,
    });
  });

  it("records a failed attempt without retrying automatically the same day", async () => {
    const storage = new MemoryStorage();
    const purgeAll = vi.fn(async () => { throw new Error("offline"); });
    const now = Date.parse("2026-07-17T10:00:00Z");

    expect(await runAutomaticTrashMaintenance(
      { trash_auto_purge_30_days: true },
      { purgeAll },
      storage,
      now,
    )).toEqual({ attempted: true, failed: true });
    expect(await runAutomaticTrashMaintenance(
      { trash_auto_purge_30_days: true },
      { purgeAll },
      storage,
      now + 60_000,
    )).toEqual({ attempted: false, failed: true });
    expect(purgeAll).toHaveBeenCalledOnce();
    expect(automaticTrashMaintenanceFailed(storage)).toBe(true);

    markTrashMaintenanceSucceeded(storage);
    expect(automaticTrashMaintenanceFailed(storage)).toBe(false);
  });
});

class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, value); }
}
