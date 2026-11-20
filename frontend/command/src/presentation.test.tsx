import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { eventPresentation, showOnMap } from './presentation';
import { EventLog } from './components/EventLog';
import type { Aircraft, Decision, Event } from './api';
const decision: Decision = { schema_version:1,request_id:'long-internal-uuid',aircraft_id:'ABC456',airport_id:'SKYNET',operation:'landing',outcome:'completed',runway_id:'R1',queue_position:null,reason:null,timestamp:'2026-11-05T19:40:02Z' };
const event: Event = { id:4,sender:'tower',recipient:'radar',message_type:'runway_completed',content:'raw',metadata:decision,sent_at:'2026-11-05T19:40:02',status:'applied',receipt:null };
describe('recorded event formatting', () => {
  it('uses human descriptions and browser-local time without exposing internal IDs', () => {
    const text=eventPresentation(event);
    expect(text.description).toBe('ABC456 completed landing on R1');
    expect(text.label).toBe('Landing completed');
    expect(text.time).toBe(new Date('2026-11-05T19:40:02Z').toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit',second:'2-digit'}));
    render(<EventLog events={[event]}/>);
    expect(screen.getByText('Tower → Radar')).toBeInTheDocument();
    expect(screen.getByText('Applied')).toBeInTheDocument();
    expect(screen.queryByText(/long-internal-uuid|runway_completed/)).not.toBeInTheDocument();
    expect(event.metadata?.request_id).toBe('long-internal-uuid');
  });
  it.each(['queued','rejected','cleared'] as const)('formats only the recorded %s outcome', outcome => {
    const text=eventPresentation({...event,message_type:'runway_response',metadata:{...decision,outcome,queue_position:2}});
    expect(text.description).not.toContain('completed');
    expect(text.label).toContain(outcome);
  });
  it('preserves failed/received labels and plain text message content', () => {
    expect(eventPresentation({...event,metadata:null,content:'Hello',status:'received'})).toMatchObject({description:'Hello',status:'Received'});
    expect(eventPresentation({...event,status:'failed'}).status).toBe('Failed');
  });
});
it('hides a completed departure at three persisted seconds without deleting or hiding other aircraft', () => {
  const plane={id:'DEPARTED',status:'Departing',departure_completed_at:1000} as Aircraft;
  expect(showOnMap(plane,1002999)).toBe(true);
  expect(showOnMap(plane,1003000)).toBe(false);
  expect(showOnMap({...plane},1009000)).toBe(false); // reopening uses persisted completion
  expect(showOnMap({...plane,status:'Landing'},1009000)).toBe(true);
  expect(showOnMap({...plane,departure_completed_at:null},1009000)).toBe(true);
  expect(plane.status).toBe('Departing');
});
