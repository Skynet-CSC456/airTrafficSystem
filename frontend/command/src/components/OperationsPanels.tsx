import { useState } from 'react';
import { naturalOrder } from './airportLayout';
import { api, type Decision, type Runway } from '../api';
export function RunwayPanel({ runways, busy, execute }: { runways: Runway[]; busy: boolean; execute: (action: () => Promise<unknown>) => Promise<void> }) {
  const [runwayId, setRunwayId] = useState('R11');
  return <section className="panel"><span className="eyebrow">TOWER / INVENTORY</span><h2>Runway control</h2><div className="runway-grid">{[...runways].sort((a,b) => naturalOrder(a.id,b.id)).map(r => <div key={r.id} className={'runway ' + (r.available ? 'available' : 'occupied')}><strong>{r.id}</strong><span>{r.available ? 'Available' : 'Occupied'}</span>{!r.available && r.aircraft_id && <small>{r.aircraft_id}</small>}</div>)}</div><form className="inline" onSubmit={e => { e.preventDefault(); void execute(() => api.addRunway(runwayId)); }}><label>New runway ID<input required value={runwayId} onChange={e => setRunwayId(e.target.value)}/></label><button disabled={busy}>Add runway</button></form><p className="muted">Adds a mixed-use runway to this airport and asks Tower to reconsider its FIFO queue.</p></section>;
}
export function QueuePanel({ queue }: { queue: Decision[] }) {
  return <section className="panel"><span className="eyebrow">TOWER / PERSISTENT FIFO</span><h2>Waiting queue <span className="count">{queue.length}</span></h2>{queue.length ? <ol className="queue">{queue.map(d => <li key={d.request_id}><strong>{d.queue_position}. {d.aircraft_id}</strong><span>{d.operation} · waiting for runway</span><small>{d.request_id}</small></li>)}</ol> : <p className="empty">No aircraft waiting.</p>}</section>;
}
