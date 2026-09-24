from src.command.dispatch import REQUIREMENT_ID, prioritize_flights


def test_command_dispatch_requirement_id_and_behavior():
    assert REQUIREMENT_ID == "REQ-CMD-001"
    flights = [
        {"flight": "A123", "priority": 2},
        {"flight": "B456", "priority": 9},
        {"flight": "C789", "priority": 5},
    ]
    assert [flight["flight"] for flight in prioritize_flights(flights)] == ["B456", "C789", "A123"]
