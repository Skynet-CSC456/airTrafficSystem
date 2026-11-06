import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, type Aircraft, type Decision, type Flight, type State } from './api';
import { interpolate, useOperations } from './useOperations';
const d: Decision = { schema_version: 1, request_id: 'r1', aircraft_id: 'A', airport_id: 'SKYNET', operation: 'landing', outcome: 'cleared', runway_id: 'R1', queue_position: null, reason: null, timestamp: 'now', applied_at: 'now' };
const a: Aircraft = { id: 'A', latitude: 40.66, longitude: -73.77, altitude: 500, status: 'Landing', timestamp: 'now', classification: 'outside', tower_decision: d, radar_clearance: d };
const flight: Flight = { request_id: 'r1', aircraft_id: 'A', operation: 'landing', runway_id: 'R1', started_at: 1000, duration: 8, phase: 'running', error: null, points: [{ latitude: 10, longitude: 20 }, { latitude: 14, longitude: 24 }] };
const state = (aircraft: Aircraft[] = [a], hold_operations = false) => ({ aircraft, hold_operations } as State);
const refresh = vi.fn().mockResolvedValue(undefined);
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(1001000); refresh.mockClear(); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });
async function tick(ms = 0) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
describe('persisted flight visualization', () => {
  it('automatically begins exactly once after applied clearance across polls', async () => {
    const begin = vi.spyOn(api, 'begin').mockResolvedValue(flight);
    const { rerender } = renderHook(({ value }) => useOperations(value, refresh), { initialProps: { value: state() } });
    await tick(); rerender({ value: state() }); await tick(2000);
    expect(begin).toHaveBeenCalledExactlyOnceWith('r1', true);
  });
  it.each(['queued', 'rejected', 'unapplied', 'uncorrelated'] as const)('never animates %s responses', async outcome => {
    const begin = vi.spyOn(api, 'begin').mockResolvedValue(flight);
    const candidate = { ...a, tower_decision: { ...d, outcome: outcome === 'queued' || outcome === 'rejected' ? outcome : 'cleared' } } as Aircraft;
    if (outcome === 'unapplied') candidate.radar_clearance = null;
    if (outcome === 'uncorrelated') candidate.radar_clearance = { ...d, request_id: 'other' };
    const { result } = renderHook(() => useOperations(state([candidate]), refresh));
    await tick(1000); expect(begin).not.toHaveBeenCalled(); expect(result.current.positions).toEqual({});
  });
  it('holds clearances but allows an explicit Begin action', async () => {
    const begin = vi.spyOn(api, 'begin').mockResolvedValue(flight);
    const { result } = renderHook(() => useOperations(state([a], true), refresh));
    await tick(1000); expect(begin).not.toHaveBeenCalled();
    await act(async () => { await result.current.begin('r1'); });
    expect(begin).toHaveBeenCalledExactlyOnceWith('r1');
  });
  it('preserves progress across polling and remounts and completes once at the end', async () => {
    const complete = vi.spyOn(api, 'complete').mockResolvedValue({});
    const { result, rerender, unmount } = renderHook(({ value }) => useOperations(value, refresh), { initialProps: { value: state([{ ...a, flight }]) } });
    expect(result.current.positions.A.latitude).toBeCloseTo(10.5);
    await tick(2000); rerender({ value: state([{ ...a, flight: { ...flight } }]) });
    expect(result.current.positions.A.latitude).toBeCloseTo(11.5);
    expect(complete).not.toHaveBeenCalled(); unmount();
    const second = renderHook(() => useOperations(state([{ ...a, flight }]), refresh));
    expect(second.result.current.positions.A.latitude).toBeCloseTo(11.5);
    await tick(6000); expect(complete).toHaveBeenCalledExactlyOnceWith('r1');
    await tick(1000); expect(complete).toHaveBeenCalledTimes(1);
  });
  it('keeps simultaneous plans independent and paused plans stationary', async () => {
    const complete = vi.spyOn(api, 'complete').mockResolvedValue({});
    const b = { ...a, id: 'B', tower_decision: { ...d, request_id: 'r2', aircraft_id: 'B' }, radar_clearance: { ...d, request_id: 'r2', aircraft_id: 'B' }, flight: { ...flight, request_id: 'r2', aircraft_id: 'B', runway_id: 'R2', paused_at: 1001 } };
    const { result } = renderHook(() => useOperations(state([{ ...a, flight }, b]), refresh));
    const before = result.current.positions.B;
    await tick(8000);
    expect(result.current.positions.B).toEqual(before);
    expect(complete).toHaveBeenCalledExactlyOnceWith('r1');
  });
  it('surfaces failed completion without an automatic retry loop', async () => {
    vi.spyOn(api, 'complete').mockRejectedValue(new Error('Radar update failed'));
    const { result } = renderHook(() => useOperations(state([{ ...a, flight }]), refresh));
    await tick(10000);
    expect(result.current.errors['finish:r1']).toBe('Radar update failed');
    expect(api.complete).toHaveBeenCalledTimes(1);
  });
  it('interpolates the assigned runway path and clamps progress at the endpoints', () => {
    expect(interpolate(flight, 0)).toEqual(flight.points[0]);
    expect(interpolate(flight, 1010000)).toEqual(flight.points[1]);
  });
});

it('keeps operation duration fixed with explicit runway phases at any starting distance', () => {
  const plan={...flight,points:[{latitude:100,longitude:20},{latitude:14,longitude:20},{latitude:10,longitude:20}],segment_weights:[.25,.75]};
  expect(interpolate(plan,1002000).latitude).toBeCloseTo(14);
  expect(interpolate(plan,1005000).latitude).toBeCloseTo(12);
  expect(interpolate(plan,1008000).latitude).toBeCloseTo(10);
  expect(interpolate({...plan,points:[{latitude:10000,longitude:20},...plan.points.slice(1)]},1005000).latitude).toBeCloseTo(12);
  expect(plan.duration).toBe(8);
});
it('clears animation/error state on reset and tags stale callbacks with the old generation', async () => {
  let rejectOld!: (error:Error)=>void;
  const begin=vi.spyOn(api,'begin').mockImplementationOnce(() => new Promise((_,reject)=>{rejectOld=reject;})).mockResolvedValue(flight);
  const {result,rerender}=renderHook(({value})=>useOperations(value,refresh),{initialProps:{value:{...state(),generation:'old'}}});
  await tick();expect(begin).toHaveBeenCalledWith('r1',true,'old');
  rerender({value:{...state([]),generation:'new'}});
  await act(async()=>{rejectOld(new Error('stale generation'));});
  expect(result.current.errors).toEqual({});expect(result.current.positions).toEqual({});
  rerender({value:{...state(),generation:'new'}});
  await tick();expect(begin).toHaveBeenLastCalledWith('r1',true,'new');
});
