import { describe, expect, it } from "vitest";
import { approach, wheelFactor } from "./smoothZoom";

describe("плавный масштаб", () => {
  it("щелчок колеса – как в d3-zoom (≈ ×1,18), щипок трекпада сильнее", () => {
    expect(wheelFactor({ deltaY: -120, deltaMode: 0, ctrlKey: false })).toBeCloseTo(1.181, 3);
    expect(wheelFactor({ deltaY: 120, deltaMode: 0, ctrlKey: false })).toBeCloseTo(1 / 1.181, 3);
    expect(wheelFactor({ deltaY: -3, deltaMode: 1, ctrlKey: false })).toBeCloseTo(Math.pow(2, 0.15), 5);
    expect(wheelFactor({ deltaY: -10, deltaMode: 0, ctrlKey: true })).toBeCloseTo(Math.pow(2, 0.2), 5);
  });
  it("масштаб догоняет цель по кадрам и встаёт ровно на неё", () => {
    let z = 1;
    let frames = 0;
    while (z !== 2 && frames < 100) { z = approach(z, 2, 16); frames++; }
    expect(z).toBe(2);
    expect(frames).toBeGreaterThan(5);   // не прыжком
    expect(frames).toBeLessThan(40);     // и не дольше ~0,6 с
  });
  it("не зависит от частоты кадров", () => {
    const a = approach(approach(1, 2, 16), 2, 16);
    const b = approach(1, 2, 32);
    expect(a).toBeCloseTo(b, 6);
  });
});
