import { useEffect, useState } from 'react';
import type { CSSProperties, FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { api, objectUrl } from '../api/client';
import { day, editionNames, getLicence, logoSlots, seatsText } from '../api/installation';
import type { Brand, Licence, LicencePreview, LogoSlot, Terms } from '../api/installation';
import { Loading } from '../components/sherd/Ostrakon';
import { Progress } from '../components/sherd/Progress';
import { Notice, messageOf } from '../components/ui';
import { MispTab } from './InstallationMisp';
import { WebhooksTab } from './InstallationWebhooks';
import './Installation.css';

const editions = [
  { name: 'Free', what: '4 people who can sign in, one of them an admin or team lead. Reports in Ostrakon.' },
  {
    name: 'Pro',
    what: 'As many people as the licence covers. Reports in your own colours and logos, and webhooks.',
  },
  {
    name: 'Services',
    what: 'Pro, for MSSPs and consultancies: a brand for each client, chosen per incident, and the right to run Ostraka for clients.',
  },
];

function Places({ licence }: { licence: Licence }) {
  if (licence.places_held === null) return null;
  const full = licence.seats !== null && licence.places_held >= licence.seats;
  return (
    <p className={full ? 'places places-full' : 'places'}>
      <strong className="num">
        {licence.places_held} of {seatsText(licence)}
      </strong>{' '}
      places taken by accounts and open invitations
      {full && '. Deactivate someone or withdraw an invitation to make room.'}
    </p>
  );
}

function LicencePanel() {
  const queryClient = useQueryClient();
  const licence = useQuery({ queryKey: ['licence'], queryFn: getLicence });
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  const [removing, setRemoving] = useState(false);
  const [text, setText] = useState('');
  const [preview, setPreview] = useState<LicencePreview | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState<'checking' | 'installing' | null>(null);
  const terms = useQuery({
    queryKey: ['licence-terms'],
    queryFn: () => api<Terms>('/api/licence/terms'),
    enabled: !!preview,
  });

  // A licence is checked first, so the admin sees whom they're accepting the terms for
  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy('checking');
    try {
      setPreview(await api<LicencePreview>('/api/licence/check', { method: 'POST', json: { text } }));
      setAccepted(false);
      setStatus({});
    } catch (e) {
      setPreview(null);
      setStatus({ error: messageOf(e) });
    } finally {
      setBusy(null);
    }
  }

  async function install() {
    setBusy('installing');
    try {
      const now = await api<Licence>('/api/licence', {
        method: 'PUT',
        json: { text, accept_terms: accepted, terms_version: terms.data?.version ?? '' },
      });
      queryClient.setQueryData(['licence'], now);
      setText('');
      setPreview(null);
      setStatus({ ok: `Installed: ${editionNames[now.edition]} for ${now.organisation}.` });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    try {
      queryClient.setQueryData(['licence'], await api<Licence>('/api/licence', { method: 'DELETE' }));
      setRemoving(false);
      setStatus({ ok: 'Removed. This installation is on the free edition.' });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }

  const l = licence.data;
  return (
    <>
      <section className="panel" aria-label="Edition">
        <header>
          <h2>{l ? `${editionNames[l.edition]} edition` : 'Edition'}</h2>
          {l?.organisation && (
            <p>
              Licensed to {l.organisation}
              {l.expires && `, to ${day(l.expires)}`}.
            </p>
          )}
        </header>
        {l?.state === 'pending' && (
          <Notice kind="ok">
            This licence starts on {day(l.not_before)}. Until then the installation stays on the free edition.
          </Notice>
        )}
        {l?.state === 'grace' && l.grace_ends && (
          <Notice kind="error">
            The licence ran out on {day(l.expires!)}. Everything keeps working until {day(l.grace_ends)},
            then this installation goes back to the free edition. Install the renewal to carry on.
          </Notice>
        )}
        {l?.state === 'lapsed' && (
          <Notice kind="error">
            The licence ran out on {day(l.expires!)}. Nobody has lost access, but nobody new can join past
            the free limit, and reports are in Ostrakon until a renewal is installed.
          </Notice>
        )}
        {l && <Places licence={l} />}
        {l?.accepted && (
          <p className="hint">
            Commercial licence v{l.accepted.terms_version} accepted by {l.accepted.by} on{' '}
            {day(l.accepted.at.slice(0, 10))}.
          </p>
        )}
        <dl className="editions">
          {editions.map((e) => (
            <div key={e.name} aria-current={l && editionNames[l.edition] === e.name ? 'true' : undefined}>
              <dt>{e.name}</dt>
              <dd>{e.what}</dd>
            </div>
          ))}
        </dl>
      </section>

      <form className="panel" onSubmit={check} aria-label="Install a licence">
        <header>
          <h2>{l && l.edition !== 'free' ? 'Renew or change the licence' : 'Install a licence'}</h2>
          <p>Paste the whole licence line you were sent. It’s checked here, without contacting anyone.</p>
        </header>
        <label className="field">
          <span>Licence</span>
          <textarea
            id="licence-text"
            name="text"
            rows={3}
            required
            className="input mono"
            spellCheck={false}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setPreview(null);
            }}
          />
        </label>
        {preview && (
          <div className="licence-preview">
            <dl>
              <div>
                <dt>Licensee</dt>
                <dd>{preview.licensee}</dd>
              </div>
              <div>
                <dt>Edition</dt>
                <dd>{editionNames[preview.edition]}</dd>
              </div>
              <div>
                <dt>Seats</dt>
                <dd>{preview.seats ?? 'unlimited'}</dd>
              </div>
              <div>
                <dt>Runs</dt>
                <dd>
                  {preview.not_before ? `${day(preview.not_before)} to ` : 'to '}
                  {preview.expires ? day(preview.expires) : 'no end date'}
                </dd>
              </div>
            </dl>
            {terms.data && (
              <>
                <div className="terms" tabIndex={0} aria-label={`Commercial licence, version ${terms.data.version}`}>
                  {terms.data.text}
                </div>
                <label className="check">
                  <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
                  <span>
                    I accept the Ostraka Commercial Licence, version {terms.data.version}, on behalf of{' '}
                    {preview.licensee}, and I have authority to do so.
                  </span>
                </label>
              </>
            )}
          </div>
        )}
        {(status.ok || status.error) && (
          <Notice kind={status.error ? 'error' : 'ok'}>{status.error ?? status.ok}</Notice>
        )}
        <div className="actions">
          {preview ? (
            <button className="btn btn-primary" type="button" disabled={!accepted || !!busy} onClick={install}>
              <Progress phase={busy === 'installing' ? 'busy' : 'idle'} label="Installing the licence" busy="Installing…">
                Install
              </Progress>
            </button>
          ) : (
            <button className="btn btn-primary" type="submit" disabled={!!busy}>
              <Progress phase={busy === 'checking' ? 'busy' : 'idle'} label="Checking the licence" busy="Checking…">
                Check
              </Progress>
            </button>
          )}
          {l?.organisation &&
            (removing ? (
              <>
                <button className="btn" type="button" onClick={remove}>
                  Remove: back to free
                </button>
                <button className="btn btn-quiet" type="button" onClick={() => setRemoving(false)}>
                  Keep it
                </button>
              </>
            ) : (
              <button className="btn btn-quiet" type="button" onClick={() => setRemoving(true)}>
                Remove licence
              </button>
            ))}
        </div>
      </form>
    </>
  );
}

