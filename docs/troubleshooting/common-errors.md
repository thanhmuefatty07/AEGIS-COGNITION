# Common errors

## Missing provider credentials

Run `aegis init` or set the provider-specific environment variable. A missing
credential is a configuration failure, not a reason to silently use another
provider.

## Browser session or action missing

Enable the browser capability, provide a launcher/session and configure an
HTTPS allowlist. The Lab records a blocker when the requested browser
capability cannot be admitted.

## Rate limit or timeout

Provider retry/fallback is bounded by the configured budget. A retry creates a
new execution identity and remains visible in the replay ledger; permanent or
unapproved effects are not retried indefinitely.

## Simulation is not a measurement

Simulation output remains `SIMULATED` or `INFERRED` unless an independently
validated calibration and replication record exists. Hardware-energy claims
require instrument evidence and are never inferred from a scalar simulation.

For the full blocker and evidence policy, read the
[`AEGIS Lab master plan`](../architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md).

