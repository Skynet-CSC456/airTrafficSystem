/** Read-only human formatting of recorded events and persisted departure facts. */
import type { Aircraft, Event } from './api';
export function showOnMap(aircraft: Aircraft, now: number) {
  return !(aircraft.status === 'Departing' && aircraft.departure_completed_at != null && now >= aircraft.departure_completed_at * 1000 + 3000);
}
export function eventPresentation(event: Event) {
  const metadata = event.metadata;
  const operation = metadata?.operation;
  const title = operation ? operation[0].toUpperCase() + operation.slice(1) : 'Message';
  const outcome = metadata && 'outcome' in metadata ? metadata.outcome : 'requested';
  const runway = metadata && 'runway_id' in metadata && metadata.runway_id ? ` on ${metadata.runway_id}` : '';
  let description = event.content, label = 'Message';
  if (metadata && operation) {
    const id = metadata.aircraft_id;
    label = `${title} ${outcome}`;
    if (outcome === 'completed') description = `${id} completed ${operation}${runway}`;
    else if (outcome === 'cleared') description = `${id} cleared for ${operation}${runway}`;
    else if (outcome === 'queued') description = `${id} waiting for ${operation}${'queue_position' in metadata && metadata.queue_position ? ` · Queue ${metadata.queue_position}` : ''}`;
    else if (outcome === 'rejected') description = `${id} ${operation} request rejected${'reason' in metadata && metadata.reason ? `: ${metadata.reason}` : ''}`;
    else description = `${id} requested ${operation}`;
  }
  // Existing SQLite created_at values without an offset are UTC, not browser local.
  const timestamp = /(?:Z|[+-]\d{2}:\d{2})$/.test(event.sent_at) ? event.sent_at : event.sent_at + 'Z';
  const date = new Date(timestamp);
  const time = Number.isNaN(date.getTime()) ? 'Time unavailable' : date.toLocaleTimeString(undefined, { hour:'numeric', minute:'2-digit', second:'2-digit' });
  const team = (value: string) => value[0].toUpperCase()+value.slice(1);
  return { description, label, time, direction: `${team(event.sender)} → ${team(event.recipient)}`, status: event.status[0].toUpperCase()+event.status.slice(1) };
}
