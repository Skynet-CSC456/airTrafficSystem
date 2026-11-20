import { useAirportView } from '../useAirportView';
import { naturalOrder, airportProjection, aircraftLabels } from './airportLayout';
import { showOnMap } from '../presentation';
import type { Airport, Aircraft, Point } from '../api';
export function AirportMap({ airport, aircraft, positions = {}, now = Date.now() }: { airport: Airport; aircraft: Aircraft[]; positions?: Record<string, Point>; now?: number }) {
  const navigation = useAirportView();
  const { zoom } = navigation;
  const { x, y } = airportProjection(airport);
  const left = x(airport.geometry.west), top = y(airport.geometry.north);
  const width = x(airport.geometry.east) - left, height = y(airport.geometry.south) - top;
  const ordered = [...airport.runways].sort((a, b) => naturalOrder(a.id, b.id));
  const visible = aircraft.filter(a => showOnMap(a,now)).sort((a,b)=>a.id.localeCompare(b.id));
  const markers = visible.map(a => {
    const position=positions[a.id] || a;
    const peers=visible.filter(other=>other.status!=='Parked' && other.latitude===a.latitude && other.longitude===a.longitude);
    const rank=Math.max(0,peers.findIndex(other=>other.id===a.id));
    return {a,id:a.id,x:Math.max(18,Math.min(982,x(position.longitude)+(positions[a.id]?0:(rank%4)*14))),
      y:Math.max(28,Math.min(572,y(position.latitude)+(positions[a.id]?0:Math.floor(rank/4)*22)))};
  });
  const labels=aircraftLabels(markers,ordered.flatMap(r=>r.points ? [{x:x(r.points[0].longitude)-9,y:y(r.points[1].latitude)+22,width:Math.max(26,r.id.length*8),height:18}] : []),zoom);
  return <section className="panel map-panel"><div className="panel-heading"><div><span className="eyebrow">SECTOR 01 / LIVE AIRSPACE</span><h2>{airport.name}</h2></div><span className="badge">{airport.runways.length} RUNWAYS</span></div>
    <div className="actions zoom"><button onClick={navigation.zoomIn}>Zoom In</button><button onClick={navigation.zoomOut}>Zoom Out</button><button onClick={navigation.reset}>Reset View</button><span>{Math.round(zoom * 100)}%</span></div>
    <svg ref={navigation.canvas} viewBox={navigation.viewBox} onPointerDown={navigation.pointerDown} onPointerMove={navigation.pointerMove} onPointerUp={navigation.pointerUp} onPointerCancel={navigation.pointerUp} style={{ touchAction: zoom > 1 ? 'none' : 'pan-y', cursor: zoom > 1 ? 'grab' : 'default' }} role="img" aria-label="Airport boundary, runways and aircraft positions">
      <defs><pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M 40 0 L 0 0 0 40" fill="none" stroke="#232329" strokeWidth="1"/></pattern><path id="plane" d="M0 -13 L3 -4 L13 3 L13 6 L3 3 L3 10 L7 13 L7 15 L0 12 L-7 15 L-7 13 L-3 10 L-3 3 L-13 6 L-13 3 L-3 -4Z"/></defs>
      <rect x="-300" y="-200" width="1600" height="1000" fill="url(#grid)"/>
      <rect x={left} y={top} width={width} height={height} fill="#1c1c21" stroke="#dd343b" strokeWidth="2" strokeDasharray="7 5"/>
      <text x={left + 10} y={top + 22} className="map-caption">SKYNET / DEMO BOUNDARY</text>
      {ordered.map((r, i) => {
        const points = r.points;
        if (!points) return <g key={r.id}><text x={left + 12} y={top + 50 + i * 16} className="map-caption">{r.id} · awaiting backend layout</text></g>;
        const rx = x(points[0].longitude), ry = y(points[0].latitude), end = y(points[1].latitude);
        return <g key={r.id} data-runway={r.id}><title>{r.id}: {r.available ? 'available' : 'occupied'} — {r.aircraft_id || 'no aircraft'}</title><rect x={rx - 5} y={ry} width="10" height={end - ry} fill={r.available ? '#303039' : '#751f29'} stroke={r.available ? '#72727e' : '#ff545e'}/><path d={`M${rx} ${ry + 4} V${end - 4}`} stroke="#ccc" strokeDasharray="5 7"/><text x={rx - 6} y={end + 36} className="map-caption">{r.id}</text></g>;
      })}
      {markers.map(({a,x:px,y:py}) => {
        const label=labels[a.id];
        return <g key={a.id} data-aircraft={a.id} transform={`translate(${px},${py})`}><title>{a.id}: {a.classification}; {a.altitude} m; authoritative {a.latitude}, {a.longitude}</title><circle r="13" fill="none" stroke={a.classification === 'outside' ? '#ef4853' : 'transparent'} strokeWidth="1.5"/><use href="#plane" fill="white" stroke="#111" strokeWidth="1" transform={a.status === 'Landing' && positions[a.id] ? 'scale(.65) rotate(180)' : 'scale(.65)'}/><text x="0" textAnchor="middle" y={label.y+13/zoom-py} style={{fontSize:12/zoom}} textLength={label.width-6/zoom} lengthAdjust="spacingAndGlyphs" className="aircraft-label">{a.id}</text></g>;
      })}
    </svg><p className="muted">Airport location: {Math.abs(airport.latitude).toFixed(4)}° {airport.latitude >= 0 ? 'N' : 'S'}, {Math.abs(airport.longitude).toFixed(4)}° {airport.longitude >= 0 ? 'E' : 'W'}</p>
  </section>;
}
