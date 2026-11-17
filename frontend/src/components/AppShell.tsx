import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';
import { day, getLicence } from '../api/installation';
import type { Me } from '../api/types';
import { signOut } from '../auth/session';
import { brand } from '../brand';
import { startLive } from '../live/live';
import { Bell } from './Bell';
import { CogIcon } from './CogIcon';
import { SignOutIcon } from './SignOutIcon';
import { SiteLinks } from './SiteLinks';
import { SherdField } from './sherd/SherdField';
import { Wordmark } from './Wordmark';
import './AppShell.css';

// Admins hear about a lapsing licence wherever they are, not only on the Installation page
function LicenceRunningOut() {
  const licence = useQuery({ queryKey: ['licence'], queryFn: getLicence, staleTime: 60 * 60 * 1000 });
  const l = licence.data;
  if (!l || (l.state !== 'grace' && l.state !== 'lapsed')) return null;
  return (
    <p className="notice notice-error licence-banner" role="status">
      {l.state === 'grace'
        ? `The licence has run out. It keeps working until ${day(l.grace_ends)}, then Ostraka goes back to the free edition.`
        : 'The licence has run out: Ostraka is on the free edition.'}{' '}
      <Link to="/config?tab=licence">Install the renewal</Link>
    </p>
  );
}

// Two letters for the profile chip: first and last name, or the start of an address used as a name
export function initials(name: string): string {
  const words = name
    .split('@')[0]
    .split(/[\s._-]+/)
    .filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[words.length - 1][0] : (words[0] ?? '?').slice(0, 2);
  return letters.toUpperCase();
}

export function AppShell({ me }: { me: Me }) {
  const manages = me.role === 'admin' || me.role === 'team_lead';
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  useEffect(() => startLive(queryClient), [queryClient]);
  return (
    <div className="shell">
      <SherdField />
      <header className="shell-header">
        <NavLink to="/" className="shell-home" aria-label="Ostraka home">
          <Wordmark size="md" />
        </NavLink>
        <nav className="shell-nav">
          <NavLink to="/" end>
            Incidents
          </NavLink>
          {me.role !== 'observer' && <NavLink to="/work">Your work</NavLink>}
          <NavLink to="/detection-gaps">Detections</NavLink>
          {manages && <NavLink to="/playbooks">Playbooks</NavLink>}
        </nav>
        <div className="shell-user">
          {me.role !== 'observer' && (
            // On every page: the form opens over the incident list
            <span className="shell-declare">
              <button className="btn" type="button" onClick={() => navigate('/?declare=hunt')}>
                Start a hunt
              </button>
              <button className="btn btn-primary" type="button" onClick={() => navigate('/?declare=incident')}>
                Declare an incident
              </button>
            </span>
          )}
          {me.role !== 'observer' && <span className="shell-divider" aria-hidden="true" />}
          <Bell />
          {/* you, as one chip: your initials and name, the way to your profile */}
          <NavLink to="/profile" className="shell-me" aria-label="Profile" title={`${me.name}: your profile`}>
            <span className="shell-avatar" aria-hidden="true">
              {initials(me.name)}
            </span>
            <span className="shell-name">{me.name}</span>
          </NavLink>
          {/* People (or the directory) for everyone, and the installation for admins */}
          <NavLink to="/config" className="shell-icon" aria-label="Config" title="Config">
            <CogIcon />
          </NavLink>
          <button className="shell-icon" type="button" aria-label="Sign out" title="Sign out" onClick={() => signOut()}>
            <SignOutIcon />
          </button>
        </div>
      </header>
      <main className="shell-main">
        {me.role === 'admin' && <LicenceRunningOut />}
        <Outlet />
      </main>
      <footer className="shell-footer">
        <SiteLinks inApp />
        <p className="hint">{brand.trademark}</p>
      </footer>
    </div>
  );
}
