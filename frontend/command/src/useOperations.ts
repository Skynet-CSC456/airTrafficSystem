/** Persisted operation timelines survive polling/remounts. No clearance logic here. */
import { useEffect, useRef, useState } from 'react';
import { api, type Aircraft, type Flight, type Point, type State } from './api';
export function interpolate(flight: Flight, now: number): Point {
  const fraction = flight.duration <= 0 ? 1 : Math.max(0, Math.min(1, ((flight.paused_at ?? now / 1000) - flight.started_at) / flight.duration));
  const weights = flight.segment_weights || flight.points.slice(1).map(() => 1 / (flight.points.length - 1));
  let index = 0, elapsed = fraction;
  while (index < weights.length - 1 && elapsed > weights[index]) { elapsed -= weights[index]; index++; }
  const t = Math.max(0, Math.min(1, elapsed / weights[index]));
  const a = flight.points[index], b = flight.points[index + 1];
  return { latitude: a.latitude + (b.latitude - a.latitude) * t, longitude: a.longitude + (b.longitude - a.longitude) * t };
}
export function canBegin(a: Aircraft) {
  const d = a.tower_decision, r = a.radar_clearance;
  return !!(d?.outcome === 'cleared' && r?.outcome === 'cleared' && r.request_id === d.request_id && r.applied_at);
}
export function useOperations(state: State | null, refresh: () => Promise<void>) {
  const [now, setNow] = useState(Date.now());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const pending = useRef(new Set<string>());
  const mounted = useRef(true);
  const currentGeneration = useRef(state?.generation);
  currentGeneration.current = state?.generation;
  useEffect(() => { pending.current.clear(); setErrors({}); }, [state?.generation]);
  useEffect(() => { mounted.current = true; const timer = setInterval(() => setNow(Date.now()), 40); return () => { mounted.current = false; clearInterval(timer); }; }, []);
  const run = (key: string, action: () => Promise<unknown>) => {
    if (pending.current.has(key)) return;
    pending.current.add(key);
    const startedGeneration = currentGeneration.current;
    void action().then(refresh).catch(e => {
      if (mounted.current && currentGeneration.current === startedGeneration) setErrors(old => ({ ...old, [key]: String(e.message) }));
    });
  };
  useEffect(() => {
    if (state?.hold_operations === false) {
      for (const key of pending.current) if (key.startsWith('begin:')) pending.current.delete(key);
      setErrors(old => Object.fromEntries(Object.entries(old).filter(([key]) => !key.startsWith('begin:'))));
    }
  }, [state?.hold_operations]);
  useEffect(() => {
    if (!state) return;
    for (const a of state.aircraft) {
      const id = a.tower_decision?.request_id;
      if (!id || !canBegin(a)) continue;
      const flight = a.flight?.request_id === id ? a.flight : null;
      if (!flight && !state.hold_operations) run('begin:' + id, () => state.generation ? api.begin(id, true, state.generation) : api.begin(id, true));
      if (flight?.phase === 'running' && !flight.paused_at && now >= (flight.started_at + flight.duration) * 1000)
        run('finish:' + id, () => state.generation ? api.complete(id, state.generation) : api.complete(id));
    }
  }, [state, now]); // pending keys prevent duplicate starts/completions between polls
  const begin = async (id: string) => {
    if (state?.generation) await api.begin(id, false, state.generation); else await api.begin(id);
    setErrors(old => { const copy = { ...old }; delete copy['begin:' + id]; return copy; });
    await refresh();
  };
  const retry = async (id: string) => {
    await api.complete(id, state?.generation); setErrors(old => { const copy = { ...old }; delete copy['finish:' + id]; return copy; }); await refresh();
  };
  const positions: Record<string, Point> = {};
  for (const a of state?.aircraft || []) {
    if (canBegin(a) && a.flight && a.flight.request_id === a.tower_decision?.request_id && a.flight.phase !== 'completed')
      positions[a.id] = interpolate(a.flight, now);
  }
  return { positions, errors, begin, retry, now };
}
