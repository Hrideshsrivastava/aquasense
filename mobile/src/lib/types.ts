import type { Reading } from './protocol';

export type Role = 'admin' | 'technician' | 'customer';
export interface User { id: number; username: string; name: string; role: Role; phone: string; area: string }

export type ComponentKey = 'pump' | 'membrane' | 'filter' | 'prefilter' | 'sensors';
export interface Baseline {
  flow_rate_Lmin: number;
  pump_current_A: number;
  turbidity_voltage_V: number;
  pressure_bar: number;
  rejection: number;
  n?: number;
}

export interface Hypothesis { fault: string; label: string; score: number; evidence: string[]; streak: number }

export interface Device {
  id: string;
  name: string;
  address: string;
  customer: { id: number; name: string; phone: string; area: string } | null;
  online: boolean;
  last_seen: number;
  status: 'normal' | 'learning' | 'idle' | 'suspect' | 'alert' | 'service' | 'offline';
  hypothesis: Hypothesis | null;
  health: Record<ComponentKey, number>;
  baseline: Baseline | null;
  latest: (Reading & { ts: number }) | null;
  job: Job | null;
}

export type JobStatus = 'new' | 'assigned' | 'en_route' | 'on_site' | 'confirmed' | 'no_fault' | 'resolved' | 'cancelled';

export interface Job {
  id: number;
  device_id: string;
  source: 'quick_check' | 'customer' | 'company';
  hypothesis: string | null;
  hypothesis_label: string;
  status: JobStatus;
  note: string;
  created_at: number;
  updated_at: number;
  technician: { id: number; name: string; phone: string } | null;
  device?: { id: string; name: string; address: string; customer: { id: number; name: string; phone: string } | null; baseline: Baseline | null };
  estimate?: { part: string; parts: number; labour: number; visit: number; total: number };
  report: Report | null;
}

export interface Report {
  verdict: 'confirmed' | 'different' | 'no_fault';
  fault: string;
  fault_label: string;
  confidence: number;
  reason: string;
  evidence: string[];
  health: Record<ComponentKey, number>;
  rul: Partial<Record<ComponentKey, { low: number; high: number } | null>>;
  quote: { replace: string[]; lines: { part: string; cost: number }[]; labour: number; visit: number; total: number };
  readings: number;
  collected_at: string;
  source: 'bluetooth' | 'server-relay';
}

export const JOB_LABEL: Record<JobStatus, string> = {
  new: 'Waiting for a technician', assigned: 'Technician assigned', en_route: 'Technician on the way', on_site: 'Technician on site',
  confirmed: 'Fault confirmed', no_fault: 'No fault found', resolved: 'Resolved', cancelled: 'Cancelled',
};
export const STATUS_LABEL: Record<Device['status'], string> = {
  normal: 'Healthy', learning: 'Setting up', idle: 'Idle', suspect: 'Checking a possible issue', alert: 'Issue detected',
  service: 'Service in progress', offline: 'Offline',
};
export const COMPONENT_LABEL: Record<ComponentKey, string> = {
  pump: 'Booster pump', membrane: 'RO membrane', filter: 'Filter / line', prefilter: 'Sediment pre-filter', sensors: 'Sensors',
};
export const ACTIVE: JobStatus[] = ['new', 'assigned', 'en_route', 'on_site', 'confirmed'];
