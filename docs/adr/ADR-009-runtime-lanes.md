# ADR-009: Separate execution lanes

Status: Accepted

I/O, CPU, Python cognition, accelerator, and untrusted work are distinct lanes with
independent limits. Tokio remains an I/O coordination runtime; CPU-heavy work must not
block its workers. Rayon or another bounded CPU pool is added only after workload
measurements justify it.
