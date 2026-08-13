# ADR-007: Normalized hardware profile

Status: Accepted

Hardware is represented by one Rust-owned `HardwareProfile` with CPU features, memory
domains, accelerators, storage, OS enforcement capabilities, and a profile epoch.
Unified memory is a domain kind rather than a second independent RAM/VRAM pool.
