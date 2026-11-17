import type { Phase, RecordKind, Severity, Tlp } from '../brand';
import { phasesOf } from '../brand';
import './Pills.css';

// How many of the four bars a severity fills, so it reads by shape as well as colour
const barsOf: Record<Severity, number> = { critical: 4, high: 3, medium: 2, low: 1 };

export function SeverityPill({ level }: { level: Severity }) {
  return (
    <span className={`pill sev-${level}`}>
      <span className="sev-bars" aria-hidden="true">
        {[1, 2, 3, 4].map((n) => (
          <i key={n} className={n <= barsOf[level] ? 'on' : undefined} />
        ))}
      </span>
      {level.toUpperCase()}
    </span>
  );
}

export function TlpPill({ level }: { level: Tlp }) {
  const colour = level.startsWith('amber') ? 'amber' : level;
  return <span className={`pill tlp tlp-${colour}`}>TLP:{level.toUpperCase()}</span>;
}

export function PhasePill({ phase }: { phase: Phase }) {
  return <span className="pill phase-pill">{phase.toUpperCase()}</span>;
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
