# Fuzz campaign evidence

The targets cover the current public trust boundaries:

- `resource_contract`: JSON resource request parsing and validation;
- `protocol_frame`: message framing and identity validation;
- `runtime_ffi_contract`: runtime/FFI JSON request boundary;
- `archive_prefix`: binary archive header/partial-prefix recovery.

The deep workflow runs each target for the configured campaign duration and
uploads the corpus/artifact directory. A target build or a short smoke is not
equivalent to a retained campaign. Each retained report must include target,
command, commit, seed, duration, executions, corpus, crashes, and sanitizer
result. Reproducible crash or UB means the evidence lane fails.
