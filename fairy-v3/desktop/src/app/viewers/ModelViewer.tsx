import { Box, Grid3X3, Maximize2, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  AmbientLight,
  Box3,
  BoxHelper,
  Color,
  DirectionalLight,
  GridHelper,
  Material,
  Mesh,
  Object3D,
  PerspectiveCamera,
  Raycaster,
  Scene,
  SRGBColorSpace,
  Sphere,
  Texture,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

import { rewriteGltfResources, sceneNodes, type SceneNodeItem } from "./modelScene";
import "./model-viewer.css";

interface ModelViewerProps {
  path: string;
  mediaType: string;
  primaryUrl: string;
  sourceText: string | null;
  resources: Readonly<Record<string, string>>;
  onSelectNode(path: string, label: string): void;
}

interface SceneMetrics {
  objects: number;
  meshes: number;
  triangles: number;
}

export default function ModelViewer(props: ModelViewerProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [nodes, setNodes] = useState<SceneNodeItem[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<SceneMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showGrid, setShowGrid] = useState(true);
  const [generation, setGeneration] = useState(0);
  const controlsRef = useRef<
    { fit(): void; reset(): void; setGrid(visible: boolean): void; select(path: string): void } | undefined
  >(undefined);

  useEffect(() => {
    const host = hostRef.current;
    if (host === null) return;
    setError(null);
    setNodes([]);
    setSelectedPath(null);
    setMetrics(null);

    const scene = new Scene();
    scene.background = new Color(0x171a18);
    const camera = new PerspectiveCamera(45, 1, 0.01, 100_000);
    camera.position.set(3, 2, 4);
    const renderer = new WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = SRGBColorSpace;
    renderer.domElement.setAttribute("aria-label", `3D viewport: ${props.path}`);
    host.append(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    const grid = new GridHelper(20, 20, 0x53615a, 0x303632);
    scene.add(grid, new AmbientLight(0xffffff, 1.4));
    const key = new DirectionalLight(0xffffff, 2.6);
    key.position.set(4, 8, 6);
    scene.add(key);
    let model: Object3D | null = null;
    let selectionHelper: BoxHelper | null = null;
    let disposed = false;
    let nodeItems: SceneNodeItem[] = [];

    const resize = () => {
      const width = Math.max(host.clientWidth, 1);
      const height = Math.max(host.clientHeight, 1);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
    };
    const fit = () => {
      const currentModel = model;
      if (currentModel === null) return;
      const bounds = new Box3().setFromObject(currentModel);
      if (bounds.isEmpty()) return;
      const sphere = bounds.getBoundingSphere(new Sphere());
      const distance = Math.max(sphere.radius * 2.8, 0.5);
      const direction = new Vector3(1, 0.7, 1).normalize();
      controls.target.copy(sphere.center);
      camera.position.copy(sphere.center).addScaledVector(direction, distance);
      camera.near = Math.max(distance / 10_000, 0.001);
      camera.far = Math.max(distance * 100, 100);
      camera.updateProjectionMatrix();
      controls.update();
    };
    const select = (path: string) => {
      const item = nodeItems.find((candidate) => candidate.path === path);
      if (item === undefined) return;
      if (selectionHelper !== null) scene.remove(selectionHelper);
      selectionHelper = new BoxHelper(item.object, 0x65d6cb);
      scene.add(selectionHelper);
      setSelectedPath(path);
      props.onSelectNode(path, item.label);
    };
    controlsRef.current = {
      fit,
      reset: () => {
        controls.reset();
        fit();
      },
      setGrid: (visible) => {
        grid.visible = visible;
      },
      select,
    };
    const raycaster = new Raycaster();
    const pointer = new Vector2();
    const onPointerDown = (event: PointerEvent) => {
      const currentModel = model;
      if (currentModel === null) return;
      const bounds = renderer.domElement.getBoundingClientRect();
      pointer.set(((event.clientX - bounds.left) / bounds.width) * 2 - 1, -((event.clientY - bounds.top) / bounds.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObject(currentModel, true)[0]?.object;
      if (hit === undefined) return;
      const parent = nearestNamedParent(hit, currentModel);
      const item = nodeItems.find((candidate) => candidate.object === hit || candidate.object === parent);
      if (item !== undefined) select(item.path);
    };
    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
      setError("3D graphics context was lost");
    });
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();
    renderer.setAnimationLoop(() => {
      controls.update();
      selectionHelper?.update();
      renderer.render(scene, camera);
    });

    void loadModel(props)
      .then((loaded) => {
        if (disposed) {
          disposeObject(loaded.scene);
          return;
        }
        model = loaded.scene;
        scene.add(model);
        nodeItems = sceneNodes(model);
        setNodes(nodeItems);
        setMetrics(measureScene(model));
        fit();
      })
      .catch((reason: unknown) => {
        if (!disposed) setError(reason instanceof Error ? reason.message : "3D model could not be decoded");
      });

    return () => {
      disposed = true;
      controlsRef.current = undefined;
      observer.disconnect();
      renderer.setAnimationLoop(null);
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      controls.dispose();
      if (model !== null) disposeObject(model);
      selectionHelper?.geometry.dispose();
      grid.geometry.dispose();
      disposeMaterial(grid.material);
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    };
  }, [generation, props.mediaType, props.path, props.primaryUrl, props.resources, props.sourceText]);

  useEffect(() => controlsRef.current?.setGrid(showGrid), [showGrid]);
  const selected = nodes.find((item) => item.path === selectedPath) ?? null;
  return (
    <div className="model-viewer" aria-label={`3D model viewer: ${props.path}`}>
      <div className="model-toolbar">
        <button type="button" aria-label="Fit 3D model" onClick={() => controlsRef.current?.fit()}><Maximize2 size={15} /></button>
        <button type="button" aria-label="Reset 3D camera" onClick={() => controlsRef.current?.reset()}><RotateCcw size={15} /></button>
        <button type="button" aria-label="Toggle 3D grid" aria-pressed={showGrid} onClick={() => setShowGrid((value) => !value)}><Grid3X3 size={15} /></button>
        <i />
        <span>{metrics === null ? "Loading scene" : `${metrics.meshes} meshes · ${metrics.triangles.toLocaleString()} triangles`}</span>
      </div>
      <div className="model-layout">
        <nav className="model-tree" aria-label="3D object tree">
          <strong>Objects</strong>
          {nodes.map((node) => (
            <button key={node.path} type="button" className={node.path === selectedPath ? "selected" : undefined} style={{ paddingLeft: `${8 + node.depth * 12}px` }} title={node.label} onClick={() => controlsRef.current?.select(node.path)}>
              <Box size={13} /><span>{node.label}</span>
            </button>
          ))}
        </nav>
        <div ref={hostRef} className="model-canvas-host">
          {error !== null ? (
            <div className="model-error" role="alert"><span>{error}</span><button type="button" onClick={() => setGeneration((value) => value + 1)}>Retry</button></div>
          ) : null}
        </div>
        <aside className="model-properties" aria-label="3D object properties">
          <strong>Scene</strong>
          <dl>
            <dt>Objects</dt><dd>{metrics?.objects ?? 0}</dd>
            <dt>Meshes</dt><dd>{metrics?.meshes ?? 0}</dd>
            <dt>Triangles</dt><dd>{metrics?.triangles.toLocaleString() ?? 0}</dd>
          </dl>
          {selected !== null ? <><strong>Selected</strong><dl><dt>Name</dt><dd>{selected.label}</dd><dt>Type</dt><dd>{selected.type}</dd><dt>Path</dt><dd>{selected.path}</dd></dl></> : null}
        </aside>
      </div>
    </div>
  );
}

async function loadModel(props: ModelViewerProps) {
  const loader = new GLTFLoader();
  if (props.mediaType === "model/gltf+json") {
    if (props.sourceText === null) throw new Error("glTF manifest is unavailable");
    return loader.parseAsync(rewriteGltfResources(props.sourceText, props.path, props.resources), "");
  }
  const response = await fetch(props.primaryUrl, { cache: "no-store" });
  if (!response.ok) throw new Error("3D model stream could not be read");
  return loader.parseAsync(await response.arrayBuffer(), "");
}

function nearestNamedParent(object: Object3D, root: Object3D): Object3D {
  let current = object;
  while (current.parent !== null && current.parent !== root && !current.name) current = current.parent;
  return current;
}

function measureScene(root: Object3D): SceneMetrics {
  let objects = 0;
  let meshes = 0;
  let triangles = 0;
  root.traverse((object) => {
    objects += 1;
    if (!(object instanceof Mesh)) return;
    meshes += 1;
    const geometry = object.geometry;
    triangles += geometry.index === null ? geometry.attributes.position.count / 3 : geometry.index.count / 3;
  });
  return { objects, meshes, triangles: Math.floor(triangles) };
}

function disposeObject(root: Object3D): void {
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    object.geometry.dispose();
    if (Array.isArray(object.material)) object.material.forEach(disposeMaterial);
    else disposeMaterial(object.material);
  });
}

function disposeMaterial(material: Material): void {
  for (const value of Object.values(material)) if (value instanceof Texture) value.dispose();
  material.dispose();
}
