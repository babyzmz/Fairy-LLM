import { Group, Mesh, BoxGeometry, MeshBasicMaterial } from "three";
import { describe, expect, it } from "vitest";

import { rewriteGltfResources, sceneNodes } from "./modelScene";

describe("3D scene resources", () => {
  it("rewrites only dependencies resolved by the immutable FileSet", () => {
    const source = JSON.stringify({ buffers: [{ uri: "scene.bin" }], images: [{ uri: "textures/albedo.png" }] });
    const rewritten = JSON.parse(
      rewriteGltfResources(source, "models/scene.gltf", {
        "models/scene.bin": "http://127.0.0.1/buffer",
        "models/textures/albedo.png": "http://127.0.0.1/image",
      }),
    );
    expect(rewritten.buffers[0].uri).toBe("http://127.0.0.1/buffer");
    expect(rewritten.images[0].uri).toBe("http://127.0.0.1/image");
  });

  it.each(["https://example.test/model.bin", "../../outside.bin", "model.bin?token=forged"])(
    "blocks an unsafe URI: %s",
    (uri) => {
      expect(() =>
        rewriteGltfResources(JSON.stringify({ buffers: [{ uri }] }), "scene/model.gltf", {}),
      ).toThrow();
    },
  );

  it("builds stable index paths for the object tree", () => {
    const root = new Group();
    const assembly = new Group();
    assembly.name = "Assembly";
    assembly.add(new Mesh(new BoxGeometry(), new MeshBasicMaterial()));
    root.add(assembly);

    expect(sceneNodes(root).map(({ path, label }) => [path, label])).toEqual([
      ["0", "Assembly"],
      ["0/0", "Mesh 0/0"],
    ]);
  });
});
