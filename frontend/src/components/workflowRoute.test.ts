import { describe, expect, it } from 'vitest';
import { HEAD, route } from './workflowRoute';

const box = (left: number, top: number, width = 100, height = 40) => ({
  left,
  top,
  right: left + width,
  bottom: top + height,
});
const start = (d: string) =>
  d
    .match(/^M([\d.-]+),([\d.-]+)/)!
    .slice(1)
    .map(Number);
const finish = (d: string) => d.trim().split(' ').pop()!.split(',').map(Number);
const tip = (head: string) => start(head);

describe('arrows between steps', () => {
  it('runs side to side, leaving the right edge and stopping an arrowhead short of the left one', () => {
    const r = route(box(0, 0), box(200, 100));
    expect(start(r.d)).toEqual([100, 20]);
    expect(finish(r.d)).toEqual([200 - HEAD, 120]);
    expect(tip(r.head)).toEqual([200, 120]);
  });

  it('runs down when one step is under the other, even across lanes', () => {
    const r = route(box(0, 0), box(30, 150));
    expect(start(r.d)).toEqual([50, 40]);
    expect(tip(r.head)).toEqual([80, 150]);
  });

  it('runs up when the step it opens is above', () => {
    const r = route(box(0, 150), box(0, 0));
    expect(start(r.d)).toEqual([50, 150]);
    expect(tip(r.head)).toEqual([50, 40]);
  });

  it('goes over the top when a decision sends the work back', () => {
    const r = route(box(300, 100), box(0, 0), true);
    expect(start(r.d)).toEqual([350, 100]);
    expect(tip(r.head)).toEqual([50, 0]);
    expect(r.y).toBeLessThan(0);
  });
});
