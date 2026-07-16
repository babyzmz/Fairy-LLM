import * as THREE from "three";

import type { PresenceExperimentMode } from "../diagnostics/experimentMode";
import {
  PRESENCE_RUNTIME_METRICS_RESET_EVENT,
  PresenceRuntimeMetrics,
  writeRuntimeMetricsDataset,
} from "../diagnostics/PresenceRuntimeMetrics";
import {
  rendererFrameInterval,
  type PresenceRenderer,
  type PresenceRenderSnapshot,
} from "./presenceRenderer";
import { RendererFrameLoop } from "./RendererFrameLoop";
import {
  GPU_TIMER_SAMPLE_LIMIT,
  readPerformanceHeapBytes,
  RendererPerformanceSampler,
  shouldSampleGpuFrame,
  WebGlGpuTimer,
  writePerformanceDataset,
} from "./RendererPerformanceSampler";
import {
  LIQUID_GLASS_FRAGMENT_SHADER,
  LIQUID_GLASS_VERTEX_SHADER,
  liquidDirectionForSnapshot,
  liquidShapeTargetForPhase,
} from "./liquidGlassMaterial";
import { LiquidMotionController } from "./liquidMotion";
import { liquidOpticsForSnapshot } from "./liquidOptics";
import { liquidVisualStyleForSnapshot } from "./liquidVisualState";
import {
  NativeBackdropStream,
  type NativeBackdropFrame,
} from "./nativeBackdrop";

