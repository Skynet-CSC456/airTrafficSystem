from datetime import datetime, timezone
import threading
import time

from src.db import Team, create_database
from src.radar.aircraft import AircraftState, AircraftStore
from src.radar.messaging import process_runway_assignments, request_runway
from src.radar.position_update import update_aircraft_position
from src.radar.runway_operations import radar_tick
from src.tower.messaging import (
    assign_runway_to_aircraft,
    is_waiting_to_depart,
    list_assignable_aircraft,
    load_runways,
)

MAX_MESSAGES = 10_000


def choose_team(prompt, exclude=None):
    while True:
        team = input(prompt).strip().lower()

        if team == "radar":
            choice = Team.RADAR
        elif team == "tower":
            choice = Team.TOWER
        elif team == "command":
            choice = Team.COMMAND
        else:
            print("Invalid team. Choose radar, tower, or command.")
            continue

        if choice == exclude:
            print("You can't send a message to your own team.")
            continue

        return choice


def count_unread(db, team):
    return len(db.receive_messages(team, unread_only=True, limit=MAX_MESSAGES))


def choose_option(prompt, options):
    """Ask for one of `options` (a list of labels) and return its 0-based index."""
    for number, label in enumerate(options, start=1):
        print(f"{number}. {label}")
    while True:
        answer = input(prompt).strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print(f"Invalid option. Select a number from 1 to {len(options)}.")


def assign_runway_function(db):
    """Tower function: pick an airplane in the air or waiting to depart, assign it a runway, notify Radar and Command."""
    aircraft = list_assignable_aircraft(db)
    if not aircraft:
        print("\nNo airplanes are in the air or waiting to depart.")
        return

    print("\nAirplanes in the air or waiting to depart:")
    chosen = aircraft[
        choose_option(
            "Select an airplane: ",
            [
                f"{a.id}  ({a.status}, {a.altitude:g} m"
                + (", waiting to depart)" if is_waiting_to_depart(db, a.id) else ")")
                for a in aircraft
            ],
        )
    ]

    runways = load_runways(db)
    print(f"\nRunways for {chosen.id}:")
    runway = runways[
        choose_option(
            "Select a runway: ",
            [f"{r['id']}  ({'available' if r.get('available') else 'occupied'})" for r in runways],
        )
    ]

    try:
        decision = assign_runway_to_aircraft(db, chosen.id, runway["id"])
    except ValueError as error:
        print(f"\nCould not assign runway: {error}")
        return

    print("\nRunway assigned! Function message sent to radar and command.")
    print(f"Airplane: {decision['aircraft_id']}")
    print(f"Runway: {decision['runway_id']}")
    for recipient, message_id in decision["message_ids"].items():
        print(f"Message ID ({recipient}): {message_id}")


def request_runway_function(db):
    """Radar function: pick a tracked airplane and ask Tower for a runway (REQ-RAD-006)."""
    aircraft = AircraftStore(db).list()
    if not aircraft:
        print("\nRadar is not tracking any airplanes.")
        return

    print("\nTracked airplanes:")
    chosen = aircraft[
        choose_option(
            "Select an airplane: ",
            [f"{a.id}  ({a.status}, {a.altitude:g} m)" for a in aircraft],
        )
    ]

    print(f"\nRunway request for {chosen.id}:")
    request_type = ("landing", "departure")[
        choose_option("Select a request: ", ["Landing", "Departure"])
    ]

    try:
        message_id = request_runway(db, chosen.id, request_type)
    except ValueError as error:
        print(f"\nCould not request a runway: {error}")
        return

    print("\nRunway request sent to tower.")
    print(f"Airplane: {chosen.id}")
    print(f"Request: {request_type}")
    print(f"Message ID: {message_id}")


def send_messages(db, team):
    if team == Team.TOWER:
        kind = choose_option(
            "What do you want to send? ",
            ["Text message", "Function: assign runway to an airplane"],
        )
        if kind == 1:
            assign_runway_function(db)
            return

    if team == Team.RADAR:
        kind = choose_option(
            "What do you want to send? ",
            ["Text message", "Function: request a runway for an airplane"],
        )
        if kind == 1:
            request_runway_function(db)
            return

    recipient = choose_team(
        "Who do you want to send the message to? (radar/tower/command): ",
        exclude=team,
    )

    message = input("Enter your message: ")

    try:
        message_id = db.send_message(team, recipient, message)

        print("\nMessage sent successfully!")
        print(f"Message ID: {message_id}")
        print(f"From: {team.value}")
        print(f"To: {recipient.value}")
        print(f"Message: {message}")

    except ValueError as error:
        print(f"\nCould not send message: {error}")


def format_value(value):
    """Show ISO timestamps as 'YYYY-MM-DD HH:MM:SS UTC' and snake_case names as words."""
    if not isinstance(value, str):
        return value
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value.replace("_", " ") if value.isidentifier() else value
    if moment.tzinfo is not None:
        return f"{moment.astimezone(timezone.utc):%Y-%m-%d %H:%M:%S} UTC"
    return f"{moment:%Y-%m-%d %H:%M:%S}"


def describe_metadata(metadata):
    """Turn a message's metadata dict into readable (label, value) pairs."""
    if not metadata:
        return []
    return [
        (key.removesuffix("_id").replace("_", " ").capitalize(), format_value(value))
        for key, value in metadata.items()
    ]


