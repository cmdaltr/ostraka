import { Link } from 'react-router-dom';
import { GitHubMark } from './GitHubMark';
import './SiteLinks.css';

// The licence and the author, at the foot of every page. Inside the app the licence opens without a reload,
// so the session carries over; before sign-in it's a plain link.
export function SiteLinks({ inApp = false }: { inApp?: boolean }) {
  return (
    <p className="hint site-links">
      {inApp ? <Link to="/help/licence">Licence</Link> : <a href="/help/licence">Licence</a>}
      <a href="https://github.com/cmdaltr" target="_blank" rel="noopener noreferrer" aria-label="cmdaltr on GitHub">
        <GitHubMark />
      </a>
    </p>
  );
}
