import { brand } from '../brand';
import './Wordmark.css';

// The product name as a row of split-flap tiles, like the sign-in board, with the ™ the mark is claimed by
export function Wordmark({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  return (
    <span className={`wordmark wordmark-${size}`} role="img" aria-label={brand.name}>
      {brand.name.toUpperCase().split('').map((ch, i) => (
        <span key={i} aria-hidden="true">
          {ch}
        </span>
      ))}
      <sup className="wordmark-tm" aria-hidden="true">
        ™
      </sup>
    </span>
  );
}
