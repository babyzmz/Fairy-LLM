import { useEffect, useRef, type CSSProperties } from 'react';
import { mountFairyEye, type EyeController, type EyeOptions } from './runtime.js';
import './eye.css';

export interface FairyEyeProps extends EyeOptions {
  className?: string;
  style?: CSSProperties;
}
/** React owns the mount; the isolated SVG runtime owns frame-by-frame attributes. */
export function FairyEye({className='', style, ...options}: FairyEyeProps) {
  const host=useRef<HTMLDivElement>(null);
  const controller=useRef<EyeController | null>(null);
  const latest=useRef(options);
  latest.current=options;
  useEffect(()=>{
    if (!host.current) return;
    const instance=mountFairyEye(host.current, latest.current);
    controller.current=instance;
    return ()=>{ instance.dispose(); if (controller.current===instance) controller.current=null; };
  },[]);
  useEffect(()=>{ controller.current?.update(options); });
  return <div ref={host} className={`fairy-eye-host ${className}`} style={style} aria-hidden="true" />;
}
