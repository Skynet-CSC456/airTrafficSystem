#!/usr/bin/env python3
import argparse
import re
import time
from datetime import datetime, timedelta, timezone

from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.tower.clearance import publish_runway_decision


DEFAULT_RUNWAYS = [
    {"id": "RWY 1", "available": True, "mode": "landing"},
    {"id": "RWY 2", "available": True, "mode": "landing"},
]

LANDING_COORDINATES = {"latitude": 40.6418, "longitude": -73.7788}
PARKED_COORDINATES = {"latitude": 40.6402, "longitude": -73.7798}
# Keep the airborne point just outside the airport perimeter so it stays visible on-screen.
DEPARTURE_COORDINATES = {"latitude": 40.6454, "longitude": -73.7748}
RUNWAY_COORDINATES = {
    "RWY 1": {"latitude": 40.6420, "longitude": -73.7782},
    "RWY 2": {"latitude": 40.6428, "longitude": -73.7771},
}


def ensure_runways_exist(db):
    existing = {
        row["payload"].get("id"): row["payload"]
        for row in db.list_shared_state(category="runway")
        if row["payload"].get("id")
    }
    for runway in DEFAULT_RUNWAYS:
        if runway["id"] not in existing:
            db.upsert_shared_state(f"runway:{runway['id']}", runway, category="runway")
    return load_runways(db)


def load_runways(db):
    rows = db.list_shared_state(category="runway")
    if rows:
        payloads = [row["payload"] for row in rows]
        existing_ids = {payload.get("id") for payload in payloads if payload.get("id")}
        for runway in DEFAULT_RUNWAYS:
            if runway["id"] not in existing_ids:
                db.upsert_shared_state(f"runway:{runway['id']}", runway, category="runway")
                payloads.append(runway)
        return payloads
    return DEFAULT_RUNWAYS.copy()


