export interface EyePlaybackFacts {
  playbackState: string;
  speakingTurnId: string | null;
  speakingMessageId: string | null;
}
/** The current chat may reflect only an actually-playing, non-ambient turn it owns. */
export function isScopedEyePlayback(voice: EyePlaybackFacts | null, turnIds: readonly string[]): boolean {
  return voice?.playbackState === 'speaking' && voice.speakingTurnId !== null
    && turnIds.includes(voice.speakingTurnId)
    && voice.speakingMessageId?.startsWith('ambient:') !== true;
}
