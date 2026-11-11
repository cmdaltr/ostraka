import '@fontsource-variable/inter';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/600.css';
import './styles/base.css';

import { StrictMode, useEffect } from 'react';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { restore, useSession } from './auth/session';
import { AppShell } from './components/AppShell';
import { Loading } from './components/sherd/Ostrakon';
import { AcceptInvite } from './pages/AcceptInvite';
import { DesignSystem } from './pages/DesignSystem';
import { DetectionGapsPage } from './pages/DetectionGapsPage';
import { HelpLicence } from './pages/HelpLicence';
import { IncidentPage } from './pages/incident/IncidentPage';
import { Installation } from './pages/Installation';
import { Incidents, listQuery } from './pages/Incidents';
import { People } from './pages/People';
import { Playbooks } from './pages/Playbooks';
import { Profile } from './pages/Profile';
import { SignIn } from './pages/SignIn';
import { Status } from './pages/Status';
import { Work } from './pages/Work';

function App() {
  const session = useSession();
  const location = useLocation();
  // Reachable whatever the session, and without asking for one: an invitation, status, the design
  // reference, and Help → Licence
  const isPublic = /^\/(invite\/|status$|design$|help\/licence$)/.test(location.pathname);
  useEffect(() => {
    if (!isPublic && session.status === 'loading') restore();
  }, [isPublic, session.status]);
  // Opening on the incident list: fetch it with the session, so one sherd covers both and the list
  // arrives from the cache rather than assembling a second time
  const first = useQuery({
    ...listQuery('open', 'all'),
    enabled: session.status === 'signed-in' && location.pathname === '/',
  });
  const opening = session.status === 'loading' || (first.fetchStatus === 'fetching' && first.isPending);

  if (location.pathname.startsWith('/invite/'))
    return (
      <Routes>
        <Route path="/invite/:token" element={<AcceptInvite />} />
      </Routes>
    );
  if (location.pathname === '/status' || location.pathname === '/design')
    return (
      <main className="shell-main">{location.pathname === '/status' ? <Status /> : <DesignSystem />}</main>
    );

  if (location.pathname === '/help/licence')
    return (
      <main className="shell-main">
        <HelpLicence signedIn={session.status === 'signed-in'} />
      </main>
    );

  // Opening the app: the sherd assembles while the session is restored, then mends and glows once
  // before sign-in or the app takes its place
  return (
    <Loading ready={!opening} label="Opening Ostraka">
      {session.status === 'signed-out' && <SignIn />}
      {session.status === 'signed-in' && (
        <Routes>
          <Route element={<AppShell me={session.me} />}>
            <Route index element={<Incidents me={session.me} />} />
            <Route path="incidents/:ref" element={<IncidentPage me={session.me} />} />
            <Route path="work" element={<Work me={session.me} />} />
            <Route path="detection-gaps" element={<DetectionGapsPage />} />
            <Route path="playbooks" element={<Playbooks me={session.me} />} />
            <Route path="people" element={<People me={session.me} />} />
            <Route path="profile" element={<Profile me={session.me} />} />
            {session.me.role === 'admin' && <Route path="installation" element={<Installation />} />}
            <Route path="settings" element={<Navigate to="/profile" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      )}
    </Loading>
  );
}

// Live events say what changed, so cached data only refetches when told to or when the tab returns
const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, retry: 1 } },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