def _write_aircraft_state(db, aircraft_id: str, *, latitude: float, longitude: float, altitude: float, status: str) -> None:
    store = AircraftStore(db)
    existing = store.get(aircraft_id)
    if existing is None:
        return

    next_timestamp = max(datetime.now(timezone.utc), datetime.fromisoformat(existing.timestamp).astimezone(timezone.utc) + timedelta(seconds=1))
    updated_state = AircraftState(
        id=existing.id,
        latitude=float(latitude),
        longitude=float(longitude),
        status=status,
        altitude=float(altitude),
        timestamp=next_timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    store.update(updated_state)
    db.upsert_shared_state(
        f"aircraft:{aircraft_id}",
        {
            "id": updated_state.id,
            "latitude": updated_state.latitude,
            "longitude": updated_state.longitude,
            "status": updated_state.status,
            "altitude": updated_state.altitude,
            "timestamp": updated_state.timestamp,
            "fresh": True,
        },
        category="aircraft",
    )
    try:
        from src.command.dashboard import build_dashboard_snapshot
        db.upsert_shared_state("command_snapshot", build_dashboard_snapshot(db), category="dashboard")
    except Exception:
        pass
    print(f"Tower updated {aircraft_id} to {status} position: {updated_state.latitude}, {updated_state.longitude}")


def update_aircraft_on_runway(db, aircraft_id: str, runway_id: str) -> None:
    coords = RUNWAY_COORDINATES.get(runway_id, LANDING_COORDINATES)
    _write_aircraft_state(
        db,
        aircraft_id,
        latitude=coords["latitude"],
        longitude=coords["longitude"],
        altitude=50.0,
        status="Landing",
    )


def update_aircraft_after_landing(db, aircraft_id: str) -> None:
    _write_aircraft_state(
        db,
        aircraft_id,
        latitude=PARKED_COORDINATES["latitude"],
        longitude=PARKED_COORDINATES["longitude"],
        altitude=0.0,
        status="Parked",
    )


def update_aircraft_on_departure_runway(db, aircraft_id: str, runway_id: str, altitude: float | int) -> None:
    coords = RUNWAY_COORDINATES.get(runway_id, DEPARTURE_COORDINATES)
    _write_aircraft_state(
        db,
        aircraft_id,
        latitude=coords["latitude"],
        longitude=coords["longitude"],
        altitude=float(altitude),
        status="Airborne",
    )


def update_aircraft_after_departure(db, aircraft_id: str, altitude: float | int) -> None:
    _write_aircraft_state(
        db,
        aircraft_id,
        latitude=DEPARTURE_COORDINATES["latitude"],
        longitude=DEPARTURE_COORDINATES["longitude"],
        altitude=float(altitude),
        status="Airborne",
    )


def extract_aircraft_id(message: str) -> str:
    match = re.search(r"\b[A-Z]{2,}[A-Z0-9]*\d+[A-Z0-9]*\b", message)
    if match:
        return match.group(0)
    match = re.search(r"\b[A-Z0-9]{3,}\b", message)
    if match:
        return match.group(0)
    return "UNKNOWN"


def determine_request(message: str) -> str | None:
    lower = message.lower()
    if "landing" in lower:
        return "landing"
    if "departure" in lower:
        return "departure"
    return None


def extract_altitude(message: str) -> float:
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(?:m|meters|metres)", message, re.IGNORECASE)
    if matches:
        return float(matches[-1])
    return 2000.0


def normalize_runway_id(runway_id: str) -> str:
    cleaned = str(runway_id).strip().upper()
    if cleaned.startswith("RWY"):
        digits = re.sub(r"\D+", "", cleaned)
        if digits:
            return f"RWY {digits}"
    return cleaned


def parse_manual_assignment(command: str) -> tuple[str, str, float]:
    tokens = command.strip().split()
    if len(tokens) < 3:
        raise ValueError("Usage: assign <AIRCRAFT_ID> <RWY_ID> [ALTITUDE]")

    action = tokens[0].lower()
    if action not in {"assign", "approve", "clear"}:
        raise ValueError("Command must start with 'assign' or 'approve'.")

    aircraft_id = tokens[1]
    runway_id = None
    altitude = 0.0
    idx = 2

    if idx < len(tokens):
        first_runway = tokens[idx].upper()
        if first_runway.startswith("RWY"):
            if first_runway == "RWY" and idx + 1 < len(tokens):
                runway_id = f"RWY {tokens[idx + 1]}"
                idx += 2
            else:
                runway_id = first_runway
                idx += 1
        elif re.fullmatch(r"\d+(?:\.\d+)?", first_runway):
            raise ValueError("A runway is required before the altitude, for example: RWY 1")

    if runway_id is None:
        raise ValueError("A runway is required, for example: RWY 1 or RWY 2")

    for token in tokens[idx:]:
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            altitude = float(token)
            break

    runway_id = normalize_runway_id(runway_id)
    return aircraft_id, runway_id, altitude


def assign_manual_runway(
    db,
    aircraft_id: str,
    runway_id: str,
    assigned_altitude: float | int | None = None,
    landing_deadlines=None,
    departure_deadlines=None,
    *,
    request_type: str = "landing",
) -> None:
    runway_id = normalize_runway_id(runway_id)
    runways = load_runways(db)
    known_runways = {str(r["id"]).upper() for r in runways}
    if runway_id not in known_runways:
        raise ValueError(f"Unknown runway: {runway_id}")

    altitude = float(assigned_altitude) if assigned_altitude is not None else 0.0

    if request_type == "departure":
        aircraft = {"id": aircraft_id, "status": "Departing"}
        publish_runway_decision(db, aircraft, runways, runway_id=runway_id)
        update_aircraft_on_departure_runway(db, aircraft_id, runway_id, altitude)
        if departure_deadlines is not None:
            departure_deadlines[aircraft_id] = (time.time() + 3.0, altitude)
        response = (
            f"{aircraft_id} departure clearance approved on {runway_id}. "
            f"Assigned altitude {altitude}m. Status is Airborne."
        )
    else:
        aircraft = {"id": aircraft_id, "status": "Landing"}
        publish_runway_decision(db, aircraft, runways, runway_id=runway_id)
        update_aircraft_on_runway(db, aircraft_id, runway_id)
        if landing_deadlines is not None:
            landing_deadlines[aircraft_id] = time.time() + 3.0
        response = f"{aircraft_id} has been assigned runway {runway_id} for landing clearance."
        if altitude:
            response += f" Assigned altitude {altitude}m."

    db.send_message(Team.TOWER, Team.RADAR, response)
    print(f"Tower sent to Radar: {response}")


def handle_message(db, message_text: str, pending_requests=None, landing_deadlines=None) -> None:
    request_type = determine_request(message_text)
    if request_type is None:
        print(f"Tower ignored message: {message_text}")
        return

    aircraft_id = extract_aircraft_id(message_text)
    if pending_requests is not None:
        pending_requests[aircraft_id] = {
            "type": request_type,
            "message": message_text,
            "altitude": extract_altitude(message_text),
        }
    print(f"{request_type.title()} request pending for {aircraft_id}. Use: assign {aircraft_id} RWY 1 2500")


def main():
    parser = argparse.ArgumentParser(description="Tower listens for radar requests and manually assigns runways and altitudes.")
    parser.add_argument("--db", default="data/atc_system.db", help="SQLite database file")
    args = parser.parse_args()

    db = create_database(args.db)
    landing_deadlines = {}
    departure_deadlines = {}
    pending_requests = {}
    try:
        ensure_runways_exist(db)
        print("Tower manual runway assignment mode.")
        print("Commands: pending | assign AC123 RWY 1 2500 | approve AC123 RWY 2 3200 | list | quit")
        while True:
            messages = db.receive_messages(Team.TOWER, unread_only=True, limit=20, mark_as_read=True)
            for message in messages:
                print(f"Tower received: {message.content}")
                handle_message(db, message.content, pending_requests, landing_deadlines)

            for aircraft_id, deadline in list(landing_deadlines.items()):
                if time.time() >= deadline:
                    update_aircraft_after_landing(db, aircraft_id)
                    del landing_deadlines[aircraft_id]
                    if aircraft_id in pending_requests:
                        del pending_requests[aircraft_id]

            for aircraft_id, departure_entry in list(departure_deadlines.items()):
                deadline, assigned_altitude = departure_entry
                if time.time() >= deadline:
                    update_aircraft_after_departure(db, aircraft_id, assigned_altitude)
                    del departure_deadlines[aircraft_id]
                    if aircraft_id in pending_requests:
                        del pending_requests[aircraft_id]

            cmd = input("Tower> ").strip()
            if not cmd:
                continue
            if cmd.lower() in {"quit", "exit"}:
                break
            if cmd.lower() == "pending":
                if not pending_requests:
                    print("No pending requests.")
                else:
                    for aircraft_id, data in pending_requests.items():
                        print(f"{aircraft_id}: {data['type']} request | altitude {data.get('altitude', 0)}m")
                continue
            if cmd.lower() == "list":
                print("Available runways:")
                for runway in load_runways(db):
                    print(f" - {runway['id']}")
                continue

            try:
                aircraft_id, runway_id, altitude = parse_manual_assignment(cmd)
            except ValueError as exc:
                print(f"Error: {exc}")
                print("Example: assign AC123 RWY 1 2500")
                continue

            pending = pending_requests.get(aircraft_id)
            request_type = pending.get("type", "landing") if pending else "landing"
            if aircraft_id not in pending_requests and request_type == "landing":
                pending_requests[aircraft_id] = {"type": "landing", "message": "Manual assignment", "altitude": altitude}

            try:
                assign_manual_runway(
                    db,
                    aircraft_id,
                    runway_id,
                    assigned_altitude=altitude,
                    landing_deadlines=landing_deadlines,
                    departure_deadlines=departure_deadlines,
                    request_type=request_type,
                )
                if aircraft_id in pending_requests:
                    del pending_requests[aircraft_id]
            except ValueError as exc:
                print(f"Error: {exc}")
                continue

            print(f"Tower assigned {aircraft_id} to {runway_id} at {altitude}m")
    finally:
        db.close()


if __name__ == "__main__":
    main()
