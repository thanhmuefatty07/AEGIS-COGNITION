# ADR-003: Free-threaded Python is an optimization lane

Status: Accepted

Free-threaded CPython may reduce Python glue contention, but native dependency safety,
GIL re-enabling, and race testing are not universal guarantees. It is therefore an
explicit compatibility/performance lane, never a correctness dependency or a second
scheduler authority.
