# REQ-RAD-001

from dataclasses import dataclass
from datetime import datetime

VALID_STATUSES = {
    "parked",
    "departing",
    "landing"
}

def validate_status(status):
    if status not in VALID_STATUSES:
        raise ValueError(
            "Status must be parked, departing, or landing"
        )

def validate_timestamp(timestamp):
    try:
        datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise ValueError(
            "Timestamp must use format YYYY-MM-DD HH:MM:SS"
        )

def validate_location(latitude, longitude):
    if latitude < -90 or latitude > 90:
        raise ValueError(
            "Latitude must be between -90 and 90"
        )

    if longitude < -180 or longitude > 180:
        raise ValueError(
            "Longitude must be between -180 and 180"
        )

def validate_altitude(altitude):
    if altitude < 0:
        raise ValueError(
            "Altitude cannot be negative"
        )

    
@dataclass
class AircraftState:
    aircraft_id: str
    latitude: float
    longitude: float
    status: str
    altitude: float
    timestamp: str


class AircraftStateStore:
    def __init__(self, db):
        self.db = db

    def create_aircraft_state(
        self,
        aircraft_id,
        latitude,
        longitude,
        status,
        altitude,
        timestamp
    ):
        
        validate_status(status)
        validate_timestamp(timestamp)
        validate_location(latitude, longitude)
        validate_altitude(altitude)
        
        self.db.create_aircraft_state(
            aircraft_id,
            latitude,
            longitude,
            status,
            altitude,
            timestamp
        )

        return self.db.get_aircraft_state(aircraft_id)


    def update_aircraft_state(
        self,
        aircraft_id,
        latitude,
        longitude,
        status,
        altitude,
        timestamp
    ):

        validate_status(status)
        validate_timestamp(timestamp)
        validate_location(latitude, longitude)
        validate_altitude(altitude)

        self.db.update_aircraft_state(
            aircraft_id=aircraft_id,
            latitude=latitude,
            longitude=longitude,
            status=status,
            altitude=altitude,
            timestamp=timestamp
        )

        return self.db.get_aircraft_state(aircraft_id)

    def get_aircraft_state(self, aircraft_id):
        return self.db.get_aircraft_state(aircraft_id)

    def assign_runway(self, aircraft_id, runway_id
    ):
        self.db.assign_runway_to_aircraft(
            aircraft_id,
            runway_id
        )

        return self.db.get_aircraft_state(aircraft_id)