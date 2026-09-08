/** Presentation only. Never starts tools, audio, models, or capture. */
export type EyeState = 'idle' | 'thinking' | 'comforting' | 'speaking' | 'waiting' | 'error' | 'sleeping' | 'listening';
export interface ChatEyeFacts {
  conversationId?: string | null;
  turnConversationId?: string | null;
  turnStatus?: string | null;
  busy?: boolean;
  speaking?: boolean;
  voiceConversationId?: string | null;
  awaitingConfirmation?: boolean;
  error?: boolean;
  loading?: boolean;
}
const TERMINAL = new Set(['completed', 'succeeded', 'failed', 'cancelled', 'canceled', 'timed_out', 'interrupted']);
export function chatEyeState(facts: ChatEyeFacts): EyeState {
  if (facts.error) return 'error';
  if (facts.loading) return 'idle';
  if (facts.awaitingConfirmation) return 'waiting';
  if (!facts.conversationId) return 'idle';
  if (facts.speaking && facts.voiceConversationId === facts.conversationId) return 'speaking';
  if (facts.turnConversationId !== facts.conversationId) return 'idle';
  if (['waiting_for_input', 'awaiting_confirmation', 'paused'].includes(facts.turnStatus ?? '')) return 'waiting';
  if (TERMINAL.has(facts.turnStatus ?? '')) return 'idle';
  if (facts.busy && ['running','queued','created','submitting'].includes(facts.turnStatus ?? '')) return 'thinking';
  return 'idle';
}
export function petEyeState(state: string): EyeState {
  switch (state) {
    case 'error': return 'error';
    case 'awaiting_confirmation': return 'waiting';
    case 'speaking': return 'speaking';
    case 'thinking': case 'submitting': case 'responding': return 'thinking';
    case 'sleeping': case 'suspended': return 'sleeping';
    // Opening the input window is NOT evidence that the microphone is recording.
    default: return 'idle';
  }
}
export interface EyePlacement {
  anchor: { x: number; y: number };
  render_frame: { x: number; y: number; width: number; height: number };
  scale_factor: number;
}
export function finiteClamp(value: number, min: number, max: number, fallback: number): number {
  return Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback;
}
export function petEyeLayout(placement: EyePlacement, sizeScale: number) {
  const scale = finiteClamp(placement.scale_factor, .5, 4, 1);
  const centerX = (placement.anchor.x - placement.render_frame.x) / scale;
  const centerY = (placement.anchor.y - placement.render_frame.y) / scale;
  const diameter = 96 * finiteClamp(sizeScale, .75, 1.5, 1);
  const margin = Math.min(centerX, centerY, placement.render_frame.width / scale - centerX,
    placement.render_frame.height / scale - centerY);
  // Decorative wave may shrink at a window edge; the eye and hit-target never move.
  const effectScale = finiteClamp((margin - 3) / (diameter * 1.12), .35, 1, .65);
  return { centerX, centerY, diameter, effectScale };
}
