# Sandbox Skill

## Overview
Sandbox code should be small, deterministic, and explicit about state.

## When to Use
Use to execute generated Python that combines Search SDK and Browser APIs.

## Core Concepts
Allowed imports are whitelisted. State survives through `save_state` and
`load_state`, not hidden process memory.

## Examples
```python
import json
state = {"topic": "evidence"}
```

## Best Practices
Avoid host paths, subprocesses, sockets, and dynamic execution.

## Common Pitfalls
Do not rely on REPL globals across turns.
