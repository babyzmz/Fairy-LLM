// @vitest-environment jsdom
import {StrictMode} from 'react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,render} from '@testing-library/react';
import {FairyEye} from './FairyEye';

let frames:Set<number>;
beforeEach(()=>{
  frames=new Set();let sequence=0;
  vi.stubGlobal('requestAnimationFrame',vi.fn(()=>{frames.add(++sequence);return sequence;}));
  vi.stubGlobal('cancelAnimationFrame',vi.fn((id:number)=>{frames.delete(id);}));
  vi.stubGlobal('IntersectionObserver',undefined);
  vi.stubGlobal('matchMedia',vi.fn(()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()})));
  vi.spyOn(document,'hidden','get').mockReturnValue(false);
});
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();});
describe('React SVG mount ownership',()=>{
  it('survives StrictMode effect replay without duplicate roots or RAF chains',()=>{
    const mounted=render(<StrictMode><FairyEye /></StrictMode>);
    expect(mounted.container.querySelectorAll('.fairy-eye')).toHaveLength(1);
    expect(frames.size).toBe(1);
    mounted.unmount();expect(frames.size).toBe(0);
  });
  it('keeps neighbouring instances independent and cleans up on removal',()=>{
    const mounted=render(<><FairyEye state="thinking" /><FairyEye state="idle" /></>);
    const ids=[...mounted.container.querySelectorAll('[id]')].map(node=>node.id);
    expect(new Set(ids).size).toBe(ids.length);expect(frames.size).toBe(2);
    mounted.rerender(<FairyEye active={false} />);expect(frames.size).toBe(0);
    mounted.unmount();expect(frames.size).toBe(0);
  });
  it('does not schedule animation when reduced motion is enabled',()=>{
    render(<FairyEye reducedMotion />);expect(frames.size).toBe(0);
  });
  it('keeps the quiet eye round and centered without waking its animation loop',()=>{
    const mounted=render(<FairyEye state="idle" gaze={{x:1,y:1}} />);
    const eye=()=>mounted.container.querySelector<SVGElement>('.dsh-fairy-eye')!;
    const pupil=()=>mounted.container.querySelector<SVGElement>('.dsh-fairy-eyeball')!;
    expect(pupil().style.transform).not.toBe('translate(0.000px,0.000px)');
    mounted.rerender(<FairyEye state="sleeping" gaze={{x:1,y:1}} />);
    expect(eye().style.transform).toBe('scale(.90)');
    expect(pupil().style.transform).toBe('translate(0.000px,0.000px)');
    expect(eye().style.clipPath).toBe('none');
    expect(frames.size).toBe(0);
    for(let i=0;i<20;i++) {
      mounted.rerender(<FairyEye state="idle" gaze={{x:-1,y:1}} />);
      expect(frames.size).toBe(1);
      mounted.rerender(<FairyEye state="sleeping" />);
      expect(pupil().style.transform).toBe('translate(0.000px,0.000px)');
      expect(frames.size).toBe(0);
    }
    mounted.unmount();expect(frames.size).toBe(0);
  });
  it('keeps quiet geometry independent of reduced motion and a neighbouring active eye',()=>{
    const mounted=render(<><FairyEye state="sleeping" reducedMotion gaze={{x:-1,y:-1}} /><FairyEye state="thinking" /></>);
    const eyes=mounted.container.querySelectorAll<SVGElement>('.dsh-fairy-eye');
    expect(eyes[0].style.transform).toBe('scale(.90)');
    expect(eyes[0].style.clipPath).toBe('none');
    expect(eyes[1].style.clipPath).toContain('thinking-eye-clip');
    expect(frames.size).toBe(1);
    mounted.unmount();expect(frames.size).toBe(0);
  });
});
