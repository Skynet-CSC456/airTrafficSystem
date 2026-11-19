"""Classroom traffic sequencing rules from Session 10, slides 30 and 32."""

from dataclasses import replace
from datetime import datetime, timezone

DELAY_ADVANCES = {'delay_2h': 1, 'delay_4h': 2, 'low_fuel': 3}
SEPARATION_DEGREES = 10
RUNWAY_DELAY_SECONDS = 30


def advance_in_queue(request_ids, request_id, positions):
    """Move only the named request, preserving the other requests' relative order."""
    ordered = list(request_ids)
    index = ordered.index(request_id)
    ordered.pop(index)
    ordered.insert(max(0, index - positions), request_id)
    return ordered


def separation(left, right):
    """Return the two angular differences; longitude wraps at the antimeridian."""
    latitude = abs(left.latitude - right.latitude)
    longitude = abs((left.longitude - right.longitude + 180) % 360 - 180)
    return latitude, longitude


def unsafe_separation(left, right):
    """The classroom rule is satisfied when either axis is at least ten degrees."""
    return max(separation(left, right)) < SEPARATION_DEGREES


def current_airborne(aircraft, now, max_track_age_seconds):
    if aircraft is None or aircraft.altitude <= 0 or aircraft.status == 'Parked':
        return False
    observed = datetime.fromisoformat(aircraft.timestamp).replace(tzinfo=timezone.utc).timestamp()
    age = now - observed
    return max_track_age_seconds is None or 0 <= age <= max_track_age_seconds


def corrective_target(moving, traffic):
    """Select a legal signed shift that leaves the target clear of observed traffic."""
    for latitude_step in (SEPARATION_DEGREES, -SEPARATION_DEGREES):
        latitude = moving.latitude + latitude_step
        if not -90 <= latitude <= 90:
            continue
        for longitude_step in (SEPARATION_DEGREES, -SEPARATION_DEGREES):
            longitude = (moving.longitude + longitude_step + 180) % 360 - 180
            candidate = replace(moving, latitude=latitude, longitude=longitude)
            if all(other.id == moving.id or not unsafe_separation(candidate, other) for other in traffic):
                return {'latitude': latitude, 'longitude': longitude,
                        'latitude_step': latitude_step, 'longitude_step': longitude_step}
    return None
