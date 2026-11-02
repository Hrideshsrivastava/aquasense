// Typed client for the AquaSense cloud server (cloud/server.py).

export type Status = 'normal' | 'warning' | 'replace' | 'no_data';

export interface Rul {
  low: number;
  mid: number;
  high: number;
  method: string;
}

export interface Component {
  component: 'pump' | 'membrane' | 'filter' | 'sensors';
  name: string;
  health: number;
  status: Exclude<Status, 'no_data'>;
  rul: Rul | null;
  failure_prob: number;
  decision?: 'replace' | 'watch' | 'ok';
}

export interface QuoteLine {
  component: string;
  part: string;
  cost: number;
  health: number;
  failure_prob: number;
  decision: string;
  expected_cost: number;
}

export interface Assessment {
  status: Status;
  message?: string;
  fault: string;
  fault_name: string;
  confidence: number;
  reason: string;
  evidence: string[];
  components: Component[];
  quote: {
    replace_now: QuoteLine[];
    watch_list: QuoteLine[];
    labour: number;
    visit_charge: number;
    repair_price_now: number;
    expected_cost: number;
  };
  tds_in: number;
  rejection_pct: number;
}

export interface FleetUnit {
  id: string;
  name: string;
  customer: string;
  city: string;
  status: Status;
  fault_name: string;
  simulated: boolean;
  online: boolean;
  open_alerts: number;
  min_health: number | null;
}

export interface Alert {
  id: number;
  status: string;
  fault_name: string;
  confidence: number;
  reason: string;
  created_at: string;
}

export interface Ticket {
  id: number;
  unit_id: string;
  unit_name: string;
  customer: string;
  phone: string;
  city: string;
  fault: string;
  part_to_carry: string;
  price_estimate: number;
  visit_window: string;
  status: 'open' | 'scheduled' | 'done';
  job_card: string;
  created_at: string;
}

export interface UnitDetail {
  unit: { id: string; name: string; customer: string; city: string; water_source: string; simulated: number };
  assessment: Assessment;
  online: boolean;
  last_received: string | null;
  alerts: Alert[];
  tickets: Ticket[];
}

async function request<T>(server: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(server.replace(/\/+$/, '') + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export const api = {
  health: (s: string) => request<{ ok: boolean; model: Record<string, unknown> }>(s, '/api/health'),
  units: (s: string) => request<FleetUnit[]>(s, '/api/units'),
  unit: (s: string, id: string) => request<UnitDetail>(s, `/api/units/${encodeURIComponent(id)}`),
  bookService: (s: string, id: string, note: string) =>
    request<Ticket>(s, `/api/units/${encodeURIComponent(id)}/contact`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    }),
  tickets: (s: string) => request<Ticket[]>(s, '/api/tickets'),
  setTicket: (s: string, id: number, status: Ticket['status']) =>
    request<Ticket>(s, `/api/tickets/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
};

export function inr(n: number): string {
  const s = Math.round(n).toString();
  if (s.length <= 3) return `₹${s}`;
  const head = s.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ',');
  return `₹${head},${s.slice(-3)}`;
}

export function rulText(r: Rul | null): string {
  if (!r) return '';
  if (r.high <= 0) return 'replace now';
  return `${r.low}–${r.high} days`;
}
