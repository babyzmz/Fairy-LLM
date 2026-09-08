import { useEffect, useState } from 'react';
import { useConversationEyePlayback } from '../voice/VoiceController';
import { createPresenceVoiceLevelSource } from '../presence/transport/voiceLevelEvents';
import { FairyEye } from './FairyEye';
import { chatEyeState, type EyeState } from './state.js';

export interface ChatFairyEyeProps {
  conversationId: string | null;
  turnId: string | null;
  turnConversationId: string | null;
  turnStatus: string | null;
  messageTurnIds: readonly string[];
  busy: boolean;
  waiting: boolean;
  error: boolean;
  loading: boolean;
  empty: boolean;
  reducedMotion?: boolean;
  active?: boolean;
}
const LABELS: Record<EyeState,string> = {
  idle:'Ready', thinking:'Working', comforting:'Here with you', speaking:'Speaking',
  waiting:'Waiting for you', error:'Attention needed', sleeping:'Resting', listening:'Listening',
};
export function ChatFairyEye(props: ChatFairyEyeProps) {
  const currentTurn=props.turnConversationId===props.conversationId ? props.turnId : null;
  const allowedTurnIds=currentTurn ? [...props.messageTurnIds,currentTurn] : props.messageTurnIds;
  const speaking=useConversationEyePlayback(allowedTurnIds);
  const [sample,setSample]=useState({level:0,sampled_at_ms:0});
  useEffect(()=>{
    // Subscribe only during a scoped, actually-playing reply. Never start the voice worker.
    if (!speaking || props.loading) { setSample({level:0,sampled_at_ms:0}); return; }
    return createPresenceVoiceLevelSource().subscribe(next=>setSample(previous=>
      next.sampled_at_ms>=previous.sampled_at_ms ? next : previous));
  },[speaking,props.conversationId,props.loading]);
  const state=chatEyeState({
    conversationId:props.conversationId, turnConversationId:props.turnConversationId,
    turnStatus:props.turnStatus, busy:props.busy, error:props.error,
    loading:props.loading, awaitingConfirmation:props.waiting && props.turnConversationId===props.conversationId,
    speaking, voiceConversationId:speaking ? props.conversationId : null,
  });
  return <div className="fairy-eye-chat" data-empty={props.empty} data-conversation-id={props.conversationId ?? ''}
    data-eye-state={state} data-testid="fairy-eye-chat">
    <div className="fairy-eye-chat-visual"><FairyEye state={state} trackPointer active={props.active}
      reducedMotion={props.reducedMotion} effectScale={props.empty ? .8 : .62}
      voiceLevel={speaking ? sample.level : 0} /></div>
    <div className="fairy-eye-chat-copy"><strong>FAIRY</strong><small>{props.loading ? 'Loading conversation' : LABELS[state]}</small></div>
  </div>;
}
