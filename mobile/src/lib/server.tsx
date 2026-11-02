import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

// Laptop running `python -m cloud.server` on the phone hotspot.
// Set EXPO_PUBLIC_AQUASENSE_SERVER in mobile/.env.local, or change it in the Settings tab.
const DEFAULT_SERVER = process.env.EXPO_PUBLIC_AQUASENSE_SERVER ?? 'http://192.168.43.100:5000';

interface ServerState {
  server: string;
  setServer: (s: string) => void;
  unitId: string | null;
  setUnitId: (id: string | null) => void;
}

const Ctx = createContext<ServerState | null>(null);

export function ServerProvider({ children }: { children: ReactNode }) {
  const [server, setServer] = useState(DEFAULT_SERVER);
  const [unitId, setUnitId] = useState<string | null>(null);
  return <Ctx.Provider value={{ server, setServer, unitId, setUnitId }}>{children}</Ctx.Provider>;
}

export function useServer(): ServerState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useServer must be used inside ServerProvider');
  return v;
}

/** Calls `load` now and every `ms` milliseconds; keeps the last good value. */
export function usePolling<T>(load: () => Promise<T>, ms: number, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });

  const refresh = useCallback(async () => {
    try {
      const v = await loadRef.current();
      setData(v);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    refresh();
    const t = setInterval(refresh, ms);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, refresh };
}
