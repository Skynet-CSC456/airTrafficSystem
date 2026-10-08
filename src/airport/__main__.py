"""REQ-APT-001: Small command line process for airport records and messages."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime

from src.airport.messaging import receive_messages, send_message
from src.db import Airport, create_database


def _json(value: object) -> None:
    print(json.dumps(value, default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item)))


def _record(value: object) -> object:
    record = asdict(value)
    if isinstance(value, Airport):
        record["number_of_runways"] = value.number_of_runways
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Airport records and ATC messages")
    parser.add_argument("--database", help="SQLite path (defaults to SQLITE_DATABASE or data/atc_system.db)")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create")
    update = commands.add_parser("update")
    for command in (create, update):
        if command is update:
            command.add_argument("airport_id", type=int)
        command.add_argument("perimeter", type=float, help="Perimeter in meters")
        command.add_argument("longitude", type=float)
        command.add_argument("latitude", type=float)

    commands.add_parser("list")
    for name in ("get", "delete"):
        commands.add_parser(name).add_argument("airport_id", type=int)

    add_runway = commands.add_parser("add-runway")
    status = commands.add_parser("status")
    for command in (add_runway, status):
        command.add_argument("airport_id", type=int)
        command.add_argument("runway_id")
        command.add_argument("availability", choices=("available", "unavailable"))
        command.add_argument("--time", help="ISO 8601 status timestamp; defaults to current UTC")
    remove_runway = commands.add_parser("remove-runway")
    remove_runway.add_argument("airport_id", type=int)
    remove_runway.add_argument("runway_id")

    send = commands.add_parser("send")
    send.add_argument("recipient", choices=("radar", "command"))
    send.add_argument("content")
    receive = commands.add_parser("receive")
    receive.add_argument("--unread", action="store_true")
    receive.add_argument("--mark-read", action="store_true")

    args = parser.parse_args()
    db = create_database(args.database)
    try:
        if args.command == "create":
            result = db.create_airport(args.perimeter, args.longitude, args.latitude)
        elif args.command == "get":
            result = db.get_airport(args.airport_id)
        elif args.command == "list":
            result = db.list_airports()
        elif args.command == "update":
            result = db.update_airport(args.airport_id, args.perimeter, args.longitude, args.latitude)
        elif args.command == "delete":
            result = db.delete_airport(args.airport_id)
        elif args.command == "add-runway":
            result = db.create_runway(
                args.airport_id, args.runway_id, args.availability == "available", args.time
            )
        elif args.command == "status":
            result = db.update_runway_status(
                args.airport_id, args.runway_id, args.availability == "available", args.time
            )
        elif args.command == "remove-runway":
            result = db.delete_runway(args.airport_id, args.runway_id)
        elif args.command == "send":
            result = {"message_id": send_message(db, args.recipient, args.content)}
        else:
            result = receive_messages(db, unread_only=args.unread, mark_as_read=args.mark_read)
        if isinstance(result, list):
            result = [_record(item) for item in result]
        elif hasattr(result, "__dataclass_fields__"):
            result = _record(result)
        _json(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
