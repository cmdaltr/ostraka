import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { getConnections, getKinds } from '../api/siem';
import type { SiemConnection, SiemKind, SiemKindInfo } from '../api/siem';
import { Notice, formatDate, messageOf } from '../components/ui';

function values(form: FormData, info: SiemKindInfo): Record<string, string> {
  return Object.fromEntries(info.fields.map((f) => [f.key, String(form.get(f.key) ?? '')]));
}

function Fields({ info, saved }: { info: SiemKindInfo; saved?: SiemConnection }) {
  return (
    <div className="form-row wrap">
      {info.fields.map((f) => {
        const set = saved?.secrets_set?.includes(f.key);
        const big = f.key === 'service_account';
        return (
          <label key={f.key} className={`field${big ? ' wide' : ''}`}>
            <span>
              {f.label}
              {!f.required && <span className="hint"> (if used)</span>}
            </span>
            {big ? (
              <textarea
                name={f.key}
                className="input mono"
                rows={4}
                autoComplete="off"
                spellCheck={false}
                placeholder={set ? 'Saved: leave empty to keep it' : '{ "type": "service_account", … }'}
              />
            ) : (
              <input
                name={f.key}
                className={`input${f.secret || f.key.endsWith('url') ? ' mono' : ''}`}
                type={f.secret ? 'password' : 'text'}
                autoComplete="off"
                defaultValue={f.secret ? '' : (saved?.settings?.[f.key] ?? '')}
                placeholder={f.secret && set ? 'Saved: leave empty to keep it' : f.hint}
              />
            )}
          </label>
        );
      })}
    </div>
  );
}

function Connection({ saved, info, onChange }: { saved: SiemConnection; info: SiemKindInfo; onChange: () => void }) {
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  const [testing, setTesting] = useState(false);
  const [removing, setRemoving] = useState(false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api(`/api/siem/connections/${saved.id}`, {
        method: 'PATCH',
        json: {
          version: saved.version,
          name: String(form.get('name')),
          values: values(form, info),
          verify_tls: form.get('verify_tls') === 'on',
          enabled: form.get('enabled') === 'on',
        },
      });
      setStatus({ ok: 'Saved. Test it to check.' });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
    onChange();
  }

  async function test() {
    setTesting(true);
    try {
      const out = await api<SiemConnection>(`/api/siem/connections/${saved.id}/test`, { method: 'POST' });
      setStatus(out.check_ok ? { ok: out.check_message } : { error: out.check_message });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    } finally {
      setTesting(false);
      onChange();
    }
  }

  async function remove() {
    try {
      await api(`/api/siem/connections/${saved.id}`, { method: 'DELETE' });
      onChange();
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }

  return (
    <section className="panel" aria-label={saved.name}>
      <header>
        <h2>{saved.name}</h2>
        <p className="hint">
          {info.name} · {info.language}
          {saved.checked_at &&
            ` · last tested ${formatDate(saved.checked_at)}: ${saved.check_ok ? 'connected' : saved.check_message}`}
        </p>
      </header>
      <form onSubmit={save} key={saved.version ?? 0} aria-label={`${saved.name} settings`}>
        <label className="field">
          <span>Name</span>
          <input name="name" className="input" required minLength={2} maxLength={80} defaultValue={saved.name} />
        </label>
        <Fields info={info} saved={saved} />
        <label className="check">
          <input type="checkbox" name="verify_tls" defaultChecked={saved.verify_tls ?? true} />
          <span>Check its TLS certificate (turn off only for a lab with a self-signed one)</span>
        </label>
        <label className="check">
          <input type="checkbox" name="enabled" defaultChecked={saved.enabled} />
          <span>On: offered on the Queries page</span>
        </label>
        {(status.ok || status.error) && <Notice kind={status.error ? 'error' : 'ok'}>{status.error ?? status.ok}</Notice>}
        <div className="actions">
          <button className="btn btn-primary" type="submit">
            Save
          </button>
          <button className="btn" type="button" onClick={test} disabled={testing}>
            {testing ? 'Testing…' : 'Test the connection'}
          </button>
          {removing ? (
            <>
              <button className="btn" type="button" onClick={remove}>
                Remove {saved.name}
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
    </section>
  );
}

function AddConnection({ kinds, onDone }: { kinds: SiemKindInfo[]; onDone: () => void }) {
  const [kind, setKind] = useState<SiemKind>(kinds[0].kind);
  const [error, setError] = useState<string | null>(null);
  const info = kinds.find((k) => k.kind === kind)!;

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api('/api/siem/connections', {
        method: 'POST',
        json: {
          name: String(form.get('name')),
          kind,
          values: values(form, info),
          verify_tls: form.get('verify_tls') === 'on',
        },
      });
      event.currentTarget?.reset();
      setError(null);
      onDone();
    } catch (e) {
      setError(messageOf(e));
    }
  }

  return (
    <form className="panel" onSubmit={add} aria-label="Connect a SIEM" key={kind}>
      <header>
        <h2>Connect a SIEM</h2>
        <p className="hint">Its credentials only need to read: Ostraka never changes anything in a SIEM.</p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>SIEM</span>
          <select id="siem-kind" value={kind} onChange={(e) => setKind(e.target.value as SiemKind)}>
            {kinds.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.name} ({k.language})
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Name</span>
          <input id="siem-name" name="name" className="input" required minLength={2} maxLength={80} placeholder={`${info.name} production`} />
        </label>
      </div>
      <Fields info={info} />
      <label className="check">
        <input type="checkbox" name="verify_tls" defaultChecked />
        <span>Check its TLS certificate</span>
      </label>
      {error && <Notice kind="error">{error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Connect
        </button>
      </div>
    </form>
  );
}

export function SiemsTab() {
  const queryClient = useQueryClient();
  const connections = useQuery({ queryKey: ['siem-connections'], queryFn: getConnections });
  const kinds = useQuery({ queryKey: ['siem-kinds'], queryFn: getKinds, staleTime: Infinity });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['siem-connections'] });
  if (connections.error) return <Notice kind="error">{messageOf(connections.error)}</Notice>;
  if (!connections.data || !kinds.data) return null;
  return (
    <>
      <section className="panel" aria-label="SIEMs">
        <header>
          <h2>SIEMs</h2>
          <p>
            Connect Splunk, Elasticsearch, Defender XDR or Google SecOps, and anyone but an observer can query them from
            the Queries page. Credentials are stored encrypted and never shown again. Ostraka keeps every query run and
            never what it returned.
          </p>
        </header>
      </section>
      {connections.data.map((c) => (
        <Connection key={c.id} saved={c} info={kinds.data.find((k) => k.kind === c.kind)!} onChange={refresh} />
      ))}
      <AddConnection kinds={kinds.data} onDone={refresh} />
    </>
  );
}
