import { describe, expect, it } from "vitest";

import { LiquidMotionController, SpeechLevelEnvelope } from "./liquidMotion";

describe("Liquid motion", () => {
  it("converges through a bounded damping spring", () => {
    const motion = new LiquidMotionController({ droplet: 0, bridge: 0, capsule: 0 });
    motion.setShapeTarget({ droplet: 1, bridge: 1, capsule: 1 });
    let maximum = 0;
    let sample = motion.sample(0);
    for (let now = 16; now <= 800; now += 16) {
      sample = motion.sample(now);
      maximum = Math.max(maximum, sample.droplet, sample.bridge, sample.capsule);
    }
    expect(maximum).toBeLessThanOrEqual(1.04);
    expect(sample.droplet).toBeCloseTo(1, 3);
    expect(sample.bridge).toBeCloseTo(1, 3);
    expect(sample.capsule).toBeCloseTo(1, 3);

    motion.setShapeTarget({ droplet: 0, bridge: 0, capsule: 0 });
    for (let now = 816; now <= 1_600; now += 16) sample = motion.sample(now);
    expect(sample.droplet).toBeCloseTo(0, 3);
    expect(sample.bridge).toBeCloseTo(0, 3);
    expect(sample.capsule).toBeCloseTo(0, 3);
  });

  it("snaps directly to the target for reduced motion", () => {
    const motion = new LiquidMotionController({ droplet: 0, bridge: 0, capsule: 0 });
    motion.setShapeTarget({ droplet: 1, bridge: 1, capsule: 1 }, true);
    expect(motion.sample(0)).toMatchObject({ droplet: 1, bridge: 1, capsule: 1 });
  });

  it("uses a 60ms speech attack and 180ms release envelope", () => {
    const envelope = new SpeechLevelEnvelope();
    envelope.setTarget(1);
    expect(envelope.step(60)).toBeCloseTo(1 - Math.exp(-1), 5);
    envelope.setTarget(0);
    expect(envelope.step(180)).toBeCloseTo((1 - Math.exp(-1)) * Math.exp(-1), 5);
  });
});
