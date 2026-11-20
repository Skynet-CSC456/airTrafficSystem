import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, it, expect, vi } from 'vitest';
import { api, type Airport, type Aircraft, type Decision } from '../api';
import { AircraftForm, AircraftTable } from './AircraftPanel';
import { AirportMap } from './AirportMap';
import { RunwayPanel, QueuePanel } from './OperationsPanels';
import { EventLog } from './EventLog';
const decision: Decision = { schema_version: 1, request_id: 'req-A', aircraft_id: 'A', airport_id: 'SKYNET', operation: 'landing', outcome: 'cleared', runway_id: 'R1', queue_position: null, reason: null, timestamp: '2026-11-05T15:00:00+00:00' };
const aircraft: Aircraft = { id: 'A', latitude: 40.6413, longitude: -73.7781, altitude: 500, status: 'Landing', timestamp: '2026-11-05T15:00:00', classification: 'outside', tower_decision: decision, radar_clearance: null };
const airport: Airport = { airport_id: 'SKYNET', name: 'Skynet International Airport', latitude: 40.6413, longitude: -73.7781, runways: Array.from({ length: 11 }, (_, i) => ({ id: `R${i + 1}`, runway_id: `R${i + 1}`, available: i > 0, open: true, mode: 'mixed', request_id: i === 0 ? 'req-A' : null, aircraft_id: i === 0 ? 'A' : null })), geometry: { south: 40.6313, north: 40.6513, west: -73.7881, east: -73.7681, rule: 'Demo rectangle', viewport: { south: 40.6213, north: 40.6613, west: -73.8081, east: -73.7481 } } };
const execute = async (action: () => Promise<unknown>) => { await action(); };
describe('Command components', () => {
  it('renders dynamic R11 and aircraft labels from backend data', () => {
    render(<AirportMap airport={airport} aircraft={[aircraft]}/>);
    expect(screen.getByRole('img')).toHaveTextContent('R11');
    expect(screen.getByRole('img')).toHaveTextContent('A');
    expect(screen.getByText(/Airport location: 40.6413° N, 73.7781° W/)).toBeInTheDocument();
  });
  it('creates an aircraft through the API', async () => {
    const create = vi.spyOn(api, 'createAircraft').mockResolvedValue(aircraft);
    render(<AircraftForm airport={airport} busy={false} execute={execute}/>);
    await userEvent.type(screen.getByLabelText('Aircraft ID'), 'NEW');
    await userEvent.click(screen.getByRole('button', { name: 'Add aircraft' }));
    expect(create).toHaveBeenCalledWith({ id: 'NEW', latitude: airport.latitude, longitude: airport.longitude, altitude: 0, status: 'Parked' });
  });
  it('requests landing and departure through the API', async () => {
    const request = vi.spyOn(api, 'request').mockResolvedValue({});
    render(<AircraftTable aircraft={[{ ...aircraft, tower_decision: null }, { ...aircraft, id: 'P', status: 'Parked', altitude: 0, tower_decision: null }]} busy={false} execute={execute}/>);
    await userEvent.click(screen.getByRole('button', { name: 'Land A' }));
    await userEvent.click(screen.getByRole('button', { name: 'Depart P' }));
    expect(request).toHaveBeenCalledWith('A', 'landing');
    expect(request).toHaveBeenCalledWith('P', 'departure');
    expect(screen.getAllByText('No applied response')[0]).toBeInTheDocument();
  });
  it('updates altitude through Radar’s API route', async () => {
    const update = vi.spyOn(api, 'updateAircraft').mockResolvedValue(aircraft);
    render(<AircraftTable aircraft={[aircraft]} busy={false} execute={execute}/>);
    await userEvent.click(screen.getByRole('button', { name: 'Edit A' }));
    await userEvent.clear(screen.getByLabelText('Update altitude'));
    await userEvent.type(screen.getByLabelText('Update altitude'), '750');
    await userEvent.click(screen.getByRole('button', { name: 'Save aircraft update' }));
    expect(update).toHaveBeenCalledWith('A', { latitude: aircraft.latitude, longitude: aircraft.longitude, altitude: 750, status: 'Landing' });
  });
  it('retries a failed automatic start through the backend', async () => {
    const begin = vi.spyOn(api, 'begin').mockResolvedValue({} as never);
    render(<AircraftTable aircraft={[{ ...aircraft, radar_clearance: { ...decision, applied_at: decision.timestamp } }]} busy={false} execute={execute} operationErrors={{ 'begin:req-A': 'network failure' }}/>);
    await userEvent.click(screen.getByRole('button', { name: 'Retry start A' }));
    expect(begin).toHaveBeenCalledWith('req-A');
  });
  it('adds R11 through the API and shows occupied backend state', async () => {
    const add = vi.spyOn(api, 'addRunway').mockResolvedValue({});
    render(<RunwayPanel runways={airport.runways} busy={false} execute={execute}/>);
    await userEvent.click(screen.getByRole('button', { name: 'Add runway' }));
    expect(add).toHaveBeenCalledWith('R11');
    expect(screen.getByText('Occupied')).toBeInTheDocument();
  });
  it('renders FIFO position and applied message status', () => {
    render(<><QueuePanel queue={[{ ...decision, outcome: 'queued', runway_id: null, queue_position: 1 }]}/><EventLog events={[{ id: 7, sender: 'tower', recipient: 'radar', message_type: 'runway_response', content: '', metadata: decision, sent_at: decision.timestamp, status: 'applied', receipt: { received_at: decision.timestamp, applied_at: decision.timestamp, error: null } }]}/></>);
    expect(screen.getByText('1. A')).toBeInTheDocument();
    expect(screen.getByText('Applied', { selector: '.event-status' })).toBeInTheDocument();
    expect(screen.getByText(/Tower → Radar/)).toBeInTheDocument();
  });
});

