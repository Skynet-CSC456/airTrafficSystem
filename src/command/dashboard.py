"""Command dashboard and shared-state queries.

REQ-CMD-001: command receives tower messages and system state.
REQ-CMD-004: unified dashboard shows aircraft and decisions with normal/degraded state.
"""

from __future__ import annotations

from typing import Any

from src.db import MessageDatabase
from src.radar.aircraft import AircraftStore
from src.radar.runway_operations import PARKING_STANDS
from src.tower.clearance import update_runway_availability

AIRPORT_CENTER = (40.6413, -73.7781)  # latitude, longitude
# At zoom 1.0 the map shows this far from the airport center in each direction (about 11 x 8.5 km).
MAP_HALF_SPAN_DEGREES = 0.05
# The airport perimeter drawn on the map; radar_create_plane.py uses the same +/-0.01 degrees.
AIRPORT_HALF_SPAN_DEGREES = 0.01
MAP_MARGIN = 45
DEFAULT_ZOOM = 2.0
MIN_ZOOM, MAX_ZOOM, ZOOM_STEP = 1.0, 5.0, 0.5
DEFAULT_RUNWAYS = [{"id": "RWY 1", "available": True, "mode": "landing"}, {"id": "RWY 2", "available": True, "mode": "landing"}]


def aircraft_map_position(aircraft: dict[str, Any], width: float, height: float, margin: float = MAP_MARGIN, zoom: float = 1.0) -> tuple[float, float, bool]:
    """Convert an aircraft's latitude/longitude to a pixel position on the map.

    Zooming in shows a smaller area around the airport. Returns (x, y, inside).
    Aircraft outside the shown area are clamped to its edge with inside=False,
    so they stay visible.
    """
    lat0, lon0 = AIRPORT_CENTER
    span = MAP_HALF_SPAN_DEGREES / zoom
    fx = (aircraft["longitude"] - (lon0 - span)) / (2 * span)  # 0 = west edge, 1 = east edge
    fy = ((lat0 + span) - aircraft["latitude"]) / (2 * span)   # 0 = north edge, 1 = south edge
    inside = 0 <= fx <= 1 and 0 <= fy <= 1
    fx, fy = min(max(fx, 0), 1), min(max(fy, 0), 1)
    return margin + fx * (width - 2 * margin), margin + fy * (height - 2 * margin), inside


def airport_map_box(width: float, height: float, margin: float = MAP_MARGIN, zoom: float = 1.0) -> tuple[float, float, float, float]:
    """Pixel rectangle (left, top, right, bottom) of the airport perimeter at this zoom."""
    lat0, lon0 = AIRPORT_CENTER
    d = AIRPORT_HALF_SPAN_DEGREES
    left, top, _ = aircraft_map_position({"latitude": lat0 + d, "longitude": lon0 - d}, width, height, margin, zoom)
    right, bottom, _ = aircraft_map_position({"latitude": lat0 - d, "longitude": lon0 + d}, width, height, margin, zoom)
    return left, top, right, bottom


def system_status_for_snapshot(snapshot: dict[str, Any]) -> str:
    """Return "normal" if all systems are healthy and "degraded" otherwise."""
    aircraft = snapshot.get("aircraft", [])
    runways = snapshot.get("runways", [])
    decisions = snapshot.get("decisions", [])
    queue = snapshot.get("landing_queue", [])
    if snapshot.get("unsafe_pairs"):
        return "degraded"
    if not aircraft and not runways and not decisions and not queue:
        return "normal"
    degraded = False
    if any(not runway.get("available", True) for runway in runways):
        degraded = True
    if any(aircraft_item.get("status") in {"warning", "unknown", "stale"} for aircraft_item in aircraft):
        degraded = True
    if any(decision.get("status") in {"degraded", "queued"} for decision in decisions):
        degraded = True
    return "degraded" if degraded else "normal"


def _decision_display(decision: dict[str, Any]) -> str:
    status = str(decision.get("status", "unknown")).lower()
    aircraft_id = decision.get("aircraft_id", decision.get("id", "AIRCRAFT"))
    runway_id = decision.get("runway_id")
    operation = decision.get("operation") or decision.get("request_type") or "landing"
    message = decision.get("message")
    on_runway = f" on {runway_id}" if runway_id else ""
    if status == "requested":
        return f"{aircraft_id} requested {operation} clearance"
    if status == "queued":
        queue_position = decision.get("queue_position")
        if queue_position is not None:
            return f"Queued for {operation} (position {queue_position}) - {aircraft_id}"
        return f"Queued for {operation} - {aircraft_id}"
    if status == "cleared":
        return f"Cleared for {operation}{on_runway} - {aircraft_id}"
    if status == "completed":
        return f"{'Departed' if operation == 'departure' else 'Landed'}{on_runway} - {aircraft_id}"
    if status == "rejected" and decision.get("reason"):
        return f"Rejected ({decision['reason']}) - {aircraft_id}"
    if message:
        return message
    return f"{status.title()} - {aircraft_id}"


