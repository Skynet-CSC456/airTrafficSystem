from datetime import datetime

from src.db import create_database
from src.radar.aircraft_state import AircraftStateStore


def get_aircraft_data():
    aircraft_id = input("Aircraft ID: ").strip()
    latitude = float(input("Latitude: "))
    longitude = float(input("Longitude: "))
    status = input(
        "Status (parked/departing/landing): "
    ).strip().lower()
    altitude = float(input("Altitude: "))

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return (
        aircraft_id,
        latitude,
        longitude,
        status,
        altitude,
        timestamp
    )


def display_aircraft(aircraft):
    print("\n=== Aircraft State ===")
    print(f"ID:        {aircraft.aircraft_id}")
    print(f"Latitude:  {aircraft.latitude}")
    print(f"Longitude: {aircraft.longitude}")
    print(f"Status:    {aircraft.status}")
    print(f"Altitude:  {aircraft.altitude}")
    print(f"Timestamp: {aircraft.timestamp}")


def main():
    db = create_database()
    store = AircraftStateStore(db)

    try:
        print("=== Radar Aircraft State Demo ===")
        print("1. Create Aircraft")
        print("2. Update Aircraft")
        print("3. Retrieve Aircraft")
        print("4. Exit")

        choice = input("\nSelect an option (1-4): ").strip()

        # CREATE
        if choice == "1":
            try:
                (
                    aircraft_id,
                    latitude,
                    longitude,
                    status,
                    altitude,
                    timestamp
                ) = get_aircraft_data()

                aircraft = store.create_aircraft_state(
                    aircraft_id=aircraft_id,
                    latitude=latitude,
                    longitude=longitude,
                    status=status,
                    altitude=altitude,
                    timestamp=timestamp
                )

                print("\nAircraft created successfully.")
                display_aircraft(aircraft)

            except ValueError as error:
                print(f"\nCould not create aircraft: {error}")

        # UPDATE
        elif choice == "2":
            try:
                (
                    aircraft_id,
                    latitude,
                    longitude,
                    status,
                    altitude,
                    timestamp
                ) = get_aircraft_data()

                aircraft = store.update_aircraft_state(
                    aircraft_id=aircraft_id,
                    latitude=latitude,
                    longitude=longitude,
                    status=status,
                    altitude=altitude,
                    timestamp=timestamp
                )

                print("\nAircraft updated successfully.")
                display_aircraft(aircraft)

            except ValueError as error:
                print(f"\nCould not update aircraft: {error}")

        # RETRIEVE
        elif choice == "3":
            aircraft_id = input(
                "Enter aircraft ID: "
            ).strip()

            try:
                aircraft = store.get_aircraft_state(
                    aircraft_id
                )

                display_aircraft(aircraft)

            except ValueError as error:
                print(f"\nCould not retrieve aircraft: {error}")

        elif choice == "4":
            print("Exiting.")

        else:
            print("Invalid option.")

    finally:
        db.close()


if __name__ == "__main__":
    main()

# python -m pytest tests/radar/test_aircraft_state.py -v