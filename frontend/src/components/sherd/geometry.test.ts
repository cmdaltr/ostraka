import { describe, expect, it } from 'vitest';
import { FRAGMENTS, SHERD, area, clipPath, creep, scatter, towards } from './geometry';

describe('the sherd cut along its cracks', () => {
  it('tiles the whole image, so the pieces together are exactly the mark', () => {
    const total = FRAGMENTS.reduce((sum, f) => sum + area(f), 0);
    expect(total).toBeCloseTo(SHERD.width * SHERD.height, 0);
  });

  it('gives each piece a clip-path in percentages of the image', () => {
    expect(clipPath(FRAGMENTS[0])).toMatch(/^polygon\(0\.00% 0\.00%, 36\.61% 0\.00%/);
  });
});

describe('scattering and gathering', () => {
  it('scatters the same way for the same seed, and differently for another', () => {
    expect(scatter(7)).toEqual(scatter(7));
    expect(scatter(7)).not.toEqual(scatter(8));
    expect(scatter(7)).toHaveLength(FRAGMENTS.length);
  });

  it('throws every piece clear of its place', () => {
    for (const pose of scatter(3)) expect(Math.hypot(pose.x, pose.y)).toBeGreaterThan(20);
  });

  it('brings a piece home as t reaches 1', () => {
    const pose = scatter(5)[2];
    expect(towards(pose, 0)).toEqual(pose);
    const home = towards(pose, 1);
    expect([home.x, home.y, home.r].map(Math.abs)).toEqual([0, 0, 0]);
  });

  it('creeps closer for work of unknown length but never closes until it is done', () => {
    const steps = [0, 500, 1000, 3000, 60000].map((ms) => creep(ms));
    expect(steps[0]).toBe(0);
    for (let i = 1; i < steps.length; i += 1) expect(steps[i]).toBeGreaterThan(steps[i - 1]);
    expect(steps.at(-1)).toBeLessThanOrEqual(0.9);
  });
});
