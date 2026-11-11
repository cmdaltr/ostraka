import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { download } from '../../api/client';
import { getExports, intelFormats, intelPath, pushToMisp, pushedWords } from '../../api/exports';
import type { Incident } from '../../api/incidents';
import { Notice, messageOf } from '../../components/ui';

const restricted = (tlp: string) => tlp === 'red' || tlp === 'amber+strict';

// STIX, MISP and the ATT&CK Navigator layer, in the Export menu under the reports. Pushing to MISP
// happens only from the button here, never on its own.
export function IntelExports({ incident }: { incident: Incident }) {
  const offered = useQuery({ queryKey: ['incident', incident.id, 'exports'], queryFn: () => getExports(incident.id) });
  const [status, setStatus] = useState<{ ok?: string; error?: string }>({});
  const [busy, setBusy] = useState(false);

  const run = async (work: () => Promise<string | void>) => {
    setBusy(true);
    try {
      const ok = await work();
      setStatus(ok ? { ok } : {});
    } catch (e) {
      setStatus({ error: messageOf(e) });
    } finally {
      setBusy(false);
    }
  };
  const get = (format: 'stix' | 'misp' | 'navigator', leads = false) =>
    run(async () => {
      await download(intelPath(incident.id, format, leads));
    });
  const push = () => run(async () => pushedWords(await pushToMisp(incident.id)));

  const data = offered.data;
  if (data && !data.downloads) return data.reason ? <p className="hint">{data.reason}</p> : null;
  return (
    <div className="export-intel" aria-label="Intelligence exports">
      <h3 className="export-heading">For other tools</h3>
      {intelFormats.map(([format, name]) => (
        <div key={format} className="export-row">
          <span>{name}</span>
          <button className="link-button" type="button" role="menuitem" disabled={busy} onClick={() => get(format)}>
            JSON
          </button>
          {format === 'misp' && data?.misp.available && (
            <button className="link-button" type="button" role="menuitem" disabled={busy} onClick={push}>
              Send to MISP
            </button>
          )}
        </div>
      ))}
      <div className="export-row">
        <span>ATT&amp;CK Navigator layer</span>
        <button className="link-button" type="button" role="menuitem" disabled={busy} onClick={() => get('navigator')}>
          Confirmed
        </button>
        <button className="link-button" type="button" role="menuitem" disabled={busy} onClick={() => get('navigator', true)}>
          With leads
        </button>
      </div>
      {restricted(incident.tlp) ? (
        <p className="hint">TLP:{incident.tlp.toUpperCase()}: a download for the commander only, never sent to another system.</p>
      ) : (
        data && !data.misp.available && data.misp.reason && <p className="hint">{data.misp.reason}</p>
      )}
      {status.ok && <Notice kind="ok">{status.ok}</Notice>}
      {status.error && <Notice kind="error">{status.error}</Notice>}
    </div>
  );
}
