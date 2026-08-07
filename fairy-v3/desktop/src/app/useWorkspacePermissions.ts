import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import type { CapabilityManifest } from "../core/client";
import type { PermissionProfile, WorkspaceClient } from "./workspaceTypes";
import { equalOverrides, permissionUpdateKey } from "./workspaceCommandKeys";
import {
  capabilityQueryKey,
  coreErrorCode,
  permissionQueryKey,
} from "./workspaceModelUtils";

type PermissionSettings = Awaited<ReturnType<WorkspaceClient["permissions"]["get"]>>;
type RunAction = <T>(operation: () => Promise<T>) => Promise<T>;

export function useWorkspacePermissions({
  client,
  current,
  capabilities,
  runAction,
  onConflict,
}: {
  client: WorkspaceClient;
  current: PermissionSettings | undefined;
  capabilities: CapabilityManifest | undefined;
  runAction: RunAction;
  onConflict(message: string, code: string): void;
}) {
  const queryClient = useQueryClient();
  const persist = useCallback(
    async (profile: PermissionProfile, overrides: Record<string, boolean>): Promise<void> => {
      if (current === undefined) throw new Error("Core permission settings are unavailable");
      if (current.profile === profile && equalOverrides(current.capability_overrides, overrides)) {
        return;
      }
      try {
        const updated = await runAction(() => client.permissions.update({
          profile,
          capability_overrides: overrides,
          expected_revision: current.revision,
          idempotency_key: permissionUpdateKey(current.revision, profile, overrides),
        }));
        queryClient.setQueryData(permissionQueryKey, updated);
      } catch (error) {
        if (coreErrorCode(error) !== "VERSION_CONFLICT") throw error;
        await Promise.allSettled([
          queryClient.refetchQueries({ queryKey: permissionQueryKey, exact: true }),
          queryClient.invalidateQueries({ queryKey: capabilityQueryKey }),
        ]);
        const message = "Permissions changed on another device. Latest settings loaded; review and retry.";
        onConflict(message, "PERMISSION_CONFLICT");
        throw new Error(message);
      }
    },
    [client.permissions, current, onConflict, queryClient, runAction],
  );
  const setPermissionProfile = useCallback(async (profile: PermissionProfile) => {
    if (current === undefined) throw new Error("Core permission settings are unavailable");
    await persist(profile, current.capability_overrides);
  }, [current, persist]);
  const setCapabilityEnabled = useCallback(async (name: string, enabled: boolean) => {
    const known = capabilities?.command_metadata.some(
      (definition) => definition.name === name && definition.model_visible,
    );
    if (current === undefined || !known) {
      throw new Error("Core capability metadata is unavailable");
    }
    const overrides = { ...current.capability_overrides };
    if (enabled) delete overrides[name];
    else overrides[name] = false;
    await persist(current.profile, overrides);
  }, [capabilities?.command_metadata, current, persist]);
  return { setPermissionProfile, setCapabilityEnabled };
}
