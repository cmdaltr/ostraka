import { brand } from '../brand';
import { Inscription } from './Inscription';
import './BrandMark.css';

// The sign-in lockup: the name as an inscription, and the tagline
export function BrandMark() {
  return (
    <div className="brandmark">
      <Inscription />
      <p>{brand.tagline}</p>
    </div>
  );
}
