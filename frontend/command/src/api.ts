export type FlightStatus = 'Parked' | 'Landing' | 'Departing';
export type Operation = 'landing' | 'departure';
export interface Decision { schema_version: number; request_id: string; aircraft_id: string; airport_id: string; operation: Operation; outcome: 'cleared' | 'queued' | 'rejected' | 'completed'; runway_id: string | null; queue_position: number | null; reason: string | null; timestamp: string; source_message_id?: number; applied_at?: string; }
export interface AircraftInput { id: string; latitude: number; longitude: number; altitude: number; status: FlightStatus; }
export interface Point { latitude: number; longitude: number; }
export interface Flight { request_id: string; aircraft_id: string; operation: Operation; runway_id: string; segment_weights?: number[]; completed_at?: number; started_at: number; paused_at?: number; duration: number; phase: 'running' | 'failed' | 'completed'; error: string | null; points: Point[]; }
export interface Occupancy { aircraft_id: string; runway_id: string; phase: 'parked' | 'departing'; }
export interface Aircraft extends AircraftInput { timestamp: string; classification: 'inside' | 'outside'; departure_completed_at?: number | null; occupancy?: Occupancy | null; flight?: Flight | null; tower_decision: Decision | null; radar_clearance: Decision | null; }
export interface Runway { stationary?: Occupancy; points?: Point[]; id: string; runway_id: string; available: boolean; open: boolean; mode: string; request_id: string | null; aircraft_id: string | null; }
export interface Bounds { north: number; south: number; east: number; west: number; }
export interface Airport { airport_id: string; name: string; latitude: number; longitude: number; runways: Runway[]; geometry: Bounds & { rule: string; viewport: Bounds }; }
export interface RunwayRequest { schema_version: number; request_id: string; aircraft_id: string; airport_id: string; operation: Operation; timestamp: string; runway_id?: string; }
export interface Event { id: number; sender: string; recipient: string; message_type: string; content: string; metadata: Decision | RunwayRequest | null; sent_at: string; status: string; receipt: { received_at: string; applied_at: string | null; error: string | null } | null; }
export interface State { generation?: string; hold_operations?: boolean; airport: Airport; aircraft: Aircraft[]; requests: Decision[]; queue: Decision[]; events: Event[]; system_status: string; workers: Record<string, { healthy: boolean; error: string | null }>; }
let generation: string | undefined;
let stateSequence = 0;
async function call<T>(path: string, method = 'GET', body?: unknown, expectedGeneration = generation): Promise<T> {
  const response = await fetch('/api' + path, { method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json', ...(expectedGeneration ? { 'X-Demo-Generation': expectedGeneration } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result as T;
}
export const api = {
  state: async () => {
    const sequence = ++stateSequence;
    const result = await call<State>('/state');
    if (sequence === stateSequence) generation = result.generation;
    return result;
  },
  reset: async () => {
    ++stateSequence;
    const result = await call<State>('/reset', 'POST', {});
    ++stateSequence; generation = result.generation;
    return result;
  },
  createAircraft: (data: AircraftInput) => call<AircraftInput>('/aircraft', 'POST', data),
  updateAircraft: (id: string, data: Omit<AircraftInput, 'id'>) => call<AircraftInput>('/aircraft/' + encodeURIComponent(id), 'PATCH', data),
  request: (aircraft_id: string, operation: Operation, request_id: string = crypto.randomUUID()) => call('/requests', 'POST', { aircraft_id, operation, request_id }),
  addRunway: (runway_id: string) => call('/runways', 'POST', { runway_id }),
  hold: (enabled: boolean) => call('/hold', 'POST', { enabled }),
  begin: (request_id: string, automatic = false, expectedGeneration?: string) => call<Flight>('/operations/' + encodeURIComponent(request_id) + '/begin', 'POST', { automatic }, expectedGeneration),
  complete: (request_id: string, expectedGeneration?: string) => call('/operations/' + encodeURIComponent(request_id) + '/complete', 'POST', {}, expectedGeneration),
};
