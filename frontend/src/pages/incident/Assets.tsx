import { useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../../api/client';
import { label } from '../../api/incidents';
import type { Asset, AssetLink, AssetStatus, Incident, LinkKind } from '../../api/incidents';
import { severities } from '../../brand';
import { SeverityPill } from '../../components/Pills';
import { Notice, formatDate } from '../../components/ui';
import { Tags, fromLocalInput, splitTags, useSave, useSection } from './shared';

const statuses: AssetStatus[] = ['identified', 'investigating', 'contained', 'eradicated', 'recovered', 'clean'];
const linkKinds: LinkKind[] = ['lateral_movement', 'c2', 'exfiltration', 'other'];

function AddAsset({ incident, onDone }: { incident: Incident; onDone: () => void }) {
  const save = useSave(incident, ['assets']);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/assets`, {
        method: 'POST',
        json: {
          hostname: form.get('hostname'),
          ip: form.get('ip'),
          os: form.get('os'),
          primary_user: form.get('primary_user'),
          purpose: form.get('purpose'),
          criticality: form.get('criticality'),
          is_patient_zero: form.get('patient_zero') === 'on',
          tags: splitTags(String(form.get('tags') ?? '')),
        },
      }),
    );
    if (done !== undefined) onDone();
  }

  return (
    <form className="panel" onSubmit={submit} aria-label="Add an asset">
      <header>
        <h2>Add an asset</h2>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Hostname</span>
          <input id="asset-hostname" name="hostname" required />
        </label>
        <label className="field">
          <span>IP address</span>
          <input name="ip" className="mono" />
        </label>
        <label className="field">
          <span>Operating system</span>
          <input name="os" />
        </label>
        <label className="field">
          <span>Primary user</span>
          <input name="primary_user" />
        </label>
        <label className="field">
          <span>Criticality</span>
          <select name="criticality" defaultValue="medium">
            {severities.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Tags</span>
          <input name="tags" />
        </label>
      </div>
      <label className="field">
        <span>What it does</span>
        <input name="purpose" placeholder="Finance file server, domain controller…" />
      </label>
      <label className="check">
        <input type="checkbox" name="patient_zero" />
        <span>Patient zero: the first asset compromised, as far as we know</span>
      </label>
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Add asset
        </button>
        <button className="btn btn-quiet" type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </form>
  );
}

function AssetCard({ incident, asset }: { incident: Incident; asset: Asset }) {
  const save = useSave(incident, ['assets', 'timeline']);
  const patch = (json: Record<string, unknown>) =>
    save.run(() =>
      api(`/api/incidents/${incident.id}/assets/${asset.id}`, {
        method: 'PATCH',
        json: { version: asset.version, ...json },
      }),
    );
  return (
    <li className="record" id={asset.ref}>
      <div className="record-head">
        <span className="mono ref">{asset.ref}</span>
        <strong>{asset.hostname}</strong>
        <SeverityPill level={asset.criticality} />
        {asset.is_patient_zero && <span className="chip chip-accent">Patient zero</span>}
        <Tags tags={asset.tags} />
      </div>
      <div className="hint">
        {[asset.ip && <span key="ip" className="mono">{asset.ip}</span>, asset.os, asset.primary_user, asset.purpose]
          .filter(Boolean)
          .flatMap((part, i) => (i ? [' · ', part] : [part]))}
      </div>
      {incident.can_edit ? (
        <div className="actions">
          <select
            className="input inline"
            aria-label={`Status of ${asset.hostname}`}
            value={asset.status}
            onChange={(e) => patch({ status: e.target.value })}
          >
            {statuses.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button className="link-button" type="button" onClick={() => patch({ is_patient_zero: !asset.is_patient_zero })}>
            {asset.is_patient_zero ? 'Not patient zero' : 'Mark as patient zero'}
          </button>
        </div>
      ) : (
        <span className="chip">{asset.status}</span>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </li>
  );
}

function Links({ incident, assets }: { incident: Incident; assets: Asset[] }) {
  const links = useSection<AssetLink[]>(incident, 'links', 'asset-links');
  const save = useSave(incident, ['links', 'timeline']);
  const name = (id: string) => assets.find((a) => a.id === id)?.hostname ?? 'unknown';

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const done = await save.run(() =>
      api(`/api/incidents/${incident.id}/asset-links`, {
        method: 'POST',
        json: {
          source_id: form.get('source_id'),
          target_id: form.get('target_id'),
          kind: form.get('kind'),
          seen_at: fromLocalInput(form.get('seen_at')),
          technique: form.get('technique'),
          note: form.get('note'),
        },
      }),
    );
    if (done !== undefined) formEl.reset();
  }

  return (
    <section className="panel">
      <header>
        <h2>Movement between assets</h2>
        <p>Each link is a step the attacker took from one asset to another.</p>
      </header>
      {links.data?.length ? (
        <ul className="links">
          {links.data.map((l) => (
            <li key={l.id}>
              <span>{name(l.source_id)}</span>
              <span className="arrow" aria-label={label(l.kind)}>
                → {label(l.kind)} →
              </span>
              <span>{name(l.target_id)}</span>
              {l.technique && <strong className="mono">{l.technique}</strong>}
              <span className="hint num">{formatDate(l.seen_at)}</span>
              {incident.can_edit && (
                <button
                  className="link-button"
                  type="button"
                  onClick={() =>
                    save.run(() => api(`/api/incidents/${incident.id}/asset-links/${l.id}`, { method: 'DELETE' }))
                  }
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="hint">No movement recorded.</p>
      )}
      {incident.can_edit && assets.length > 1 && (
        <form className="form-row" onSubmit={submit} aria-label="Link two assets">
          {(['source_id', 'target_id'] as const).map((field) => (
            <label key={field} className="field">
              <span>{field === 'source_id' ? 'From' : 'To'}</span>
              <select name={field} required defaultValue="">
                <option value="" disabled>
                  Choose…
                </option>
                {assets.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.hostname}
                  </option>
                ))}
              </select>
            </label>
          ))}
          <label className="field">
            <span>How</span>
            <select name="kind" defaultValue="lateral_movement">
              {linkKinds.map((k) => (
                <option key={k} value={k}>
                  {label(k)}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>When</span>
            <input type="datetime-local" name="seen_at" />
          </label>
          <label className="field">
            <span>Technique</span>
            <input name="technique" className="mono" placeholder="T1021.001" />
          </label>
          <label className="field">
            <span>Note</span>
            <input name="note" />
          </label>
          <div className="actions">
            <button className="btn btn-primary" type="submit">
              Link
            </button>
          </div>
        </form>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}

export function Assets({ incident }: { incident: Incident }) {
  const assets = useSection<Asset[]>(incident, 'assets');
  const [adding, setAdding] = useState(false);
  return (
    <>
      {incident.can_edit &&
        (adding ? (
          <AddAsset incident={incident} onDone={() => setAdding(false)} />
        ) : (
          <div className="actions">
            <button className="btn btn-primary" type="button" onClick={() => setAdding(true)}>
              Add an asset
            </button>
          </div>
        ))}
      {assets.data?.length === 0 && <p className="hint">No assets yet.</p>}
      <ul className="records" aria-label="Assets">
        {assets.data?.map((a) => <AssetCard key={a.id} incident={incident} asset={a} />)}
      </ul>
      {assets.data && assets.data.length > 0 && <Links incident={incident} assets={assets.data} />}
    </>
  );
}
