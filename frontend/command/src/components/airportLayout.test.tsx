import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { AirportMap } from './AirportMap';
import { naturalOrder } from './airportLayout';
import type { Airport, Aircraft } from '../api';
const airport: Airport = { airport_id: 'SKYNET', name: 'Skynet', latitude: 40.6413, longitude: -73.7781, geometry: { south: 40.6313, north: 40.6513, west: -73.7881, east: -73.7681, rule: 'demo', viewport: { south: 40.6233, north: 40.6593, west: -73.7921, east: -73.7641 } }, runways: ['R11','R2','R10','R1'].map(id => ({ id, runway_id: id, available: true, open: true, mode: 'mixed', request_id: null, aircraft_id: null, points: [{ latitude: 40.6483, longitude: -73.7881 + .0005 + (Number(id.slice(1)) - .5) * .019 / 20 }, { latitude: 40.6353, longitude: -73.7881 + .0005 + (Number(id.slice(1)) - .5) * .019 / 20 }] })) };
it('sorts numeric and arbitrary runway identifiers stably', () => {
  expect(['R11','R2','R10','R1'].sort(naturalOrder)).toEqual(['R1','R2','R10','R11']);
  expect(['WEST','A2','A11','A1'].sort(naturalOrder)).toEqual(['A1','A2','A11','WEST']);
});
it('zooms a shared viewBox without moving markers relative to real runway points', async () => {
  const a = { id: 'AIR', status: 'Landing', altitude: 500, classification: 'outside', latitude: 40.6563, longitude: -73.7781 } as Aircraft;
  const { container } = render(<AirportMap airport={airport} aircraft={[a]}/>);
  const svg = screen.getByRole('img'), initial = svg.getAttribute('viewBox');
  const marker = container.querySelector('[data-aircraft="AIR"]')!.getAttribute('transform');
  expect([...container.querySelectorAll('[data-runway]')].map(e => e.getAttribute('data-runway'))).toEqual(['R1','R2','R10','R11']);
  await userEvent.click(screen.getByRole('button', { name: 'Zoom In' }));
  expect(svg.getAttribute('viewBox')).not.toBe(initial);
  expect(container.querySelector('[data-aircraft="AIR"]')!.getAttribute('transform')).toBe(marker);
  await userEvent.click(screen.getByRole('button', { name: 'Zoom Out' }));
  await userEvent.click(screen.getByRole('button', { name: 'Reset View' }));
  expect(svg.getAttribute('viewBox')).toBe(initial);
  expect(screen.getByText(/Airport location: 40.6413° N, 73.7781° W/)).toBeInTheDocument();
  expect(screen.getByRole('img')).not.toHaveTextContent('PARKING / GROUND');
});

it('wheel and trackpad pinch zoom only the map and preserve runway positions', () => {
  const { container } = render(<AirportMap airport={airport} aircraft={[]}/>);
  const svg = screen.getByRole('img');
  const runway = container.querySelector('[data-runway="R1"] rect')!;
  const x = runway.getAttribute('x'), initial = svg.getAttribute('viewBox');
  const event = new WheelEvent('wheel', { deltaY: -100, bubbles: true, cancelable: true });
  act(() => { svg.dispatchEvent(event); });
  expect(event.defaultPrevented).toBe(true);
  expect(svg.getAttribute('viewBox')).not.toBe(initial);
  const wheelView = svg.getAttribute('viewBox');
  fireEvent.wheel(svg, { deltaY: -50, ctrlKey: true });
  expect(svg.getAttribute('viewBox')).not.toBe(wheelView);
  expect(runway.getAttribute('x')).toBe(x);
  const page = new WheelEvent('wheel', { deltaY: 50, bubbles: true, cancelable: true });
  document.body.dispatchEvent(page); expect(page.defaultPrevented).toBe(false);
});
it('supports Safari gesture scaling, drag/trackpad pan, and reset', async () => {
  render(<AirportMap airport={airport} aircraft={[]}/>);
  const svg = screen.getByRole('img'), initial = svg.getAttribute('viewBox');
  const start = new Event('gesturestart', { cancelable: true });
  const change = new Event('gesturechange', { cancelable: true });
  Object.defineProperty(change, 'scale', { value: 1.5 });
  act(() => { svg.dispatchEvent(start); svg.dispatchEvent(change); });
  expect(change.defaultPrevented).toBe(true);
  const zoomed = svg.getAttribute('viewBox');
  act(() => {
    svg.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 100, clientY: 100 }));
    svg.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 150, clientY: 140 }));
    svg.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
  });
  expect(svg.getAttribute('viewBox')).not.toBe(zoomed);
  const panned = svg.getAttribute('viewBox');
  fireEvent.wheel(svg, { deltaX: 40, deltaY: 0 });
  expect(svg.getAttribute('viewBox')).not.toBe(panned);
  await userEvent.click(screen.getByRole('button', { name: 'Reset View' }));
  expect(svg.getAttribute('viewBox')).toBe(initial);
});
it('retains zoom and pan during aircraft movement and keeps labels above the boundary', async () => {
  const a = { id: 'AIR', status: 'Landing', altitude: 500, classification: 'outside', latitude: 40.6563, longitude: -73.7781 } as Aircraft;
  const { container, rerender } = render(<AirportMap airport={airport} aircraft={[a]}/>);
  const svg = screen.getByRole('img');
  await userEvent.click(screen.getByRole('button', { name: 'Zoom In' }));
  fireEvent.wheel(svg, { deltaY: 30, shiftKey: true });
  const view = svg.getAttribute('viewBox');
  const marker = container.querySelector('[data-aircraft="AIR"]')!.getAttribute('transform');
  const point = airport.runways[0].points![1];
  rerender(<AirportMap airport={airport} aircraft={[a]} positions={{ AIR: point }}/>);
  expect(svg.getAttribute('viewBox')).toBe(view);
  expect(container.querySelector('[data-aircraft="AIR"]')!.getAttribute('transform')).not.toBe(marker);
  const labels = [...container.querySelectorAll('[data-runway] text')];
  const ys = labels.map(t => Number(t.getAttribute('y')));
  expect(ys.every(y => y < 460 && y > 400)).toBe(true);
  const xs = labels.map(t => Number(t.getAttribute('x')));
  expect(xs).toEqual([...xs].sort((a,b) => a-b));
  expect(xs.every((x,i) => !i || x-xs[i-1]>25)).toBe(true);
  expect(container.textContent).not.toMatch(/PARKING|Inclusive|glyph|timeline/i);
});

