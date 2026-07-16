import type { NativeBackdropFrame } from "../render/nativeBackdrop";

export const INPUT_GLASS_VERTEX_SHADER = `#version 300 es
  in vec2 aPosition;
  out vec2 vUv;

  void main() {
    vUv = aPosition * 0.5 + 0.5;
    gl_Position = vec4(aPosition, 0.0, 1.0);
  }
`;

export const INPUT_GLASS_FRAGMENT_SHADER = `#version 300 es
  precision highp float;

  uniform sampler2D uBackdrop;
  uniform vec2 uResolution;
  uniform float uDpr;
  uniform float uReady;
  in vec2 vUv;
  out vec4 outputColor;

  float saturate(float value) {
    return clamp(value, 0.0, 1.0);
  }

  float roundedRectDistance(vec2 pixel) {
    vec2 halfSize = uResolution * 0.5 - vec2(0.75 * uDpr);
    float radius = min(halfSize.y, 26.0 * uDpr);
    vec2 point = pixel - uResolution * 0.5;
    vec2 q = abs(point) - halfSize + vec2(radius);
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
  }

  vec2 surfaceNormal(vec2 pixel) {
    float stepSize = max(0.75, uDpr);
    float horizontal = roundedRectDistance(pixel + vec2(stepSize, 0.0))
      - roundedRectDistance(pixel - vec2(stepSize, 0.0));
    float vertical = roundedRectDistance(pixel + vec2(0.0, stepSize))
      - roundedRectDistance(pixel - vec2(0.0, stepSize));
    return normalize(vec2(horizontal, vertical) + vec2(0.0001));
  }

  vec3 sampleBackdrop(vec2 uv) {
    vec2 halfTexel = 0.5 / max(uResolution, vec2(1.0));
    return texture(uBackdrop, clamp(uv, halfTexel, vec2(1.0) - halfTexel)).rgb;
  }

  void main() {
    vec2 pixel = vUv * uResolution;
    float distanceField = roundedRectDistance(pixel);
    float antialias = max(fwidth(distanceField), 0.7 * uDpr);
    float coverage = 1.0 - smoothstep(-antialias, antialias, distanceField);
    if (coverage <= 0.001) discard;

    float interiorDepth = max(-distanceField, 0.0);
    float boundaryContinuity = smoothstep(0.0, 2.4 * uDpr, interiorDepth);
    float edgeLens = exp(-pow(
      (interiorDepth - 8.5 * uDpr) / max(1.0, 6.5 * uDpr),
      2.0
    )) * boundaryContinuity;
    vec2 normal = surfaceNormal(pixel);
    vec2 textureNormal = vec2(normal.x, -normal.y);
    vec2 directUv = vec2(vUv.x, 1.0 - vUv.y);
    vec2 normalizedPoint = (pixel - uResolution * 0.5)
      / max(uResolution * 0.5, vec2(1.0));
    float normalizedRadius = saturate(length(normalizedPoint));
    float bodyLens = boundaryContinuity
      * normalizedRadius
      * (1.0 - normalizedRadius)
      * 4.0;
    vec2 refraction = textureNormal
      * (5.8 * uDpr * edgeLens)
      / max(uResolution, vec2(1.0));
    refraction += normalizedPoint
      * (1.75 * uDpr * bodyLens)
      / max(uResolution, vec2(1.0));
    vec2 tangent = vec2(-textureNormal.y, textureNormal.x);
    vec2 dispersion = tangent
      * (1.35 * uDpr * edgeLens)
      / max(uResolution, vec2(1.0));

    vec3 direct = sampleBackdrop(directUv);
    vec3 redSample = sampleBackdrop(directUv + refraction + dispersion * 1.08);
    vec3 greenSample = sampleBackdrop(directUv + refraction);
    vec3 blueSample = sampleBackdrop(directUv + refraction - dispersion * 0.92);
    vec3 transmitted = vec3(redSample.r, greenSample.g, blueSample.b);
    vec3 glass = direct + (transmitted - direct) * (1.18 + edgeLens * 0.34);

    float normalizedDepth = saturate(interiorDepth / max(1.0, 22.0 * uDpr));
    float fresnel = pow(1.0 - normalizedDepth, 3.2) * edgeLens;
    float keyHighlight = pow(max(dot(normal, normalize(vec2(-0.58, 0.82))), 0.0), 18.0)
      * edgeLens;
    float fillHighlight = pow(max(dot(normal, normalize(vec2(0.72, -0.69))), 0.0), 24.0)
      * edgeLens;
    float caustic = exp(-pow(
      (interiorDepth - 12.0 * uDpr) / max(1.0, 3.4 * uDpr),
      2.0
    )) * pow(max(dot(normal, normalize(vec2(-0.72, 0.7))), 0.0), 1.4);
    float lowerShadow = exp(-interiorDepth / max(1.0, 2.8 * uDpr))
      * pow(max(dot(normal, vec2(0.0, -1.0)), 0.0), 1.5);

    glass = mix(glass, vec3(0.94, 0.985, 1.0), 0.025 + fresnel * 0.12);
    glass += vec3(1.0, 0.99, 0.95) * keyHighlight * 0.28;
    glass += vec3(0.68, 0.9, 1.0) * fillHighlight * 0.16;
    glass += vec3(1.0, 0.94, 0.79) * caustic * 0.22;
    glass *= 1.0 - lowerShadow * 0.12;

    float materialAlpha = 0.09
      + edgeLens * 0.7
      + bodyLens * 0.035
      + keyHighlight * 0.08
      + caustic * 0.12;
    float alpha = coverage * mix(0.1, min(materialAlpha, 0.88), uReady);
    outputColor = vec4(clamp(glass, 0.0, 1.0) * alpha, alpha);
  }
`;