def format_clock(value: Any) -> str:
    """Return HH:MM for ISO timestamps or leave other values alone."""
    if value is None:
        return "--"
    text = str(value).strip()
    if not text or text == "--":
        return "--"
    if "T" in text and len(text) >= 16:
        time_part = text.split("T", 1)[1]
        if len(time_part) >= 5:
            return time_part[:5]
    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            return f"{parts[0]}:{parts[1]}"
    return text


def aircraft_map_state(decision: dict[str, Any] | None, aircraft_runway_id: str | None) -> tuple[str, str | None]:
    """Where an aircraft belongs on the map, given Tower's latest decision for it.

    Returns ("runway", runway_id) while Tower has cleared it for the runway Radar
    has recorded on it (REQ-RAD-006/007): the landing or take-off is under way.
    Otherwise ("position", None): draw it at its latitude/longitude, which for a
    landed aircraft is its parking stand.
    """
    if decision and decision.get("status") == "cleared" and aircraft_runway_id and decision.get("runway_id") == aircraft_runway_id:
        return "runway", aircraft_runway_id
    return "position", None


def build_dashboard_snapshot(db: MessageDatabase) -> dict[str, Any]:
    """Build the unified command snapshot from shared state and live aircraft data.

    Read-only apart from the snapshot itself: in particular it never writes
    runway records, which Tower owns (REQ-TWR-007 refuses to configure an
    airport over leftover runway rows).
    """
    shared = db.list_shared_state()
    decision_for = {row["payload"].get("aircraft_id"): row["payload"] for row in shared if row["key"].startswith("decision:")}
    aircraft = [
        {
            "id": state.id,
            "latitude": state.latitude,
            "longitude": state.longitude,
            "status": state.status,
            "altitude": state.altitude,
            "timestamp": state.timestamp,
            "runway_id": state.runway_id,
            "fresh": True,
        }
        for state in AircraftStore(db).list()
    ]
    if not aircraft:
        aircraft = [dict(row["payload"]) for row in shared if row["category"] == "aircraft"]
    for plane in aircraft:
        plane["map_state"], plane["on_runway"] = aircraft_map_state(decision_for.get(plane.get("id")), plane.get("runway_id"))
    tracked = {plane.get("id") for plane in aircraft}

    runway_map = {str(row["payload"]["id"]): dict(row["payload"]) for row in shared
                  if row["category"] == "runway" and isinstance(row["payload"], dict) and row["payload"].get("id") is not None}
    # Sort by runway ID so each runway keeps its place on the map and in the list.
    runways = update_runway_availability(sorted(runway_map.values(), key=lambda runway: str(runway["id"])))
    if not runways:  # Tower has not configured an airport yet: show placeholders, write nothing
        runways = [dict(runway) for runway in DEFAULT_RUNWAYS]

    # Drop decisions for aircraft that have departed: Tower completed them and Radar no longer tracks them.
    decisions = [
        row["payload"] for row in shared
        if row["key"].startswith("decision:")
        and (row["payload"].get("aircraft_id") in tracked or row["payload"].get("status") != "completed")
    ]
    # Only requests Tower has not answered yet; answered ones are covered by their decision.
    requests = [row["payload"] for row in shared if row["key"].startswith("request:") and row["payload"].get("status") == "requested"]
    # REQ-TWR-011: Tower's active unsafe-separation pairs stay listed until resolved.
    unsafe_pairs = [row["payload"]["aircraft_ids"] for row in shared
                    if row["category"] == "tower_r2_separation" and row["payload"].get("active")]
    separation_alerts = [{"status": "degraded", "message": f"Unsafe Separation: {first} / {second}"} for first, second in unsafe_pairs]
    landing_queue = [
        {
            "aircraft_id": decision.get("aircraft_id"),
            "queue_position": decision.get("queue_position"),
            "status": decision.get("status"),
            "runway_id": decision.get("runway_id"),
            "display": _decision_display(decision),
        }
        for decision in decisions
        if decision.get("status") in {"queued", "cleared"}
    ]
    snapshot = {
        "system_status": "normal",
        "aircraft": aircraft,
        "runways": runways,
        "decisions": decisions + requests + separation_alerts,
        "landing_queue": sorted(landing_queue, key=lambda item: (item.get("queue_position") is None, item.get("queue_position") or 999)),
        # Active separation alerts keep the system degraded until Radar's positions are safe again.
        "unsafe_pairs": unsafe_pairs,
    }
    snapshot["system_status"] = system_status_for_snapshot(snapshot)
    db.upsert_shared_state("command_snapshot", snapshot, category="dashboard")
    return snapshot


