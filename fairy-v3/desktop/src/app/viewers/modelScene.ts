import type { Object3D } from "three";

export interface SceneNodeItem {
  path: string;
  label: string;
  type: string;
  depth: number;
  object: Object3D;
}

export function rewriteGltfResources(
  source: string,
  primaryPath: string,
  resources: Readonly<Record<string, string>>,
): string {
  const document = JSON.parse(source) as Record<string, unknown>;
  for (const collectionName of ["buffers", "images"] as const) {
    const collection = document[collectionName];
    if (collection === undefined) continue;
    if (!Array.isArray(collection)) throw new Error(`glTF ${collectionName} must be an array`);
    for (const entry of collection) {
      if (!isRecord(entry) || typeof entry.uri !== "string") continue;
      if (entry.uri.startsWith("data:")) continue;
      const dependency = resolveDependencyPath(primaryPath, entry.uri);
      const streamUrl = resources[dependency];
      if (streamUrl === undefined) throw new Error(`glTF dependency is not in the FileSet: ${dependency}`);
      entry.uri = streamUrl;
    }
  }
  return JSON.stringify(document);
}

export function sceneNodes(root: Object3D): SceneNodeItem[] {
  const items: SceneNodeItem[] = [];
  const visit = (object: Object3D, path: string, depth: number) => {
    items.push({
      path,
      label: object.name.trim() || `${object.type} ${path}`,
      type: object.type,
      depth,
      object,
    });
    object.children.forEach((child, index) => visit(child, `${path}/${index}`, depth + 1));
  };
  root.children.forEach((child, index) => visit(child, String(index), 0));
  return items;
}

function resolveDependencyPath(primaryPath: string, uri: string): string {
  if (/^[a-z][a-z0-9+.-]*:/i.test(uri) || uri.startsWith("//") || uri.includes("?") || uri.includes("#")) {
    throw new Error("glTF external or ambiguous resource URI was blocked");
  }
  let decoded: string;
  try {
    decoded = decodeURIComponent(uri.replaceAll("\\", "/"));
  } catch {
    throw new Error("glTF resource URI is invalid");
  }
  const segments = [...primaryPath.split("/").slice(0, -1), ...decoded.split("/")];
  const normalized: string[] = [];
  for (const segment of segments) {
    if (segment === "" || segment === ".") continue;
    if (segment === "..") {
      if (normalized.length === 0) throw new Error("glTF resource escapes the Workspace FileSet");
      normalized.pop();
      continue;
    }
    normalized.push(segment);
  }
  return normalized.join("/");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
