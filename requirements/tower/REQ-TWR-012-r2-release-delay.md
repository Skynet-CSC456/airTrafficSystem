# REQ-TWR-012 · R2 release delay

Session 10, slide 36. Start the 30-second hold at assignment. The R2 worker
records the deadline once, reserves the runway until completion has been
confirmed by Radar and the deadline has elapsed, then completes the request and
promotes the next eligible waiter. The existing completion message represents
the landed or departed operation. Expiration alone does not establish that an
aircraft is clear of the runway.

Completed landings retain stationary ownership. Existing administrative closure
continues to control availability. When an R2 assignment is current, cancelling
it before the deadline is rejected so cancellation cannot bypass the hold.
Deadlines and early completion reports persist across worker restarts.

Implementation: `src/tower/r2.py`. Tests: `tests/tower/test_r2.py`.
