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
import { LiquidMotionController } from "./liquidMotion";
import { liquidVisualStyleForSnapshot } from "./liquidVisualState";

export class ThreeLiquidRenderer implements PresenceRenderer {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly camera: THREE.OrthographicCamera;
  private readonly scene: THREE.Scene;
  private readonly geometry: THREE.PlaneGeometry;
  private readonly material: THREE.ShaderMaterial;
  private readonly motion: LiquidMotionController;
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
    this.motion = new LiquidMotionController(
      liquidShapeTargetForPhase(initialSnapshot.interaction?.phase ?? null),
      initialSnapshot.speaking ? initialSnapshot.voice_level : 0,
    );
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
        uAccent: { value: new THREE.Vector3(0.38, 0.75, 0.9) },
        uTime: { value: 0 },
        uEnergy: { value: 0.35 },
        uDpr: { value: 1 },
        uParticleCount: { value: 10 },
        uParticleSeed: { value: 23 },
        uPulseSpeed: { value: 0.42 },
        uSpeechLevel: { value: 0 },
        uSizeScale: { value: initialSnapshot.size_scale },
        uOpacity: { value: initialSnapshot.opacity },
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
    this.motion.resetClock();
    this.loop.start();
  }

  stop(): void {
    this.loop.stop();
  }

  suspend(): void {
    this.stop();
    this.motion.resetClock();
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
    const motion = this.motion.sample(now);
    this.material.uniforms.uShape.value.set(
      motion.droplet,
      motion.bridge,
      motion.capsule,
    );
    this.material.uniforms.uSpeechLevel.value = motion.speech_level;
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
    this.motion.setShapeTarget(shape, this.snapshot.reduced_motion);
    this.motion.setSpeechTarget(
      this.snapshot.speaking ? Math.max(0.2, this.snapshot.voice_level) : 0,
      this.snapshot.reduced_motion,
    );
    const style = liquidVisualStyleForSnapshot(this.snapshot);
    this.material.uniforms.uAccent.value.set(...style.accent);
    this.material.uniforms.uEnergy.value = style.energy;
    this.material.uniforms.uParticleSeed.value = style.particle_seed;
    this.material.uniforms.uPulseSpeed.value = style.pulse_speed;
    this.material.uniforms.uSizeScale.value = this.snapshot.size_scale;
    this.material.uniforms.uOpacity.value = this.snapshot.opacity;
    this.material.uniforms.uParticleCount.value = this.snapshot.particles_enabled
      ? style.particle_count
      : 0;
    const sizeShift = Math.max(0, 72 * this.snapshot.size_scale + 4 - 96);
    const expansionSign = interaction?.placement.expansion_direction === "left" ? -1 : 1;
    if (interaction === null) {
      this.material.uniforms.uAnchor.value.set(
        (96 + sizeShift) * this.dpr,
        130 * this.dpr,
      );
      return;
    }
    const localX = interaction.placement.anchor.x - interaction.placement.render_frame.x;
    const localY = interaction.placement.anchor.y - interaction.placement.render_frame.y;
    this.material.uniforms.uAnchor.value.set(
      (localX + sizeShift * expansionSign) * this.dpr,
      this.height * this.dpr - localY * this.dpr,
    );
  }
}
