import type { Phase, RecordKind, Severity, Tlp } from '../brand';
import { phasesOf } from '../brand';
import './Pills.css';

export function SeverityPill({ level }: { level: Severity }) {
  return <span className={`pill sev-${level}`}>{level.toUpperCase()}</span>;
}

export function TlpPill({ level }: { level: Tlp }) {
  const colour = level.startsWith('amber') ? 'amber' : level;
  return <span className={`pill tlp tlp-${colour}`}>TLP:{level.toUpperCase()}</span>;
}

// The incident's phase as a row of tiles; done phases read in ink, the current one in the accent
export function PhaseStrip({ current, kind = 'incident' }: { current: Phase; kind?: RecordKind }) {
  const phases = phasesOf(kind);
  const at = phases.indexOf(current);
  return (
    <ol className="phase-strip" aria-label={`Phase: ${current}`}>
      {phases.map((p, i) => (
        <li key={p} className={i < at ? 'done' : i === at ? 'on' : undefined} aria-current={i === at ? 'step' : undefined}>
          {p}
        </li>
      ))}
    </ol>
  );
}
