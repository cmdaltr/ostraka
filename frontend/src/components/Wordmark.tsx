import { brand } from '../brand';
import { NAME, layout } from './lettering';
import './Wordmark.css';

const { width, strokes } = layout(NAME);

// The name in the sign-in inscription's lettering, still and in red ochre, with the ™ the mark is
// claimed by
export function Wordmark({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  return (
    <span className={`wordmark wordmark-${size}`} role="img" aria-label={brand.name}>
      <svg viewBox={`-6 -6 ${width + 12} 112`} aria-hidden="true">
        {strokes.map((s, i) => (
          <path key={i} d={s.d} transform={`translate(${s.x} 0)`} />
        ))}
      </svg>
      <sup className="wordmark-tm" aria-hidden="true">
        ™
      </sup>
    </span>
  );
}
