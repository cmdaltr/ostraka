// The mended sherd (public/brand/ostrakon-*.png, 560 × 408) cut along its own cracks. The pieces tile
// the whole image, so put back together they are exactly the mark; apart, each is the artwork clipped
// to one fragment. Coordinates are the image's pixels.

export const SHERD = { width: 560, height: 408 } as const;

export type Point = readonly [number, number];

export const FRAGMENTS: readonly (readonly Point[])[] = [
  [[0, 0], [205, 0], [215, 45], [240, 95], [245, 178], [110, 165], [0, 158]],
  [[205, 0], [345, 0], [345, 48], [330, 75], [240, 95], [215, 45]],
  [[345, 0], [560, 0], [560, 205], [430, 168], [330, 175], [245, 178], [240, 95], [330, 75], [345, 48]],
  [[0, 158], [110, 165], [245, 178], [265, 240], [130, 240], [0, 248]],
  [[245, 178], [330, 175], [430, 168], [435, 210], [440, 262], [360, 255], [265, 240]],
  [[430, 168], [560, 205], [560, 272], [440, 262], [435, 210]],
  [[0, 248], [130, 240], [265, 240], [270, 320], [300, 408], [0, 408]],
  [[265, 240], [360, 255], [440, 262], [420, 320], [385, 365], [300, 408], [270, 320]],
  [[440, 262], [560, 272], [560, 408], [300, 408], [385, 365], [420, 320]],
];

const pct = ([x, y]: Point) => `${((x / SHERD.width) * 100).toFixed(2)}% ${((y / SHERD.height) * 100).toFixed(2)}%`;

/** A fragment as a CSS clip-path over the whole image. */
export const clipPath = (fragment: readonly Point[]) => `polygon(${fragment.map(pct).join(', ')})`;

/** The fragment's centre, as percentages of the image: where it turns about. */
export function centre(fragment: readonly Point[]): Point {
  const x = fragment.reduce((sum, [px]) => sum + px, 0) / fragment.length;
  const y = fragment.reduce((sum, [, py]) => sum + py, 0) / fragment.length;
  return [(x / SHERD.width) * 100, (y / SHERD.height) * 100];
}

/** Shoelace area, in square pixels. */
export function area(fragment: readonly Point[]): number {
  let twice = 0;
  fragment.forEach(([x1, y1], i) => {
    const [x2, y2] = fragment[(i + 1) % fragment.length];
    twice += x1 * y2 - x2 * y1;
  });
  return Math.abs(twice) / 2;
}

/** Where a fragment sits relative to its place in the whole: x and y as percentages of the sherd's
 * own size, r in degrees. */
export interface Pose {
  x: number;
  y: number;
  r: number;
}

export const HOME: Pose = { x: 0, y: 0, r: 0 };

/** A small seeded generator, so a scatter can be repeated in tests. */
export function random(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Every fragment thrown outwards from the middle, as if the sherd were dropped: each along the line
 * from the sherd's centre through its own, a little off it, and turned. */
export function scatter(seed: number, spread = 1): Pose[] {
  const next = random(seed);
  return FRAGMENTS.map((fragment) => {
    const [cx, cy] = centre(fragment);
    const angle = Math.atan2(cy - 50, cx - 50) + (next() - 0.5) * 0.9;
    const distance = (32 + next() * 30) * spread;
    return {
      x: Math.cos(angle) * distance,
      y: Math.sin(angle) * distance,
      r: (next() - 0.5) * 70 * spread,
    };
  });
}

/** Part of the way home: 0 is the pose given, 1 is in place. */
export const towards = (pose: Pose, t: number): Pose => ({
  x: pose.x * (1 - t),
  y: pose.y * (1 - t),
  r: pose.r * (1 - t),
});

/** How far together the pieces are after `elapsed` ms of work whose length nobody knows: quick at
 * first, slower and slower, and never quite whole until the work says it's done. */
export const creep = (elapsed: number, halfLife = 1200, ceiling = 0.9) =>
  Math.min(ceiling, ceiling * (1 - 0.5 ** (Math.max(0, elapsed) / halfLife)));

export const transform = ({ x, y, r }: Pose) =>
  `translate(${x.toFixed(2)}%, ${y.toFixed(2)}%) rotate(${r.toFixed(1)}deg)`;
