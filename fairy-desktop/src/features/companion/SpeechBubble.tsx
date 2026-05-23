import type { CSSProperties } from "react";

import type { BubbleViewState } from "./useCompanionStream";

interface SpeechBubbleProps {
  bubble: BubbleViewState | null;
  position?: "top" | "right";
}

const BASE_STYLE: CSSProperties = {
  position: "absolute",
  display: "inline-block",
  maxWidth: 220,
  padding: "6px 12px",
  borderRadius: 14,
  border: "1px solid rgba(143,181,255,0.45)",
  backgroundColor: "rgba(8, 21, 54, 0.88)",
  color: "rgba(236, 240, 246, 0.96)",
  fontSize: 13,
  lineHeight: 1.35,
  letterSpacing: "0.02em",
  pointerEvents: "none",
  transition: "opacity 0.6s ease-out, transform 0.4s ease-out",
  whiteSpace: "pre-wrap",
};

export function SpeechBubble({ bubble, position = "top" }: SpeechBubbleProps): JSX.Element | null {
  if (!bubble || !bubble.text) {
    return null;
  }
  const style: CSSProperties = {
    ...BASE_STYLE,
    opacity: bubble.fading ? 0.35 : 1,
    transform: bubble.fading ? "translateY(-4px)" : "translateY(0px)",
    ...(position === "top"
      ? { top: -12, left: "50%", marginLeft: -110 }
      : { right: -240, top: "50%", marginTop: -16 }),
  };
  return (
    <div className={`fairy-speech-bubble fairy-speech-bubble--${bubble.category}`} style={style} aria-live="polite">
      {bubble.text}
    </div>
  );
}