def load_dashboard_snapshot(db: MessageDatabase) -> dict[str, Any]:
    """Read the latest dashboard snapshot from the shared-state table."""
    record = db.get_shared_state("command_snapshot")
    if record is None:
        return build_dashboard_snapshot(db)
    return record["payload"]


def refresh_dashboard(db: MessageDatabase) -> dict[str, Any]:
    """Refresh the shared dashboard snapshot from the current aircraft and runway state."""
    return build_dashboard_snapshot(db)


def _launch_tk_dashboard(db: MessageDatabase | None = None):
    """Launch the interactive dashboard window for local use."""
    import tkinter as tk
    from tkinter import ttk

    if db is None:
        from src.db import create_database

        db = create_database()

    root = tk.Tk()
    root.title("ATC Command Center")
    root.geometry("1250x760")
    root.configure(bg="#101827")

    header = tk.Frame(root, bg="#1E293B", height=78)
    header.pack(fill="x")
    title = tk.Label(
        header,
        text="AIR TRAFFIC CONTROL | COMMAND CENTER",
        font=("Arial", 19, "bold"),
        fg="white",
        bg="#1E293B",
    )
    title.pack(side="left", padx=20, pady=20)

    status_var = tk.StringVar(value="NORMAL")
    status_label = tk.Label(
        header,
        textvariable=status_var,
        fg="#86EFAC",
        bg="#1E293B",
        font=("Arial", 13, "bold"),
    )
    status_label.pack(side="right", padx=20, pady=20)

    main = tk.Frame(root, bg="#101827")
    main.pack(fill="both", expand=True, padx=20, pady=20)

    airport_frame = tk.LabelFrame(main, text=" AIRPORT MAP ", font=("Arial", 13, "bold"), fg="white", bg="#1E293B")
    airport_frame.pack(side="left", fill="both", expand=True, padx=(0, 15))

    zoom_level = DEFAULT_ZOOM
    map_controls = tk.Frame(airport_frame, bg="#1E293B")
    map_controls.pack(fill="x", padx=15, pady=(10, 0))
    zoom_label = tk.Label(map_controls, text=f"Zoom {zoom_level:.1f}x", fg="white", bg="#1E293B", font=("Arial", 10, "bold"))
    zoom_label.pack(side="left")
    tk.Button(map_controls, text="-", width=3, command=lambda: set_map_zoom(zoom_level - ZOOM_STEP), bg="#334155", fg="white", activebackground="#475569", borderwidth=0).pack(side="right", padx=(0, 8))
    tk.Button(map_controls, text="+", width=3, command=lambda: set_map_zoom(zoom_level + ZOOM_STEP), bg="#334155", fg="white", activebackground="#475569", borderwidth=0).pack(side="right")

    canvas = tk.Canvas(airport_frame, bg="#142536", highlightthickness=0)
    canvas.pack(fill="both", expand=True, padx=15, pady=15)

    right_panel = tk.Frame(main, bg="#101827", width=440)
    right_panel.pack(side="right", fill="y")
    right_panel.pack_propagate(False)

    runway_status_frame = tk.LabelFrame(right_panel, text=" RUNWAY STATUS ", font=("Arial", 12, "bold"), fg="white", bg="#1E293B")
    runway_status_frame.pack(fill="both", expand=True, pady=5)

    queue_frame = tk.LabelFrame(right_panel, text=" LANDING QUEUE ", font=("Arial", 12, "bold"), fg="white", bg="#1E293B")
    queue_frame.pack(fill="both", expand=True, pady=5)
    # Fixed list heights keep every panel on screen; the default (10 rows each) pushed DECISIONS off the bottom.
    queue_list = tk.Listbox(queue_frame, bg="#1E293B", fg="white", font=("Arial", 11), selectbackground="#334155", height=4)
    queue_list.pack(fill="both", expand=True, padx=10, pady=10)

    aircraft_frame = tk.LabelFrame(right_panel, text=" AIRCRAFT ", font=("Arial", 12, "bold"), fg="white", bg="#1E293B")
    aircraft_frame.pack(fill="both", expand=True, pady=5)
    columns = ("Aircraft", "Status", "Altitude", "Timestamp")
    aircraft_table = ttk.Treeview(aircraft_frame, columns=columns, show="headings", height=6)
    for column in columns:
        aircraft_table.heading(column, text=column)
        aircraft_table.column(column, width=95, anchor="center")
    style = ttk.Style()
    style.configure("Treeview", font=("Arial", 11))
    style.configure("Treeview.Heading", font=("Arial", 11, "bold"))
    aircraft_table.pack(fill="both", expand=True, padx=10, pady=10)

    decision_frame = tk.LabelFrame(right_panel, text=" DECISIONS ", font=("Arial", 12, "bold"), fg="white", bg="#1E293B")
    decision_frame.pack(fill="both", expand=True, pady=5)
    decision_list = tk.Listbox(decision_frame, bg="#1E293B", fg="white", font=("Arial", 11), selectbackground="#334155", height=5)
    decision_list.pack(fill="both", expand=True, padx=10, pady=10)

    last_snapshot = {}

    def set_map_zoom(new_zoom):
        nonlocal zoom_level
        zoom_level = max(MIN_ZOOM, min(MAX_ZOOM, new_zoom))
        zoom_label.config(text=f"Zoom {zoom_level:.1f}x")
        draw_map(last_snapshot)

    def draw_airport(runways):
        """Draw the map area, the airport perimeter and its runways; return each runway's center."""
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        margin = MAP_MARGIN
        canvas.create_rectangle(margin, margin, width - margin, height - margin, fill="#0F172A", outline="#1E293B", width=2)

        left, top, right, bottom = airport_map_box(width, height, zoom=zoom_level)
        canvas.create_rectangle(left, top, right, bottom, fill="#0B1220", outline="#38BDF8", width=2)

        # Runways are sized to the airport, so they grow and shrink with the zoom.
        box_width, box_height = right - left, bottom - top
        runway_width = max(8, box_width * 0.12)
        runway_height = box_height * 0.7
        center_x, center_y = (left + right) / 2, (top + bottom) / 2
        shown = runways[:3]  # room for three runways inside the airport
        runway_centers = {}
        for i, runway in enumerate(shown):
            x, y = center_x + (i - (len(shown) - 1) / 2) * box_width * 0.27, center_y  # centered as a group
            runway_centers[runway.get("id")] = (x, y)
            color = "#22C55E" if runway.get("available", True) else "#EF4444"
            x1, y1 = x - runway_width / 2, y - runway_height / 2
            x2, y2 = x + runway_width / 2, y + runway_height / 2
            canvas.create_rectangle(x1, y1, x2, y2, fill="#243447", outline=color, width=3)
            canvas.create_line(x, y1 + 8, x, y2 - 8, fill="#CBD5E1", width=2, dash=(10, 10))
            canvas.create_text(x, y2 + 14, text=runway.get("id", f"RWY {i + 1}"), fill="white", font=("Arial", 10, "bold"))

        # Parking stands, where Radar moves aircraft after landing (REQ-RAD-007).
        stands = [aircraft_map_position({"latitude": lat, "longitude": lon}, width, height, zoom=zoom_level) for lat, lon in PARKING_STANDS]
        xs, ys = [x for x, _, _ in stands], [y for _, y, _ in stands]
        canvas.create_rectangle(min(xs) - 14, min(ys) - 14, max(xs) + 14, max(ys) + 14, outline="#64748B", width=1, dash=(4, 3))
        canvas.create_text((min(xs) + max(xs)) / 2, min(ys) - 24, text="PARKING", fill="#94A3B8", font=("Arial", 9, "bold"))
        for x, y, _ in stands:
            canvas.create_line(x - 10, y + 12, x + 10, y + 12, fill="#64748B", width=1)
        return runway_centers

    def draw_aircraft(aircraft, runway_centers):
        width = canvas.winfo_width()
        positions = []
        for plane in aircraft:
            if plane.get("map_state") == "runway" and plane.get("on_runway") in runway_centers:
                positions.append((*runway_centers[plane["on_runway"]], True))  # drawn on its runway
            else:
                positions.append(aircraft_map_position(plane, width, canvas.winfo_height(), zoom=zoom_level))
        # Boxes (center x, center y, half width) a label must not cover: every icon, then each label drawn.
        placed = [(x, y, 12) for x, y, _ in positions]
        for plane, (x, y, inside) in zip(aircraft, positions):
            # Grey icon = outside the shown area, drawn at the nearest edge; zoom out to see it.
            color = "#FBBF24" if inside else "#94A3B8"
            canvas.create_text(x, y, text="✈", fill=color, font=("Arial", 22, "bold"))
            if plane.get("map_state") == "runway":
                where = f" on {plane['on_runway']}"
            else:
                where = "" if inside else " (off map)"
            label = f"{plane.get('id', 'AIRCRAFT')} {plane.get('status', '')} {plane.get('altitude', 0):g} m{where}"
            half_width = 4 * len(label)  # approximate, for a 10 pt bold font
            if (plane.get("latitude"), plane.get("longitude")) in PARKING_STANDS:
                # Stands are stacked in a column, so their labels go beside the plane instead of above it.
                label_x = min(x + 16 + half_width, width - half_width - 4)
                placed.append((label_x, y, half_width))
                canvas.create_text(label_x, y, text=label, fill="white", font=("Arial", 10, "bold"))
                continue
            label_x = min(max(x, half_width + 4), width - half_width - 4)  # keep the label inside the map
            label_y = y - 22
            while any(abs(label_x - px) < half_width + pw and abs(label_y - py) < 18 for px, py, pw in placed):
                label_y -= 18
            placed.append((label_x, label_y, half_width))
            if label_y < y - 22:  # label had to move up: link it to its icon
                canvas.create_line(x, y - 12, label_x, label_y + 8, fill=color, width=1)
            canvas.create_text(label_x, label_y, text=label, fill="white", font=("Arial", 10, "bold"))

    def draw_map(snapshot):
        canvas.delete("all")
        runway_centers = draw_airport(snapshot.get("runways", DEFAULT_RUNWAYS))
        draw_aircraft(snapshot.get("aircraft", []), runway_centers)

    def refresh_view():
        nonlocal last_snapshot
        snapshot = last_snapshot = build_dashboard_snapshot(db)
        status_var.set(snapshot["system_status"].upper())
        if snapshot["system_status"] == "degraded":
            status_label.config(fg="#FCA5A5")
        else:
            status_label.config(fg="#86EFAC")

        for widget in runway_status_frame.winfo_children():
            widget.destroy()
        for runway in snapshot.get("runways", []):
            status = "AVAILABLE" if runway.get("available", True) else "UNAVAILABLE"
            color = "#22C55E" if runway.get("available", True) else "#EF4444"
            occupied_until = runway.get("occupied_until")
            row = tk.Frame(runway_status_frame, bg="#1E293B")
            row.pack(fill="x", padx=15, pady=8)
            tk.Label(row, text=runway.get("id", "RUNWAY"), fg="white", bg="#1E293B", font=("Arial", 12, "bold")).pack(side="left")
            detail = f"{status} | {occupied_until}" if occupied_until else status
            tk.Label(row, text=detail, fg=color, bg="#1E293B", font=("Arial", 10, "bold")).pack(side="right")

        queue_list.delete(0, tk.END)
        queue_items = snapshot.get("landing_queue", [])
        if not queue_items:
            queue_list.insert(tk.END, "No aircraft requesting landing")
        else:
            for item in queue_items:
                queue_list.insert(tk.END, item.get("display", f"{item.get('aircraft_id', 'AIRCRAFT')} -> {item.get('status', 'unknown')}"))

        for item in aircraft_table.get_children():
            aircraft_table.delete(item)
        for aircraft in snapshot.get("aircraft", []):
            aircraft_table.insert(
                "",
                "end",
                values=(
                    aircraft.get("id", "--"),
                    aircraft.get("status", "Unknown"),
                    f"{aircraft.get('altitude', 0)} m",
                    format_clock(aircraft.get("timestamp", "--")),
                ),
            )

        if not snapshot.get("aircraft"):
            aircraft_table.insert("", "end", values=("--", "No aircraft", "--", "--"))

        decision_list.delete(0, tk.END)
        if not snapshot.get("decisions"):
            decision_list.insert(tk.END, "No active decisions")
        else:
            for decision in snapshot["decisions"]:
                decision_list.insert(tk.END, _decision_display(decision))

        draw_map(snapshot)
        root.after(2000, refresh_view)

    canvas.bind("<Configure>", lambda event: draw_map(last_snapshot))
    refresh_view()
    root.mainloop()


if __name__ == "__main__":
    from src.db import create_database

    _launch_tk_dashboard(create_database())
