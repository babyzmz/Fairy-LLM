export const MAX_MODEL_MEMBERS = 256;
export const MAX_MODEL_SOURCE_BYTES = 128 * 1024 * 1024;
export const MAX_SCENE_OBJECTS = 25_000;
export const MAX_SCENE_MESHES = 10_000;
export const MAX_SCENE_TRIANGLES = 5_000_000;
export const MAX_IMAGE_PIXELS = 64_000_000;
export const MAX_PDF_CANVAS_PIXELS = 16_777_216;

interface ModelFileSetBudget {
  members: ReadonlyArray<{ byte_length: number }>;
}

interface ScenePreviewBudget {
  objects: number;
  meshes: number;
  triangles: number;
}

export function assertModelFileSetBudget(fileSet: ModelFileSetBudget): void {
  if (fileSet.members.length > MAX_MODEL_MEMBERS) {
    throw new Error(`3D model source exceeds the ${MAX_MODEL_MEMBERS} member limit`);
  }
  let totalBytes = 0;
  for (const member of fileSet.members) {
    assertNonNegativeInteger(member.byte_length, "3D model member byte length");
    totalBytes += member.byte_length;
    if (totalBytes > MAX_MODEL_SOURCE_BYTES) {
      throw new Error("3D model source exceeds the 128 MiB preview limit");
    }
  }
}

export function assertScenePreviewBudget(metrics: ScenePreviewBudget): void {
  assertNonNegativeInteger(metrics.objects, "3D scene object count");
  assertNonNegativeInteger(metrics.meshes, "3D scene mesh count");
  assertNonNegativeInteger(metrics.triangles, "3D scene triangle count");
  if (metrics.objects > MAX_SCENE_OBJECTS) {
    throw new Error(`3D scene exceeds the ${MAX_SCENE_OBJECTS.toLocaleString()} object limit`);
  }
  if (metrics.meshes > MAX_SCENE_MESHES) {
    throw new Error(`3D scene exceeds the ${MAX_SCENE_MESHES.toLocaleString()} mesh limit`);
  }
  if (metrics.triangles > MAX_SCENE_TRIANGLES) {
    throw new Error(`3D scene exceeds the ${MAX_SCENE_TRIANGLES.toLocaleString()} triangle limit`);
  }
}

export function assertImagePreviewBudget(width: number, height: number): void {
  assertPositiveDimension(width, "Image width");
  assertPositiveDimension(height, "Image height");
  if (width * height > MAX_IMAGE_PIXELS) {
    throw new Error("Image exceeds the 64 megapixels preview limit");
  }
}

export function pdfCanvasScale(width: number, height: number, preferredScale: number): number {
  assertPositiveDimension(width, "PDF page width");
  assertPositiveDimension(height, "PDF page height");
  const pagePixels = width * height;
  if (pagePixels > MAX_PDF_CANVAS_PIXELS) {
    throw new Error("PDF page dimensions exceed the preview limit");
  }
  const normalizedPreferred = Number.isFinite(preferredScale)
    ? Math.min(2, Math.max(1, preferredScale))
    : 1;
  const pixelBound = Math.sqrt(MAX_PDF_CANVAS_PIXELS / pagePixels);
  const quantizedBound = Math.max(1, Math.floor(pixelBound * 4) / 4);
  return Math.min(normalizedPreferred, quantizedBound);
}

function assertNonNegativeInteger(value: number, label: string): void {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${label} is invalid`);
  }
}

function assertPositiveDimension(value: number, label: string): void {
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${label} is invalid`);
  }
}
