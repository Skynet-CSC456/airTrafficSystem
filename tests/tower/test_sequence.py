from src.tower.sequence import REQUIREMENT_ID, sequence_arrivals


def test_tower_sequence_requirement_id_and_behavior():
    assert REQUIREMENT_ID == "REQ-TWR-001"
    assert sequence_arrivals([5, 1, 3]) == [1, 3, 5]
