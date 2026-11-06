import type { Event } from '../api';
import { eventPresentation } from '../presentation';
export function EventLog({ events }: { events: Event[] }) {
  return <section className="panel"><div className="panel-heading"><div><span className="eyebrow">LIVE SUBSYSTEM TRAFFIC</span><h2>Live event log</h2></div><span>{events.length} EVENTS</span></div><div className="event-scroll">{[...events].reverse().map(e => {
    const text=eventPresentation(e);
    return <article className="event" key={e.id}><div><strong>{text.direction}</strong><span className={'tag event-status ' + e.status}>{text.status}</span></div><p>{text.description}</p><small>{text.time} · {text.label}</small>{e.receipt?.error && <p role="alert">{e.receipt.error}</p>}</article>;
  })}{!events.length && <p className="empty">No traffic yet. Send a landing or departure request.</p>}</div></section>;
}
