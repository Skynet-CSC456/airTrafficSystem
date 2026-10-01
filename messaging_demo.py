from src.db import Team, create_database


def choose_team(prompt):
    while True:
        team = input(prompt).strip().lower()

        if team == "radar":
            return Team.RADAR
        elif team == "tower":
            return Team.TOWER
        elif team == "command":
            return Team.COMMAND
        else:
            print("Invalid team. Choose radar, tower, or command.")


def main():
    db = create_database()

    try:
        print("=== ATC Messaging Demo ===")
        print("1. Send Message")
        print("2. Receive Messages")

        choice = input("Select an option (1 or 2): ").strip()

        # -------------------------
        # SEND MESSAGE
        # -------------------------
        if choice == "1":
            sender = choose_team(
                "What team are you? (radar/tower/command): "
            )

            recipient = choose_team(
                "Who do you want to send the message to? "
                "(radar/tower/command): "
            )

            message = input("Enter your message: ")

            try:
                message_id = db.send_message(
                    sender,
                    recipient,
                    message
                )

                print("\nMessage sent successfully!")
                print(f"Message ID: {message_id}")
                print(f"From: {sender.value}")
                print(f"To: {recipient.value}")
                print(f"Message: {message}")

            except ValueError as error:
                print(f"\nCould not send message: {error}")

        # -------------------------
        # RECEIVE MESSAGES
        # -------------------------
        elif choice == "2":
            team = choose_team(
                "What team are you? (radar/tower/command): "
            )

            messages = db.receive_messages(team)

            print(f"\n=== {team.value.upper()} INBOX ===")

            if not messages:
                print("No messages.")
            else:
                for message in messages:
                    print("\n--------------------------")
                    print(f"Message ID: {message.id}")
                    print(f"From: {message.sender.value}")
                    print(f"Message: {message.content}")
                    print(f"Sent: {message.created_at}")

        else:
            print("Invalid option. Please select 1 or 2.")

    finally:
        db.close()


if __name__ == "__main__":
    main()