export class ThreeLiquidRenderer implements PresenceRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly camera: THREE.OrthographicCamera;
  private readonly scene: THREE.Scene;
  private readonly geometry: THREE.PlaneGeometry;
  private readonly material: THREE.ShaderMaterial;
  private readonly backdropTexture: THREE.DataTexture;
  private readonly backdropStream = new NativeBackdropStream();
  private readonly motion: LiquidMotionController;
  private snapshot: PresenceRenderSnapshot;
  private readonly loop: RendererFrameLoop;
  private readonly performanceSampler = new RendererPerformanceSampler();
  private readonly gpuTimer: WebGlGpuTimer;
  private readonly runtimeMetrics = new PresenceRuntimeMetrics();
  private renderedFrames = 0;
  private hasRendered = false;
  private gpuTimerSamplesStarted = 0;
  private performanceSamplingComplete = false;
  private interactionPhase: NonNullable<
    PresenceRenderSnapshot["interaction"]
  >["phase"] | null;
  private disposed = false;
  private backdropRunning = false;
  private width = 1;
  private height = 1;
  private dpr = 1;
  private readonly resetRuntimeMetrics = () => {
    this.runtimeMetrics.reset();
    writeRuntimeMetricsDataset(this.canvas, this.runtimeMetrics.snapshot());
  };

  constructor(
    canvas: HTMLCanvasElement,
    initialSnapshot: PresenceRenderSnapshot,
    private readonly experimentMode: PresenceExperimentMode = "normal",
  ) {
    this.canvas = canvas;
    this.canvas.dataset.experimentMode = experimentMode;
    this.snapshot = initialSnapshot;
    this.interactionPhase = initialSnapshot.interaction?.phase ?? null;
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
    this.gpuTimer = new WebGlGpuTimer(
      this.renderer.getContext() as WebGL2RenderingContext,
    );
    this.renderer.autoClear = true;
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    this.scene = new THREE.Scene();
    this.geometry = new THREE.PlaneGeometry(2, 2);
    this.backdropTexture = new THREE.DataTexture(
      new Uint8Array([0, 0, 0, 0]),
      1,
      1,
      THREE.RGBAFormat,
      THREE.UnsignedByteType,
    );
    this.backdropTexture.colorSpace = THREE.SRGBColorSpace;
    this.backdropTexture.generateMipmaps = false;
    this.backdropTexture.minFilter = THREE.LinearFilter;
    this.backdropTexture.magFilter = THREE.LinearFilter;
    this.backdropTexture.wrapS = THREE.ClampToEdgeWrapping;
    this.backdropTexture.wrapT = THREE.ClampToEdgeWrapping;
    this.backdropTexture.needsUpdate = true;
    const initialOptics = liquidOpticsForSnapshot(initialSnapshot, 1, 1, 1);
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
        uRenderOrigin: { value: new THREE.Vector2(...initialOptics.render_origin) },
        uMonitorOrigin: { value: new THREE.Vector2(...initialOptics.monitor_origin) },
        uMonitorSize: { value: new THREE.Vector2(...initialOptics.monitor_size) },
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
        uReturnBounce: { value: 0 },
        uRefractionPx: { value: initialOptics.refraction_px },
        uDispersionPx: { value: initialOptics.dispersion_px },
        uCausticStrength: { value: initialOptics.caustic_strength },
        uLensStrength: { value: initialOptics.lens_strength },
        uRimStrength: { value: initialOptics.rim_strength },
        uShadowStrength: { value: initialOptics.shadow_strength },
        uBackdropTexture: { value: this.backdropTexture },
        uBackdropSize: { value: new THREE.Vector2(1, 1) },
        uBackdropReady: { value: 0 },
      },
    });
    this.scene.add(new THREE.Mesh(this.geometry, this.material));
    this.renderer.compile(this.scene, this.camera);
    this.loop = new RendererFrameLoop(
      (now) => this.drawFrame(now),
      () => rendererFrameInterval(this.snapshot),
    );
    if (import.meta.env.DEV) {
      window.addEventListener(
        PRESENCE_RUNTIME_METRICS_RESET_EVENT,
        this.resetRuntimeMetrics,
      );
    }
  }

  start(): void {
    if (this.disposed) return;
    this.motion.resetClock();
    if (this.canCaptureBackdrop()) this.startBackdrop();
    this.loop.start();
  }

  stop(): void {
    this.loop.stop();
    this.stopBackdrop();
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
    const couldCaptureBackdrop = this.canCaptureBackdrop();
    this.snapshot = snapshot;
    this.backdropStream.setFrameRate(backdropFrameRate(snapshot));
    const canCaptureBackdrop = this.canCaptureBackdrop();
    if (!canCaptureBackdrop) this.stopBackdrop();
    else if (!couldCaptureBackdrop && this.loop.running) this.startBackdrop();
    this.updateSnapshotUniforms();
    this.loop.refresh();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.stopBackdrop();
    this.loop.dispose();
    this.geometry.dispose();
    this.material.dispose();
    this.backdropTexture.dispose();
    this.renderer.dispose();
    this.gpuTimer.dispose();
    if (import.meta.env.DEV) {
      window.removeEventListener(
        PRESENCE_RUNTIME_METRICS_RESET_EVENT,
        this.resetRuntimeMetrics,
      );
    }
  }

  private startBackdrop() {
    if (this.backdropRunning || this.disposed) return;
    this.backdropRunning = true;
    this.canvas.dataset.backdropStatus = "starting";
    this.backdropStream.start({
      framesPerSecond: backdropFrameRate(this.snapshot),
      experimentMode: this.experimentMode,
      onFrame: (frame) => this.acceptBackdropFrame(frame),
      onError: (error) => {
        if (!this.backdropRunning) return;
        this.canvas.dataset.backdropStatus = "unavailable";
        this.canvas.dataset.backdropError = safeBackdropError(error);
        this.material.uniforms.uBackdropReady.value = 0;
      },
    });
  }

  private canCaptureBackdrop(): boolean {
    return this.snapshot.interaction !== null
      && this.snapshot.interaction.phase !== "repositioning";
  }

  private stopBackdrop() {
    if (!this.backdropRunning) return;
    this.backdropRunning = false;
    this.backdropStream.stop();
    this.material.uniforms.uBackdropReady.value = 0;
    this.canvas.dataset.backdropStatus = "stopped";
  }

  private acceptBackdropFrame(frame: NativeBackdropFrame) {
    if (!this.backdropRunning || this.disposed) return;
    this.runtimeMetrics.recordBackdrop({
      sequence: frame.sequence,
      captured_at_ms: frame.capturedAtMs,
      capture_total_ms: frame.captureTotalMs,
      frame_pack_ms: frame.framePackMs,
      ipc_roundtrip_ms: frame.ipcRoundtripMs,
      js_parse_ms: frame.jsParseMs,
    });
    const uploadStartedAt = performance.now();
    this.backdropTexture.image = {
      data: frame.rgba,
      width: frame.width,
      height: frame.height,
    };
    this.backdropTexture.needsUpdate = true;
    this.runtimeMetrics.recordTextureUploadCpu(performance.now() - uploadStartedAt);
    this.material.uniforms.uBackdropSize.value.set(frame.width, frame.height);
    this.material.uniforms.uBackdropReady.value = 1;
    this.canvas.dataset.backdropStatus = "ready";
    this.canvas.dataset.backdropSequence = String(frame.sequence);
    this.canvas.dataset.backdropWidth = String(frame.width);
    this.canvas.dataset.backdropHeight = String(frame.height);
    this.canvas.dataset.backdropAgeMs = String(
      Math.max(0, Date.now() - frame.capturedAtMs),
    );
    delete this.canvas.dataset.backdropError;
  }

  private drawFrame(now: number) {
    const cpuStartedAt = performance.now();
    const frameInterval = rendererFrameInterval(this.snapshot);
    if (Number.isFinite(frameInterval)) {
      this.runtimeMetrics.recordAnimationFrame(now, 1_000 / frameInterval);
    }
    const motion = this.motion.sample(now);
    this.material.uniforms.uShape.value.set(
      motion.droplet,
      motion.bridge,
      motion.capsule,
    );
    this.material.uniforms.uSpeechLevel.value = motion.speech_level;
    this.material.uniforms.uReturnBounce.value = motion.return_bounce;
    this.material.uniforms.uTime.value = this.snapshot.reduced_motion ? 0 : now / 1_000;
    if (!this.performanceSamplingComplete && this.gpuTimer.hasPendingResults()) {
      this.recordCompletedGpuFrames();
    }
    let sampleGpuFrame = false;
    if (
      !this.performanceSamplingComplete &&
      this.gpuTimer.supported &&
      shouldSampleGpuFrame(this.renderedFrames, this.gpuTimerSamplesStarted)
    ) {
      sampleGpuFrame = this.gpuTimer.begin();
      if (sampleGpuFrame) this.gpuTimerSamplesStarted += 1;
    }
    this.renderer.render(this.scene, this.camera);
    if (sampleGpuFrame) this.gpuTimer.end();
    if (!this.performanceSamplingComplete) {
      this.performanceSampler.recordCpuFrame(performance.now() - cpuStartedAt);
    }
    this.renderedFrames += 1;
    if (!this.performanceSamplingComplete && this.renderedFrames % 60 === 0) {
      this.performanceSampler.recordHeap(readPerformanceHeapBytes());
      writePerformanceDataset(
        this.renderer.domElement,
        this.performanceSampler.snapshot(),
      );
      const gpuSamplingComplete =
        !this.gpuTimer.supported ||
        (this.gpuTimerSamplesStarted >= GPU_TIMER_SAMPLE_LIMIT &&
          !this.gpuTimer.hasPendingResults());
      this.performanceSamplingComplete =
        this.performanceSampler.cpuSamplingComplete && gpuSamplingComplete;
      this.renderer.domElement.dataset.timingComplete = String(
        this.performanceSamplingComplete,
      );
    }
    if (this.renderedFrames % 60 === 0) {
      writeRuntimeMetricsDataset(
        this.renderer.domElement,
        this.runtimeMetrics.snapshot(),
      );
    }
    if (!this.hasRendered) {
      this.hasRendered = true;
      this.renderer.domElement.dataset.rendered = "true";
    }
  }

  private recordCompletedGpuFrames() {
    for (const duration of this.gpuTimer.collect()) {
      this.performanceSampler.recordGpuFrame(duration);
    }
  }

  private updateSnapshotUniforms() {
    const interaction = this.snapshot.interaction;
    const optics = liquidOpticsForSnapshot(
      this.snapshot,
      this.width,
      this.height,
      this.dpr,
    );
    this.material.uniforms.uRenderOrigin.value.set(...optics.render_origin);
    this.material.uniforms.uMonitorOrigin.value.set(...optics.monitor_origin);
    this.material.uniforms.uMonitorSize.value.set(...optics.monitor_size);
    this.material.uniforms.uRefractionPx.value = optics.refraction_px;
    this.material.uniforms.uDispersionPx.value = optics.dispersion_px;
    this.material.uniforms.uCausticStrength.value = optics.caustic_strength;
    this.material.uniforms.uLensStrength.value = optics.lens_strength;
    this.material.uniforms.uRimStrength.value = optics.rim_strength;
    this.material.uniforms.uShadowStrength.value = optics.shadow_strength;
    if (this.experimentMode === "no-refraction") {
      this.material.uniforms.uRefractionPx.value = 0;
      this.material.uniforms.uDispersionPx.value = 0;
      this.material.uniforms.uCausticStrength.value = 0;
      this.material.uniforms.uLensStrength.value = 0;
    }
    const gaze = interaction?.cursor.direction ?? { x: 0, y: 0 };
    this.material.uniforms.uGaze.value.set(gaze.x, -gaze.y);
    const direction = liquidDirectionForSnapshot(this.snapshot);
    this.material.uniforms.uDirection.value.set(direction.x, -direction.y);
    const shape = liquidShapeTargetForPhase(interaction?.phase ?? null);
    const nextPhase = interaction?.phase ?? null;
    if (nextPhase === "returning" && this.interactionPhase !== "returning") {
      this.motion.beginReturn(performance.now(), this.snapshot.reduced_motion);
    } else if (nextPhase !== "returning") {
      this.motion.cancelReturn(performance.now());
      this.motion.setShapeTarget(shape, this.snapshot.reduced_motion);
    }
    this.interactionPhase = nextPhase;
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

function backdropFrameRate(snapshot: PresenceRenderSnapshot): number {
  const active =
    snapshot.speaking ||
    snapshot.work_state !== "idle" ||
    (snapshot.interaction !== null &&
      snapshot.interaction.cursor.band !== "outside" &&
      !["idle", "suspended"].includes(snapshot.interaction.phase));
  return Math.min(snapshot.frame_rate_limit, active ? 30 : 15);
}

function safeBackdropError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.slice(0, 96);
}
