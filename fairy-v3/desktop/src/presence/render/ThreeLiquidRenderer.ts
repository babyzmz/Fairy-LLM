import * as THREE from "three";

import {
  rendererFrameInterval,
  type PresenceRenderer,
  type PresenceRenderSnapshot,
} from "./presenceRenderer";
import { RendererFrameLoop } from "./RendererFrameLoop";

const vertexShader = `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`;

const fragmentShader = `
  precision highp float;
  uniform vec2 uResolution;
  uniform vec2 uAnchor;
  uniform vec2 uGaze;
  uniform float uTime;
  uniform float uEnergy;
  varying vec2 vUv;

  void main() {
    vec2 pixel = vec2(vUv.x * uResolution.x, vUv.y * uResolution.y);
    vec2 center = uAnchor + uGaze * 3.0;
    float distanceToCore = length(pixel - center);
    float breathe = sin(uTime * 1.35) * 1.4 * uEnergy;
    float radius = 72.0 + breathe;
    float shell = 1.0 - smoothstep(radius - 2.0, radius + 2.0, distanceToCore);
    float edge = smoothstep(radius - 16.0, radius - 1.0, distanceToCore) * shell;
    float inner = 1.0 - smoothstep(radius * 0.22, radius * 0.7, distanceToCore);
    float alpha = shell * 0.08 + edge * 0.24 + inner * 0.34;
    vec3 glass = mix(vec3(0.82, 0.93, 1.0), vec3(0.20, 0.72, 0.94), inner);
    glass += edge * vec3(0.30, 0.34, 0.38);
    gl_FragColor = vec4(glass * alpha, alpha);
  }
`;

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
      vertexShader,
      fragmentShader,
      transparent: true,
      depthTest: false,
      depthWrite: false,
      blending: THREE.NoBlending,
      premultipliedAlpha: true,
      uniforms: {
        uResolution: { value: new THREE.Vector2(1, 1) },
        uAnchor: { value: new THREE.Vector2(96, 130) },
        uGaze: { value: new THREE.Vector2(0, 0) },
        uTime: { value: 0 },
        uEnergy: { value: 0.35 },
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
