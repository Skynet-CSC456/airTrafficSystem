import { afterEach, expect, it, vi } from 'vitest';
import { api } from './api';
afterEach(() => vi.unstubAllGlobals());
it('sends the existing request ID and operation to the API', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ delivery: 'sent' }) });
  vi.stubGlobal('fetch', fetch);
  await api.request('AC001', 'landing', 'same-request');
  expect(fetch).toHaveBeenCalledWith('/api/requests', expect.objectContaining({ method: 'POST', body: JSON.stringify({ aircraft_id: 'AC001', operation: 'landing', request_id: 'same-request' }) }));
});
it('propagates meaningful backend errors', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ error: 'Unknown aircraft' }) }));
  await expect(api.state()).rejects.toThrow('Unknown aircraft');
});

it('preserves an old callback generation after reset while new controls use the new token', async () => {
  const fetch=vi.fn().mockImplementation(async (path:string)=>({ok:true,json:async()=>path==='/api/state'?{generation:'old'}:path==='/api/reset'?{generation:'new'}:{}}));
  vi.stubGlobal('fetch',fetch);
  await api.state();await api.reset();await api.complete('same-request','old');
  expect(fetch).toHaveBeenLastCalledWith('/api/operations/same-request/complete',expect.objectContaining({headers:expect.objectContaining({'X-Demo-Generation':'old'})}));
  await api.addRunway('R11');
  expect(fetch).toHaveBeenLastCalledWith('/api/runways',expect.objectContaining({headers:expect.objectContaining({'X-Demo-Generation':'new'})}));
});
