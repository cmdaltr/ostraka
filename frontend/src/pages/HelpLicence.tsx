import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { brand } from '../brand';
import { Loading } from '../components/sherd/Ostrakon';
import { Notice, messageOf } from '../components/ui';
import './HelpLicence.css';

interface Legal {
  licence: string;
  terms_version: string;
  terms: string;
}

const tabs = [
  { key: 'licence', label: 'Elastic License 2.0' },
  { key: 'terms', label: 'Commercial licence' },
] as const;

// Help → Licence. Public, like the status page, so it opens before sign-in and from the desktop app's
// Help menu; the text comes from the server, so it's always what this installation ships.
export function HelpLicence({ signedIn }: { signedIn: boolean }) {
  const [params, setParams] = useSearchParams();
  const tab = params.get('show') === 'terms' ? 'terms' : 'licence';
  const legal = useQuery({ queryKey: ['legal'], queryFn: () => api<Legal>('/api/legal') });
  return (
    <div className="help-licence">
      <div className="page-head">
        <div>
          <h1>Licence</h1>
          <p className="hint">
            <Link to="/">{signedIn ? `Back to ${brand.name}` : 'Sign in'}</Link>
          </p>
        </div>
      </div>
      <section className="panel" aria-label="What you agree to">
        <p>
          <strong>
            By downloading, installing or using {brand.name}, you agree to the Elastic License 2.0, and, where you
            install a Pro or Services licence key, to the {brand.name} Commercial Licence. If you do not agree, do
            not install or use the software.
          </strong>{' '}
          Using it is what signifies acceptance, so installers don’t stop to ask. Installing a licence key is the
          exception: an admin accepts the commercial terms then, and {brand.name} records who accepted, when and
          which version.
        </p>
      </section>
      <nav className="tabs" aria-label="Licence documents">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            aria-current={tab === t.key ? 'page' : undefined}
            onClick={() => setParams(t.key === 'licence' ? {} : { show: t.key })}
          >
            {t.label}
            {t.key === 'terms' && legal.data && `, version ${legal.data.terms_version}`}
          </button>
        ))}
      </nav>
      {legal.error && <Notice kind="error">{messageOf(legal.error)}</Notice>}
      <Loading ready={!legal.isPending} label="Loading the licence">
        {legal.data && (
          <section
            className="panel legal-text"
            tabIndex={0}
            aria-label={tab === 'licence' ? 'Elastic License 2.0' : `Commercial licence, version ${legal.data.terms_version}`}
          >
            {tab === 'licence' ? legal.data.licence : legal.data.terms}
          </section>
        )}
      </Loading>
    </div>
  );
}
