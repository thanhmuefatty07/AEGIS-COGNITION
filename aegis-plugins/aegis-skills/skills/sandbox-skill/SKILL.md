# Sandbox Skill

## Current Runtime Status

AEGIS currently provides a policy-only backend: it checks source text and stores
JSON state, but it does not execute code or provide OS-level isolation. This
skill is guidance, not a sandbox or a permission grant. Do not run Python or
Node code based on this skill or a source-validation result. Native plugin
execution remains unavailable until a real OS-isolated backend is implemented
and verified.

## Overview

Use this guidance to draft deterministic code for a future sandboxed worker.
Keep state and external capabilities explicit.

## When to Use

Use only as design guidance when preparing source for a future approved,
sandboxed worker. For live Search or Browser tasks, call the registered AEGIS
tools; do not try to execute generated Python.

## Core Concepts

The current validator performs source-text checks; its import allowlist and
blocked-token list are not a security boundary. The Rust `SandboxStateStore`
persists JSON when the host calls it, but generated Python does not
automatically receive `save_state` or `load_state` functions.

## Example

Illustrative Python source only; the current AEGIS runtime does not execute it.

```python
import json
state = {"topic": "evidence"}
```

## Best Practices

Keep future sandbox-bound code small and deterministic. Avoid host paths,
subprocesses, sockets, and dynamic execution; do not treat these guidelines as
enforcement.

## Common Pitfalls

Do not rely on REPL globals across turns or assume a skill, approval, import
allowlist, or blocked-token check isolates code from the host.
