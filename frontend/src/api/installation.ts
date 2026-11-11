import { api } from './client';

export type Edition = 'free' | 'pro' | 'services';

export interface Licence {
  edition: Edition;
  state: 'free' | 'pending' | 'active' | 'grace' | 'lapsed';
  organisation: string;
  seats: number | null; // null: unlimited
  features: string[];
  places_held: number | null;
  not_before: string | null;
  expires: string | null;
  grace_ends: string | null;
  custom_branding: boolean;
  brand_library: boolean;
  terms_version: string;
  accepted: { by: string; at: string; terms_version: string } | null;
}

export interface LicencePreview {
  licence_id: string;
  licensee: string;
  edition: Edition;
  seats: number | null;
  features: string[];
  not_before: string | null;
  expires: string | null;
  state: Licence['state'];
}

export interface Terms {
  version: string;
  text: string;
}

export type LogoSlot = 'cover' | 'header' | 'client';

export interface Brand {
  id: string;
  name: string;
  primary: string;
  secondary: string;
  prepared_for: string;
  is_default: boolean;
  logos: LogoSlot[];
  palette: Record<string, string>;
  notes: string[];
  updated_at: string;
  version: number;
}

export const editionNames: Record<Edition, string> = { free: 'Free', pro: 'Pro', services: 'Services' };

export const logoSlots: { slot: LogoSlot; label: string; hint: string }[] = [
  { slot: 'cover', label: 'Cover logo', hint: 'Large, on the title page and the back cover.' },
  { slot: 'header', label: 'Header mark', hint: 'Small, at the top of every page. A square icon works best.' },
  { slot: 'client', label: 'Client logo', hint: 'Beside “Prepared for” on the title page.' },
];

// A licence date (YYYY-MM-DD) as a day: 30 November 2027
export const day = (iso: string | null) =>
  iso ? new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', { dateStyle: 'long', timeZone: 'UTC' }) : '—';

export const seatsText = (l: Licence) => (l.seats === null ? 'unlimited' : String(l.seats));

export const getLicence = () => api<Licence>('/api/licence');
