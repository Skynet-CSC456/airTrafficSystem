import { useOperations } from './useOperations';
import { useDashboard } from './useDashboard';
import { AirportMap } from './components/AirportMap';
import { AircraftForm, AircraftTable } from './components/AircraftPanel';
import { RunwayPanel, QueuePanel } from './components/OperationsPanels';
import { EventLog } from './components/EventLog';
export default function App() {
  const { state, error, busy, execute, refresh, reset } = useDashboard();
  const operations = useOperations(state, refresh);
  return <><header><a className="brand" href="/">SKY<span>NET</span><small>COMMAND / CSC 456</small></a><div className="workers">{state && Object.entries(state.workers).map(([name, worker]) => <span key={name} title={worker.error || 'Worker running'} className={worker.healthy ? 'online' : 'offline'}>{name.toUpperCase()} {worker.healthy ? 'ONLINE' : 'ERROR'}</span>)}</div></header>
    <main><div className="intro"><div><span className="eyebrow">INTEGRATED AIR TRAFFIC CONTROL</span><h1>Command overview<span className="red-dot">.</span></h1><p>Real aircraft records. Persistent Tower clearances. One shared database.</p></div><div className="actions"><span className="badge">LOCAL DEMONSTRATION · AUTO REFRESH 1s</span><button className="secondary" disabled={!state || busy} onClick={() => void reset()}>Reset Demo</button></div></div>
      {Object.entries(operations.errors).map(([id, message]) => <div role="alert" className="error" key={id}>{id}: {message}. Check backend state and use the aircraft operation control to retry.</div>)}
      {error && <div role="alert" className="error">{error}</div>}
      {!state ? <p role="status">Connecting to the Python backend…</p> : <div className="dashboard-session" key={state.generation}><div className="metrics"><div><span>AIRCRAFT</span><strong>{state.aircraft.length}</strong></div><div><span>AVAILABLE RUNWAYS</span><strong>{state.airport.runways.filter(r => r.available).length}<small> / {state.airport.runways.length}</small></strong></div><div><span>WAITING</span><strong>{state.queue.length}</strong></div><div><span>RADAR APPLIED</span><strong>{state.aircraft.filter(a => a.radar_clearance).length}</strong></div></div>
      <div className="top-grid"><AirportMap airport={state.airport} aircraft={state.aircraft} positions={operations.positions} now={operations.now}/><AircraftForm airport={state.airport} busy={busy} execute={execute}/></div>
      <AircraftTable aircraft={state.aircraft} busy={busy} execute={execute} begin={operations.begin} retry={operations.retry} operationErrors={operations.errors}/>
      <div className="operations-grid"><RunwayPanel runways={state.airport.runways} busy={busy} execute={execute}/><QueuePanel queue={state.queue}/></div>
      <EventLog events={state.events}/></div>}
      <footer>SKYNET INTERNATIONAL AIRPORT · Local integration prototype · Illustrative geometry and flight animation · Real Radar updates and Tower reservations</footer></main></>;
}
