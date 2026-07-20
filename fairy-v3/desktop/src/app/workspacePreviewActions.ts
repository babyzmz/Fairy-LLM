import type { Preview } from "../core/client";

type RestartablePreview = Pick<Preview, "status" | "idempotency_key">;

export function previewStartIdempotencyKey(
  taskId: string,
  preview: RestartablePreview | null | undefined,
): string {
  if (preview?.status === "failed" || preview?.status === "interrupted") {
    return preview.idempotency_key;
  }
  return `desktop:preview:${taskId}`;
}
