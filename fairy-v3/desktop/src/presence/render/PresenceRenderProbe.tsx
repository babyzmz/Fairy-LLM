import { useEffect, useRef, useState } from "react";

import {
  detectPresenceRendererCapability,
  resolvePresenceRendererMode,
  type PresenceRendererCapability,
} from "./rendererSupport";
import "./render-surface.css";

export function PresenceRenderProbe() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [capability, setCapability] = useState<PresenceRendererCapability | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const next = detectPresenceRendererCapability(canvas);
    setCapability(next);
    if (resolvePresenceRendererMode("auto", next) === "liquid") {
      drawProbe(canvas);
    }
  }, []);

  const mode = capability === null ? "probing" : resolvePresenceRendererMode("auto", capability);
  return (
    <main className="presence-render-surface" data-renderer={mode} data-testid="presence-render-surface">
      <canvas aria-label="Fairy WebGL renderer probe" ref={canvasRef} role="img" />
      {mode === "compatibility" ? <div aria-label="Compatibility renderer" className="probe-fallback" /> : null}
    </main>
  );
}

function drawProbe(canvas: HTMLCanvasElement): void {
  const ratio = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.max(1, Math.round(canvas.clientWidth * ratio));
  canvas.height = Math.max(1, Math.round(canvas.clientHeight * ratio));
  const gl = canvas.getContext("webgl2", {
    alpha: true,
    antialias: true,
    premultipliedAlpha: true,
    powerPreference: "high-performance",
  });
  if (gl === null) return;
  const program = createProgram(gl, VERTEX_SOURCE, FRAGMENT_SOURCE);
  const buffer = gl.createBuffer();
  if (buffer === null) throw new Error("WEBGL2_BUFFER_UNAVAILABLE");
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
    gl.STATIC_DRAW,
  );
  const position = gl.getAttribLocation(program, "aPosition");
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.useProgram(program);
  gl.uniform2f(gl.getUniformLocation(program, "uResolution"), canvas.width, canvas.height);
  gl.drawArrays(gl.TRIANGLES, 0, 6);
  const center = new Uint8Array(4);
  const corner = new Uint8Array(4);
  gl.readPixels(
    Math.floor(canvas.width / 2),
    Math.floor(canvas.height / 2),
    1,
    1,
    gl.RGBA,
    gl.UNSIGNED_BYTE,
    center,
  );
  gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, corner);
  canvas.dataset.centerAlpha = String(center[3]);
  canvas.dataset.cornerAlpha = String(corner[3]);
  gl.deleteBuffer(buffer);
  gl.deleteProgram(program);
  canvas.dataset.rendered = "true";
}

function createProgram(gl: WebGL2RenderingContext, vertexSource: string, fragmentSource: string) {
  const program = gl.createProgram();
  if (program === null) throw new Error("WEBGL2_PROGRAM_UNAVAILABLE");
  const vertex = compileShader(gl, gl.VERTEX_SHADER, vertexSource);
  const fragment = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const details = gl.getProgramInfoLog(program) ?? "unknown";
    gl.deleteProgram(program);
    throw new Error(`WEBGL2_PROGRAM_LINK_FAILED: ${details}`);
  }
  return program;
}

function compileShader(gl: WebGL2RenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (shader === null) throw new Error("WEBGL2_SHADER_UNAVAILABLE");
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const details = gl.getShaderInfoLog(shader) ?? "unknown";
    gl.deleteShader(shader);
    throw new Error(`WEBGL2_SHADER_COMPILE_FAILED: ${details}`);
  }
  return shader;
}

const VERTEX_SOURCE = `#version 300 es
in vec2 aPosition;
void main() {
  gl_Position = vec4(aPosition, 0.0, 1.0);
}`;

const FRAGMENT_SOURCE = `#version 300 es
precision highp float;
uniform vec2 uResolution;
out vec4 outColor;
void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * uResolution) / min(uResolution.x, uResolution.y);
  float distanceToRing = abs(length(p) - 0.235);
  float ring = 1.0 - smoothstep(0.012, 0.028, distanceToRing);
  float core = 1.0 - smoothstep(0.065, 0.085, length(p));
  float alpha = ring * 0.42 + core * 0.18;
  vec3 color = mix(vec3(0.93, 0.98, 1.0), vec3(0.38, 0.78, 0.98), core);
  outColor = vec4(color * alpha, alpha);
}`;
