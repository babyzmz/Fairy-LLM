import {describe,expect,it} from 'vitest';
import {createEyeMarkup} from './eyeSvg';
import {createMotionClock,eyeMotionAt} from './motion';
import {chatEyeState,petEyeLayout} from './state';
import {isScopedEyePlayback} from './playback';
import {eyeMayMount,nativeFormAllowed} from './nativePolicy';
import {DEFAULT_PRESENCE_RENDER_SETTINGS,loadNativePresenceRenderSettings} from '../presence/transport/renderSettings';

describe('Fairy SVG migration',()=>{
  it('namespaces every resource for two simultaneous instances',()=>{
    const a=createEyeMarkup('chat'),b=createEyeMarkup('preview');
    const ids=[...(a+b).matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
    expect(new Set(ids).size).toBe(ids.length);
    expect(a).toContain('chat-dsh-fairy-outer-gradient');
    expect(a).not.toContain('preview-dsh-fairy-outer-gradient');
  });
  it('preserves clock phase while paused',()=>{
    let now=0;const clock=createMotionClock(()=>now);now=720;clock.pause();now=90000;
    expect(clock.phase()).toBe(.5);clock.resume();now+=720;expect(clock.phase()).toBe(0);
    expect(eyeMotionAt(0).layerThree).not.toBe(eyeMotionAt(0).layerTwo);
  });
  it('refuses stale conversation tasks and unrelated speech',()=>{
    expect(chatEyeState({conversationId:'b',turnConversationId:'a',turnStatus:'running',busy:true})).toBe('idle');
    expect(chatEyeState({conversationId:'b',turnConversationId:'b',turnStatus:'completed',busy:true})).toBe('idle');
    expect(isScopedEyePlayback({playbackState:'preparing',speakingTurnId:'b',speakingMessageId:'m'},['b'])).toBe(false);
    expect(isScopedEyePlayback({playbackState:'speaking',speakingTurnId:'a',speakingMessageId:'m'},['b'])).toBe(false);
    expect(isScopedEyePlayback({playbackState:'speaking',speakingTurnId:'b',speakingMessageId:'m'},['b'])).toBe(true);
  });
  it('never requests native acquisition for the SVG form',()=>{
    expect(nativeFormAllowed('hdd_eye')).toBe(false);
    expect(nativeFormAllowed(undefined)).toBe(true);
    for(const state of ['starting','running','stopping'])expect(eyeMayMount(state)).toBe(false);
    expect(eyeMayMount('idle')).toBe(true);
  });
  it('retains the physical anchor at different display scales',()=>{
    for(const scale of [1,1.25,1.5,2]) {
      const layout=petEyeLayout({anchor:{x:96*scale,y:88*scale},render_frame:{x:0,y:0,width:640*scale,height:420*scale},scale_factor:scale},1);
      expect(layout.centerX).toBe(96);expect(layout.centerY).toBe(88);
    }
  });
  it('loads v5 form without leaking additional settings',async()=>{
    await expect(loadNativePresenceRenderSettings(async()=>({...DEFAULT_PRESENCE_RENDER_SETTINGS,form:'hdd_eye'}))).resolves.toMatchObject({schema_version:5,form:'hdd_eye'});
    await expect(loadNativePresenceRenderSettings(async()=>({...DEFAULT_PRESENCE_RENDER_SETTINGS,developer_mode:true}))).resolves.toBeNull();
  });
  it('upgrades legacy v4 render settings while preserving optics',async()=>{
    const legacy: Record<string, unknown> = {...DEFAULT_PRESENCE_RENDER_SETTINGS, schema_version:4};
    delete legacy.form;
    await expect(loadNativePresenceRenderSettings(async()=>({...legacy,optics_mode:'enhanced'}))).resolves.toMatchObject({schema_version:5,form:'liquid_glass',optics_mode:'enhanced'});
  });
});
