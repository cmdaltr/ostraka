import { useState } from "react";
import { api } from "../../api/client";
import type { Incident, Worded } from "../../api/incidents";
import { Notice, formatDate } from "../../components/ui";
import { useSave } from "./shared";

interface Draft {
  sentences: string[];
  text: string;
  worded: Worded;
  set_aside: string;
  can_ask: boolean;
  why_not: string;
}

export const wordedNames: Record<Worded, string> = {
  ostraka: "worded by Ostraka",
  ollama: "worded by Ollama, checked by Ostraka",
  anthropic: "worded by Anthropic, checked by Ostraka",
  person: "worded by hand",
};

/** Four sentences for senior readers: what it is, what's confirmed, and the conclusion. Ostraka drafts
 * them from the whole record (a model may word them, checked by Ostraka's rules); a person saves them. */
export function ExecutiveSummary({ incident }: { incident: Incident }) {
  const saved = incident.executive ?? null;
  const save = useSave(incident, ["activity"]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const base = `/api/incidents/${incident.id}/executive-summary`;

  async function generate(ai: boolean) {
    setBusy(true);
    const made = await save.run(() =>
      api<Draft>(`${base}/draft`, { method: "POST", json: { ai } }),
    );
    setBusy(false);
    if (made) {
      setDraft(made);
      setText(made.text);
      setEditing(true);
    }
  }

  function edit() {
    setDraft(null);
    setText(saved?.text ?? "");
    setEditing(true);
  }

  async function keep(value: string) {
    const worded: Worded =
      draft && value.trim() === draft.text ? draft.worded : "person";
    const done = await save.run(() =>
      api(base, {
        method: "PUT",
        json: { version: incident.version, text: value, worded },
      }),
    );
    if (done !== undefined) {
      setEditing(false);
      setDraft(null);
    }
  }

  if (!saved && !incident.can_edit) return null;
  return (
    <section className="panel executive" aria-labelledby="executive-head">
      <header className="panel-head">
        <h2 id="executive-head">Executive summary</h2>
        {incident.can_edit && !editing && (
          <span className="actions">
            {saved && (
              <button className="btn btn-quiet" type="button" onClick={edit}>
                Edit
              </button>
            )}
            <button
              className="btn"
              type="button"
              onClick={() => generate(true)}
              disabled={busy}
            >
              {busy
                ? "Reading the record…"
                : saved
                  ? "Generate again"
                  : "Generate"}
            </button>
          </span>
        )}
      </header>

      {editing ? (
        <form
          className="stack"
          aria-label="Executive summary"
          onSubmit={(e) => {
            e.preventDefault();
            void keep(text);
          }}
        >
          {draft?.set_aside && (
            <Notice>
              The model’s wording was set aside: {draft.set_aside} This is
              Ostraka’s own draft.
            </Notice>
          )}
          {draft && !draft.can_ask && draft.why_not && (
            <p className="hint">{draft.why_not}</p>
          )}
          <label className="field">
            <span>
              {draft
                ? `Four sentences from the record, ${wordedNames[text.trim() === draft.text ? draft.worded : "person"]}`
                : "Executive summary"}
            </span>
            <textarea
              className="input"
              rows={6}
              value={text}
              maxLength={3000}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <p className="hint">
            What it is, what’s been confirmed, and the conclusion. Indicators
            are masked when it’s saved, and no lead may be called confirmed.
          </p>
          <div className="actions">
            <button className="btn btn-primary" type="submit" disabled={busy}>
              Save
            </button>
            {draft && draft.worded !== "ostraka" && (
              <button
                className="btn btn-quiet"
                type="button"
                onClick={() => generate(false)}
                disabled={busy}
              >
                Use Ostraka’s own
              </button>
            )}
            {saved && (
              <button
                className="btn btn-quiet"
                type="button"
                onClick={() => keep("")}
                disabled={busy}
              >
                Clear
              </button>
            )}
            <button
              className="btn btn-quiet"
              type="button"
              onClick={() => setEditing(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : saved ? (
        <>
          <p className="prose prose-wide">{saved.text}</p>
          <p className="hint">
            {saved.by ? `Saved by ${saved.by.name}` : "Saved"}
            {saved.at ? ` ${formatDate(saved.at)}` : ""},{" "}
            {wordedNames[saved.worded] ?? "worded by hand"}.
          </p>
        </>
      ) : (
        <p className="hint">
          None yet. Generate one from the record: its summary, findings,
          assessment, lessons and discussion.
        </p>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </section>
  );
}