function Logo({ brand, slot, label, hint, onChange }: {
  brand: Brand;
  slot: LogoSlot;
  label: string;
  hint: string;
  onChange: (b: Brand) => void;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const has = brand.logos.includes(slot);
  useEffect(() => {
    if (!has) return setSrc(null);
    let url: string | null = null;
    objectUrl(`/api/brands/${brand.id}/logos/${slot}`)
      .then((u) => setSrc((url = u)))
      .catch(() => setSrc(null));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [brand.id, brand.version, slot, has]);

  async function upload(file: File | undefined) {
    if (!file) return;
    try {
      onChange(
        await api<Brand>(`/api/brands/${brand.id}/logos/${slot}`, {
          method: 'PUT',
          body: file,
          headers: { 'Content-Type': file.type || 'application/octet-stream' },
        }),
      );
      setError(null);
    } catch (e) {
      setError(messageOf(e));
    }
  }

  return (
    <div className="logo-slot">
      <div className="logo-preview">{src ? <img src={src} alt={`${label} for ${brand.name}`} /> : <span>None</span>}</div>
      <div>
        <strong>{label}</strong>
        <p className="hint">{hint}</p>
        <div className="actions">
          <label className="btn btn-quiet">
            {has ? 'Replace' : 'Upload'}
            <input
              type="file"
              accept="image/png,image/jpeg"
              aria-label={`${label} file`}
              className="visually-hidden"
              onChange={(e) => upload(e.target.files?.[0])}
            />
          </label>
          {has && (
            <button
              className="btn btn-quiet"
              type="button"
              onClick={() =>
                api<Brand>(`/api/brands/${brand.id}/logos/${slot}`, { method: 'DELETE' }).then(onChange, (e) =>
                  setError(messageOf(e)),
                )
              }
            >
              Remove
            </button>
          )}
        </div>
        {error && <Notice kind="error">{error}</Notice>}
      </div>
    </div>
  );
}

function Colour({ name, label, value, required }: { name: string; label: string; value: string; required?: boolean }) {
  const [colour, setColour] = useState(value);
  useEffect(() => setColour(value), [value]);
  return (
    <label className="field colour-field">
      <span>{label}</span>
      <span className="colour-pair">
        <input
          type="color"
          aria-label={`${label} picker`}
          className={colour ? undefined : 'unset'}
          value={colour || '#000000'}
          onChange={(e) => setColour(e.target.value)}
        />
        <input
          name={name}
          aria-label={label}
          value={colour}
          onChange={(e) => setColour(e.target.value)}
          pattern="#[0-9a-fA-F]{6}"
          placeholder={required ? '#1f6feb' : 'Optional'}
          required={required}
          className="mono"
        />
      </span>
    </label>
  );
}

const swatches: { key: string; label: string }[] = [
  { key: 'clay', label: 'Headings' },
  { key: 'ink', label: 'Text' },
  { key: 'muted', label: 'Secondary text' },
  { key: 'quiet', label: 'Second series' },
  { key: 'limestone', label: 'Cover ground' },
  { key: 'band', label: 'Table rows' },
];

function BrandEditor({ brand, library, onChange, onDelete }: {
  brand: Brand;
  library: boolean;
  onChange: (b: Brand) => void;
  onDelete: () => void;
}) {
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  const [deleting, setDeleting] = useState(false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      onChange(
        await api<Brand>(`/api/brands/${brand.id}`, {
          method: 'PATCH',
          json: {
            version: brand.version,
            name: String(form.get('name')),
            primary: String(form.get('primary')).toLowerCase(),
            secondary: String(form.get('secondary') ?? '').toLowerCase(),
            prepared_for: String(form.get('prepared_for')),
          },
        }),
      );
      setStatus({ ok: 'Saved.' });
    } catch (e) {
      setStatus({ error: messageOf(e) });
    }
  }

  const makeDefault = () =>
    api<Brand>(`/api/brands/${brand.id}`, { method: 'PATCH', json: { version: brand.version, is_default: true } }).then(
      onChange,
      (e) => setStatus({ error: messageOf(e) }),
    );

  return (
    <section className="panel brand" aria-label={`Brand ${brand.name}`}>
      <header>
        <h2>
          {brand.name} {brand.is_default && library && <span className="chip chip-accent">Default</span>}
        </h2>
        <p>
          {library
            ? brand.is_default
              ? 'Used for every incident that doesn’t choose another brand.'
              : 'Chosen on an incident’s overview, under Exports.'
            : 'Every report this installation exports uses it.'}
        </p>
      </header>
      <form onSubmit={save}>
        <div className="form-row">
          <label className="field">
            <span>Name</span>
            <input name="name" defaultValue={brand.name} required maxLength={80} />
          </label>
          <label className="field">
            <span>Prepared for</span>
            <input
              name="prepared_for"
              defaultValue={brand.prepared_for}
              maxLength={160}
              placeholder="Shown on the cover, e.g. Contoso Bank plc"
            />
          </label>
        </div>
        <div className="form-row">
          <Colour name="primary" label="Primary colour" value={brand.primary} required />
          <Colour name="secondary" label="Secondary colour" value={brand.secondary} />
        </div>
        <div className="palette" aria-label="Colours a report uses">
          {swatches.map((s) => (
            <span key={s.key} className="swatch" style={{ '--swatch': brand.palette[s.key] } as CSSProperties}>
              <i aria-hidden="true" />
              {s.label} <code>{brand.palette[s.key]}</code>
            </span>
          ))}
        </div>
        {brand.notes.map((n) => (
          <p key={n} className="hint">
            {n}
          </p>
        ))}
        <p className="hint">
          Severity and TLP colours never change: TLP:AMBER stays amber in every brand.
        </p>
        {(status.ok || status.error) && (
          <Notice kind={status.error ? 'error' : 'ok'}>{status.error ?? status.ok}</Notice>
        )}
        <div className="actions">
          <button className="btn btn-primary" type="submit">
            Save
          </button>
          {library && !brand.is_default && (
            <button className="btn btn-quiet" type="button" onClick={makeDefault}>
              Make default
            </button>
          )}
          {deleting ? (
            <>
              <button className="btn" type="button" onClick={onDelete}>
                Delete {brand.name}
              </button>
              <button className="btn btn-quiet" type="button" onClick={() => setDeleting(false)}>
                Keep it
              </button>
            </>
          ) : (
            <button className="btn btn-quiet" type="button" onClick={() => setDeleting(true)}>
              Delete…
            </button>
          )}
        </div>
      </form>
      <div className="logos">
        {logoSlots.map((s) => (
          <Logo key={s.slot} brand={brand} {...s} onChange={onChange} />
        ))}
      </div>
      <p className="hint">PNG or JPEG, up to 2 MB. Ostraka redraws each logo before it’s stored; SVG isn’t accepted.</p>
    </section>
  );
}

function NewBrand({ onCreated }: { onCreated: (b: Brand) => void }) {
  const [error, setError] = useState<string | null>(null);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    try {
      onCreated(
        await api<Brand>('/api/brands', {
          method: 'POST',
          json: {
            name: String(form.get('name')),
            primary: String(form.get('primary')).toLowerCase(),
            secondary: String(form.get('secondary') ?? '').toLowerCase(),
            prepared_for: String(form.get('prepared_for')),
          },
        }),
      );
      formEl.reset();
      setError(null);
    } catch (e) {
      setError(messageOf(e));
    }
  }
  return (
    <form className="panel" onSubmit={create} aria-label="New brand">
      <header>
        <h2>New brand</h2>
        <p>Add the colours now and the logos once it’s saved.</p>
      </header>
      <div className="form-row">
        <label className="field">
          <span>Name</span>
          <input id="brand-name" name="name" required maxLength={80} />
        </label>
        <label className="field">
          <span>Prepared for</span>
          <input name="prepared_for" maxLength={160} placeholder="Optional" />
        </label>
      </div>
      <div className="form-row">
        <Colour name="primary" label="Primary colour" value="" required />
        <Colour name="secondary" label="Secondary colour" value="" />
      </div>
      {error && <Notice kind="error">{error}</Notice>}
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Add brand
        </button>
      </div>
    </form>
  );
}

