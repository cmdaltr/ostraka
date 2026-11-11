import { modes, palettes, setTheme, useTheme } from '../theme/theme';
import type { Mode, Palette } from '../theme/theme';

const paletteNames: Record<Palette, string> = { cobalt: 'Cobalt', ostrakon: 'Ostrakon', ledger: 'Ledger' };
const modeNames: Record<Mode, string> = { system: 'System', light: 'Light', dark: 'Dark' };

export function ThemePicker() {
  const theme = useTheme();
  return (
    <div className="theme-picker">
      <label>
        <span className="label">Theme</span>
        <select id="theme-palette" value={theme.palette} onChange={(e) => setTheme({ palette: e.target.value as Palette })}>
          {palettes.map((p) => (
            <option key={p} value={p}>
              {paletteNames[p]}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span className="label">Appearance</span>
        <select id="theme-mode" value={theme.mode} onChange={(e) => setTheme({ mode: e.target.value as Mode })}>
          {modes.map((m) => (
            <option key={m} value={m}>
              {modeNames[m]}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
