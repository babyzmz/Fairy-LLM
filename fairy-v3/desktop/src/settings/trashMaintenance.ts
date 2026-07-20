import type { DesktopPreferences } from "./client";

const lastAttemptKey = "fairy.trash.auto-purge.last-attempt";
const lastFailureKey = "fairy.trash.auto-purge.last-failure";
const oneDayMilliseconds = 24 * 60 * 60 * 1_000;
const retentionMilliseconds = 30 * oneDayMilliseconds;

interface TrashPurgeClient {
  purgeAll(input: {
    user_confirmed: true;
    deleted_before: string;
    maintenance: true;
  }): Promise<unknown>;
}

export interface TrashMaintenanceResult {
  attempted: boolean;
  failed: boolean;
}

export async function runAutomaticTrashMaintenance(
  preferences: Pick<DesktopPreferences, "trash_auto_purge_30_days">,
  trash: TrashPurgeClient | undefined,
  storage: Storage = window.localStorage,
  now = Date.now(),
): Promise<TrashMaintenanceResult> {
  if (!preferences.trash_auto_purge_30_days || trash === undefined) {
    return { attempted: false, failed: false };
  }
  const lastAttempt = Number(storage.getItem(lastAttemptKey));
  if (Number.isFinite(lastAttempt) && now - lastAttempt < oneDayMilliseconds) {
    return { attempted: false, failed: automaticTrashMaintenanceFailed(storage) };
  }

  storage.setItem(lastAttemptKey, String(now));
  try {
    await trash.purgeAll({
      user_confirmed: true,
      deleted_before: new Date(now - retentionMilliseconds).toISOString(),
      maintenance: true,
    });
    markTrashMaintenanceSucceeded(storage);
    return { attempted: true, failed: false };
  } catch {
    storage.setItem(lastFailureKey, "true");
    return { attempted: true, failed: true };
  }
}

export function automaticTrashMaintenanceFailed(
  storage: Storage = window.localStorage,
): boolean {
  return storage.getItem(lastFailureKey) === "true";
}

export function markTrashMaintenanceSucceeded(
  storage: Storage = window.localStorage,
): void {
  storage.removeItem(lastFailureKey);
}
