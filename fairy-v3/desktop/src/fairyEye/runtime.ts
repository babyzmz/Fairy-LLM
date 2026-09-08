/** Fairy-DSH SVG adaptation. Apache-2.0; upstream Copyright 2026 Chengzhibense.
 * This host/lifecycle implementation is new. No singleton, network, audio or tools.
 * Every animation, including pulse and transient glitches, uses one mount-owned clock.
 */
import { createEyeMarkup } from './eyeSvg.js';
import { createMotionClock, eyeMotionAt } from './motion.js';
import { finiteClamp, type EyeState } from './state.js';

export interface EyeOptions {
  state?: EyeState;
  active?: boolean;
  reducedMotion?: boolean;
  lowPower?: boolean;
  effects?: boolean;
  opacity?: number;
  frameRate?: number;
  animationRate?: number;
  voiceLevel?: number;
  gaze?: { x: number; y: number };
  trackPointer?: boolean;
  effectScale?: number;
}
export interface EyeController { update(options: EyeOptions): void; dispose(): void; }
let nextId = 0;

export function mountFairyEye(host: HTMLElement, initial: EyeOptions = {}): EyeController {
  if (!host || !host.ownerDocument || !host.ownerDocument.defaultView) throw new Error('A connected DOM host is required');
  const doc = host.ownerDocument, win = doc.defaultView!;
  const prefix = `fairy-eye-${++nextId}-${Math.random().toString(36).slice(2,10)}`;
  const root = doc.createElement('div');
  root.className = 'fairy-eye';
  root.setAttribute('aria-hidden','true');
  // Only the checked-in SVG constants enter this sink. Prefix accepts a strict identifier grammar.
  root.innerHTML = createEyeMarkup(prefix);
  host.appendChild(root);
  const find = (selector: string) => root.querySelector<SVGElement>(selector)!;
  const all = (selector: string) => Array.from(root.querySelectorAll<SVGElement>(selector));
  const sclera=find('.dsh-fairy-sclera'), corners=find('.dsh-fairy-corners');
  const three=all('.dsh-fairy-layer-three'), two=all('.dsh-fairy-layer-two'), one=all('.dsh-fairy-layer-one');
  const eye=find('.dsh-fairy-eye'), eyeball=find('.dsh-fairy-eyeball');
  const thinking=find('.dsh-fairy-thinking-clip-shape'), comforting=find('.dsh-fairy-comforting-clip-shape');
  const signal=find('.dsh-fairy-signal'), image=find('.dsh-fairy-image');
  const blocks=find('.dsh-fairy-glitch-blocks'), slices=all('.dsh-fairy-glitch-blocks > use');
  const pulse=find('.dsh-fairy-lash-pulse-wave');
  const displacement=find(`[id="${prefix}-dsh-fairy-displace"]`);
  const noise=find(`[id="${prefix}-dsh-fairy-noise"]`);
  let options: EyeOptions = {...initial};
  let disposed=false, raf=0, lastPaint=-Infinity;
  let intersects=true, pageActive=true;
  let pointer={x:0,y:0}, gaze={x:0,y:0};
  let displayedVoice=0;
  const motionQuery = typeof win.matchMedia === 'function' ? win.matchMedia('(prefers-reduced-motion: reduce)') : null;
  const hasFrameScheduler = typeof win.requestAnimationFrame === 'function';
  root.dataset.rendererMode = hasFrameScheduler ? 'svg-raf' : 'svg-static';
  const clock=createMotionClock(()=>win.performance.now());
  clock.setRate(options.animationRate ?? 1);
  const reduce = () => options.reducedMotion === true || motionQuery?.matches === true;
  const visible = () => options.active !== false && pageActive && !doc.hidden && intersects;
  const animate = () => hasFrameScheduler && visible() && !reduce() && options.state !== 'sleeping';
  const id = (name: string) => `url(#${prefix}-dsh-fairy-${name})`;
  const transformScale = (elements: SVGElement[], value: number) => {
    for (const element of elements) element.style.transform=`scale(${value.toFixed(6)})`;
  };
  function draw() {
    if (disposed) return;
    const state=options.state ?? 'idle';
    const motion=eyeMotionAt(clock.elapsed());
    const decorative=options.effects !== false && !options.lowPower && !reduce();
    root.dataset.state=state;
    root.dataset.effects=String(decorative);
    root.dataset.lowPower=String(options.lowPower === true);
    root.dataset.reducedMotion=String(reduce());
    root.style.setProperty('--fairy-eye-opacity',String(finiteClamp(options.opacity ?? 1,.1,1,1)));
    root.style.setProperty('--fairy-eye-effect-scale',String(finiteClamp(options.effectScale ?? 1,.25,1,1)));
    const value=reduce() ? eyeMotionAt(0) : motion;
    sclera.style.transform=`scale(${value.sclera})`;
    transformScale(three,value.layerThree); transformScale(two,value.layerTwo); transformScale(one,value.layerOne);
    corners.style.transform=`rotate(${value.lashAngle}deg)`;
    let clip='none';
    thinking.style.transform=''; comforting.style.transform='';
    if (state==='thinking' || state==='error') {
      clip=id('thinking-eye-clip');
      thinking.style.transform=`translateY(${value.thinkingY}px) scaleY(${value.thinkingScaleY})`;
    } else if (state==='comforting') {
      clip=id('comforting-eye-clip'); comforting.style.transform=`translateY(${value.comfortingY}px)`;
    }
    eye.style.clipPath=clip;
    eye.style.transform=state==='sleeping' ? 'translateY(42px) scale(.90,.35)' : 'scale(.90)';
    const target=options.gaze ?? (options.trackPointer ? pointer : {x:0,y:0});
    const blend=reduce() ? 1 : .22;
    gaze.x+=(finiteClamp(target.x,-1,1,0)*6-gaze.x)*blend;
    gaze.y+=(finiteClamp(target.y,-1,1,0)*5-gaze.y)*blend;
    eyeball.style.transform=`translate(${gaze.x.toFixed(3)}px,${gaze.y.toFixed(3)}px)`;
    const level=state==='speaking' ? finiteClamp(options.voiceLevel ?? 0,0,1,0) : 0;
    displayedVoice += (level-displayedVoice)*.3;
    pulse.style.transform=`scale(${value.pulseScale + displayedVoice*.25})`;
    pulse.style.opacity=decorative ? String(Math.min(.85,value.pulseOpacity+displayedVoice*.15)) : '0';
    // Quiet fault accents. No white flash overlay or rapid full-surface brightness modulation.
    // Alternating threads / slices are restricted to a short, single burst per 9-second cycle.
    const time=clock.elapsed(), period=time%9000;
    const glitch=decorative && ['idle','thinking','error'].includes(state) && period>8450 && period<8670;
    signal.style.transform=''; image.style.filter='none'; blocks.style.display='none';
    if (glitch) {
      const strength=Math.sin((period-8450)/220*Math.PI);
      if (Math.floor(time/9000)%2===0) {
        image.style.filter=id('interference');
        displacement.setAttribute('scale',String(3*strength));
        noise.setAttribute('seed',String(Math.floor(time/110)%29));
      } else {
        blocks.style.display='block';
        slices.forEach((slice,i)=>{slice.style.transform=`translateX(${(i%2 ? -1 : 1)*(1+i%3)*strength}px)`;});
      }
    }
  }
  function tick(timestamp: number) {
    raf=0;
    if (disposed || !animate()) { reconcile(); return; }
    const fps=options.lowPower ? 15 : finiteClamp(options.frameRate ?? 60,1,60,60);
    if (timestamp-lastPaint >= 1000/fps-1) { lastPaint=timestamp; draw(); }
    raf=win.requestAnimationFrame(tick);
  }
  function reconcile() {
    if (disposed) return;
    if (animate()) {
      clock.resume();
      if (!raf) { draw(); lastPaint=win.performance.now(); raf=win.requestAnimationFrame(tick); }
    } else {
      clock.pause();
      if (raf) win.cancelAnimationFrame(raf);
      raf=0;
      if (visible()) draw();
    }
  }
  function onVisibility() { reconcile(); }
  function onPageHide() { pageActive=false; reconcile(); }
  function onPageShow() { pageActive=true; reconcile(); }
  function onPointer(event: PointerEvent) {
    if (!options.trackPointer || !visible() || reduce()) return;
    const rect=host.getBoundingClientRect();
    if (rect.width<=0 || rect.height<=0) return;
    pointer={x:(event.clientX-rect.left-rect.width/2)/Math.max(80,rect.width),
      y:(event.clientY-rect.top-rect.height/2)/Math.max(80,rect.height)};
  }
  doc.addEventListener('visibilitychange',onVisibility);
  win.addEventListener('pagehide',onPageHide);
  win.addEventListener('pageshow',onPageShow);
  win.addEventListener('pointermove',onPointer,{passive:true});
  if (motionQuery?.addEventListener) motionQuery.addEventListener('change',onVisibility);
  else motionQuery?.addListener?.(onVisibility);
  const observer=typeof IntersectionObserver==='undefined' ? null : new IntersectionObserver(entries=>{
    if (disposed) return;
    intersects=entries.some(entry=>entry.isIntersecting);
    reconcile();
  });
  observer?.observe(host);
  draw(); reconcile();
  return {
    update(next) {
      if (disposed) return;
      const oldState=options.state;
      options={...options,...next};
      if (next.animationRate !== undefined) clock.setRate(next.animationRate);
      if (options.state !== oldState) displayedVoice=0;
      if (!animate() && visible()) draw();
      reconcile();
    },
    dispose() {
      if (disposed) return;
      disposed=true;
      if (raf) win.cancelAnimationFrame(raf);
      raf=0; clock.pause(); observer?.disconnect();
      doc.removeEventListener('visibilitychange',onVisibility);
      win.removeEventListener('pagehide',onPageHide);
      win.removeEventListener('pageshow',onPageShow);
      win.removeEventListener('pointermove',onPointer);
      if (motionQuery?.removeEventListener) motionQuery.removeEventListener('change',onVisibility);
      else motionQuery?.removeListener?.(onVisibility);
      root.remove();
    },
  };
}
