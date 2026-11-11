// Shapes returned by the ostraka API (backend/ostraka/schemas.py)

import type { Mode, Palette } from '../theme/theme';

export type Role = 'admin' | 'team_lead' | 'responder' | 'observer';

export const roleNames: Record<Role, string> = {
  admin: 'Admin',
  team_lead: 'Team lead',
  responder: 'Responder',
  observer: 'Observer',
};

export interface Team {
  id: string;
  name: string;
  code?: string | null;
  aliases?: string[];
  description: string;
  has_channel?: boolean;
}

export interface Person {
  id: string;
  name: string;
  email: string;
  role: Role;
  team: Team | null;
  is_active: boolean;
  job_title: string;
  availability: Availability;
  timezone: string;
  // Blank for observers, who don't see out-of-hours numbers
  phone: string | null;
}

export type Availability = 'available' | 'busy' | 'away';

export const availabilityNames: Record<Availability, string> = {
  available: 'Available',
  busy: 'Busy',
  away: 'Away',
};

export interface Me extends Person {
  theme_palette: Palette;
  theme_mode: Mode;
  show_raw_iocs: boolean;
  notify_email: boolean;
  notify_push: boolean;
  last_login_at: string | null;
  backup_codes_left: number;
  muted_kinds: string[];
  quiet_from: string | null;
  quiet_until: string | null;
  new_device_alerts: boolean;
}

export interface SignedInBrowser {
  family: string;
  device: string;
  ip: string;
  signed_in_at: string;
  last_active_at: string;
  expires_at: string;
  current: boolean;
}

export interface SecurityEvent {
  id: string;
  at: string;
  action: string;
  ip: string;
  by: string | null;
  detail: Record<string, unknown>;
}

export interface Grant {
  access_token: string;
  expires_in: number;
}

export interface Invitation {
  id: string;
  email: string;
  role: Role;
  team: Team | null;
  created_at: string;
  expires_at: string;
  is_reset: boolean;
}

export interface InvitationCreated extends Invitation {
  link: string;
}

export interface InvitationPreview {
  email: string;
  role: Role;
  team: string | null;
  is_reset: boolean;
  expires_at: string;
}

export interface Enrolment {
  otpauth_uri: string;
  qr_svg: string;
  secret: string;
}

export interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  scope: 'full' | 'mcp';
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
}
