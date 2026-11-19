"""Shared illustrative bounds and ordering used by Tower occupancy consumers."""
import pytest
from src.shared.geometry import GEOMETRY, LATITUDE, LONGITUDE, classify, natural_key


def test_natural_runway_order_is_numeric_with_deterministic_leading_zero_ties():
    assert sorted(['R11', 'R2', 'R10', 'R01', 'R1'], key=natural_key) == [
        'R1', 'R01', 'R2', 'R10', 'R11']


@pytest.mark.parametrize('latitude,longitude,expected', [
    (LATITUDE, LONGITUDE, 'inside'),
    (GEOMETRY['north'], GEOMETRY['east'], 'inside'),
    (GEOMETRY['south'], GEOMETRY['west'], 'inside'),
    (GEOMETRY['north'] + .000001, LONGITUDE, 'outside'),
    (GEOMETRY['south'] - .000001, LONGITUDE, 'outside'),
    (LATITUDE, GEOMETRY['east'] + .000001, 'outside'),
    (LATITUDE, GEOMETRY['west'] - .000001, 'outside'),
])
def test_illustrative_boundary_contract_is_inclusive(latitude, longitude, expected):
    assert classify(latitude, longitude) == expected
