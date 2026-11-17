// How an arrow runs between two steps in a playbook's drawing. Pure, so it's unit-tested. The line
// stops short of the card and a drawn arrowhead (not an SVG marker, which can't take the line's colour
// in every browser) puts its tip on the card's edge.

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface Route {
  d: string; // the line
  head: string; // the arrowhead, a filled triangle
  x: number; // where a label sits
  y: number;
}

export const HEAD = 8;
const GAP = 12; // less space than this between two cards side by side, and the arrow goes up or down

function arrowhead(tipX: number, tipY: number, dx: number, dy: number): string {
  const bx = tipX - dx * HEAD;
  const by = tipY - dy * HEAD;
  const px = -dy * HEAD * 0.55;
  const py = dx * HEAD * 0.55;
  const f = (n: number) => Math.round(n * 10) / 10;
  return `M${f(tipX)},${f(tipY)} L${f(bx + px)},${f(by + py)} L${f(bx - px)},${f(by - py)} Z`;
}

/** From card `a` to card `b`. `back` is a decision sending the work back to an earlier step: it goes
 * over the top. Otherwise side to side when there's room, and top to bottom when one card is above
 * the other. Every curve leaves and arrives square to the card's edge. */
export function route(a: Box, b: Box, back = false): Route {
  const acx = (a.left + a.right) / 2;
  const bcx = (b.left + b.right) / 2;
  const acy = (a.top + a.bottom) / 2;
  const bcy = (b.top + b.bottom) / 2;

  if (back) {
    const lift = Math.min(a.top, b.top) - 22;
    const end = b.top - HEAD;
    return {
      d: `M${acx},${a.top} C${acx},${lift} ${bcx},${lift} ${bcx},${end}`,
      head: arrowhead(bcx, b.top, 0, 1),
      x: (acx + bcx) / 2,
      y: lift + 4,
    };
  }
  if (b.left >= a.right + GAP || a.left >= b.right + GAP) {
    const rightwards = b.left >= a.right + GAP;
    const dir = rightwards ? 1 : -1;
    const x1 = rightwards ? a.right : a.left;
    const tip = rightwards ? b.left : b.right;
    const end = tip - dir * HEAD;
    const k = Math.max(24, Math.abs(end - x1) / 2);
    return {
      d: `M${x1},${acy} C${x1 + dir * k},${acy} ${end - dir * k},${bcy} ${end},${bcy}`,
      head: arrowhead(tip, bcy, dir, 0),
      x: (x1 + end) / 2,
      y: (acy + bcy) / 2 - 6,
    };
  }
  const downwards = b.top >= a.bottom;
  const dir = downwards ? 1 : -1;
  const y1 = downwards ? a.bottom : a.top;
  const tip = downwards ? b.top : b.bottom;
  const end = tip - dir * HEAD;
  const k = Math.max(12, Math.abs(end - y1) / 2);
  return {
    d: `M${acx},${y1} C${acx},${y1 + dir * k} ${bcx},${end - dir * k} ${bcx},${end}`,
    head: arrowhead(bcx, tip, 0, dir),
    x: (acx + bcx) / 2 + 6,
    y: (y1 + end) / 2,
  };
}
