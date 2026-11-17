import { Navigate, useLocation, useSearchParams } from 'react-router-dom';
import type { Me } from '../api/types';
import { Installation, installationTabs } from './Installation';
import type { InstallationTab } from './Installation';
import { People } from './People';

type Tab = 'people' | InstallationTab;

// Config, behind the cog: the people (or, for those who don't manage them, the directory), then for
// admins the installation's licence, report brands, webhooks, integrations and MISP
export function Config({ me }: { me: Me }) {
  const [params, setParams] = useSearchParams();
  const manages = me.role === 'admin' || me.role === 'team_lead';
  const tabs: { key: Tab; label: string }[] = [
    { key: 'people', label: manages ? 'People' : 'Directory' },
    ...(me.role === 'admin' ? installationTabs : []),
  ];
  const tab = tabs.find((t) => t.key === params.get('tab'))?.key ?? 'people';
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Config</h1>
          <p className="hint">
            {me.role === 'admin'
              ? 'People and teams, the licence, how exported reports look, webhooks, Rowanholt and Shikari, and MISP.'
              : manages
                ? 'People and teams.'
                : 'Who’s who.'}
          </p>
        </div>
      </div>
      {tabs.length > 1 && (
        <nav className="tabs" aria-label="Config sections">
          {tabs.map((t) => (
            <button
              key={t.key}
              type="button"
              aria-current={tab === t.key ? 'page' : undefined}
              onClick={() => setParams(t.key === 'people' ? {} : { tab: t.key })}
            >
              {t.label}
            </button>
          ))}
        </nav>
      )}
      {tab === 'people' ? <People me={me} /> : <Installation tab={tab} />}
    </>
  );
}

/** The old addresses, /installation and /people, still lead to the same place under Config */
export function ToConfig({ tab }: { tab?: Tab }) {
  const asked = new URLSearchParams(useLocation().search).get('tab');
  const to = asked ?? tab;
  return <Navigate to={to ? `/config?tab=${to}` : '/config'} replace />;
}
