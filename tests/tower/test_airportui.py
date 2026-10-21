# REQ-CMD-004: AirportUI test
from src.tower.airportui import create_runways, toggle_runway, update_runway_visuals


class FakeCanvas:
    def __init__(self):
        self.configured = []

    def itemconfig(self, item_id, **kwargs):
        self.configured.append((item_id, kwargs))


def test_create_runways_preserves_initial_statuses():
    runways = create_runways()

    assert [runway["id"] for runway in runways] == ["RWY 1", "RWY 2", "RWY 3"]
    assert [runway["available"] for runway in runways] == [True, False, True]


def test_update_runway_visuals_uses_available_color_and_text():
    canvas = FakeCanvas()
    runway = create_runways()[0]
    runway["rect_id"] = "rectangle"
    runway["text_id"] = "text"

    update_runway_visuals(canvas, runway)

    assert canvas.configured == [
        ("rectangle", {"outline": "green"}),
        ("text", {"text": "RWY 1\n[Available]", "fill": "green"}),
    ]


def test_toggle_runway_changes_status_and_visuals():
    canvas = FakeCanvas()
    runway = create_runways()[1]
    runway["rect_id"] = "rectangle"
    runway["text_id"] = "text"

    toggle_runway(canvas, runway)

    assert runway["available"] is True
    assert canvas.configured[-2:] == [
        ("rectangle", {"outline": "green"}),
        ("text", {"text": "RWY 2\n[Available]", "fill": "green"}),
    ]
