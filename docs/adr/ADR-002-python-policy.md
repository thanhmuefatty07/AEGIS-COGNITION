# ADR-002: Python support policy

Status: Accepted

CPython 3.14 is the production default. CPython 3.15 is a forward-compatibility lane
until a final release and the promotion criteria in the master plan pass. Python
metadata, CI, and packaging must express one consistent supported range.

Free-threaded builds are experimental and do not change runtime authority.
