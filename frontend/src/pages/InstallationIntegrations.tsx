import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { getIntegrations, names } from '../api/integrations';
import type { ConnectionTest, Integration, Integrations, Product } from '../api/integrations';
import type { Team } from '../api/types';
import { useFinishing } from '../components/sherd/Ostrakon';
import { Progress } from '../components/sherd/Progress';
import { CopyBox, Notice, messageOf } from '../components/ui';

const when = (iso: string) => new Date(iso).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });

const about: Record<Product, { purpose: string; token: string; placeholder: string }> = {
  rowanholt: {
    purpose:
      'Link incidents to Rowanholt jobs and cases, see their status, cite Rowanholt rows in findings and bring Rowanholt’s findings in as leads.',
    token:
      'A Rowanholt API token (rvt_…) from its Account page. Ostraka only reads, so a read-only aule-mcp token is enough. Leave it empty for Rowanholt in local mode.',
    placeholder: 'https://rowanholt.tail180683.ts.net',
  },
  shikari: {
    purpose:
      'Every confirmed technique without a Shikari detection becomes a detection gap with a task for the detection team. A gap only closes when Shikari has a detection for it.',
    token: 'A Shikari API token (shik_…) from Profile → Security → API tokens, made by an account with the fewest rights that can read coverage.',
    placeholder: 'https://shikari.tail180683.ts.net',
  },
};

