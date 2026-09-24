from src.command.availability import is_command_available


def test_command_is_available():
    # REQ-CMD-002
    assert is_command_available() is True
