import { api } from './client';

export interface Delivery {
  event: string;
  at: string;
  ok: boolean;
  status: number | null;
  error: string;
  tries: number;
}

export interface Webhook {
  id: string;
  url: string;
  events: string[];
  description: string;
  enabled: boolean;
  has_secret: boolean;
  created_at: string;
  version: number;
  deliveries: Delivery[];
  secret: string | null; // only in the answer to creating one
}

export interface Webhooks {
  events: string[];
  sending: boolean; // false: the edition doesn't include automation
  webhooks: Webhook[];
}

export const eventWords: Record<string, string> = {
  'incident.created': 'An incident is declared',
  'incident.status_changed': 'An incident moves phase or is reopened',
  'incident.closed': 'An incident is closed',
  'task.assigned': 'A task is given to someone or claimed',
  'hunt.completed': 'A hunt is closed',
};

export const getWebhooks = () => api<Webhooks>('/api/webhooks');
