from datetime import datetime, timezone

from src.db import Team, create_database
from src.tower.messaging import (
    assign_runway_to_aircraft,
    list_airborne_aircraft,
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
    """Tower function: pick an airborne airplane, assign it a runway, notify Radar and Command."""
    aircraft = list_airborne_aircraft(db)
    if not aircraft:
        print("\nNo airplanes are in the air right now.")
        return

    print("\nAirplanes in the air:")
    chosen = aircraft[
        choose_option(
            "Select an airplane: ",
            [f"{a.id}  ({a.status}, {a.altitude:g} m)" for a in aircraft],
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


def send_messages(db, team):
    if team == Team.TOWER:
        kind = choose_option(
            "What do you want to send? ",
            ["Text message", "Function: assign runway to an airplane"],
        )
        if kind == 1:
            assign_runway_function(db)
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

    # Everything shown has now been seen, so clear the unread counter.
    db.receive_messages(
        team, unread_only=True, limit=MAX_MESSAGES, mark_as_read=True
    )


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
            print("q. Quit")

            choice = input("Select an option: ").strip().lower()

            if choice == "1":
                send_messages(db, team)
            elif choice == "2":
                read_messages(db, team)
            elif choice in ("q", "quit"):
                break
            else:
                print("Invalid option. Please select 1, 2, or q.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
