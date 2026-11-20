/** Shared SVG viewport only; never changes flight coordinates or animation clocks. */
import { useEffect, useRef, useState } from 'react';
export function useAirportView() {
  const canvas = useRef<SVGSVGElement>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const [view, setView] = useState({ zoom: 1, x: 0, y: 0 });
  const zoomBy = (factor: number) => setView(v => ({ ...v, zoom: Math.max(.75, Math.min(3, v.zoom * factor)) }));
  const panBy = (dx: number, dy: number) => setView(v => v.zoom > 1 ? ({ ...v, x: Math.max(-700, Math.min(700, v.x + dx / v.zoom)), y: Math.max(-400, Math.min(400, v.y + dy / v.zoom)) }) : v);
  useEffect(() => {
    const svg = canvas.current;
    if (!svg) return;
    const wheel = (event: WheelEvent) => {
      // Ctrl-wheel is Chrome's trackpad pinch. Plain wheel zooms while directly
      // over the map; Shift-wheel pans when zoomed. Outside the SVG scrolls normally.
      event.preventDefault();
      if (event.shiftKey || !event.ctrlKey && event.deltaX !== 0) panBy(event.deltaX || event.deltaY, event.deltaY && event.deltaX ? event.deltaY : 0);
      else zoomBy(Math.exp(-Math.max(-200, Math.min(200, event.deltaY)) * .002));
    };
    let previousScale = 1;
    const gestureStart = (event: Event) => { event.preventDefault(); previousScale = 1; };
    const gestureChange = (event: Event) => {
      event.preventDefault();
      const scale = (event as Event & { scale?: number }).scale;
      if (scale && Number.isFinite(scale)) { zoomBy(scale / previousScale); previousScale = scale; }
    };
    svg.addEventListener('wheel', wheel, { passive: false });
    svg.addEventListener('gesturestart', gestureStart, { passive: false });
    svg.addEventListener('gesturechange', gestureChange, { passive: false });
    return () => { svg.removeEventListener('wheel', wheel); svg.removeEventListener('gesturestart', gestureStart); svg.removeEventListener('gesturechange', gestureChange); };
  }, []);
  return { canvas, zoom: view.zoom,
    viewBox: `${500 - 500 / view.zoom + view.x} ${300 - 300 / view.zoom + view.y} ${1000 / view.zoom} ${600 / view.zoom}`,
    zoomIn: () => zoomBy(1.25), zoomOut: () => zoomBy(.8), reset: () => setView({ zoom: 1, x: 0, y: 0 }),
    pointerDown: (e: React.PointerEvent<SVGSVGElement>) => { if (view.zoom <= 1) return; drag.current = { x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture?.(e.pointerId); },
    pointerMove: (e: React.PointerEvent<SVGSVGElement>) => {
      if (!drag.current) return;
      const rect = e.currentTarget.getBoundingClientRect();
      panBy(-(e.clientX - drag.current.x) * 1000 / (rect.width || 1000), -(e.clientY - drag.current.y) * 600 / (rect.height || 600));
      drag.current = { x: e.clientX, y: e.clientY };
    },
    pointerUp: () => { drag.current = null; },
  };
}
