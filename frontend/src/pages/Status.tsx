import { useEffect, useState } from 'react';
import { brand } from '../brand';
import { Wordmark } from '../components/Wordmark';
import './Status.css';
import { Assembling } from '../components/sherd/Ostrakon';

interface Ready {
  status: 'ok' | 'degraded';
  checks: Record<string, string>;
}

type State = { kind: 'loading' } | { kind: 'ready'; body: Ready } | { kind: 'unreachable' };

// Until accounts exist (phase 1) the front page reports whether ostraka and its dependencies are up
export function Status() {
  const [state, setState] = useState<State>({ kind: 'loading' });
  const [version, setVersion] = useState<string>();

  useEffect(() => {
    fetch('/api/ready')
      .then(async (r) => setState({ kind: 'ready', body: (await r.json()) as Ready }))
      .catch(() => setState({ kind: 'unreachable' }));
    fetch('/api/health')
      .then(async (r) => setVersion(((await r.json()) as { version: string }).version))
      .catch(() => undefined);
  }, []);

  return (
    <>
      <section className="status-hero">
        <Wordmark size="lg" />
        <p>
          {brand.tagline}. The platform is being rebuilt; this page shows whether the server and its
          dependencies are answering.
        </p>
      </section>
      <section className="status-panel" aria-live="polite">
        <div className="status-row">
          <span className="label">Server</span>
          <span data-testid="server-status">
            {state.kind === 'loading' && (
              <>
                <Assembling inline width="1.4em" label="Checking the server" /> Checking…
              </>
            )}
            {state.kind === 'unreachable' && <strong className="bad">Not answering</strong>}
            {state.kind === 'ready' && (
              <strong className={state.body.status === 'ok' ? 'good' : 'bad'}>
                {state.body.status === 'ok' ? 'Ready' : 'Degraded'}
              </strong>
            )}
          </span>
          {version && <span className="mono num muted">v{version}</span>}
        </div>
        {state.kind === 'ready' &&
          Object.entries(state.body.checks).map(([name, result]) => (
            <div className="status-row" key={name}>
              <span className="label">{name}</span>
              <span className={result === 'ok' ? 'good' : 'bad'}>{result === 'ok' ? 'Answering' : 'Unavailable'}</span>
            </div>
          ))}
      </section>
    </>
  );
}
