from src.db import Team, create_database

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


def send_messages(db, team):
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
        print(f"Message: {message.content}")
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
