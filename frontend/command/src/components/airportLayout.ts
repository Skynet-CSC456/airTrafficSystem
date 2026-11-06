/** Pure display geometry and ordering; never changes aircraft or Tower state. */
import type { Bounds } from '../api';
export function naturalOrder(a: string, b: string) {
  return a.localeCompare(b, 'en', { numeric: true, sensitivity: 'base' }) || a.localeCompare(b, 'en');
}
export function projection(view: Bounds) {
  return {
    x: (longitude: number) => (longitude - view.west) / (view.east - view.west) * 1000,
    y: (latitude: number) => (view.north - latitude) / (view.north - view.south) * 600,
  };
}

/** Spread the diagram without relocating authoritative Radar/runway coordinates.
 * Every marker and path uses the same piecewise longitude projection. */
export function airportProjection(airport: import('../api').Airport) {
  const base = projection(airport.geometry.viewport);
  const ordered = [...airport.runways].sort((a,b) => naturalOrder(a.id,b.id));
  const left = base.x(airport.geometry.west), right = base.x(airport.geometry.east);
  const anchors = [{ longitude: airport.geometry.west, x: left },
    ...ordered.flatMap((r,i) => r.points ? [{ longitude: r.points[0].longitude, x: left + 32 + (i + .5) * (right - left - 64) / ordered.length }] : []),
    { longitude: airport.geometry.east, x: right }].sort((a,b) => a.longitude-b.longitude);
  return { y: base.y, x: (longitude: number) => {
    for (let i=1;i<anchors.length;i++) {
      const a=anchors[i-1], b=anchors[i];
      if (longitude>=a.longitude && longitude<=b.longitude && b.longitude>a.longitude)
        return a.x+(b.x-a.x)*(longitude-a.longitude)/(b.longitude-a.longitude);
    }
    return base.x(longitude);
  } };
}

export interface LabelBox { x: number; y: number; width: number; height: number; }
/** Center IDs over their own icons. Try the same small gap above/below only. */
export function aircraftLabels(markers: { id: string; x: number; y: number }[], runwayLabels: LabelBox[], zoom = 1) {
  const placed = [...runwayLabels];
  const result: Record<string, LabelBox> = {};
  const overlaps = (a:LabelBox,b:LabelBox) => a.x < b.x+b.width+3 && a.x+a.width+3 > b.x && a.y < b.y+b.height+3 && a.y+a.height+3 > b.y;
  for (const marker of [...markers].sort((a,b)=>a.id.localeCompare(b.id))) {
    const width=(Math.min(110,Math.max(8,marker.id.length*7.5))+6)/zoom;
    const above={x:marker.x-width/2,y:marker.y-13-23/zoom,width,height:18/zoom};
    const below={x:marker.x-width/2,y:marker.y+13+5/zoom,width,height:18/zoom};
    const box=above.y>=0 && !placed.some(other=>overlaps(above,other)) ? above : below;
    result[marker.id]=box;placed.push(box);
  }
  return result;
}
