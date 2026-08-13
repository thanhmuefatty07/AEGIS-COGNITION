# ADR-010: Wasmtime is a migration, not a version bump

Status: Accepted

The existing Wasmtime dependency requires a dedicated migration with fuel, epoch,
memory, WASI capability, replay compatibility, fuzz, and adversarial tests. Until
those checks run on the chosen patched release, sandbox claims remain scoped and not
absolute.
