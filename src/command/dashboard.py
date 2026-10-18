import tkinter as tk
from src.db import Team, create_database
# Main window
root = tk.Tk()
db = create_database()
root.title("ATC Command Center")
root.geometry("1100x700")
root.configure(bg="#101827")

# Header
header = tk.Frame(root, bg="#1E293B", height=75)
header.pack(fill="x")

title = tk.Label(
    header,
    text="AIR TRAFFIC CONTROL | COMMAND CENTER",
    font=("Arial", 20, "bold"),
    fg="white",
    bg="#1E293B"
)
title.pack(pady=20)

# Main dashboard
main = tk.Frame(root, bg="#101827")
main.pack(fill="both", expand=True, padx=20, pady=20)

# Airport map
airport_frame = tk.LabelFrame(
    main,
    text=" AIRPORT MAP ",
    font=("Arial", 13, "bold"),
    fg="white",
    bg="#1E293B"
)
airport_frame.pack(side="left", fill="both", expand=True, padx=(0, 15))

canvas = tk.Canvas(
    airport_frame,
    bg="#142536",
    highlightthickness=0
)
canvas.pack(fill="both", expand=True, padx=15, pady=15)

# Right panel
right_panel = tk.Frame(main, bg="#101827", width=320)
right_panel.pack(side="right", fill="y")
right_panel.pack_propagate(False)

for section in ["RUNWAY STATUS", "AIRCRAFT", "TOWER MESSAGES"]:
    frame = tk.LabelFrame(
        right_panel,
        text=f" {section} ",
        font=("Arial", 12, "bold"),
        fg="white",
        bg="#1E293B"
    )
    frame.pack(fill="both", expand=True, pady=5)

    label = tk.Label(
        frame,
        text="Waiting for data...",
        fg="#94A3B8",
        bg="#1E293B"
    )
    label.pack(pady=20)

# ----------------------------------
# AIRPORT MAP AND RUNWAY STATUS
# ----------------------------------

runways = [
    {"id": "RWY 1", "available": True},
    {"id": "RWY 2", "available": False},
    {"id": "RWY 3", "available": True}
]

runway_status_frame = right_panel.winfo_children()[0]

# Remove the temporary "Waiting for data..." label
for widget in runway_status_frame.winfo_children():
    widget.destroy()


def update_runway_status():
    """Refresh the runway status panel."""
    for widget in runway_status_frame.winfo_children():
        widget.destroy()

    for runway in runways:
        status = "AVAILABLE" if runway["available"] else "UNAVAILABLE"
        color = "#22C55E" if runway["available"] else "#EF4444"

        row = tk.Frame(runway_status_frame, bg="#1E293B")
        row.pack(fill="x", padx=15, pady=10)

        tk.Label(
            row,
            text=runway["id"],
            fg="white",
            bg="#1E293B",
            font=("Arial", 12, "bold")
        ).pack(side="left")

        tk.Label(
            row,
            text=status,
            fg=color,
            bg="#1E293B",
            font=("Arial", 11, "bold")
        ).pack(side="right")


def toggle_runway(index):
    """Change runway availability when clicked."""
    runways[index]["available"] = not runways[index]["available"]
    draw_airport()
    update_runway_status()


def draw_airport(event=None):
    """Draw the airport and its runways."""
    canvas.delete("all")

    width = canvas.winfo_width()
    height = canvas.winfo_height()

    # Airport perimeter
    margin = 45
    canvas.create_rectangle(
        margin, margin,
        width - margin, height - margin,
        outline="#94A3B8",
        width=2,
        dash=(6, 4)
    )

    runway_width = 65
    runway_height = min(350, height - 160)
    center_x = width / 2
    center_y = height / 2

    positions = [
        center_x - 170,
        center_x,
        center_x + 170
    ]

    for i, runway in enumerate(runways):
        x = positions[i]
        y = center_y

        color = "#22C55E" if runway["available"] else "#EF4444"

        x1 = x - runway_width / 2
        y1 = y - runway_height / 2
        x2 = x + runway_width / 2
        y2 = y + runway_height / 2

        tag = f"runway_{i}"

        canvas.create_rectangle(
            x1, y1, x2, y2,
            fill="#243447",
            outline=color,
            width=3,
            tags=tag
        )

        # Runway centerline
        canvas.create_line(
            x, y1 + 15,
            x, y2 - 15,
            fill="#CBD5E1",
            width=2,
            dash=(10, 10),
            tags=tag
        )

        # Runway label
        canvas.create_text(
            x, y2 + 22,
            text=runway["id"],
            fill="white",
            font=("Arial", 12, "bold")
        )

        canvas.tag_bind(
            tag,
            "<Button-1>",
            lambda event, index=i: toggle_runway(index)
        )


# Redraw the airport when the window is resized
canvas.bind("<Configure>", draw_airport)

# Display initial runway statuses
update_runway_status()


# ----------------------------------
# AIRCRAFT INFORMATION PANEL
# ----------------------------------

from tkinter import ttk

aircraft_frame = right_panel.winfo_children()[1]

# Remove the temporary label
for widget in aircraft_frame.winfo_children():
    widget.destroy()

# Sample aircraft data for UI testing
aircraft_data = [
    ("AC001", "Approaching", "1500 m", "RWY 1"),
    ("AC002", "Waiting", "0 m", "RWY 2"),
    ("AC003", "Departing", "500 m", "RWY 3")
]

# Table columns
columns = ("Aircraft", "Status", "Altitude", "Runway")

aircraft_table = ttk.Treeview(
    aircraft_frame,
    columns=columns,
    show="headings",
    height=5
)

# Column headings
for column in columns:
    aircraft_table.heading(column, text=column)
    aircraft_table.column(column, width=85, anchor="center")

# Add aircraft records
for aircraft in aircraft_data:
    aircraft_table.insert("", "end", values=aircraft)

# Table styling
style = ttk.Style()
style.theme_use("clam")

style.configure(
    "Aircraft.Treeview",
    background="#1E293B",
    foreground="white",
    fieldbackground="#1E293B",
    rowheight=32,
    font=("Arial", 10)
)

style.configure(
    "Aircraft.Treeview.Heading",
    background="#334155",
    foreground="white",
    font=("Arial", 10, "bold")
)

aircraft_table.configure(style="Aircraft.Treeview")

aircraft_table.pack(fill="both", expand=True, padx=10, pady=10)



# ----------------------------------
# LIVE TOWER MESSAGES
# ----------------------------------
# REQ-CMD-001: Receive and display messages from Tower.
messages_frame = right_panel.winfo_children()[2]

for widget in messages_frame.winfo_children():
    widget.destroy()

message_list = tk.Listbox(
    messages_frame,
    bg="#1E293B",
    fg="white",
    font=("Arial", 10),
    selectbackground="#334155",
    borderwidth=0
)
message_list.pack(fill="both", expand=True, padx=10, pady=10)


def refresh_messages():
    """Check the database for messages sent to Command."""

    try:
        messages = db.receive_messages(Team.COMMAND, limit=100)

        message_list.delete(0, tk.END)

        for message in messages:
            message_list.insert(
                tk.END,
                f"{message.sender.value.upper()}: {message.content}"
            )

    except Exception as error:
        print("Database error:", error)

    root.after(2000, refresh_messages)


refresh_messages()
root.mainloop()