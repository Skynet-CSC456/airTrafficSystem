"""REQ-TWR-008: persistent Tower requests, FIFO queue and explicit completion.

The only operational allocator. One DB connection per worker; all effects and
outgoing messages commit together. Radar remains the aircraft-state owner.
"""
from __future__ import annotations

from src.radar.aircraft import AircraftStore
from src.tower._request_protocol import publish_decision
from src.tower._request_store import RequestStore
from src.tower.airport import AirportDatabase, current_timestamp, _identifier
from src.tower.clearance import find_safe_runway


class TowerService:
    """Public synchronous boundary for Radar, Command and future HTTP adapters."""

    def __init__(self, db):
        if db.connection.in_transaction:
            raise RuntimeError("Construct TowerService before opening a transaction")
        self._db = db
        self.airports = AirportDatabase(db)
        self._aircraft = AircraftStore(db)
        self._requests = RequestStore(db.connection)

    def get_decision(self, request_id):
        return self._requests.decision(request_id)

    def request_runway(self, request_id, aircraft_id, airport_id, operation, *, runway_id=None):
        """Submit once; replay returns latest decision without repeating side effects.

        Unknown resources/invalid flight states return a persisted rejected decision.
        Invalid identifiers and request-ID collisions raise ValueError.
        """
        return self._submit(request_id, aircraft_id, airport_id, operation, runway_id=runway_id)

    def _submit(self, request_id, aircraft_id, airport_id, operation, *, runway_id=None,
                legacy=False, reject_if_busy=False):
        for value, label in ((request_id,"Request ID"), (aircraft_id,"Aircraft ID"), (airport_id,"Airport ID")):
            _identifier(value, label)
        if runway_id is not None:
            _identifier(runway_id, "Runway ID")
        if not isinstance(operation, str):
            raise ValueError("Operation must be landing or departure")
        with self._db.transaction():
            old = self._requests.get(request_id)
            if old:
                signature = (old["aircraft_id"],old["airport_id"],old["operation"],old["preferred_runway"],bool(old["legacy"]))
                if signature != (aircraft_id,airport_id,operation,runway_id,legacy):
                    raise ValueError("request_id already belongs to a different request")
                return self.get_decision(request_id)
            # Validate before insert so the unique active-aircraft index stays a safety net.
            reason = self._validation_error(aircraft_id, airport_id, operation, runway_id)
            active_request = self._requests.active_request_id(aircraft_id)
            if active_request is not None:
                reason = f"Aircraft already has active request {active_request}"
            row = self._requests.insert(
                request_id, aircraft_id, airport_id, operation, runway_id,
                outcome="rejected" if reason else "queued", timestamp=current_timestamp(), legacy=legacy,
            )
            if reason:
                return self._save(row, "rejected", reason=reason)
            self._promote(airport_id)
            result = self.get_decision(request_id)
            if reject_if_busy and result["outcome"] != "cleared":
                raise ValueError("No safe runway is available for the current aircraft state")
            return result

    def _validation_error(self, aircraft_id, airport_id, operation, runway_id):
        if operation not in ("landing", "departure"):
            return "Operation must be landing or departure"
        if self.airports.get_airport(airport_id) is None:
            return "Unknown airport"
        aircraft = self._aircraft.get(aircraft_id)
        if aircraft is None:
            return "Unknown aircraft"
        if operation == "landing" and (aircraft.altitude <= 0 or aircraft.status == "Parked"):
            return "Landing requires an airborne aircraft"
        if operation == "departure" and (aircraft.altitude != 0 or aircraft.status != "Parked"):
            return "Departure requires a grounded Parked aircraft"
        runways = self.airports.list_runways(airport_id)
        if runway_id is not None:
            runways = [r for r in runways if r["id"] == runway_id]
            if not runways:
                return "Unknown runway"
        # Check compatibility independently of occupancy/administrative closure.
        if not find_safe_runway([{**r, "available": True} for r in runways],
                                {"status": "Landing" if operation == "landing" else "Departing"}):
            return "No compatible runway"
        return None

    def _save(self, row, outcome, *, runway_id=None, reason=None):
        position = None
        if outcome == "queued":
            position = self._requests.queue_position(row["airport_id"], row["sequence"])
            previous = self.get_decision(row["request_id"])
            if previous and previous["outcome"] == "queued" and previous["queue_position"] == position:
                return previous
        response = {"schema_version":1,"request_id":row["request_id"],
                    "aircraft_id":row["aircraft_id"],"airport_id":row["airport_id"],
                    "operation":row["operation"],"outcome":outcome,"status":outcome,
                    "runway_id":runway_id,"queue_position":position,"reason":reason,
                    "timestamp":current_timestamp(),"safe":outcome=="cleared","occupied_until":None}
        self._requests.set_outcome(row["request_id"], outcome, runway_id)
        response["message_ids"] = publish_decision(self._db, response, legacy=row["legacy"])
        self._requests.save_response(row["request_id"], response)
        self._db.upsert_shared_state(f"tower_request:{row['request_id']}", response, category="tower_request")
        active = self._requests.active_request_id(row["aircraft_id"])
        if active is None or active == row["request_id"]:
            self._db.upsert_shared_state(f"decision:{row['aircraft_id']}", response, category="decision")
        return response

    def _promote(self, airport_id):
        # Strict FIFO per airport; an unavailable preferred runway blocks followers.
        rows = self._requests.queued_rows(airport_id)
        blocked = False
        for row in rows:
            reason = self._validation_error(row["aircraft_id"], airport_id, row["operation"], row["preferred_runway"])
            if reason:
                self._save(row,"rejected",reason=reason)
                continue
            runways = self.airports.list_runways(airport_id)
            if row["preferred_runway"] is not None:
                runways = [r for r in runways if r["id"] == row["preferred_runway"]]
            runway = None if blocked else find_safe_runway(runways,
                {"status":"Landing" if row["operation"] == "landing" else "Departing"})
            if runway:
                self._save(row,"cleared",runway_id=runway["id"])
            else:
                blocked = True
                self._save(row,"queued")
        self.airports._project(airport_id)

    def promote_queue(self, airport_id):
        """Recheck FIFO after administrative changes or Radar flight-state changes."""
        with self._db.transaction():
            if self.airports.get_airport(airport_id) is None:
                raise ValueError("Unknown airport")
            self._promote(airport_id)
            return self.list_queue(airport_id)

    def complete_request(self, request_id):
        """Release the owned reservation and promote FIFO, atomically and idempotently."""
        with self._db.transaction():
            row = self._requests.get(request_id)
            if row is None:
                raise KeyError(request_id)
            if row["outcome"] == "completed":
                return self.get_decision(request_id)
            if row["outcome"] != "cleared":
                raise ValueError("Only a cleared request can complete")
            result = self._save(row,"completed",runway_id=row["runway_id"])
            self._promote(row["airport_id"])
            return result

    def cancel_request(self, request_id, *, reason="Cancelled by operator"):
        """Cancel a queued/cleared request safely; release and promote atomically."""
        _identifier(reason, "Cancellation reason")
        with self._db.transaction():
            row = self._requests.get(request_id)
            if row is None:
                raise KeyError(request_id)
            if row["outcome"] in ("rejected", "completed"):
                return self.get_decision(request_id)
            result = self._save(row, "rejected", reason=reason)
            self._promote(row["airport_id"])
            return result

    def release_runway(self, airport_id, runway_id, request_id):
        """Release requires the matching request ID; never releases another owner."""
        with self._db.transaction():
            decision = self.get_decision(request_id)
            if decision is None or (decision["airport_id"], decision["runway_id"]) != (airport_id,runway_id):
                raise ValueError("Runway reservation does not belong to this request")
            return self.complete_request(request_id)

    def list_queue(self, airport_id):
        with self._db.transaction():
            return self._requests.queued_decisions(airport_id)

    def snapshot(self):
        """Versioned read model; Command must render operation/outcome explicitly."""
        with self._db.transaction():
            return {"schema_version":1,"airports":self.airports.list_airports(),
                    "requests":self._requests.decisions()}
