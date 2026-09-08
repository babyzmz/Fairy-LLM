import type { PresenceRenderSnapshot } from '../presence/render/presenceRenderer';
import { FairyEye } from './FairyEye';
import { petEyeLayout, petEyeState } from './state.js';

export function PetFairyEye({snapshot, active=true}: {snapshot: PresenceRenderSnapshot; active?: boolean}) {
  const placement=snapshot.interaction?.placement;
  // No guessed anchor while native placement is not ready.
  if (!placement) return null;
  const layout=petEyeLayout(placement,snapshot.size_scale);
  return <div className="fairy-eye-pet" data-testid="fairy-eye-pet" style={{
    left:layout.centerX-layout.diameter/2, top:layout.centerY-layout.diameter/2,
    width:layout.diameter, height:layout.diameter,
  }}>
    <FairyEye state={petEyeState(snapshot.motion.state)} active={active}
      gaze={snapshot.interaction?.cursor.band==='outside' ? {x:0,y:0} : snapshot.interaction?.cursor.direction}
      reducedMotion={snapshot.reduced_motion}
      lowPower={snapshot.frame_rate_limit<=15}
      effects={snapshot.particles_enabled && !snapshot.increased_contrast}
      opacity={snapshot.opacity} effectScale={layout.effectScale}
      voiceLevel={snapshot.speaking ? snapshot.voice_level : 0}
      frameRate={Math.min(60,snapshot.target_frame_rate,snapshot.frame_rate_limit)} />
  </div>;
}