export class InputLiquidGlassRenderer {
  private readonly gl: WebGL2RenderingContext;
  private readonly program: WebGLProgram;
  private readonly buffer: WebGLBuffer;
  private readonly texture: WebGLTexture;
  private readonly resolutionLocation: WebGLUniformLocation;
  private readonly dprLocation: WebGLUniformLocation;
  private readonly readyLocation: WebGLUniformLocation;
  private textureWidth = 0;
  private textureHeight = 0;
  private disposed = false;

  constructor(private readonly canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", {
      alpha: true,
      antialias: true,
      premultipliedAlpha: true,
      powerPreference: "high-performance",
    });
    if (gl === null) throw new Error("PRESENCE_INPUT_GLASS_WEBGL2_UNAVAILABLE");
    this.gl = gl;
    this.program = createProgram(gl, INPUT_GLASS_VERTEX_SHADER, INPUT_GLASS_FRAGMENT_SHADER);
    this.buffer = requireResource(gl.createBuffer(), "PRESENCE_INPUT_GLASS_BUFFER_FAILED");
    this.texture = requireResource(gl.createTexture(), "PRESENCE_INPUT_GLASS_TEXTURE_FAILED");
    this.resolutionLocation = requireUniform(gl, this.program, "uResolution");
    this.dprLocation = requireUniform(gl, this.program, "uDpr");
    this.readyLocation = requireUniform(gl, this.program, "uReady");

    gl.useProgram(this.program);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      gl.STATIC_DRAW,
    );
    const position = gl.getAttribLocation(this.program, "aPosition");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.uniform1i(requireUniform(gl, this.program, "uBackdrop"), 0);
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.BLEND);
  }

  resize(width: number, height: number, dpr: number): void {
    if (this.disposed) return;
    const boundedDpr = Math.min(2, Math.max(0.5, dpr));
    const physicalWidth = Math.max(1, Math.round(width * boundedDpr));
    const physicalHeight = Math.max(1, Math.round(height * boundedDpr));
    if (this.canvas.width !== physicalWidth) this.canvas.width = physicalWidth;
    if (this.canvas.height !== physicalHeight) this.canvas.height = physicalHeight;
    this.gl.viewport(0, 0, physicalWidth, physicalHeight);
    this.gl.useProgram(this.program);
    this.gl.uniform2f(this.resolutionLocation, physicalWidth, physicalHeight);
    this.gl.uniform1f(this.dprLocation, boundedDpr);
    this.draw(false);
  }

  render(frame: NativeBackdropFrame): void {
    if (this.disposed) return;
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    if (frame.width === this.textureWidth && frame.height === this.textureHeight) {
      gl.texSubImage2D(
        gl.TEXTURE_2D,
        0,
        0,
        0,
        frame.width,
        frame.height,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        frame.rgba,
      );
    } else {
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA,
        frame.width,
        frame.height,
        0,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        frame.rgba,
      );
      this.textureWidth = frame.width;
      this.textureHeight = frame.height;
    }
    this.draw(true);
  }

  clear(): void {
    if (this.disposed) return;
    this.gl.clearColor(0, 0, 0, 0);
    this.gl.clear(this.gl.COLOR_BUFFER_BIT);
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.gl.deleteTexture(this.texture);
    this.gl.deleteBuffer(this.buffer);
    this.gl.deleteProgram(this.program);
  }

  private draw(ready: boolean) {
    const gl = this.gl;
    gl.useProgram(this.program);
    gl.uniform1f(this.readyLocation, ready ? 1 : 0);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }
}

function createProgram(
  gl: WebGL2RenderingContext,
  vertexSource: string,
  fragmentSource: string,
): WebGLProgram {
  const vertex = createShader(gl, gl.VERTEX_SHADER, vertexSource);
  const fragment = createShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
  const program = requireResource(gl.createProgram(), "PRESENCE_INPUT_GLASS_PROGRAM_FAILED");
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const log = gl.getProgramInfoLog(program) ?? "unknown";
    gl.deleteProgram(program);
    throw new Error(`PRESENCE_INPUT_GLASS_LINK_FAILED: ${log}`);
  }
  return program;
}

function createShader(
  gl: WebGL2RenderingContext,
  kind: number,
  source: string,
): WebGLShader {
  const shader = requireResource(gl.createShader(kind), "PRESENCE_INPUT_GLASS_SHADER_FAILED");
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader) ?? "unknown";
    gl.deleteShader(shader);
    throw new Error(`PRESENCE_INPUT_GLASS_COMPILE_FAILED: ${log}`);
  }
  return shader;
}

function requireUniform(
  gl: WebGL2RenderingContext,
  program: WebGLProgram,
  name: string,
): WebGLUniformLocation {
  return requireResource(
    gl.getUniformLocation(program, name),
    `PRESENCE_INPUT_GLASS_UNIFORM_MISSING: ${name}`,
  );
}

function requireResource<T>(value: T | null, message: string): T {
  if (value === null) throw new Error(message);
  return value;
}
