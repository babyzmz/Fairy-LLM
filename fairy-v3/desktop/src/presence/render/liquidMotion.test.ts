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

  it("contracts for 220ms and applies a bounded 180ms core rebound", () => {
    const motion = new LiquidMotionController({ droplet: 1, bridge: 1, capsule: 1 });
    motion.sample(0);
    motion.beginReturn(0);
    expect(motion.sample(110)).toMatchObject({
      droplet: 0.5,
      bridge: 0.5,
      capsule: 0.5,
      return_bounce: 0,
    });
    expect(motion.sample(220)).toMatchObject({
      droplet: 0,
      bridge: 0,
      capsule: 0,
      return_bounce: 0,
    });
    expect(motion.sample(310).return_bounce).toBeCloseTo(0.04, 5);
    expect(motion.sample(400)).toMatchObject({
      droplet: 0,
      bridge: 0,
      capsule: 0,
      return_bounce: 0,
    });
  });

  it("uses a 160ms opacity-safe return for reduced motion", () => {
    const motion = new LiquidMotionController({ droplet: 1, bridge: 1, capsule: 1 });
    motion.beginReturn(0, true);
    expect(motion.sample(0)).toMatchObject({
      droplet: 0,
      bridge: 0,
      capsule: 0,
      return_bounce: 0,
    });
  });

  it("continues from the contracted shape when return is interrupted", () => {
    const motion = new LiquidMotionController({ droplet: 1, bridge: 1, capsule: 1 });
    motion.sample(0);
    motion.beginReturn(0);
    expect(motion.sample(110).capsule).toBeCloseTo(0.5, 5);

    motion.cancelReturn(110);
    motion.setShapeTarget({ droplet: 0, bridge: 0, capsule: 0 });

    expect(motion.sample(110).capsule).toBeCloseTo(0.5, 5);
    expect(motion.sample(126).capsule).toBeLessThan(0.5);
  });
});
