import type { FormEvent } from 'react';
import type { Incident } from '../../api/incidents';
import type { Me } from '../../api/types';

// Who a follow-up task goes to: one of the incident's teams, or yourself
export function FollowUp({
  incident,
  me,
  label,
  onSubmit,
  onCancel,
}: {
  incident: Incident;
  me: Me;
  label: string;
  onSubmit: (owner: { team_id?: string; assignee_id?: string }) => void;
  onCancel: () => void;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const owner = String(new FormData(event.currentTarget).get('owner'));
    onSubmit(owner === 'me' ? { assignee_id: me.id } : { team_id: owner });
  }
  return (
    <form className="inline-form" onSubmit={submit} aria-label={label}>
      <select className="input" name="owner" aria-label="Give the task to" defaultValue={incident.teams[0]?.id ?? 'me'}>
        {incident.teams.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name} queue
          </option>
        ))}
        <option value="me">Me</option>
      </select>
      <button className="btn" type="submit">
        Make the task
      </button>
      <button className="btn btn-quiet" type="button" onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}
