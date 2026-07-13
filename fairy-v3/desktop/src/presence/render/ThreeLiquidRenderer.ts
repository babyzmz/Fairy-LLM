import * as THREE from "three";

import {
  rendererFrameInterval,
  type PresenceRenderer,
  type PresenceRenderSnapshot,
} from "./presenceRenderer";
import { RendererFrameLoop } from "./RendererFrameLoop";
import {
  LIQUID_GLASS_FRAGMENT_SHADER,
  LIQUID_GLASS_VERTEX_SHADER,
  liquidDirectionForSnapshot,
  liquidShapeTargetForPhase,
} from "./liquidGlassMaterial";

export class ThreeLiquidRenderer implements PresenceRenderer {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly camera: THREE.OrthographicCamera;
  private readonly scene: THREE.Scene;
  private readonly geometry: THREE.PlaneGeometry;
  private readonly material: THREE.ShaderMaterial;
  private snapshot: PresenceRenderSnapshot;
  private readonly loop: RendererFrameLoop;
  private disposed = false;
  private width = 1;
  private height = 1;
  private dpr = 1;

  constructor(
    canvas: HTMLCanvasElement,
    initialSnapshot: PresenceRenderSnapshot,
  ) {
    this.snapshot = initialSnapshot;
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
      premultipliedAlpha: true,
      powerPreference: "high-performance",
    });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.autoClear = true;
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    this.scene = new THREE.Scene();
    this.geometry = new THREE.PlaneGeometry(2, 2);
    this.material = new THREE.ShaderMaterial({
      vertexShader: LIQUID_GLASS_VERTEX_SHADER,
      fragmentShader: LIQUID_GLASS_FRAGMENT_SHADER,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      blending: THREE.NoBlending,
      premultipliedAlpha: true,
      uniforms: {
        uResolution: { value: new THREE.Vector2(1, 1) },
        uAnchor: { value: new THREE.Vector2(96, 130) },
        uDirection: { value: new THREE.Vector2(1, 0) },
        uGaze: { value: new THREE.Vector2(0, 0) },
        uShape: { value: new THREE.Vector3(0, 0, 0) },
        uTime: { value: 0 },
        uEnergy: { value: 0.35 },
        uDpr: { value: 1 },
      },
    });
    this.scene.add(new THREE.Mesh(this.geometry, this.material));
    this.renderer.compile(this.scene, this.camera);
    this.loop = new RendererFrameLoop(
      (now) => this.drawFrame(now),
      () => rendererFrameInterval(this.snapshot),
    );
  }

  start(): void {
    if (this.disposed) return;
    this.loop.start();
  }

  stop(): void {
    this.loop.stop();
  }

  suspend(): void {
    this.stop();
  }

  resize(width: number, height: number, devicePixelRatio: number): void {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    this.dpr = Math.min(2, Math.max(0.5, devicePixelRatio));
    this.renderer.setPixelRatio(this.dpr);
    this.renderer.setSize(this.width, this.height, false);
    this.material.uniforms.uResolution.value.set(
      this.width * this.dpr,
      this.height * this.dpr,
    );
    this.material.uniforms.uDpr.value = this.dpr;
    this.updateSnapshotUniforms();
    this.loop.drawImmediately();
  }

  setSnapshot(snapshot: PresenceRenderSnapshot): void {
    this.snapshot = snapshot;
    this.updateSnapshotUniforms();
    this.loop.refresh();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.loop.dispose();
    this.geometry.dispose();
    this.material.dispose();
    this.renderer.dispose();
  }

  private drawFrame(now: number) {
    this.material.uniforms.uTime.value = this.snapshot.reduced_motion ? 0 : now / 1_000;
    this.renderer.render(this.scene, this.camera);
    this.renderer.domElement.dataset.rendered = "true";
  }

  private updateSnapshotUniforms() {
    const interaction = this.snapshot.interaction;
    const gaze = interaction?.cursor.direction ?? { x: 0, y: 0 };
    this.material.uniforms.uGaze.value.set(gaze.x, -gaze.y);
    const direction = liquidDirectionForSnapshot(this.snapshot);
    this.material.uniforms.uDirection.value.set(direction.x, -direction.y);
    const shape = liquidShapeTargetForPhase(interaction?.phase ?? null);
    this.material.uniforms.uShape.value.set(
      shape.droplet,
      shape.bridge,
      shape.capsule,
    );
    const phase = interaction?.phase ?? "idle";
    this.material.uniforms.uEnergy.value = phase === "idle" ? 0.35 : 0.72;
    if (interaction === null) {
      this.material.uniforms.uAnchor.value.set(96 * this.dpr, 130 * this.dpr);
      return;
    }
    const localX = interaction.placement.anchor.x - interaction.placement.render_frame.x;
    const localY = interaction.placement.anchor.y - interaction.placement.render_frame.y;
    this.material.uniforms.uAnchor.value.set(localX, this.height * this.dpr - localY);
  }
}
