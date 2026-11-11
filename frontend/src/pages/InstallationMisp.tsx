import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { getMisp } from '../api/exports';
import type { MispCheck, MispSettings } from '../api/exports';
import { Notice, messageOf } from '../components/ui';

// The installation's MISP instance: where "Send to MISP" in an incident's Export menu goes
export function MispTab() {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ['misp'], queryFn: getMisp });
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  const [removing, setRemoving] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['misp'] });
  const data: MispSettings | undefined = settings.data;
  if (settings.error) return <Notice kind="error">{messageOf(settings.error)}</Notice>;
  if (!data) return null;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const key = String(form.get('api_key') ?? '').trim();
    try {
      await api<MispSettings>('/api/misp', {
        method: 'PUT',
        json: {
          url: String(form.get('url')),
          api_key: key || null,
          verify_tls: form.get('verify_tls') === 'on',
          distribution: Number(form.get('distribution')),
          version: data!.configured ? data!.version : null,
        },
      });
      setStatus({ ok: 'Saved.' });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
    refresh();
  }

  async function check() {
    try {
      const result = await api<MispCheck>('/api/misp/check', { method: 'POST' });
      setStatus(result.ok ? { ok: `MISP answered${result.version ? `: version ${result.version}` : ''}.` } : { error: result.error });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }

  async function remove() {
    try {
      await api('/api/misp', { method: 'DELETE' });
      setRemoving(false);
      setStatus({ ok: 'Removed.' });
      refresh();
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }

  return (
    <section className="panel" aria-label="MISP">
      <header>
        <h2>MISP</h2>
        <p>
          Lets the people working an incident send it to your MISP instance as an event, from the incident’s Export
          menu. Nothing is sent unless someone asks, and a TLP:RED or TLP:AMBER+STRICT incident is never sent. Only
          confirmed indicators are flagged for IDS. Events arrive unpublished. Anyone who may export can still
          download the MISP JSON without this.
        </p>
      </header>
      {!data.sending && (
        <Notice kind="error">
          Sending to MISP is part of the Pro and Services editions. These settings are kept, but nothing is sent until a
          licence with it is installed.
        </Notice>
      )}
      <form className="webhook" onSubmit={save} key={data.version} aria-label="MISP settings">
        <div className="form-row">
          <label className="field">
            <span>URL</span>
            <input name="url" className="input mono" required maxLength={2000} defaultValue={data.url} placeholder="https://misp.example.org" />
          </label>
          <label className="field">
            <span>API key</span>
            <input
              name="api_key"
              type="password"
              autoComplete="off"
              maxLength={200}
              required={!data.has_key}
              placeholder={data.has_key ? 'Stored. Leave empty to keep it' : 'The automation key of a MISP user'}
            />
          </label>
        </div>
        <div className="form-row">
          <label className="field">
            <span>Distribution</span>
            <select name="distribution" defaultValue={String(data.distribution)}>
              {Object.entries(data.distributions).map(([value, words]) => (
                <option key={value} value={value}>
                  {words}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="check">
          <input type="checkbox" name="verify_tls" defaultChecked={data.verify_tls} />
          <span>Check MISP’s TLS certificate</span>
        </label>
        {(status.ok || status.error) && <Notice kind={status.error ? 'error' : 'ok'}>{status.error ?? status.ok}</Notice>}
        <div className="actions">
          <button className="btn btn-primary" type="submit">
            Save
          </button>
          {data.configured && (
            <button className="btn" type="button" onClick={check} disabled={!data.sending}>
              Check the connection
            </button>
          )}
          {data.configured &&
            (removing ? (
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
            ))}
        </div>
      </form>
    </section>
  );
}
