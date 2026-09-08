/** Missing form comes from a pre-migration preference snapshot. */
export function nativeFormAllowed(form?: string): boolean {
  return form !== 'hdd_eye';
}
/** Even a pending native start must drain before placing an SVG in its window. */
export function eyeMayMount(state: string): boolean {
  return !['starting', 'running', 'stopping'].includes(state);
}
