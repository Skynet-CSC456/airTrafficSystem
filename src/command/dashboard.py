"""Command dashboard and shared-state queries.

REQ-CMD-001: command receives tower messages and system state.
REQ-CMD-004: unified dashboard shows aircraft and decisions with normal/degraded state.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from src.db import MessageDatabase
from src.radar.aircraft import AircraftStore
from src.tower.clearance import update_runway_availability

# The dashed rectangle on the map covers this area around the airport (about 5.5 x 4.2 km).
AIRPORT_CENTER = (40.6413, -73.7781)  # latitude, longitude
MAP_HALF_SPAN_DEGREES = 0.05
MAP_MARGIN = 45


def aircraft_map_position(aircraft: dict[str, Any], width: float, height: float, margin: float = MAP_MARGIN) -> tuple[float, float, bool]:
    """Convert an aircraft's latitude/longitude to a pixel position on the map.

    Returns (x, y, inside). Aircraft outside the mapped area are clamped to its
    edge with inside=False, so they stay visible.
    """
    lat0, lon0 = AIRPORT_CENTER
    span = MAP_HALF_SPAN_DEGREES
    fx = (aircraft["longitude"] - (lon0 - span)) / (2 * span)  # 0 = west edge, 1 = east edge
    fy = ((lat0 + span) - aircraft["latitude"]) / (2 * span)   # 0 = north edge, 1 = south edge
    inside = 0 <= fx <= 1 and 0 <= fy <= 1
    fx, fy = min(max(fx, 0), 1), min(max(fy, 0), 1)
    return margin + fx * (width - 2 * margin), margin + fy * (height - 2 * margin), inside


def system_status_for_snapshot(snapshot: dict[str, Any]) -> str:
    """Return "normal" if all systems are healthy and "degraded" otherwise."""
    aircraft = snapshot.get("aircraft", [])
    runways = snapshot.get("runways", [])
    decisions = snapshot.get("decisions", [])
    queue = snapshot.get("landing_queue", [])
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
    request_type = decision.get("request_type")
    message = decision.get("message")
    if status == "requested":
        if request_type:
            return f"{aircraft_id} requested {request_type} clearance"
        return f"{aircraft_id} requested clearance"
    if status == "queued":
        queue_position = decision.get("queue_position")
        if queue_position is not None:
            return f"Queued for landing (position {queue_position}) - {aircraft_id}"
        return f"Queued for landing - {aircraft_id}"
    if status == "cleared":
        if runway_id:
            return f"Cleared for landing on {runway_id} - {aircraft_id}"
        return f"Cleared for landing - {aircraft_id}"
    if message:
        return message
    return f"{status.title()} - {aircraft_id}"


def aircraft_map_state(decision: dict[str, Any] | None, now: datetime) -> tuple[str, str | None]:
    """Where an aircraft belongs on the map, given Tower's latest decision for it.

    Returns ("runway", runway_id) while its runway hold is active, ("hidden", None)
    once that hold has ended (it has landed or departed), and ("position", None)
    otherwise, meaning: draw it at its latitude/longitude.
    """
    if not decision or decision.get("status") != "cleared" or not decision.get("occupied_until"):
        return "position", None
    if datetime.fromisoformat(decision["occupied_until"]).astimezone(timezone.utc) > now:
        return "runway", decision.get("runway_id")
    return "hidden", None


def build_dashboard_snapshot(db: MessageDatabase) -> dict[str, Any]:
    """Build the unified command snapshot from shared state and live aircraft data."""
    shared = db.list_shared_state()
    decision_for = {row["payload"].get("aircraft_id"): row["payload"] for row in shared if row["key"].startswith("decision:")}
    now = datetime.now(timezone.utc)
    aircraft_store = AircraftStore(db)
    aircraft = []
    for state in aircraft_store.list():
        map_state, on_runway = aircraft_map_state(decision_for.get(state.id), now)
        aircraft.append(
            {
                "id": state.id,
                "latitude": state.latitude,
                "longitude": state.longitude,
                "status": state.status,
                "altitude": state.altitude,
                "timestamp": state.timestamp,
                "fresh": True,
                "map_state": map_state,
                "on_runway": on_runway,
            }
        )
    tracked = {plane["id"] for plane in aircraft}
    # Sort by runway ID so each runway keeps its place on the map and in the list.
    runway_rows = sorted((row for row in shared if row["key"].startswith("runway:")), key=lambda row: str(row["payload"].get("id", "")))
    runways = update_runway_availability([dict(row["payload"]) for row in runway_rows])
    if not runways:
        runways = [{"id": "RWY 1", "available": True, "mode": "landing"}, {"id": "RWY 2", "available": True, "mode": "landing"}]
    # Drop decisions for aircraft that have departed: no longer tracked and their runway hold is over.
    decisions = [
        row["payload"] for row in shared
        if row["key"].startswith("decision:")
        and (row["payload"].get("aircraft_id") in tracked or aircraft_map_state(row["payload"], now)[0] != "hidden")
    ]
    # Only requests Tower has not answered yet; answered ones are covered by their decision.
    requests = [row["payload"] for row in shared if row["key"].startswith("request:") and row["payload"].get("status") == "requested"]
    display_decisions = decisions + requests
    landing_queue = [
        {
            "aircraft_id": decision.get("aircraft_id"),
            "queue_position": decision.get("queue_position"),
            "status": decision.get("status"),
            "runway_id": decision.get("runway_id"),
            "display": _decision_display(decision),
        }
        for decision in decisions
        # A cleared aircraft leaves the queue once its runway hold is over (it has landed or departed).
        if decision.get("status") in {"queued", "cleared"} and aircraft_map_state(decision, now)[0] != "hidden"
    ]
    snapshot = {
        "system_status": "normal",
        "aircraft": aircraft,
        "runways": runways,
        "decisions": display_decisions,
        "landing_queue": sorted(landing_queue, key=lambda item: (item.get("queue_position") is None, item.get("queue_position") or 999)),
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

    def draw_airport(runways, aircraft=()):
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        margin = MAP_MARGIN
        canvas.create_rectangle(margin, margin, width - margin, height - margin, outline="#94A3B8", width=2, dash=(6, 4))
        runway_width = 65
        runway_height = min(350, height - 160)
        center_x = width / 2
        center_y = height / 2
        positions = [center_x - 170, center_x, center_x + 170]
        runway_centers = {}
        for i, runway in enumerate(runways):
            x = positions[i]
            y = center_y
            runway_centers[runway.get("id")] = (x, y)
            color = "#22C55E" if runway.get("available", True) else "#EF4444"
            x1 = x - runway_width / 2
            y1 = y - runway_height / 2
            x2 = x + runway_width / 2
            y2 = y + runway_height / 2
            canvas.create_rectangle(x1, y1, x2, y2, fill="#243447", outline=color, width=3)
            canvas.create_line(x, y1 + 15, x, y2 - 15, fill="#CBD5E1", width=2, dash=(10, 10))
            canvas.create_text(x, y2 + 22, text=runway.get("id", f"RWY {i + 1}"), fill="white", font=("Arial", 12, "bold"))
        # Planes that have finished with their runway (landed or departed) leave the map.
        aircraft = [plane for plane in aircraft if plane.get("map_state") != "hidden"]
        positions = []
        for plane in aircraft:
            if plane.get("map_state") == "runway" and plane.get("on_runway") in runway_centers:
                positions.append((*runway_centers[plane["on_runway"]], True))  # drawn on its runway
            else:
                positions.append(aircraft_map_position(plane, width, height))
        # Boxes (center x, center y, half width) a label must not cover: every dot, then each label drawn.
        placed = [(x, y, 8) for x, y, _ in positions]
        for plane, (x, y, inside) in zip(aircraft, positions):
            # Hollow dot = outside the mapped area, shown at the nearest edge.
            canvas.create_oval(x - 7, y - 7, x + 7, y + 7, fill="#38BDF8" if inside else "", outline="#38BDF8" if not inside else "white", width=2)
            if plane.get("map_state") == "runway":
                where = f" on {plane['on_runway']}"
            else:
                where = "" if inside else " (off map)"
            label = f"{plane['id']} {plane['status']} {plane['altitude']:g} m{where}"
            half_width = 4 * len(label)  # approximate, for an 11 pt bold font
            label_x = min(max(x, half_width + 4), width - half_width - 4)  # keep the label inside the map
            label_y = y - 16
            while any(abs(label_x - px) < half_width + pw and abs(label_y - py) < 18 for px, py, pw in placed):
                label_y -= 18
            placed.append((label_x, label_y, half_width))
            if label_y < y - 16:  # label had to move up: link it to its dot
                canvas.create_line(x, y - 7, label_x, label_y + 8, fill="#38BDF8", width=1)
            canvas.create_text(label_x, label_y, text=label, fill="#38BDF8", font=("Arial", 11, "bold"))

    def draw_snapshot(snapshot):
        draw_airport(
            snapshot.get("runways", [{"id": "RWY 1", "available": True}, {"id": "RWY 2", "available": True}]),
            snapshot.get("aircraft", []),
        )

    def refresh_view():
        snapshot = build_dashboard_snapshot(db)
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
                    aircraft.get("timestamp", "--"),
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

        draw_snapshot(snapshot)
        root.after(2000, refresh_view)

    canvas.bind("<Configure>", lambda event: draw_snapshot(build_dashboard_snapshot(db)))
    refresh_view()
    root.mainloop()


if __name__ == "__main__":
    from src.db import create_database

    _launch_tk_dashboard(create_database())
