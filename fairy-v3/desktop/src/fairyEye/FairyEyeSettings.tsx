import { useId } from 'react';
import type { DesktopPreferences } from '../settings/client';
import { FairyEye } from './FairyEye';

export function FairyEyeSettings({preferences,disabled,onChange}: {
  preferences: DesktopPreferences;
  disabled: boolean;
  onChange(patch: Partial<DesktopPreferences>): unknown;
}) {
  const id=useId();
  const eye=preferences.pet_form==='hdd_eye';
  return <div className="fairy-eye-settings">
    <div className="fairy-eye-settings-controls">
      <label htmlFor={`${id}-form`}>Pet form / 桌宠形态
        <select id={`${id}-form`} value={preferences.pet_form ?? 'liquid_glass'} disabled={disabled}
          onChange={event=>void onChange({pet_form:event.target.value==='hdd_eye' ? 'hdd_eye' : 'liquid_glass'})}>
          <option value="liquid_glass">Liquid Glass</option><option value="hdd_eye">HDD Eye (SVG)</option>
        </select>
      </label>
      <label htmlFor={`${id}-chat`}>Fairy eye in chat / 聊天形象
        <input id={`${id}-chat`} type="checkbox" checked={preferences.chat_fairy_eye_enabled ?? true}
          disabled={disabled} onChange={event=>void onChange({chat_fairy_eye_enabled:event.target.checked})} />
      </label>
      <small>{eye ? 'SVG eye. No desktop capture; capped at 60 FPS, respecting lower power limits. Liquid settings are preserved.' : 'The original liquid renderer and its saved settings are unchanged.'}</small>
    </div>
    <div className="fairy-eye-settings-preview"><FairyEye state="idle" effectScale={.55}
      reducedMotion={preferences.reduced_motion || !preferences.pet_motion_enabled}
      effects={preferences.pet_particles_enabled} /></div>
  </div>;
}
