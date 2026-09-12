# /aegis-regression-check

Check the current change set for backslides, repeated bugs, or weakened invariants.

## Steps
1. Identify the behavior that must remain unchanged.
2. Compare current implementation against guarded invariants.
3. Find missing coverage or repeated failure paths.
4. Report the smallest reinforcement needed.
5. Add or strengthen tests before proceeding.
