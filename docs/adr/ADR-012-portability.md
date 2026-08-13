# ADR-012: CPU baseline and capability honesty

Status: Accepted

Portable artifacts use a conservative CPU baseline. Runtime feature detection may
select measured hot kernels; the general binary is never compiled with `target-cpu=native`.
Unsupported OS enforcement is surfaced as unsupported or measurement-only.