function ServerPanel({
  product,
  saved,
  teams,
  webhookUrl,
  automation,
  onChange,
}: {
  product: Product;
  saved: Integration | null;
  teams: Team[];
  webhookUrl: string;
  automation: boolean;
  onChange: () => void;
}) {
  const name = names[product];
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  const [secret, setSecret] = useState<string | null>(null);
  const [test, setTest] = useState<ConnectionTest | null>(null);
  const [testing, setTesting] = useState(false);
  const testPhase = useFinishing(testing);
  const [removing, setRemoving] = useState(false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const token = String(form.get('token') ?? '');
    const team = String(form.get('team') ?? '');
    try {
      const out = await api<Integration>(`/api/integrations/${product}`, {
        method: 'PUT',
        json: {
          url: String(form.get('url')),
          // Left empty on a saved server: keep the stored token
          ...(token || !saved ? { token } : {}),
          detection_team_id: product === 'shikari' && team ? team : null,
          enabled: form.get('enabled') === 'on',
          version: saved?.version,
        },
      });
      if (out.webhook_secret) setSecret(out.webhook_secret);
      setStatus({ ok: 'Saved. Test the connection to check it.' });
      setTest(null);
      onChange();
    } catch (e) {
      setStatus({ error: messageOf(e) });
      onChange();
    }
  }

  async function runTest() {
    setTesting(true);
    setTest(null);
    try {
      const result = await api<ConnectionTest>(`/api/integrations/${product}/test`, { method: 'POST' });
      setTest(result);
      setStatus(result.ok ? {} : { error: result.message });
      onChange();
    } catch (e) {
      setStatus({ error: messageOf(e) });
    } finally {
      setTesting(false);
    }
  }

  async function newSecret() {
    try {
      const out = await api<Integration>('/api/integrations/shikari/secret', { method: 'POST' });
      setSecret(out.webhook_secret);
      onChange();
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }

  async function remove() {
    try {
      await api(`/api/integrations/${product}`, { method: 'DELETE' });
      setRemoving(false);
      setStatus({ ok: `${name} is disconnected. What incidents already hold from it stays.` });
      onChange();
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }

  return (
    <section className="panel" aria-label={name}>
      <header>
        <h2>{name}</h2>
        <p>{about[product].purpose}</p>
      </header>
      <form className="webhook" onSubmit={save} key={saved?.version ?? 0} aria-label={`${name} server`}>
        <div className="form-row">
          <label className="field">
            <span>Address</span>
            <input
              name="url"
              className="input mono"
              required
              maxLength={2000}
              defaultValue={saved?.url ?? ''}
              placeholder={about[product].placeholder}
            />
          </label>
          <label className="field">
            <span>API token</span>
            <input
              name="token"
              type="password"
              className="input mono"
              autoComplete="off"
              maxLength={500}
              placeholder={saved?.has_token ? 'Saved: leave empty to keep it' : ''}
            />
          </label>
        </div>
        <p className="hint">{about[product].token}</p>
        {product === 'shikari' && (
          <label className="field">
            <span>Detection team</span>
            <select name="team" className="input" defaultValue={saved?.detection_team_id ?? ''}>
              <option value="">Nobody yet: gaps open without a task</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="check">
          <input type="checkbox" name="enabled" defaultChecked={saved?.enabled ?? true} />
          <span>On</span>
        </label>
        {saved?.checked_at && !test && (
          <p className="hint">
            Last tested {when(saved.checked_at)}: {saved.check_ok ? 'connected' : saved.check_message}
          </p>
        )}
        {test && (
          <ul className="deliveries" aria-label={`${name} connection test`}>
            {test.steps.map((s) => (
              <li key={s.name} className={s.ok ? '' : 'delivery-failed'}>
                {s.ok ? '✓' : '✗'} {s.name}
                {s.detail && `: ${s.detail}`}
              </li>
            ))}
          </ul>
        )}
        {(status.ok || status.error) && <Notice kind={status.error ? 'error' : 'ok'}>{status.error ?? status.ok}</Notice>}
        <div className="actions">
          <button className="btn btn-primary" type="submit">
            {saved ? 'Save' : `Connect ${name}`}
          </button>
          {saved && (
            <button className="btn" type="button" onClick={runTest} disabled={testing}>
              <Progress
                phase={testPhase}
                ok={!!test?.ok}
                label={`Testing ${name}`}
                busy="Testing…"
                done="Connected"
              >
                Test the connection
              </Progress>
            </button>
          )}
          {saved &&
            (removing ? (
              <>
                <button className="btn" type="button" onClick={remove}>
                  Disconnect {name}
                </button>
                <button className="btn btn-quiet" type="button" onClick={() => setRemoving(false)}>
                  Keep it
                </button>
              </>
            ) : (
              <button className="btn btn-quiet" type="button" onClick={() => setRemoving(true)}>
                Disconnect
              </button>
            ))}
        </div>
      </form>
      {product === 'shikari' && saved && (
        <div className="stack">
          <h3>Shikari’s webhook</h3>
          <p className="hint">
            In Shikari, add a webhook for <code>run.completed</code> to this address with the secret below. After each
            run Ostraka asks Shikari again about every open gap; a webhook never closes a gap on its say-so.
          </p>
          <CopyBox value={webhookUrl} label="Webhook address" />
          {secret ? (
            <Notice kind="ok">
              Its secret is <code className="mono">{secret}</code>. Copy it into Shikari now: Ostraka won’t show it again.
            </Notice>
          ) : (
            <div className="actions">
              <button className="btn btn-quiet" type="button" onClick={newSecret}>
                Make a new secret
              </button>
            </div>
          )}
          {!automation && (
            <Notice kind="info">
              Acting on Shikari’s webhook is part of the Pro and Services editions, as sending webhooks is. Without it,
              check gaps from each incident’s Detection gaps section.
            </Notice>
          )}
        </div>
      )}
    </section>
  );
}

export function IntegrationsTab() {
  const queryClient = useQueryClient();
  const servers = useQuery({ queryKey: ['integrations'], queryFn: getIntegrations });
  const teams = useQuery({ queryKey: ['teams'], queryFn: () => api<Team[]>('/api/teams') });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['integrations'] });
  const data: Integrations | undefined = servers.data;
  if (servers.error) return <Notice kind="error">{messageOf(servers.error)}</Notice>;
  if (!data) return null;
  return (
    <>
      <section className="panel" aria-label="Rowanholt and Shikari">
        <header>
          <h2>Rowanholt and Shikari</h2>
          <p>
            Ostraka keeps the incident; Rowanholt keeps the evidence and Shikari the detections. Tokens are stored
            encrypted. A TLP:RED or TLP:AMBER+STRICT incident only exchanges anything with a server on your own network.
          </p>
        </header>
      </section>
      {(['rowanholt', 'shikari'] as const).map((p) => (
        <ServerPanel
          key={p}
          product={p}
          saved={data[p]}
          teams={teams.data ?? []}
          webhookUrl={data.shikari_webhook_url}
          automation={data.automation}
          onChange={refresh}
        />
      ))}
    </>
  );
}
