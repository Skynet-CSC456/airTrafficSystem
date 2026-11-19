# REQ-TWR-011 · R2 classroom separation

Session 10, slides 30–31 and 34–35. Detect unsafe separation from real Radar tracks.
The classroom separation threshold is 10 degrees in longitude or latitude.
Emit Unsafe Separation and Unsafe Separation Resolved to Command, at severity 1.
The slides also call for an instruction with target coordinates to Radar and for
Radar to report resolution.

The detector interprets at least ten degrees on either axis as safe, uses the
shorter longitude arc, and compares current airborne tracks. A track-age limit
can be supplied by the classroom operator. The slides give no vertical
threshold, maximum track age, or maneuver selection rule. Tower tries deterministic signed ten-degree shifts on both axes and wraps
longitude at the antimeridian. It rejects targets outside the latitude range or
targets that conflict with observed traffic or another pending target. A
correlated Radar instruction includes the timestamp of the observation used.
A Radar client should discard the instruction if that observation has since
changed. If there is no eligible target, the Command alert remains active.
Tower never writes aircraft coordinates. Only a safe Radar observation can
clear a separation alert, including after a Radar acknowledgment.

Implementation: `src/tower/r2.py`. Tests: `tests/tower/test_r2.py`.
