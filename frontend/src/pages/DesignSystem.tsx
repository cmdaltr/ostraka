import { severities, tlpLevels } from '../brand';
import { PhaseStrip, SeverityPill, TlpPill } from '../components/Pills';
import { ThemePicker } from '../components/ThemePicker';
import { Wordmark } from '../components/Wordmark';
import './DesignSystem.css';

const colourTokens = ['bg', 'surface', 'raised', 'ink', 'muted', 'line', 'accent', 'accent-soft'];

// The live reference for the current theme: every token and component, as the theme picker sets them
export function DesignSystem() {
  return (
    <>
      <header className="ds-head">
        <h1>Design system</h1>
        <p>Preview each palette here; this doesn’t change your saved theme. Severity and TLP never change with it.</p>
        <ThemePicker />
      </header>

      <section className="ds-section">
        <h2 className="label">Colour</h2>
        <div className="ds-swatches">
          {colourTokens.map((t) => (
            <div className="ds-swatch" key={t}>
              <i style={{ background: `var(--${t})` }} />
              <code>--{t}</code>
            </div>
          ))}
        </div>
      </section>

      <section className="ds-section">
        <h2 className="label">Severity and TLP</h2>
        <div className="ds-row">
          {severities.map((s) => (
            <SeverityPill key={s} level={s} />
          ))}
        </div>
        <div className="ds-row">
          {tlpLevels.map((t) => (
            <TlpPill key={t} level={t} />
          ))}
        </div>
      </section>

      <section className="ds-section">
        <h2 className="label">Phase</h2>
        <PhaseStrip current="contain" />
      </section>

      <section className="ds-section">
        <h2 className="label">Actions</h2>
        <div className="ds-row">
          <button className="btn btn-primary" type="button">
            Claim
          </button>
          <button className="btn" type="button">
            Nudge team
          </button>
          <button className="btn btn-quiet" type="button">
            Cancel
          </button>
        </div>
      </section>

      <section className="ds-section">
        <h2 className="label">Type</h2>
        <p className="ds-type-ui">Mailbox rule forwarding finance mail to an external domain</p>
        <p className="ds-type-mono mono">185.220.101.47 · T1114.003 · OST-2026-014</p>
        <Wordmark size="md" />
      </section>
    </>
  );
}
