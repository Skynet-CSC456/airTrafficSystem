# REQ-TWR-004: AirportUI implementation
import tkinter as tk


def create_runways():
    return [
        {
            "id": "RWY 1",
            "coords": (80, 80, 130, 320),
            "available": True,
            "rect_id": None,
            "text_id": None,
        },
        {
            "id": "RWY 2",
            "coords": (150, 180, 250, 220),
            "available": False,
            "rect_id": None,
            "text_id": None,
        },
        {
            "id": "RWY 3",
            "coords": (270, 80, 320, 320),
            "available": True,
            "rect_id": None,
            "text_id": None,
        },
    ]


def update_runway_visuals(canvas, rwy):
    """Updates color and text."""
    color = "green" if rwy["available"] else "red"
    status_str = "Available" if rwy["available"] else "Not Available"
    canvas.itemconfig(rwy["rect_id"], outline=color)
    canvas.itemconfig(
        rwy["text_id"], text=f"{rwy['id']}\n[{status_str}]", fill=color
    )


def toggle_runway(canvas, rwy):
    """Toggles status."""
    rwy["available"] = not rwy["available"]
    update_runway_visuals(canvas, rwy)
    print(f"{rwy['id']} is now {'Available' if rwy['available'] else 'Not Available'}")


def create_airport_ui():
    root = tk.Tk()
    root.title("Airport")

    canvas = tk.Canvas(root, width=400, height=400)
    canvas.pack()

    # Perimeter Box
    canvas.create_rectangle(50, 50, 350, 350, width=1)

    runways = create_runways()
    for rwy in runways:
        x1, y1, x2, y2 = rwy["coords"]
        mid_x, mid_y = (x1 + x2) / 2, (y1 + y2) / 2
        rect_id = canvas.create_rectangle(x1, y1, x2, y2, width=3)
        text_id = canvas.create_text(mid_x, mid_y, justify="center")

        rwy["rect_id"] = rect_id
        rwy["text_id"] = text_id

        update_runway_visuals(canvas, rwy)
        canvas.tag_bind(rect_id, "<Button-1>", lambda event, r=rwy: toggle_runway(canvas, r))
        canvas.tag_bind(text_id, "<Button-1>", lambda event, r=rwy: toggle_runway(canvas, r))

    return root


if __name__ == "__main__":
    create_airport_ui().mainloop()
