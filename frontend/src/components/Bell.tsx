import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { Notifications } from '../api/work';
import { formatDate } from './ui';
import './Bell.css';

// The notification centre: unread count on the button, the latest thirty in the panel
export function Bell() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const list = useQuery({
    queryKey: ['notifications'],
    queryFn: () => api<Notifications>('/api/notifications'),
    refetchInterval: 5 * 60_000, // the live socket is the usual trigger; this catches a long disconnect
  });
  const unread = list.data?.unread ?? 0;

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !panel.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', close);
    };
  }, [open]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['notifications'] });
  const go = async (id: string, link: string, read: boolean) => {
    setOpen(false);
    if (!read) api(`/api/notifications/${id}/read`, { method: 'POST' }).then(refresh, refresh);
    if (link) navigate(link);
  };

  return (
    <div className="bell" ref={panel}>
      <button
        className="bell-button"
        type="button"
        aria-expanded={open}
        aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}
        onClick={() => setOpen(!open)}
      >
        <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden>
          <path
            d="M10 2.5a5 5 0 0 0-5 5v3.2L3.6 13.4a.6.6 0 0 0 .5.9h11.8a.6.6 0 0 0 .5-.9L15 10.7V7.5a5 5 0 0 0-5-5Zm-2 13.3a2 2 0 0 0 4 0"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinejoin="round"
          />
        </svg>
        {unread > 0 && <span className="bell-count num">{unread > 99 ? '99+' : unread}</span>}
      </button>
      {open && (
        <div className="bell-panel" role="dialog" aria-label="Notifications">
          <header>
            <span className="label">Notifications</span>
            {unread > 0 && (
              <button
                className="link-button"
                type="button"
                onClick={() => api('/api/notifications/read-all', { method: 'POST' }).then(refresh, refresh)}
              >
                Mark all read
              </button>
            )}
          </header>
          {list.data?.items.length === 0 && <p className="hint">Nothing yet.</p>}
          <ul>
            {list.data?.items.map((n) => (
              <li key={n.id} className={n.read_at ? undefined : 'unread'}>
                <button type="button" onClick={() => go(n.id, n.link, !!n.read_at)}>
                  <span className="bell-title">{n.title}</span>
                  {n.body && <span className="bell-body">{n.body}</span>}
                  <span className="hint num">{formatDate(n.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
