export type PresenceRendererMode = "auto" | "liquid" | "compatibility";

export interface PresenceRendererCapability {
  webgl2: boolean;
  webgpu: boolean;
  alpha: boolean;
  premultiplied_alpha: boolean;
  antialias: boolean;
  renderer: string | null;
  error_code: "WEBGL2_UNAVAILABLE" | "WEBGL2_CONTEXT_ERROR" | null;
}

interface CanvasContextSource {
  getContext(
    contextId: "webgl2",
    options: WebGLContextAttributes,
  ): WebGL2RenderingContext | null;
}

export function detectPresenceRendererCapability(
  canvas: CanvasContextSource,
  browserNavigator: Pick<Navigator, "userAgent"> & { gpu?: unknown } = navigator,
): PresenceRendererCapability {
  try {
    const context = canvas.getContext("webgl2", {
      alpha: true,
      antialias: true,
      premultipliedAlpha: true,
      powerPreference: "high-performance",
    });
    if (context === null) return unavailableCapability(browserNavigator, "WEBGL2_UNAVAILABLE");
    const attributes = context.getContextAttributes();
    return {
      webgl2: true,
      webgpu: browserNavigator.gpu !== undefined,
      alpha: attributes?.alpha === true,
      premultiplied_alpha: attributes?.premultipliedAlpha === true,
      antialias: attributes?.antialias === true,
      renderer: rendererName(context),
      error_code: null,
    };
  } catch {
    return unavailableCapability(browserNavigator, "WEBGL2_CONTEXT_ERROR");
  }
}

export function resolvePresenceRendererMode(
  requested: PresenceRendererMode,
  capability: PresenceRendererCapability,
): Exclude<PresenceRendererMode, "auto"> {
  if (requested === "compatibility") return "compatibility";
  return capability.webgl2 && capability.alpha && capability.premultiplied_alpha
    ? "liquid"
    : "compatibility";
}

function unavailableCapability(
  browserNavigator: { gpu?: unknown },
  error_code: NonNullable<PresenceRendererCapability["error_code"]>,
): PresenceRendererCapability {
  return {
    webgl2: false,
    webgpu: browserNavigator.gpu !== undefined,
    alpha: false,
    premultiplied_alpha: false,
    antialias: false,
    renderer: null,
    error_code,
  };
}

function rendererName(context: WebGL2RenderingContext): string | null {
  const extension = context.getExtension("WEBGL_debug_renderer_info") as
    | { UNMASKED_RENDERER_WEBGL: number }
    | null;
  if (extension === null) return null;
  const value = context.getParameter(extension.UNMASKED_RENDERER_WEBGL);
  return typeof value === "string" ? value : null;
}
