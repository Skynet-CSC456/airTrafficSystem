REQ-RAD-001

**Owning Team:** Radar

## Intent:

Radar subsystem will ingest and update aircraft state to maintain most recent information. 

## Aircraft State FIelds:

- ID
- Current Location 
- Status: Parked, Departing, Landing
- Altitude 
- Timestamp ( yyyy-mm-dd H:M:S )

## Acceptance Criteria: 

- Radar can create a new aircraft state. 
- Each aircraft has a unique ID. 
- If an aircraft ID already exists, state updates should modify existing aircraft data rather than create a new object.
- Latitutude and longitude are saved as aircrafts current position.
- Altitude is saved as aircrafts current distance from the ground.
- Status must be either parked, departing, or landing.
- Timestamp is stored with every state update. 
