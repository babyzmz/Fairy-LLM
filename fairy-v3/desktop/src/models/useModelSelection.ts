import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ModelCatalogPage,
  ModelSelectionPreference,
  ModelSelectionUpdateInput,
} from "../core/client";
import type { WorkspaceClient } from "../app/workspaceTypes";

export const modelCatalogQueryKey = ["models", "catalog"] as const;
export const modelSelectionQueryKey = ["models", "selection"] as const;

export interface ModelSelectionController {
  catalog: ModelCatalogPage | null;
  selection: ModelSelectionPreference | null;
  loading: boolean;
  refreshing: boolean;
  error: Error | null;
  update(input: Pick<ModelSelectionUpdateInput, "mode" | "model_id">): Promise<void>;
  refresh(): Promise<void>;
}

export function useModelSelection(
  client: Pick<WorkspaceClient, "models">,
  enabled: boolean,
): ModelSelectionController {
  const queryClient = useQueryClient();
  const refreshed = useRef(false);
  const [refreshing, setRefreshing] = useState(false);
  const catalogQuery = useQuery({
    queryKey: modelCatalogQueryKey,
    queryFn: () => client.models.catalog.list(),
    enabled,
    retry: false,
  });
  const selectionQuery = useQuery({
    queryKey: modelSelectionQueryKey,
    queryFn: () => client.models.selection.get(),
    enabled,
    retry: false,
  });

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const page = await client.models.catalog.refresh();
      queryClient.setQueryData(modelCatalogQueryKey, page);
    } finally {
      setRefreshing(false);
    }
  }, [client.models.catalog, queryClient]);

  useEffect(() => {
    if (!enabled || refreshed.current || catalogQuery.data === undefined) return;
    refreshed.current = true;
    if (catalogQuery.data.stale || catalogQuery.data.revision === 0) {
      void refresh().catch(() => undefined);
    }
  }, [catalogQuery.data, enabled, refresh]);

  const update = useCallback(async (
    input: Pick<ModelSelectionUpdateInput, "mode" | "model_id">,
  ) => {
    const current = queryClient.getQueryData<ModelSelectionPreference>(modelSelectionQueryKey);
    if (current === undefined) throw new Error("Model selection is unavailable");
    const request: ModelSelectionUpdateInput = {
      mode: input.mode,
      model_id: input.model_id,
      allow_free_fallback: current.allow_free_fallback,
      zero_data_retention: current.zero_data_retention,
      expected_revision: current.revision,
      idempotency_key: selectionUpdateKey(current.revision, input),
    };
    try {
      const saved = await client.models.selection.update(request);
      queryClient.setQueryData(modelSelectionQueryKey, saved);
    } catch (error) {
      await queryClient.invalidateQueries({ queryKey: modelSelectionQueryKey });
      throw error;
    }
  }, [client.models.selection, queryClient]);

  return {
    catalog: catalogQuery.data ?? null,
    selection: selectionQuery.data ?? null,
    loading: catalogQuery.isPending || selectionQuery.isPending,
    refreshing,
    error: firstError(catalogQuery.error, selectionQuery.error),
    update,
    refresh,
  };
}

function selectionUpdateKey(
  revision: number,
  input: Pick<ModelSelectionUpdateInput, "mode" | "model_id">,
): string {
  const nonce = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}`;
  return `desktop:model-selection:${revision}:${input.mode}:${input.model_id ?? "auto"}:${nonce}`;
}

function firstError(...errors: Array<Error | null>): Error | null {
  return errors.find((error): error is Error => error !== null) ?? null;
}