it('creates Landing with outside coordinates and no separate Land click', async () => {
  const create = vi.spyOn(api, 'createAircraft').mockResolvedValue(aircraft);
  render(<AircraftForm airport={airport} busy={false} execute={execute}/>);
  await userEvent.type(screen.getByLabelText('Aircraft ID'), 'AUTO');
  await userEvent.selectOptions(screen.getByLabelText('Flight status'), 'Landing');
  expect(screen.getByRole('option', { name: 'Departing' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: 'Add aircraft' }));
  expect(create).toHaveBeenCalledExactlyOnceWith({ id: 'AUTO', latitude: airport.latitude + .015, longitude: airport.longitude, altitude: 500, status: 'Landing' });
});
it('disables duplicate active requests and waits for Radar application before Begin', () => {
  render(<AircraftTable aircraft={[aircraft]} busy={false} execute={execute}/>);
  expect(screen.getByRole('button', { name: 'Land A' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Depart A' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Retry start A' })).not.toBeInTheDocument();
});
it('orders the runway inventory naturally including R10 and R11', () => {
  const { container } = render(<RunwayPanel runways={[...airport.runways].reverse()} busy={false} execute={execute}/>);
  expect([...container.querySelectorAll('.runway strong')].map(e => e.textContent)).toEqual(Array.from({ length: 11 }, (_, i) => `R${i + 1}`));
});

it('keeps the altitude draft empty while clearing and saves 850 as a number', async () => {
  const update=vi.spyOn(api,'updateAircraft').mockResolvedValue(aircraft);
  const user=userEvent.setup();
  render(<AircraftTable aircraft={[{...aircraft,altitude:0}]} busy={false} execute={execute}/>);
  await user.click(screen.getByRole('button',{name:'Edit A'}));
  const input=screen.getByLabelText('Update altitude');
  await user.clear(input);expect(input).toHaveValue('');
  await user.type(input,'850');expect(input).toHaveValue('850');
  await user.click(screen.getByRole('button',{name:'Save aircraft update'}));
  expect(update).toHaveBeenCalledExactlyOnceWith('A',{latitude:aircraft.latitude,longitude:aircraft.longitude,altitude:850,status:'Landing'});
});
it('supports select-all replacement and deletion without resetting the altitude draft', async () => {
  const user=userEvent.setup();
  render(<AircraftTable aircraft={[aircraft]} busy={false} execute={execute}/>);
  await user.click(screen.getByRole('button',{name:'Edit A'}));
  const input=screen.getByLabelText('Update altitude');
  await user.click(input);await user.keyboard('{Control>}a{/Control}');
  await user.keyboard('850');expect(input).toHaveValue('850');
  await user.keyboard('{Backspace}{Backspace}{Backspace}');expect(input).toHaveValue('');
});
it.each(['','-1','1e999','abc','0x10'])('rejects invalid final altitude %s without invoking Radar updates', async draft => {
  const update=vi.spyOn(api,'updateAircraft').mockResolvedValue(aircraft);
  const user=userEvent.setup();
  render(<AircraftTable aircraft={[aircraft]} busy={false} execute={execute}/>);
  await user.click(screen.getByRole('button',{name:'Edit A'}));
  const input=screen.getByLabelText('Update altitude');
  await user.clear(input);if(draft) await user.type(input,draft);
  await user.click(screen.getByRole('button',{name:'Save aircraft update'}));
  expect(screen.getByRole('alert')).toHaveTextContent(/altitude/i);
  expect(update).not.toHaveBeenCalled();
});

afterEach(() => vi.restoreAllMocks());

it('allows Add Aircraft altitude to clear, replace zero and submit a numeric value', async () => {
  const create=vi.spyOn(api,'createAircraft').mockResolvedValue(aircraft);
  const user=userEvent.setup();render(<AircraftForm airport={airport} busy={false} execute={execute}/>);
  await user.type(screen.getByLabelText('Aircraft ID'),'ADD');
  const input=screen.getByLabelText('Altitude (m)');expect(input).toHaveValue('0');
  await user.clear(input);expect(input).toHaveValue('');
  await user.selectOptions(screen.getByLabelText('Flight status'),'Landing');
  await user.click(input);await user.keyboard('{Control>}a{/Control}500');
  expect(input).toHaveValue('500');
  await user.keyboard('{Backspace}{Backspace}{Backspace}');expect(input).toHaveValue('');
  await user.type(input,'850');await user.click(screen.getByRole('button',{name:'Add aircraft'}));
  expect(create).toHaveBeenCalledExactlyOnceWith({id:'ADD',latitude:airport.latitude+.015,longitude:airport.longitude,altitude:850,status:'Landing'});
});
it.each(['','-1','abc','Infinity','1e999'])('rejects invalid Add Aircraft altitude %s without creating an aircraft', async altitude => {
  const create=vi.spyOn(api,'createAircraft').mockResolvedValue(aircraft);
  const user=userEvent.setup();render(<AircraftForm airport={airport} busy={false} execute={execute}/>);
  await user.type(screen.getByLabelText('Aircraft ID'),'INVALID');
  await user.clear(screen.getByLabelText('Altitude (m)'));
  if(altitude) await user.type(screen.getByLabelText('Altitude (m)'),altitude);
  await user.click(screen.getByRole('button',{name:'Add aircraft'}));
  expect(screen.getByRole('alert')).toHaveTextContent(/altitude/i);
  expect(create).not.toHaveBeenCalled();
});
