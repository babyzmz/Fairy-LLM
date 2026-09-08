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
});
