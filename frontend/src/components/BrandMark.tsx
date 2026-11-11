import { brand } from '../brand';
import { CipherField } from './CipherField';
import { DispersingOstrakon } from './sherd/Ostrakon';
import { SplitFlap } from './SplitFlap';
import './BrandMark.css';

// The sign-in lockup: the mended sherd (clay by day, limestone by night; click it and it breaks apart and
// mends), the departures board flapping through words, what the letters stand for, and the drifting
// glyphs behind the page
export function BrandMark() {
  return (
    <div className="brandmark">
      <CipherField />
      <span className="brandmark-sherd">
        <DispersingOstrakon width="min(336px, 84vw)" label={`${brand.name}: the mended sherd`} />
      </span>
      <SplitFlap />
      <p>{brand.expansion}</p>
    </div>
  );
}
