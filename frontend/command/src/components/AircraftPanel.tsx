import { useState } from 'react';
import { canBegin } from '../useOperations';
import { api, type Aircraft, type AircraftInput, type Airport, type FlightStatus } from '../api';
type AircraftDraft = Omit<AircraftInput, 'altitude'> & { altitude: string };
function altitudeNumber(draft: string) {
  const altitude=Number(draft);
  if (!draft.trim()) throw new Error('Enter an altitude before saving.');
  if (!/^\+?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(draft.trim()) || !Number.isFinite(altitude) || altitude<0)
    throw new Error('Altitude must be a finite number of 0 or greater.');
  return altitude;
}
type Execute = (action: () => Promise<unknown>) => Promise<void>;
export function AircraftForm({ airport, busy, execute }: { airport: Airport; busy: boolean; execute: Execute }) {
  const [data, setData] = useState<AircraftDraft>({ id: '', latitude: airport.latitude, longitude: airport.longitude, altitude: '0', status: 'Parked' });
  const [formError,setFormError]=useState('');
  return <section className="panel aircraft-form"><span className="eyebrow">RADAR / AIRCRAFT CONTROL</span><h2>Add aircraft</h2><form onSubmit={e => { e.preventDefault();
    let altitude:number;
    try { altitude=altitudeNumber(data.altitude); } catch(error) { setFormError((error as Error).message); return; }
    setFormError(''); void execute(() => api.createAircraft({...data,altitude})); }}>
    <label>Aircraft ID<input required value={data.id} onChange={e => setData({ ...data, id: e.target.value })} placeholder="SKY001"/></label>
    <div className="form-grid"><label>Latitude<input type="number" step="any" required value={data.latitude} onChange={e => setData({ ...data, latitude: Number(e.target.value) })}/></label><label>Longitude<input type="number" step="any" required value={data.longitude} onChange={e => setData({ ...data, longitude: Number(e.target.value) })}/></label></div>
    <div className="form-grid"><label>Altitude (m)<input type="text" inputMode="decimal" value={data.altitude} onChange={e => setData({ ...data, altitude: e.target.value })}/></label><label>Flight status<select value={data.status} onChange={e => { const status = e.target.value as FlightStatus; setData({ ...data, status, altitude: status === 'Landing' ? '500' : '0', latitude: status === 'Landing' ? airport.latitude + .015 : airport.latitude, longitude: airport.longitude }); }}><option>Parked</option><option>Landing</option><option disabled>Departing</option></select></label></div>
    {formError && <p role="alert">{formError}</p>}<button disabled={busy}>Add aircraft</button></form><p className="muted">Parked: occupies an available runway. Landing: one automatic request. For departure, create Parked then click Depart. Outside example: latitude {(airport.latitude + 0.015).toFixed(4)}, Landing, 500 m.</p></section>;
}
export function AircraftTable({ aircraft, busy, execute, begin, retry, operationErrors = {} }: { aircraft: Aircraft[]; busy: boolean; execute: Execute; begin?: (id: string) => Promise<unknown>; retry?: (id: string) => Promise<unknown>; operationErrors?: Record<string,string> }) {
  const [editing, setEditing] = useState<AircraftDraft | null>(null);
  const [editError, setEditError] = useState('');
  return <section className="panel"><div className="panel-heading"><div><span className="eyebrow">AUTHORITATIVE RADAR RECORDS</span><h2>Aircraft & clearances</h2></div><span>{aircraft.length} TRACKED</span></div>
    <div className="table-scroll"><table><thead><tr><th>Aircraft</th><th>Position / boundary</th><th>Flight / altitude</th><th>Tower decision</th><th>Radar applied</th><th>Controls</th></tr></thead><tbody>{aircraft.map(a => <tr key={a.id}><td><strong>{a.id}</strong></td><td>{a.latitude.toFixed(4)}, {a.longitude.toFixed(4)}<br/><span className={'tag ' + a.classification}>{a.classification}</span></td><td>{a.status}<br/>{a.altitude} m{a.occupancy && <><br/>Occupies {a.occupancy.runway_id}</>}</td><td>{a.tower_decision ? <>{a.tower_decision.operation} / {a.tower_decision.outcome}<br/>{a.tower_decision.runway_id || (a.tower_decision.queue_position ? `Queue ${a.tower_decision.queue_position}` : a.tower_decision.reason)}</> : 'No request'}</td><td>{a.radar_clearance ? <>{a.radar_clearance.outcome}<br/>{a.radar_clearance.runway_id || '—'}<br/><small>message #{a.radar_clearance.source_message_id}</small></> : 'No applied response'}</td><td><div className="actions"><button className="secondary" disabled={busy || !!a.tower_decision && ['queued','cleared'].includes(a.tower_decision.outcome)} onClick={() => void execute(() => api.request(a.id, 'landing'))}>Land {a.id}</button><button className="secondary" disabled={busy || a.status !== 'Parked' || !!a.tower_decision && ['queued','cleared'].includes(a.tower_decision.outcome)} onClick={() => void execute(() => api.request(a.id, 'departure'))}>Depart {a.id}</button><button className="secondary" disabled={busy} onClick={() => { setEditError(''); setEditing({ id: a.id, latitude: a.latitude, longitude: a.longitude, altitude: String(a.altitude), status: a.status }); }}>Edit {a.id}</button>{a.tower_decision?.outcome === 'cleared' && <>{canBegin(a) && !!operationErrors['begin:' + a.tower_decision.request_id] && <button disabled={busy} onClick={() => void execute(() => (begin || api.begin)(a.tower_decision!.request_id))}>Retry start {a.id}</button>}{a.flight?.request_id === a.tower_decision.request_id && a.flight.phase === 'running' && !a.flight.paused_at && <span>In motion / awaiting backend completion</span>}{a.flight?.request_id === a.tower_decision.request_id && (a.flight.phase === 'failed' || a.flight.phase === 'running' && Date.now() >= (a.flight.started_at + a.flight.duration) * 1000 && !a.flight.paused_at) && <button disabled={busy} onClick={() => void execute(() => (retry || api.complete)(a.tower_decision!.request_id))}>Retry completion {a.id}</button>}</>}</div></td></tr>)}</tbody></table></div>
    {!aircraft.length && <p className="empty">No aircraft. Add a Radar record to begin.</p>}
    {editing && <form className="edit-form" onSubmit={e => {
      e.preventDefault();
      let altitude:number;
      try { altitude=altitudeNumber(editing.altitude); } catch(error) { setEditError((error as Error).message); return; }
      setEditError('');
      const { id, ...changes } = editing;
      void execute(() => api.updateAircraft(id, { ...changes, altitude }));
    }}><h3>Update {editing.id}</h3><div className="form-grid">{(['latitude', 'longitude', 'altitude'] as const).map(key => <label key={key}>Update {key}<input type={key === 'altitude' ? 'text' : 'number'} inputMode={key === 'altitude' ? 'decimal' : undefined} required={key !== 'altitude'} step="any" value={editing[key]} onChange={e => setEditing({ ...editing, [key]: key === 'altitude' ? e.target.value : Number(e.target.value) })}/></label>)}<label>Update status<select value={editing.status} onChange={e => setEditing({ ...editing, status: e.target.value as FlightStatus })}><option>Parked</option><option>Landing</option><option>Departing</option></select></label></div>{editError && <p role="alert">{editError}</p>}<button disabled={busy}>Save aircraft update</button><button type="button" className="secondary" onClick={() => setEditing(null)}>Close editor</button></form>}
    <p className="muted">Tower owns permissions; Radar applied means the response consumer persisted clearance state. Motion begins only after Radar applies clearance. Landing retains the occupied runway. Departure releases it after takeoff.</p></section>;
}
