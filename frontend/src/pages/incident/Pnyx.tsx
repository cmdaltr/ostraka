import { useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { keys } from '../../api/incidents';
import type { Incident } from '../../api/incidents';
import { Notice, messageOf } from '../../components/ui';
import { refTarget } from './shared';

type Answer = { key: string; question: string; lines: string[] };
type Bearings = { answers: Answer[]; can_ask: boolean; why_not: string };
type Reply = { question: string; answer: string; provider: string };

// References as Pnyx writes them, bare (FND-3, not #FND-3), and masked indicators ([ip address])
const TOKEN =
  /\b((?:OST|HNT)-\d{2}(?:\d{2})?-\d{3,})\b|\b((?:FND|AST|OBS|TSK|ASM|HYP|EVD|LSN|CLK|EXH|UPD)-\d+)\b|(\[[a-z0-9 ]+\])/gi;

export function Linked({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(TOKEN)) {
    const at = m.index ?? 0;
    if (at > last) parts.push(text.slice(last, at));
    if (m[1])
      parts.push(
        <Link key={at} className="ref-link mono" to={`/incidents/${m[1].toUpperCase()}`}>
          {m[1]}
        </Link>,
      );
    else if (m[2])
      parts.push(
        <Link key={at} className="ref-link mono" to={refTarget(m[2])}>
          {m[2]}
        </Link>,
      );
    else
      parts.push(
        <span key={at} className="masked">
          {m[3]}
        </span>,
      );
    last = at + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

// A compass: where you stand and which way to go
export function CompassIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M15.5 8.5l-2 5-5 2 2-5z" fill="currentColor" />
    </svg>
  );
}

// Pnyx: the incident's bearings, answered by Ostraka's own rules, and free questions answered by a model from
// the record. Bearings load when the drawer opens and stay fresh while it's open.
export function Pnyx({ incident, open }: { incident: Incident; open: boolean }) {
  const bearings = useQuery({
    queryKey: keys.section(incident.id, 'pnyx'),
    queryFn: () => api<Bearings>(`/api/incidents/${incident.id}/pnyx`),
    enabled: open,
    refetchInterval: open ? 60_000 : false,
  });
  const [chosen, setChosen] = useState('now');
  const [question, setQuestion] = useState('');
  const [reply, setReply] = useState<Reply | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  async function ask(event: FormEvent) {
    event.preventDefault();
    const q = question.trim();
    if (q.length < 3) return;
    setAsking(true);
    setError(null);
    try {
      const r = await api<{ answer: string; provider: string }>(`/api/incidents/${incident.id}/pnyx/ask`, {
        method: 'POST',
        json: { question: q },
      });
      setReply({ question: q, ...r });
      setQuestion('');
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setAsking(false);
    }
  }

  const data = bearings.data;
  const answer = data?.answers.find((a) => a.key === chosen) ?? data?.answers[0];
  return (
    <div className="pnyx">
      <header className="pnyx-head">
        <h2>Pnyx</h2>
        <p className="hint">
          Said “p-nicks”. Where {incident.ref} stands and what it needs next, from its record.
        </p>
      </header>

      {bearings.error && <Notice kind="error">{messageOf(bearings.error)}</Notice>}
      {!data && !bearings.error && <p className="hint">Reading the record…</p>}
      {data && (
        <>
          <div className="pnyx-questions" role="group" aria-label="Questions">
            {data.answers.map((a) => (
              <button
                key={a.key}
                type="button"
                className="pnyx-question"
                aria-pressed={answer?.key === a.key}
                onClick={() => setChosen(a.key)}
              >
                {a.question}
              </button>
            ))}
          </div>
          {answer && (
            <section className="pnyx-answer" aria-label={answer.question} aria-live="polite">
              <ul>
                {answer.lines.map((line, i) => (
                  <li key={i}>
                    <Linked text={line} />
                  </li>
                ))}
              </ul>
            </section>
          )}

          <form className="pnyx-ask" onSubmit={ask}>
            <label className="field">
              <span>Ask anything about {incident.ref}</span>
              <textarea
                className="input"
                rows={2}
                maxLength={500}
                value={question}
                disabled={!data.can_ask || asking}
                placeholder="Who is containing the finance server?"
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  // Enter asks, Shift+Enter starts a new line
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    e.currentTarget.form?.requestSubmit();
                  }
                }}
              />
            </label>
            {data.can_ask ? (
              <button className="btn btn-primary" type="submit" disabled={asking || question.trim().length < 3}>
                {asking ? 'Asking…' : 'Ask'}
              </button>
            ) : (
              <p className="hint">{data.why_not} The questions above don’t need a model.</p>
            )}
          </form>
          {error && <Notice kind="error">{error}</Notice>}
          {reply && (
            <section className="pnyx-reply" aria-label="Pnyx’s answer">
              <p className="hint">You asked: {reply.question}</p>
              <p>
                <Linked text={reply.answer} />
              </p>
              <p className="hint">
                Answered by {reply.provider === 'anthropic' ? 'Anthropic' : 'Ollama'} from the record. Check the
                records it cites before acting on it.
              </p>
            </section>
          )}
        </>
      )}
    </div>
  );
}
