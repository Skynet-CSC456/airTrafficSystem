import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type State } from './api';
export function useDashboard() {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const alive = useRef(false);
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const id = ++sequence.current;
    try { const result = await api.state(); if (alive.current && id === sequence.current) setState(result); }
    catch (e) { if (alive.current) setError((e as Error).message); }
  }, []);
  useEffect(() => { alive.current = true; void refresh(); const timer = setInterval(() => void refresh(), 1000); return () => { alive.current = false; clearInterval(timer); }; }, [refresh]);
  const execute = async (action: () => Promise<unknown>) => {
    setBusy(true); setError('');
    try { await action(); await refresh(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const reset = async () => {
    if (!window.confirm('Reset Demo? All demo aircraft, requests and messages will be cleared. The airport will return to ten available runways.')) return;
    setBusy(true); setError(''); ++sequence.current;
    try {
      const result = await api.reset();
      ++sequence.current;
      if (alive.current) setState(result);
    } catch (e) { if (alive.current) setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return { state, error, busy, execute, refresh, reset };
}
