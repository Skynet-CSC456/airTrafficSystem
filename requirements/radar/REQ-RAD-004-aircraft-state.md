# REQ-RAD-004: Aircraft state and communication

Provide aircraft create, read, update, and delete operations in the shared SQLite database.
Store ID, decimal latitude/longitude, status (Parked, Departing, Landing), altitude in
meters above ground, and UTC timestamp formatted as YYYY-MM-DDTHH:MM:SS.
Keep only the latest state per aircraft; ignore duplicate and older updates.
Aircraft shall send and receive messages with Tower using individual aircraft IDs.
Radar and Tower remain separate components. A small demo aircraft process shall
submit state to Radar every five seconds by default (REQ-RAD-003).

The initial implementation uses local Python calls and SQLite message polling;
network transport and realistic flight simulation are outside this increment.