def read_messages(db, team):
    messages = db.receive_messages(team, limit=MAX_MESSAGES)

    print(f"\n=== {team.value.upper()} INBOX ===")

    if not messages:
        print("No messages.")
        return

    for message in messages:
        status = "  [NEW]" if message.read_at is None else ""
        print("\n--------------------------")
        print(f"Message ID: {message.id}{status}")
        print(f"From: {message.sender.value}")
        if message.message_type != "text":
            print(f"Type: {message.message_type}")
        print(f"Message: {message.content}")
        for label, value in describe_metadata(message.metadata):
            print(f"  {label}: {value}")
        print(f"Sent: {message.created_at}")

    if team == Team.RADAR:
        # REQ-RAD-006: record any runway Tower has assigned since the last read.
        for aircraft in process_runway_assignments(db):
            print(f"\nRadar recorded {aircraft.runway_id} for {aircraft.id}.")

    # Everything shown has now been seen, so clear the unread counter.
    db.receive_messages(
        team, unread_only=True, limit=MAX_MESSAGES, mark_as_read=True
    )


AIRCRAFT_STATUSES = ("Parked", "Departing", "Landing")


def ask_number(label, current=None):
    """Ask until a number is entered; with `current`, pressing Enter keeps it."""
    suffix = f" [{current:g}]" if current is not None else ""
    while True:
        answer = input(f"{label}{suffix}: ").strip()
        if not answer and current is not None:
            return current
        try:
            return float(answer)
        except ValueError:
            print("Please enter a number.")


def ask_status(current=None):
    """Ask for Parked, Departing or Landing (by name or number); Enter keeps `current`."""
    options = ", ".join(f"{number}. {status}" for number, status in enumerate(AIRCRAFT_STATUSES, start=1))
    suffix = f" [{current}]" if current is not None else ""
    while True:
        answer = input(f"Status ({options}){suffix}: ").strip().lower()
        if not answer and current is not None:
            return current
        for number, status in enumerate(AIRCRAFT_STATUSES, start=1):
            if answer in (str(number), status.lower()):
                return status
        print("Invalid status. Choose Parked, Departing, or Landing.")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def create_airplane(db):
    """Radar: create a new airplane (REQ-RAD-001 / REQ-RAD-004)."""
    store = AircraftStore(db)
    aircraft_id = input("\nAircraft ID: ").strip()
    if store.get(aircraft_id) is not None:
        print(f"\nCould not create airplane: {aircraft_id} already exists. Use 'Update airplane' instead.")
        return
    print("(Airport area: latitude about 40.64, longitude about -73.78)")
    try:
        state = store.create(AircraftState(
            id=aircraft_id,
            latitude=ask_number("Latitude"),
            longitude=ask_number("Longitude"),
            status=ask_status(),
            altitude=ask_number("Altitude (m)"),
            timestamp=utc_now(),
        ))
    except ValueError as error:
        print(f"\nCould not create airplane: {error}")
        return

    print("\nAirplane created!")
    print(f"Airplane: {state.id}")
    print(f"Status: {state.status}, {state.altitude:g} m at ({state.latitude}, {state.longitude})")


def update_airplane(db):
    """Radar: send a new position/status for a tracked airplane (REQ-RAD-001 / REQ-RAD-003)."""
    aircraft = AircraftStore(db).list()
    if not aircraft:
        print("\nRadar is not tracking any airplanes.")
        return

    print("\nTracked airplanes:")
    current = aircraft[
        choose_option(
            "Select an airplane: ",
            [f"{a.id}  ({a.status}, {a.altitude:g} m)" for a in aircraft],
        )
    ]

    print(f"\nUpdating {current.id}. Press Enter to keep a value.")
    payload = {
        "id": current.id,
        "latitude": ask_number("Latitude", current.latitude),
        "longitude": ask_number("Longitude", current.longitude),
        "status": ask_status(current.status),
        "altitude": ask_number("Altitude (m)", current.altitude),
        "timestamp": utc_now(),
    }
    try:
        accepted = update_aircraft_position(AircraftStore(db), payload)
    except ValueError as error:
        print(f"\nCould not update airplane: {error}")
        return

    if not accepted:
        print("\nRadar ignored the update: it is not newer than the last one. Try again in a second.")
        return
    state = AircraftStore(db).get(current.id)
    print("\nAirplane updated!")
    print(f"Airplane: {state.id}")
    print(f"Status: {state.status}, {state.altitude:g} m at ({state.latitude}, {state.longitude})")


def main():
    db = create_database()

    try:
        print("=== ATC Messaging Demo ===")
        team = choose_team("What team are you? (radar/tower/command): ")

        while True:
            unread = count_unread(db, team)

            print(f"\n--- {team.value.upper()} ---")
            print("1. Send messages")
            print(f"2. Read messages ({unread})")
            if team == Team.RADAR:
                print("3. Create airplane")
                print("4. Update airplane")
            print("q. Quit")

            choice = input("Select an option: ").strip().lower()

            if choice == "1":
                send_messages(db, team)
            elif choice == "2":
                read_messages(db, team)
            elif choice == "3" and team == Team.RADAR:
                create_airplane(db)
            elif choice == "4" and team == Team.RADAR:
                update_airplane(db)
            elif choice in ("q", "quit"):
                break
            elif team == Team.RADAR:
                print("Invalid option. Please select 1, 2, 3, 4, or q.")
            else:
                print("Invalid option. Please select 1, 2, or q.")

    finally:
        db.close()


def run_radar_tracker(interval=1.0):
    """Background Radar work while the demo runs (REQ-RAD-006 / REQ-RAD-007).

    Every `interval` seconds: record new Tower runway assignments, park aircraft
    that have landed and stop tracking aircraft that have departed. Uses its own
    database connection, since SQLite connections cannot be shared between threads.
    Safe to run from several demo windows at once.
    """
    db = create_database()
    try:
        while True:
            try:
                radar_tick(db)
            except Exception:  # e.g. the database is briefly locked by another window
                pass
            time.sleep(interval)
    finally:
        db.close()


if __name__ == "__main__":
    threading.Thread(target=run_radar_tracker, daemon=True).start()
    main()
