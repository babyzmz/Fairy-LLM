import { describe, expect, it } from "vitest";

import { RendererRecoveryCircuit } from "./rendererRecovery";

describe("RendererRecoveryCircuit", () => {
  it("locks compatibility after two context losses within five minutes", () => {
    const circuit = new RendererRecoveryCircuit();
    expect(circuit.recordContextLoss(1_000)).toBe(false);
    expect(circuit.recordContextLoss(299_000)).toBe(true);
    expect(circuit.compatibilityLocked).toBe(true);
  });

  it("does not combine incidents outside the recovery window", () => {
    const circuit = new RendererRecoveryCircuit();
    expect(circuit.recordContextLoss(1_000)).toBe(false);
    expect(circuit.recordContextLoss(302_000)).toBe(false);
    expect(circuit.compatibilityLocked).toBe(false);
  });
});