function Brands() {
  const queryClient = useQueryClient();
  const licence = useQuery({ queryKey: ['licence'], queryFn: getLicence });
  const brands = useQuery({ queryKey: ['brands'], queryFn: () => api<Brand[]>('/api/brands') });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['brands'] });
  const replace = (b: Brand) =>
    queryClient.setQueryData<Brand[]>(['brands'], (all) => (all ?? []).map((x) => (x.id === b.id ? b : x)));

  const l = licence.data;
  if (licence.error) return <Notice kind="error">{messageOf(licence.error)}</Notice>;
  // The licence is what the tab needs; brands that won't load show as none
  return (
    <Loading ready={!!l && !brands.isPending} label="Loading report brands">
      {l && <BrandList licence={l} brands={brands.data ?? []} refresh={refresh} replace={replace} />}
    </Loading>
  );
}

function BrandList({ licence: l, brands: list, refresh, replace }: {
  licence: Licence;
  brands: Brand[];
  refresh: () => void;
  replace: (b: Brand) => void;
}) {
  if (!l.custom_branding)
    return (
      <section className="panel">
        <header>
          <h2>Report branding</h2>
          <p>
            Reports are in the Ostrakon palette. With a Pro licence they carry your colours and up to three logos;
            with Services, a brand for each client, chosen per incident.
          </p>
        </header>
        {list.length > 0 && (
          <p className="hint">
            {list.length === 1 ? 'One brand is' : `${list.length} brands are`} kept from an earlier licence and
            return when one is installed.
          </p>
        )}
      </section>
    );
  return (
    <>
      {list.map((b) => (
        <BrandEditor
          key={b.id}
          brand={b}
          library={l.brand_library}
          onChange={(nb) => (nb.is_default !== b.is_default ? refresh() : replace(nb))}
          onDelete={() => api(`/api/brands/${b.id}`, { method: 'DELETE' }).then(refresh)}
        />
      ))}
      {(l.brand_library || list.length === 0) && <NewBrand onCreated={refresh} />}
    </>
  );
}

const tabs = [
  { key: 'licence', label: 'Licence' },
  { key: 'brands', label: 'Report brands' },
  { key: 'webhooks', label: 'Webhooks' },
  { key: 'misp', label: 'MISP' },
] as const;
type Tab = (typeof tabs)[number]['key'];

export function Installation() {
  const [params, setParams] = useSearchParams();
  const tab: Tab = tabs.find((t) => t.key === params.get('tab'))?.key ?? 'licence';
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Installation</h1>
          <p className="hint">The licence, how exported reports look, webhooks and MISP.</p>
        </div>
      </div>
      <nav className="tabs" aria-label="Installation sections">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            aria-current={tab === t.key ? 'page' : undefined}
            onClick={() => setParams(t.key === 'licence' ? {} : { tab: t.key })}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {tab === 'licence' ? <LicencePanel /> : tab === 'brands' ? <Brands /> : tab === 'misp' ? <MispTab /> : <WebhooksTab />}
    </>
  );
}
