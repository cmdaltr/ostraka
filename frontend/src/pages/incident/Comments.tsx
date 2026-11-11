import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { Asset, Comment, Finding, Incident, IncidentObservable } from '../../api/incidents';
import type { Me, Person, Team } from '../../api/types';
import type { Task } from '../../api/work';
import { Notice, formatDate } from '../../components/ui';
import { announce } from '../../live/live';
import { Rich, useSave, useSection } from './shared';

interface Suggestion {
  key: string;
  label: string;
  hint: string;
  insert: string;
}

// The @name or #ref being typed just before the caret, if any
export function trigger(text: string, caret: number): { sign: '@' | '#'; query: string; start: number } | null {
  const match = /(^|\s)([@#])([\w.-]{0,40})$/.exec(text.slice(0, caret));
  if (!match) return null;
  return { sign: match[2] as '@' | '#', query: match[3].toLowerCase(), start: caret - match[3].length - 1 };
}

function useSuggestions(incident: Incident) {
  const people = useQuery({ queryKey: ['directory'], queryFn: () => api<Person[]>('/api/users') });
  const teams = useQuery({ queryKey: ['teams'], queryFn: () => api<Team[]>('/api/teams') });
  const findings = useSection<Finding[]>(incident, 'findings');
  const assets = useSection<Asset[]>(incident, 'assets');
  const observables = useSection<IncidentObservable[]>(incident, 'observables');
  const tasks = useSection<Task[]>(incident, 'tasks');

  return useMemo(() => {
    const mentions: Suggestion[] = [
      ...(people.data ?? [])
        .filter((p) => p.is_active)
        .map((p) => ({ key: p.id, label: p.name, hint: p.team?.name ?? '', insert: `@[${p.name}](user:${p.id}) ` })),
      // A team is found by its name, its code (CTI) or another name for it (Intel.)
      ...(teams.data ?? []).map((t) => ({
        key: t.id,
        label: t.name,
        hint: ['team', t.code, ...(t.aliases ?? [])].filter(Boolean).join(' · '),
        insert: `@[${t.name}](team:${t.id}) `,
      })),
    ];
    const refs: Suggestion[] = [
      { key: incident.id, label: incident.ref, hint: incident.title, insert: `#${incident.ref} ` },
      ...(tasks.data ?? []).map((t) => ({ key: t.id, label: t.ref, hint: t.title, insert: `#${t.ref} ` })),
      ...(findings.data ?? []).map((f) => ({ key: f.id, label: f.ref, hint: f.title, insert: `#${f.ref} ` })),
      ...(assets.data ?? []).map((a) => ({ key: a.id, label: a.ref, hint: a.hostname, insert: `#${a.ref} ` })),
      ...(observables.data ?? []).map((o) => ({
        key: o.id,
        label: o.ref,
        hint: o.masked ? o.kind.replace(/_/g, ' ') : (o.value ?? ''),
        insert: `#${o.ref} `,
      })),
    ];
    return { mentions, refs };
  }, [people.data, teams.data, tasks.data, findings.data, assets.data, observables.data, incident]);
}

function Composer({
  incident,
  parentId,
  initial = '',
  submitLabel,
  onSubmit,
  onCancel,
}: {
  incident: Incident;
  parentId?: string;
  initial?: string;
  submitLabel: string;
  onSubmit: (body: string) => Promise<boolean>;
  onCancel?: () => void;
}) {
  const { mentions, refs } = useSuggestions(incident);
  const box = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState(initial);
  const [caret, setCaret] = useState(initial.length);
  const [picked, setPicked] = useState(0);
  const [dismissed, setDismissed] = useState<number | null>(null);
  const pendingCaret = useRef<number | null>(null);

  // Put the caret after an inserted suggestion before the next keystroke can land
  useLayoutEffect(() => {
    if (pendingCaret.current === null) return;
    box.current?.setSelectionRange(pendingCaret.current, pendingCaret.current);
    pendingCaret.current = null;
  }, [text]);

  const at = trigger(text, caret);
  const options =
    at && dismissed !== at.start
      ? (at.sign === '@' ? mentions : refs)
          .filter((s) => s.label.toLowerCase().includes(at.query) || s.hint.toLowerCase().includes(at.query))
          .slice(0, 6)
      : [];

  const choose = (s: Suggestion) => {
    if (!at) return;
    const next = text.slice(0, at.start) + s.insert + text.slice(caret);
    const position = at.start + s.insert.length;
    setText(next);
    setCaret(position);
    setPicked(0);
    pendingCaret.current = position;
  };

  const keys = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (options.length) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        setPicked((picked + (e.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length);
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        choose(options[Math.min(picked, options.length - 1)]);
        return;
      }
      if (e.key === 'Escape') {
        setDismissed(at?.start ?? null);
        return;
      }
    }
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      send();
    }
  };

  async function send() {
    if (!text.trim()) return;
    if (await onSubmit(text.trim())) {
      setText('');
      setCaret(0);
      announce(incident.id, 'comments', false);
    }
  }

  const listId = `suggest-${parentId ?? 'root'}`;
  return (
    <div className="composer">
      <textarea
        ref={box}
        className="input"
        rows={parentId ? 2 : 3}
        value={text}
        aria-label={parentId ? 'Reply' : 'Comment'}
        aria-autocomplete="list"
        aria-controls={options.length ? listId : undefined}
        placeholder={parentId ? 'Reply…' : 'Write a comment. @ to mention, # to reference'}
        onChange={(e) => {
          setText(e.target.value);
          setCaret(e.target.selectionStart);
          setPicked(0);
        }}
        onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
        onFocus={() => announce(incident.id, 'comments', true)}
        onBlur={() => !text.trim() && announce(incident.id, 'comments', false)}
        onKeyDown={keys}
      />
      {options.length > 0 && (
        <ul className="suggest" id={listId} role="listbox" aria-label="Suggestions">
          {options.map((s, i) => (
            <li
              key={s.key}
              role="option"
              aria-selected={i === picked}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(s);
              }}
            >
              <span className={at?.sign === '#' ? 'mono' : undefined}>{s.label}</span>
              <span className="hint">{s.hint}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="actions">
        <button className="btn btn-primary" type="button" disabled={!text.trim()} onClick={send}>
          {submitLabel}
        </button>
        {onCancel && (
          <button className="btn btn-quiet" type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}

function CommentItem({
  incident,
  me,
  comment,
  save,
}: {
  incident: Incident;
  me: Me;
  comment: Comment;
  save: ReturnType<typeof useSave>;
}) {
  const [editing, setEditing] = useState(false);
  const base = `/api/incidents/${incident.id}/comments/${comment.id}`;
  const mine = comment.author?.id === me.id;
  if (comment.deleted)
    return <div className="comment withdrawn hint">{comment.author?.name ?? 'Someone'} withdrew a comment.</div>;
  return (
    <div className="comment">
      <div className="comment-head">
        <strong>{comment.author?.name ?? 'Former member'}</strong>
        <time className="hint num" dateTime={comment.created_at}>
          {formatDate(comment.created_at)}
        </time>
        {comment.edited_at && <span className="hint">edited</span>}
      </div>
      {editing ? (
        <Composer
          incident={incident}
          parentId={comment.id}
          initial={comment.body}
          submitLabel="Save"
          onCancel={() => setEditing(false)}
          onSubmit={async (body) => {
            const done = (await save.run(() => api(base, { method: 'PATCH', json: { body } }))) !== undefined;
            if (done) setEditing(false);
            return done;
          }}
        />
      ) : (
        <div className="comment-body">
          <Rich text={comment.body} />
        </div>
      )}
      {mine && incident.can_edit && !editing && (
        <div className="actions">
          <button className="link-button" type="button" onClick={() => setEditing(true)}>
            Edit
          </button>
          <button className="link-button" type="button" onClick={() => save.run(() => api(base, { method: 'DELETE' }))}>
            Withdraw
          </button>
        </div>
      )}
    </div>
  );
}

function Thread({ incident, me, root, replies }: { incident: Incident; me: Me; root: Comment; replies: Comment[] }) {
  const save = useSave(incident, ['comments']);
  const [replying, setReplying] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const resolved = !!root.resolved_at;
  const add = async (body: string) =>
    (await save.run(() =>
      api(`/api/incidents/${incident.id}/comments`, {
        method: 'POST',
        json: { body, parent_id: root.id, target_type: root.target_type, target_id: root.target_id },
      }),
    )) !== undefined;

  if (resolved && !expanded)
    return (
      <li className="thread resolved">
        <button className="link-button" type="button" onClick={() => setExpanded(true)}>
          Resolved thread from {root.author?.name ?? 'someone'} ({replies.length + 1})
        </button>
      </li>
    );

  return (
    <li className={resolved ? 'thread resolved' : 'thread'}>
      <CommentItem incident={incident} me={me} comment={root} save={save} />
      {replies.map((r) => (
        <div key={r.id} className="reply">
          <CommentItem incident={incident} me={me} comment={r} save={save} />
        </div>
      ))}
      {incident.can_edit && (
        <div className="thread-actions">
          {replying ? (
            <Composer
              incident={incident}
              parentId={root.id}
              submitLabel="Reply"
              onCancel={() => setReplying(false)}
              onSubmit={async (body) => {
                const done = await add(body);
                if (done) setReplying(false);
                return done;
              }}
            />
          ) : (
            <div className="actions">
              <button className="link-button" type="button" onClick={() => setReplying(true)}>
                Reply
              </button>
              {!root.deleted && (
                <button
                  className="link-button"
                  type="button"
                  onClick={() =>
                    save.run(() => api(`/api/incidents/${incident.id}/comments/${root.id}/resolve`, { method: 'POST' }))
                  }
                >
                  {resolved ? 'Reopen' : 'Resolve'}
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
    </li>
  );
}

export function Comments({ incident, me }: { incident: Incident; me: Me }) {
  const comments = useSection<Comment[]>(incident, 'comments');
  const save = useSave(incident, ['comments']);
  const roots = comments.data?.filter((c) => !c.parent_id) ?? [];
  const repliesTo = (id: string) => comments.data?.filter((c) => c.parent_id === id) ?? [];

  return (
    <section className="comments">
      <h2 className="label">Discussion</h2>
      {incident.can_edit && (
        <Composer
          incident={incident}
          submitLabel="Comment"
          onSubmit={async (body) =>
            (await save.run(() => api(`/api/incidents/${incident.id}/comments`, { method: 'POST', json: { body } }))) !==
            undefined
          }
        />
      )}
      {save.error && <Notice kind="error">{save.error}</Notice>}
      {comments.data?.length === 0 && <p className="hint">No comments yet.</p>}
      <ul className="threads" aria-label="Comments">
        {[...roots].reverse().map((root) => (
          <Thread key={root.id} incident={incident} me={me} root={root} replies={repliesTo(root.id)} />
        ))}
      </ul>
    </section>
  );
}
