/**
 * Adapted from Fairy-DSH mascot-effects-svg.js, Apache-2.0.
 * Copyright 2026 Chengzhibense. Original geometry and gradients retained.
 * Modification: typed ES module, instance namespacing supplied by eyeSvg.ts.
 */
function buildHaloLines(): string {
  const paths = new Map<string, string[]>();
  for (let y = -34; y <= 194; y += 1) {
    const t = y + 34;
    const leftWave = (Math.sin(t * .082 - 1.1) + .55 * Math.sin(t * .151 + 2.4) + 1.55) / 3.1;
    const rightWave = (Math.sin(t * .097 + 2.2) + .5 * Math.sin(t * .137 - 1.7) + 1.5) / 3;
    const leftLength = 158 + leftWave * 56;
    const rightLength = 158 + rightWave * 56;
    const brightness = .50 + ((leftWave + rightWave) * .5) * .50;
    const bucket = Math.max(.5, Math.min(1, Math.round(brightness * 8) / 8)).toFixed(3);
    const lines = paths.get(bucket) ?? [];
    lines.push(`M${(80-leftLength).toFixed(2)} ${(y+.25).toFixed(2)}h${(leftLength+rightLength).toFixed(2)}`);
    paths.set(bucket, lines);
  }
  return Array.from(paths, ([opacity, lines]) => `<path d="${lines.join("")}" fill="none" stroke="currentColor" stroke-width=".62" stroke-linecap="butt" opacity="${opacity}"/>`).join("");
}
export const PULSE_SVG = `
<svg class="dsh-fairy-pulse-layer" viewBox="-160 -160 480 480" xmlns="http://www.w3.org/2000/svg" role="presentation">
 <g class="dsh-fairy-lash-pulse" fill="none" stroke="#f4fdff">
  <g class="dsh-fairy-lash-pulse-wave">
   <circle cx="80" cy="80" r="52" stroke-width="20" stroke-opacity=".05"/>
   <circle cx="80" cy="80" r="52" stroke-width="16" stroke-opacity=".06"/>
   <circle cx="80" cy="80" r="52" stroke-width="12" stroke-opacity=".08"/>
   <circle cx="80" cy="80" r="52" stroke-width="8" stroke-opacity=".10"/>
   <circle cx="80" cy="80" r="52" stroke-width="4.5" stroke-opacity=".09"/>
  </g>
 </g>
</svg>`;
export const HALO_SVG = `
<svg class="dsh-fairy-halo dsh-fairy-halo-layer" viewBox="-180 -70 520 320" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg" role="presentation">
 <defs>
  <radialGradient id="dsh-fairy-halo-layer-fade" gradientUnits="userSpaceOnUse" color-interpolation="linearRGB" cx="80" cy="80" r="124">
   <stop offset="0" stop-color="black"/><stop offset=".44" stop-color="black"/>
   <stop offset=".45" stop-color="white" stop-opacity=".08"/>
   <stop offset=".46" stop-color="white" stop-opacity=".30"/>
   <stop offset=".47" stop-color="white" stop-opacity=".65"/>
   <stop offset=".48" stop-color="white"/>
   <stop offset=".52" stop-color="white" stop-opacity=".72"/>
   <stop offset=".56" stop-color="white" stop-opacity=".56"/>
   <stop offset=".60" stop-color="white" stop-opacity=".44"/>
   <stop offset=".65" stop-color="white" stop-opacity=".30"/>
   <stop offset=".71" stop-color="white" stop-opacity=".19"/>
   <stop offset=".77" stop-color="white" stop-opacity=".135"/>
   <stop offset=".83" stop-color="white" stop-opacity=".09"/>
   <stop offset=".89" stop-color="white" stop-opacity=".058"/>
   <stop offset=".90" stop-color="white" stop-opacity=".052"/>
   <stop offset=".91" stop-color="white" stop-opacity=".046"/>
   <stop offset=".92" stop-color="white" stop-opacity=".039"/>
   <stop offset=".93" stop-color="white" stop-opacity=".032"/>
   <stop offset=".94" stop-color="white" stop-opacity=".025"/>
   <stop offset=".95" stop-color="white" stop-opacity=".019"/>
   <stop offset=".96" stop-color="white" stop-opacity=".013"/>
   <stop offset=".97" stop-color="white" stop-opacity=".0075"/>
   <stop offset=".98" stop-color="white" stop-opacity=".0035"/>
   <stop offset=".99" stop-color="white" stop-opacity=".001"/>
   <stop offset="1" stop-color="white" stop-opacity="0"/>
  </radialGradient>
  <mask id="dsh-fairy-halo-layer-mask" mask-type="luminance" maskUnits="userSpaceOnUse" maskContentUnits="userSpaceOnUse" x="-260" y="-120" width="680" height="440">
   <rect x="-260" y="-120" width="680" height="440" fill="url(#dsh-fairy-halo-layer-fade)"/>
  </mask>
 </defs>
 <g mask="url(#dsh-fairy-halo-layer-mask)" opacity=".99">${buildHaloLines()}</g>
</svg>`;
