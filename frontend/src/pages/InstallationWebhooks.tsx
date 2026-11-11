import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { eventWords, getWebhooks } from '../api/webhooks';
import type { Delivery, Webhook, Webhooks as WebhookList } from '../api/webhooks';
import { Loading, useFinishing } from '../components/sherd/Ostrakon';
import { Progress } from '../components/sherd/Progress';
import { Notice, messageOf } from '../components/ui';

const when = (iso: string) => new Date(iso).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });

function result(d: Delivery) {
  if (d.ok) return `Delivered (${d.status})`;
  return `${d.error || 'Failed'}${d.tries > 1 ? `, after ${d.tries} tries` : ''}`;
}

function Events({ all, chosen, name }: { all: string[]; chosen: string[]; name: string }) {
  return (
    <fieldset className="webhook-events">
      <legend>Send when</legend>
      {all.map((event) => (
        <label key={event} className="check">
          <input type="checkbox" name={name} value={event} defaultChecked={chosen.includes(event)} />
          <span>
            {eventWords[event] ?? event} <code>{event}</code>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function WebhookPanel({ hook, all, sending, onChange }: {
  hook: Webhook;
  all: string[];
  sending: boolean;
  onChange: () => void;
}) {
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  const [removing, setRemoving] = useState(false);
  const [testing, setTesting] = useState(false);
  const testPhase = useFinishing(testing);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api<Webhook>(`/api/webhooks/${hook.id}`, {
        method: 'PATCH',
        json: {
          version: hook.version,
          url: String(form.get('url')),
          description: String(form.get('description')),
          events: form.getAll('events').map(String),
          enabled: form.get('enabled') === 'on',
        },
      });
      setStatus({ ok: 'Saved.' });
      onChange();
    } catch (e) {
      setStatus({ error: messageOf(e) });
      onChange(); // a stale version: show what's there now
    }
  }

  async function test() {
    setTesting(true);
    try {
      const d = await api<Delivery>(`/api/webhooks/${hook.id}/test`, { method: 'POST' });
      setStatus(d.ok ? { ok: `Test delivered (${d.status}).` } : { error: `Test failed: ${result(d)}.` });
      onChange();
    } catch (e) {
      setStatus({ error: messageOf(e) });
    } finally {
      setTesting(false);
    }
  }

  async function remove() {
    try {
      await api(`/api/webhooks/${hook.id}`, { method: 'DELETE' });
      onChange();
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }

  return (
    <section className="panel" aria-label={`Webhook ${hook.url}`}>
      <form className="webhook" onSubmit={save} key={hook.version}>
        <div className="form-row">
          <label className="field">
            <span>URL</span>
            <input name="url" className="input mono" required maxLength={2000} defaultValue={hook.url} />
          </label>
          <label className="field">
            <span>Description</span>
            <input name="description" maxLength={200} defaultValue={hook.description} placeholder="Optional" />
          </label>
        </div>
        <Events all={all} chosen={hook.events} name="events" />
        <label className="check">
          <input type="checkbox" name="enabled" defaultChecked={hook.enabled} />
          <span>On</span>
        </label>
        {(status.ok || status.error) && (
          <Notice kind={status.error ? 'error' : 'ok'}>{status.error ?? status.ok}</Notice>
        )}
        <div className="actions">
          <button className="btn btn-primary" type="submit">
            Save
          </button>
          <button className="btn" type="button" onClick={test} disabled={!sending || testing}>
            <Progress phase={testPhase} ok={!status.error} label="Sending a test" busy="Sending…" done="Sent">
              Send a test
            </Progress>
          </button>
          {removing ? (
            <>
              <button className="btn" type="button" onClick={remove}>
                Remove: stop sending
              </button>
              <button className="btn btn-quiet" type="button" onClick={() => setRemoving(false)}>
                Keep it
              </button>
            </>
          ) : (
            <button className="btn btn-quiet" type="button" onClick={() => setRemoving(true)}>
              Remove
            </button>
          )}
        </div>
      </form>
      <h3>Recent deliveries</h3>
      {hook.deliveries.length === 0 ? (
        <p className="hint">Nothing sent yet.</p>
      ) : (
        <ul className="deliveries" aria-label="Recent deliveries">
          {hook.deliveries.map((d, i) => (
            <li key={i} className={d.ok ? '' : 'delivery-failed'}>
              <code>{d.event}</code> <span className="num">{when(d.at)}</span> {result(d)}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function NewWebhook({ all, onCreated }: { all: string[]; onCreated: (secret: string) => void }) {
  const [error, setError] = useState<string | null>(null);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    try {
      const made = await api<Webhook>('/api/webhooks', {
        method: 'POST',
        json: {
          url: String(form.get('url')),
          description: String(form.get('description')),
          events: form.getAll('new-events').map(String),
        },
      });
      formEl.reset();
      setError(null);
      onCreated(made.secret ?? '');
    } catch (e) {
      setError(messageOf(e));
    }
  }
  return (
    <form className="panel webhook" onSubmit={create} aria-label="New webhook">
      <header>
        <h2>New webhook</h2>
        <p>
          Ostraka POSTs JSON to this address. Use https://; plain http:// only reaches this machine or a private
          network.
        </p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>URL</span>
          <input name="url" className="input mono" required maxLength={2000} placeholder="https://" />
        </label>
        <label className="field">
          <span>Description</span>
          <input name="description" maxLength={200} placeholder="Optional" />
        </label>
      </div>
      <Events all={all} chosen={[]} name="new-events" />
      {error && <Notice kind="error">{error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Add webhook
        </button>
      </div>
    </form>
  );
}

export function WebhooksTab() {
  const queryClient = useQueryClient();
  const hooks = useQuery({ queryKey: ['webhooks'], queryFn: getWebhooks });
  const [secret, setSecret] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['webhooks'] });
  const data: WebhookList | undefined = hooks.data;
  if (hooks.error) return <Notice kind="error">{messageOf(hooks.error)}</Notice>;
  // One Loading whether or not the list is here yet, so it stays mounted to mend and glow before the list
  return (
    <Loading ready={!!data} label="Loading webhooks">
      {data && (
        <>
          <section className="panel" aria-label="Webhooks">
            <header>
              <h2>Webhooks</h2>
              <p>
                Tell your other systems when something happens in Ostraka. Each request is signed with the webhook’s
                secret in <code>X-Ostraka-Signature</code>. Indicators are masked, and a TLP:RED or TLP:AMBER+STRICT
                incident is sent as its reference only.
              </p>
            </header>
            {!data.sending && (
              <Notice kind="error">
                Webhooks are part of the Pro and Services editions. They’re kept here, but nothing is sent until a
                licence with them is installed.
              </Notice>
            )}
            {secret && (
              <Notice kind="ok">
                Added. Its secret is <code className="mono">{secret}</code>. Copy it now: Ostraka won’t show it again.
              </Notice>
            )}
          </section>
          {data.webhooks.map((h) => (
            <WebhookPanel key={h.id} hook={h} all={data.events} sending={data.sending} onChange={refresh} />
          ))}
          <NewWebhook
            all={data.events}
            onCreated={(s) => {
              setSecret(s);
              refresh();
            }}
          />
        </>
      )}
    </Loading>
  );
}
