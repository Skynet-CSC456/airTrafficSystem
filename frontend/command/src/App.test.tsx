import { render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { api, type State } from './api';
import App from './App';
it('offers automatic operations without a Hold or normal Begin control', async () => {
  vi.spyOn(api,'state').mockResolvedValue({ airport: { name: 'Skynet',latitude:40.6413,longitude:-73.7781,runways:[],geometry: { north:40.65,south:40.63,east:-73.76,west:-73.79,viewport: { north:40.67,south:40.61,east:-73.74,west:-73.81 } } },aircraft:[],queue:[],events:[],workers:{} } as unknown as State);
  render(<App/>);
  await screen.findByRole('heading',{ name:'Skynet' });
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.queryByText(/Hold Operations/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{ name:/^Begin/ })).not.toBeInTheDocument();
});

it('requires confirmation before calling the cohesive reset API', async () => {
  const user=(await import('@testing-library/user-event')).default;
  const state={generation:'old',airport:{name:'Skynet',latitude:40.6413,longitude:-73.7781,runways:[],geometry:{north:40.65,south:40.63,east:-73.76,west:-73.79,viewport:{north:40.67,south:40.61,east:-73.74,west:-73.81}}},aircraft:[],queue:[],events:[],workers:{}} as unknown as State;
  vi.spyOn(api,'state').mockResolvedValue(state);
  const reset=vi.spyOn(api,'reset').mockResolvedValue({...state,generation:'new'});
  const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
  render(<App/>);await screen.findByRole('heading',{name:'Skynet'});
  await user.click(screen.getByRole('button',{name:'Reset Demo'}));expect(reset).not.toHaveBeenCalled();
  confirm.mockReturnValue(true);
  await user.click(screen.getByRole('button',{name:'Reset Demo'}));expect(reset).toHaveBeenCalledOnce();
});

afterEach(() => vi.restoreAllMocks());

it('resets one map/form subtree completely without duplicate keys after repeated interactions', async () => {
  const {waitFor}=await import('@testing-library/react');
  const user=(await import('@testing-library/user-event')).default.setup();
  const errors=vi.spyOn(console,'error').mockImplementation(()=>{});
  const airport={airport_id:'SKYNET',name:'Skynet',latitude:40.6413,longitude:-73.7781,
    geometry:{north:40.6513,south:40.6313,east:-73.7681,west:-73.7881,rule:'demo',viewport:{north:40.6593,south:40.6233,east:-73.7641,west:-73.7921}},
    runways:Array.from({length:10},(_,i)=>({id:`R${i+1}`,runway_id:`R${i+1}`,available:true,open:true,mode:'mixed',request_id:null,aircraft_id:null}))};
  let state:State={generation:'one',airport,aircraft:[{id:'P',latitude:40.6353,longitude:-73.7871,altitude:0,status:'Parked',timestamp:'2026-11-05T19:00:00',classification:'inside',tower_decision:null,radar_clearance:null}],requests:[],queue:[],events:[],system_status:'normal',workers:{}};
  const fetch=vi.spyOn(api,'state').mockImplementation(async()=>state);
  let generation=1;
  vi.spyOn(api,'reset').mockImplementation(async()=>{state={...state,generation:String(++generation),aircraft:[],queue:[],requests:[],events:[]};return state;});
  vi.spyOn(window,'confirm').mockReturnValue(true);
  const {container}=render(<App/>);await screen.findByRole('heading',{name:'Skynet'});
  const initialView=screen.getByRole('img').getAttribute('viewBox');
  await user.click(screen.getByRole('button',{name:'Zoom In'}));
  await user.type(screen.getByLabelText('Aircraft ID'),'UNSAVED');
  await user.selectOptions(screen.getByLabelText('Flight status'),'Landing');
  await user.click(screen.getByRole('button',{name:'Edit P'}));
  expect(screen.getByRole('heading',{name:'Update P'})).toBeInTheDocument();
  await user.clear(screen.getByLabelText('Update altitude'));
  await user.click(screen.getByRole('button',{name:'Reset Demo'}));
  await waitFor(()=>expect(screen.queryByRole('heading',{name:'Update P'})).not.toBeInTheDocument());
  for(let reset=0;reset<2;reset++) {
    expect(screen.getAllByRole('img')).toHaveLength(1);
    expect(screen.getAllByRole('heading',{name:'Add aircraft'})).toHaveLength(1);
    expect(screen.getByRole('img').getAttribute('viewBox')).toBe(initialView);
    expect(screen.getByLabelText('Aircraft ID')).toHaveValue('');
    expect(screen.getByLabelText('Latitude')).toHaveValue(airport.latitude);
    expect(screen.getByLabelText('Longitude')).toHaveValue(airport.longitude);
    expect(screen.getByLabelText('Altitude (m)')).toHaveValue('0');
    expect(screen.getByLabelText('Flight status')).toHaveValue('Parked');
    const grid=container.querySelector('.top-grid')!;
    expect(grid.children).toHaveLength(2);
    expect(grid.children[0]).toHaveClass('map-panel');
    expect(grid.children[1]).toHaveClass('aircraft-form');
    expect(container.querySelectorAll('[data-aircraft]')).toHaveLength(0);
    expect(screen.getByText('No aircraft waiting.')).toBeInTheDocument();
    expect(screen.getByText(/No traffic yet/)).toBeInTheDocument();
    expect(screen.getAllByText('Available',{selector:'.runway span'})).toHaveLength(10);
    if(reset===0) {
      await user.click(screen.getByRole('button',{name:'Zoom Out'}));
      await user.type(screen.getByLabelText('Aircraft ID'),'AGAIN');
      await user.click(screen.getByRole('button',{name:'Reset Demo'}));
    }
  }
  await waitFor(()=>expect(fetch.mock.calls.length).toBeGreaterThanOrEqual(2),{timeout:2000});
  expect(screen.getAllByRole('img')).toHaveLength(1);
  expect(container.querySelector('.top-grid')!.children[1]).toHaveClass('aircraft-form');
  expect(errors.mock.calls.some(args=>args.some(value=>String(value).includes('same key')))).toBe(false);
});