it('spreads 10/11 runways across the airport while aligning authoritative parked coordinates', () => {
  for (const count of [10,11]) {
    const runways=Array.from({length:count},(_,i) => ({...airport.runways[0],id:`R${i+1}`,runway_id:`R${i+1}`,points:[{latitude:40.6483,longitude:-73.7881+.0005+(i+.5)*.019/20},{latitude:40.6353,longitude:-73.7881+.0005+(i+.5)*.019/20}]}));
    const model={...airport,runways};
    const point=runways[0].points[1];
    const plane={id:'PARKED',status:'Parked',altitude:0,classification:'inside',...point} as Aircraft;
    const {container,unmount}=render(<AirportMap airport={model} aircraft={[plane]}/>);
    const xs=[...container.querySelectorAll('[data-runway] rect')].map(e => Number(e.getAttribute('x'))+5);
    expect(xs[xs.length-1]-xs[0]).toBeGreaterThan(550);
    const gaps=xs.slice(1).map((x,i)=>x-xs[i]);
    expect(Math.max(...gaps)-Math.min(...gaps)).toBeLessThan(.001);
    const marker=container.querySelector('[data-aircraft="PARKED"]')!.getAttribute('transform')!;
    expect(Number(marker.match(/translate\(([^,]+)/)![1])).toBeCloseTo(xs[0]);
    expect(plane.longitude).toBe(point.longitude);
    unmount();
  }
});
it('hides departed markers from persisted completion while retaining the input record', () => {
  const plane={id:'LEFT',status:'Departing',altitude:500,classification:'outside',latitude:40.6593,longitude:-73.7781,departure_completed_at:1000} as Aircraft;
  const {container,rerender}=render(<AirportMap airport={airport} aircraft={[plane]} now={1002000}/>);
  expect(container.querySelector('[data-aircraft="LEFT"]')).toBeInTheDocument();
  rerender(<AirportMap airport={airport} aircraft={[plane]} now={1003001}/>);
  expect(container.querySelector('[data-aircraft="LEFT"]')).not.toBeInTheDocument();
  expect(plane.altitude).toBe(500);
});

it('keeps crowded IDs centered on their own icons and only switches above/below', async () => {
  const {aircraftLabels}=await import('./airportLayout');
  const markers=[{id:'A',x:500,y:100},{id:'B',x:510,y:100}];
  const labels=aircraftLabels(markers,[]);
  for (const marker of markers) expect(labels[marker.id].x+labels[marker.id].width/2).toBeCloseTo(marker.x);
  expect(labels.A.y+labels.A.height).toBeLessThan(markers[0].y);
  expect(labels.B.y).toBeGreaterThan(markers[1].y);
  expect(aircraftLabels(markers,[])).toEqual(labels);
});

it.each([.75,1,1.1])('keeps labels centered/readable at %s zoom during motion', async zoom => {
  const plane={id:'ABC456',status:'Landing',altitude:500,classification:'outside',latitude:40.6563,longitude:-73.7781} as Aircraft;
  const {container,rerender}=render(<AirportMap airport={airport} aircraft={[plane]}/>);
  const svg=screen.getByRole('img');
  fireEvent.wheel(svg,{deltaY:-Math.log(zoom)/.002});
  let label=container.querySelector('[data-aircraft="ABC456"] text')!;
  expect(label.getAttribute('text-anchor')).toBe('middle');
  expect(Number(label.getAttribute('x'))).toBe(0);
  expect(parseFloat((label as SVGElement).style.fontSize)*zoom).toBeCloseTo(12);
  expect(Number(label.getAttribute('textLength'))*zoom).toBeCloseTo(45);
  const view=svg.getAttribute('viewBox');
  rerender(<AirportMap airport={airport} aircraft={[plane]} positions={{ABC456:airport.runways[0].points![1]}}/>);
  label=container.querySelector('[data-aircraft="ABC456"] text')!;
  expect(label.getAttribute('x')).toBe('0');
  expect(svg.getAttribute('viewBox')).toBe(view);
});
