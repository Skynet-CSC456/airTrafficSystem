# REQ-RAD-001

from src.db import Message, MessageDatabase, Team


VALID_RUNWAY_REQUESTS = {
    "landing",
    "departing"
}


def request_runway(
    db: MessageDatabase,
    aircraft_id: str,
    request_type: str
) -> int:

    request_type = request_type.strip().lower()

    if not aircraft_id or not aircraft_id.strip():
        raise ValueError("Aircraft ID cannot be empty")

    if request_type not in VALID_RUNWAY_REQUESTS:
        raise ValueError(
            "Runway request must be landing or departing"
        )

    content = (
        f"Aircraft {aircraft_id} requests runway "
        f"for {request_type}"
    )

    return db.send_message(
        Team.RADAR,
        Team.TOWER,
        content,
        message_type="runway_request",
        metadata={
            "aircraft_id": aircraft_id,
            "request_type": request_type
        }
    )

from src.db import Message, MessageDatabase, Team


def receive_tower_responses(
    db: MessageDatabase,
    *,
    unread_only=False,
    limit=100,
    mark_as_read=False
) -> list[Message]:

    messages = db.receive_messages(
        Team.RADAR,
        unread_only=unread_only,
        limit=limit,
        mark_as_read=mark_as_read
    )

    return [
        message
        for message in messages
        if message.sender == Team.TOWER
        and message.message_type == "runway_response"
    ]

def process_runway_response(store, message: Message):

    if message.sender != Team.TOWER:
        raise ValueError(
            "Runway response must come from Tower"
        )

    if message.recipient != Team.RADAR:
        raise ValueError(
            "Runway response must be addressed to Radar"
        )

    if message.message_type != "runway_response":
        raise ValueError(
            "Message is not a runway response"
        )

    if not message.metadata:
        raise ValueError(
            "Runway response is missing metadata"
        )

    aircraft_id = message.metadata.get(
        "aircraft_id"
    )

    response = message.metadata.get(
        "response"
    )

    if not aircraft_id:
        raise ValueError(
            "Runway response is missing aircraft ID"
        )

    # Runway successfully assigned
    if response == "assigned":

        runway_id = message.metadata.get(
            "runway_id"
        )

        if not runway_id:
            raise ValueError(
                "Assigned runway response is missing runway ID"
            )

        return store.assign_runway(
            aircraft_id,
            runway_id
        )

    # No runway available
    elif response == "unavailable":

        return store.assign_runway(
            aircraft_id,
            None
        )

    else:
        raise ValueError(
            "Unknown runway response"
        )

def test_receive_assigned_runway(db):
    store = AircraftStateStore(db)

    store.create_aircraft_state(
        aircraft_id="AAL123",
        latitude=40.6413,
        longitude=-73.7781,
        status="landing",
        altitude=1500,
        timestamp="2026-10-22 13:30:00"
    )

    # Simulate Tower sending Radar a response
    db.send_message(
        Team.TOWER,
        Team.RADAR,
        "Runway RWY 1 assigned to AAL123",
        message_type="runway_response",
        metadata={
            "aircraft_id": "AAL123",
            "response": "assigned",
            "runway_id": "RWY 1"
        }
    )

    messages = receive_tower_responses(db)

    assert len(messages) == 1

    aircraft = process_runway_response(
        store,
        messages[0]
    )

    assert aircraft.aircraft_id == "AAL123"
    assert aircraft.runway_id == "RWY 1"


def test_receive_no_runway_available(db):
    store = AircraftStateStore(db)

    store.create_aircraft_state(
        aircraft_id="AAL123",
        latitude=40.6413,
        longitude=-73.7781,
        status="landing",
        altitude=1500,
        timestamp="2026-10-22 13:30:00"
    )

    db.send_message(
        Team.TOWER,
        Team.RADAR,
        "No runway available for AAL123",
        message_type="runway_response",
        metadata={
            "aircraft_id": "AAL123",
            "response": "unavailable"
        }
    )

    messages = receive_tower_responses(db)

    assert len(messages) == 1

    aircraft = process_runway_response(
        store,
        messages[0]
    )

    assert aircraft.aircraft_id == "AAL123"
    assert aircraft.runway_id is None