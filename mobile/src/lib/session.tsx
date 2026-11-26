// Sign-in, REST calls and the real-time WebSocket to the AquaSense server.
import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Platform, Vibration } from 'react-native';

import type { Reading } from './protocol';
import type { Device, Job, Report, User } from './types';

const DEFAULT_SERVER = process.env.EXPO_PUBLIC_AQUASENSE_SERVER ?? 'http://192.168.1.100:5000';
const K = { server: 'aq.server', token: 'aq.token', user: 'aq.user', pending: 'aq.pendingReports' };

interface PendingReport { jobId: number; report: Report }

interface SessionState {
  ready: boolean;
  server: string;
  token: string | null;
  user: User | null;
  connected: boolean;
  devices: Record<string, Device>;
  jobs: Record<number, Job>;
  readings: Record<string, Reading[]>;
  pending: PendingReport[];
  setServer: (s: string) => Promise<void>;
  login: (username: string, pin: string) => Promise<User>;
  logout: () => Promise<void>;
  api: <T>(path: string, method?: string, body?: unknown) => Promise<T>;
  submitReport: (jobId: number, report: Report) => Promise<'sent' | 'queued'>;
}

const Ctx = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [server, setServerState] = useState(DEFAULT_SERVER);
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [connected, setConnected] = useState(false);
  const [devices, setDevices] = useState<Record<string, Device>>({});
  const [jobs, setJobs] = useState<Record<number, Job>>({});
  const [readings, setReadings] = useState<Record<string, Reading[]>>({});
  const [pending, setPending] = useState<PendingReport[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  // ---- restore
  useEffect(() => {
    (async () => {
      const [s, t, u, p] = await Promise.all([K.server, K.token, K.user, K.pending].map((k) => AsyncStorage.getItem(k)));
      if (s) setServerState(s);
      if (t && u) { setToken(t); setUser(JSON.parse(u)); }
      if (p) setPending(JSON.parse(p));
      setReady(true);
    })();
  }, []);

  const base = server.replace(/\/+$/, '');

  const api = useCallback(async <T,>(path: string, method = 'GET', body?: unknown): Promise<T> => {
    const r = await fetch(base + path, {
      method,
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((j as { error?: string }).error || `HTTP ${r.status}`);
    return j as T;
  }, [base, token]);

  const setServer = async (s: string) => {
    setServerState(s.trim());
    await AsyncStorage.setItem(K.server, s.trim());
  };

  const login = async (username: string, pin: string) => {
    const r = await fetch(`${base}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, pin }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `Cannot reach ${base}`);
    if (j.user.role === 'admin') throw new Error('Service desk accounts use the web console.');
    setToken(j.token);
    setUser(j.user);
    await AsyncStorage.multiSet([[K.token, j.token], [K.user, JSON.stringify(j.user)]]);
    return j.user as User;
  };

  const logout = async () => {
    wsRef.current?.close();
    setToken(null); setUser(null); setDevices({}); setJobs({}); setReadings({});
    await AsyncStorage.multiRemove([K.token, K.user]);
  };

  // ---- offline queue for on-site reports
  const flush = useCallback(async (queue: PendingReport[]) => {
    const left: PendingReport[] = [];
    for (const p of queue) {
      try { await api(`/api/jobs/${p.jobId}/report`, 'POST', p.report); } catch { left.push(p); }
    }
    setPending(left);
    await AsyncStorage.setItem(K.pending, JSON.stringify(left));
  }, [api]);

  const submitReport = async (jobId: number, report: Report) => {
    try {
      await api(`/api/jobs/${jobId}/report`, 'POST', report);
      return 'sent' as const;
    } catch {
      const q = [...pending.filter((p) => p.jobId !== jobId), { jobId, report }];
      setPending(q);
      await AsyncStorage.setItem(K.pending, JSON.stringify(q));
      return 'queued' as const;
    }
  };

  // ---- real time
  useEffect(() => {
    if (!token || !user) return;
    let stop = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const open = () => {
      const ws = new WebSocket(`${base.replace(/^http/, 'ws')}/ws?token=${token}`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!stop) retry = setTimeout(open, 3000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        const m = JSON.parse(String(e.data));
        if (m.type === 'error') { logout(); return; }
        if (m.type === 'snapshot') {
          setDevices(Object.fromEntries((m.devices as Device[]).map((d) => [d.id, d])));
          setJobs(Object.fromEntries((m.jobs as Job[]).map((j) => [j.id, j])));
        } else if (m.type === 'device') {
          setDevices((d) => ({ ...d, [m.device.id]: m.device }));
        } else if (m.type === 'reading') {
          setReadings((r) => ({ ...r, [m.device_id]: [...(r[m.device_id] || []).slice(-299), m.reading] }));
          setDevices((d) => (d[m.device_id] ? { ...d, [m.device_id]: { ...d[m.device_id], latest: m.reading, online: true } } : d));
        } else if (m.type === 'job') {
          setJobs((j) => ({ ...j, [m.job.id]: m.job }));
        } else if (m.type === 'notify') {
          if (Platform.OS !== 'web') Vibration.vibrate([0, 300, 150, 300]);
          Alert.alert(m.title, m.body);
        }
      };
    };
    open();
    return () => { stop = true; clearTimeout(retry); wsRef.current?.close(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, user?.id, base]);

  useEffect(() => {
    if (connected && pending.length) flush(pending);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  return (
    <Ctx.Provider value={{ ready, server, token, user, connected, devices, jobs, readings, pending, setServer, login, logout, api, submitReport }}>
      {children}
    </Ctx.Provider>
  );
}

export function useSession(): SessionState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useSession must be used inside SessionProvider');
  return v;
}
