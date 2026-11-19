"""Shared fixtures for Radar tests that talk to main's real Tower R2 service (REQ-TWR-007 to REQ-TWR-012)."""

from datetime import datetime, timezone

import pytest

from src.db import Team, create_database
from src.tower.r2 import TowerR2Service
from src.tower.r2_worker import run_cycle

AIRPORT = 'JFK'


class Tower:
    """Tower's R2 service with a clock the test controls, plus its worker cycle."""

    def __init__(self, db, runways):
        self.now = datetime.now(timezone.utc).timestamp()
        self.service = TowerR2Service(db, clock=lambda: self.now)
        self.service.airports.create_airport(AIRPORT, 22000, 40.6413, -73.7781, list(runways))
        self.db = db

    def cycle(self):
        """One Tower worker pass: requests, Radar's R2 events, timed releases, separation."""
        return run_cycle(self.db, self.service)

    def advance(self, seconds):
        self.now += seconds

    def queue(self):
        return [item['aircraft_id'] for item in self.service.list_queue(AIRPORT)]

    def messages_to(self, team, message_type):
        return [m for m in self.db.receive_messages(team, limit=10_000) if m.message_type == message_type]


@pytest.fixture
def db():
    database = create_database(':memory:')
    yield database
    database.close()


@pytest.fixture
def tower(db):
    """Tower with one airport and a single runway, so later requests queue."""
    return Tower(db, ['RWY 1'])


@pytest.fixture
def tower2(db):
    """Tower with one airport and two runways."""
    return Tower(db, ['RWY 1', 'RWY 2'])


def radar_messages(db, message_type):
    return [m for m in db.receive_messages(Team.TOWER, limit=10_000) if m.sender == Team.RADAR and m.message_type == message_type]
