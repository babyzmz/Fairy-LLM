const RECOVERY_WINDOW_MS = 5 * 60 * 1_000;

export class RendererRecoveryCircuit {
  private contextLosses: number[] = [];
  private locked = false;

  recordContextLoss(nowMs: number): boolean {
    const cutoff = nowMs - RECOVERY_WINDOW_MS;
    this.contextLosses = this.contextLosses.filter((at) => at >= cutoff);
    this.contextLosses.push(nowMs);
    if (this.contextLosses.length >= 2) this.locked = true;
    return this.locked;
  }

  get compatibilityLocked(): boolean {
    return this.locked;
  }
}
