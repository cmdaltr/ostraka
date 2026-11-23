import { useState } from 'react';
import { api } from '../api/client';
import { messageOf } from './ui';

export type ChannelKind = '' | 'teams' | 'slack';

export const channelNames: Record<Exclude<ChannelKind, ''>, string> = {
  teams: 'Microsoft Teams',
  slack: 'Slack',
};

export function channelStatus(kind: ChannelKind | undefined, connected: boolean | undefined): string {
  if (!connected && !kind) return 'Not connected';
  return kind ? `${channelNames[kind]} connected` : 'Connected';
}

// How to get a webhook URL from each, shown wherever a channel is connected
export function ChannelHelp() {
  return (
    <p>
      <strong>Teams:</strong> in the channel, open Workflows and choose “Post to a channel when a webhook request is
      received”. <strong>Slack:</strong> add an app with Incoming Webhooks and pick the channel. Paste the URL it gives
      you; Ostraka works out which it is, stores it encrypted and never shows it again.
    </p>
  );
}

// Paste, replace, test or disconnect a channel's webhook at `base` (PUT to set, POST base/test)
export function ChannelForm({
  base,
  name,
  connected,
  onChanged,
}: {
  base: string;
  name: string;
  connected: boolean;
  onChanged: () => void;
}) {
  const [url, setUrl] = useState('');
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const call = (path: string, init: RequestInit & { json?: unknown }, ok: string) =>
    api(path, init)
      .then(() => {
        setMessage({ kind: 'ok', text: ok });
        setUrl('');
        onChanged();
      })
      .catch((e) => setMessage({ kind: 'error', text: messageOf(e) }));
  return (
    <div>
      <form
        className="copy-box"
        onSubmit={(e) => {
          e.preventDefault();
          call(base, { method: 'PUT', json: { url } }, 'Connected. Send a test to check it reaches the channel.');
        }}
      >
        <input
          className="input"
          type="url"
          required
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder={connected ? 'Paste a new URL to replace it' : 'A Teams Workflows or Slack webhook URL'}
          aria-label={`Channel webhook URL for ${name}`}
        />
        <button className="btn btn-quiet" type="submit">
          {connected ? 'Replace' : 'Connect'}
        </button>
      </form>
      {connected && (
        <div className="actions" style={{ marginTop: 4 }}>
          <button className="link-button" type="button" onClick={() => call(`${base}/test`, { method: 'POST' }, 'Test sent.')}>
            Send a test
          </button>
          <button className="link-button" type="button" onClick={() => call(base, { method: 'PUT', json: { url: '' } }, 'Disconnected.')}>
            Disconnect
          </button>
        </div>
      )}
      {message && <div className={`notice-${message.kind}`} style={{ fontSize: 12, marginTop: 4 }}>{message.text}</div>}
    </div>
  );
}
