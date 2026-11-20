import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useDashboard } from './useDashboard';
import { api, type State } from './api';
afterEach(() => vi.restoreAllMocks());
it('loads actual API state and refreshes after a successful control action', async () => {
  const state = { aircraft: [], queue: [], events: [] } as unknown as State;
  const fetch = vi.spyOn(api, 'state').mockResolvedValue(state);
  const { result, unmount } = renderHook(() => useDashboard());
  await waitFor(() => expect(result.current.state).toBe(state));
  const action = vi.fn().mockResolvedValue({});
  const { act } = await import('@testing-library/react');
  await act(async () => { await result.current.execute(action); });
  expect(action).toHaveBeenCalledOnce();
  expect(fetch).toHaveBeenCalledTimes(2);
  unmount();
});
it('polls once per second and stops polling after unmount', async () => {
  vi.useFakeTimers();
  try {
    const fetch = vi.spyOn(api, 'state').mockResolvedValue({ aircraft: [] } as unknown as State);
    const { act } = await import('@testing-library/react');
    const { unmount } = renderHook(() => useDashboard());
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetch).toHaveBeenCalledTimes(2);
    unmount();
    await vi.advanceTimersByTimeAsync(2000);
    expect(fetch).toHaveBeenCalledTimes(2);
  } finally { vi.useRealTimers(); }
});

it('preserves current airport state if confirmed reset fails', async () => {
  const state={aircraft:[{id:'KEEP'}],queue:[],events:[],generation:'old'} as unknown as State;
  vi.spyOn(api,'state').mockResolvedValue(state);
  vi.spyOn(api,'reset').mockRejectedValue(new Error('Reset failed safely'));
  vi.spyOn(window,'confirm').mockReturnValue(true);
  const {result}=renderHook(()=>useDashboard());
  await waitFor(()=>expect(result.current.state).toBe(state));
  const {act}=await import('@testing-library/react');
  await act(async()=>{await result.current.reset();});
  expect(result.current.state).toBe(state);expect(result.current.error).toBe('Reset failed safely');
});

it('ignores a pre-reset poll that resolves after the reset response', async () => {
  const old={aircraft:[{id:'OLD'}],generation:'old'} as unknown as State;
  const fresh={aircraft:[],generation:'new'} as unknown as State;
  let resolveOld!: (state:State)=>void;
  vi.spyOn(api,'state').mockResolvedValueOnce(old).mockImplementationOnce(()=>new Promise(resolve=>{resolveOld=resolve;}));
  vi.spyOn(api,'reset').mockResolvedValue(fresh);vi.spyOn(window,'confirm').mockReturnValue(true);
  const {result}=renderHook(()=>useDashboard());await waitFor(()=>expect(result.current.state).toBe(old));
  const {act}=await import('@testing-library/react');
  let poll!:Promise<void>;
  await act(async()=>{poll=result.current.refresh();await result.current.reset();});
  await act(async()=>{resolveOld(old);await poll;});
  expect(result.current.state).toBe(fresh);
});
