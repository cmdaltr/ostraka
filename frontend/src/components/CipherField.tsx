import { useEffect, useRef } from 'react';

const GLYPHS = [
  ...'01ABCDEF', ...'ΣΔΩΦΨλπ', ...'∴∵⊕⊗∞≡≠', ...'†‡§¶※', ...'ᚠᚢᚦᚨᚱᚲᚷᚹ', ...'#@%&*^',
];
const PHRASES = ['E(m) = mᵉ mod n', 'H(x) = SHA-256', 'AES-256-GCM', 'ECDSA P-384', '∀x ∃y: f(x) = y', 'T1059.001'];

interface Glyph {
  x: number;
  y: number;
  char: string;
  speed: number;
  alpha: number;
  size: number;
  angle: number;
  spin: number;
}

const pick = <T,>(list: T[]) => list[Math.floor(Math.random() * list.length)];

function glyph(width: number, height: number): Glyph {
  return {
    x: Math.random() * width,
    y: Math.random() * height,
    char: pick(GLYPHS),
    speed: 0.15 + Math.random() * 0.35,
    alpha: 0.03 + Math.random() * 0.09,
    size: 12 + Math.random() * 14,
    angle: Math.random() * Math.PI * 2,
    spin: (Math.random() - 0.5) * 0.008,
  };
}

// The drifting cipher glyphs behind sign-in, from the first ciphr. Colour comes from --backdrop-glyph,
// so it follows the palette; with reduced motion it draws one still frame.
export function CipherField() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let glyphs: Glyph[] = [];
    let colour = '';
    let frame = 0;
    let raf = 0;

    const readColour = () => {
      const root = getComputedStyle(document.documentElement);
      colour = root.getPropertyValue('--backdrop-glyph').trim();
    };
    const resize = () => {
      const scale = window.devicePixelRatio || 1;
      canvas.width = window.innerWidth * scale;
      canvas.height = window.innerHeight * scale;
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      const count = Math.floor((window.innerWidth * window.innerHeight) / 11000);
      glyphs = Array.from({ length: count }, () => glyph(window.innerWidth, window.innerHeight));
    };

    const draw = () => {
      const width = window.innerWidth;
      const height = window.innerHeight;
      // The palette can change under the page; checking once a second is plenty
      if (frame++ % 60 === 0) readColour();
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = colour;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      for (const g of glyphs) {
        ctx.save();
        ctx.globalAlpha = g.alpha;
        ctx.translate(g.x, g.y);
        ctx.rotate(g.angle);
        ctx.font = `${g.size}px "JetBrains Mono", monospace`;
        ctx.fillText(g.char, 0, 0);
        ctx.restore();
        g.y += g.speed;
        g.angle += g.spin;
        if (Math.random() < 0.002) g.char = pick(GLYPHS);
        if (g.y > height + 20) Object.assign(g, { y: -20, x: Math.random() * width });
      }
      const time = performance.now() / 1000;
      ctx.font = '14px "JetBrains Mono", monospace';
      PHRASES.forEach((text, i) => {
        ctx.globalAlpha = 0.06 + Math.sin(time + i) * 0.02;
        const x = (Math.sin(time * 0.3 + i * 1.5) * 0.3 + 0.5) * width;
        const y = (Math.cos(time * 0.2 + i * 2) * 0.3 + 0.5) * height;
        ctx.fillText(text, x, y);
      });
      ctx.globalAlpha = 1;
      if (!still) raf = requestAnimationFrame(draw);
    };

    resize();
    draw();
    const onResize = () => {
      resize();
      if (still) draw();
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      cancelAnimationFrame(raf);
    };
  }, []);

  return <canvas ref={ref} className="cipher-field" aria-hidden="true" />;
}
