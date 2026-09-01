# Browser automation

Browser access is an explicit capability. Configure an HTTPS host allowlist
and use a managed browser launcher or an already-owned session. Actor actions
and read-only observer operations have separate typed boundaries and quotas.

```python
from aegis_cognition import Lab, LabMissionSpec, LabPolicy

lab = Lab(policy=LabPolicy(allowed_hosts=("example.com",), max_browser_actions=20))
session = lab.start(LabMissionSpec("Inspect the allowed page and record evidence"), browser=True)
result, dossier = await session.result()
```

A page cannot change mission scope, policy, credentials or evidence status.
Process/OS isolation and hosted cross-platform enforcement remain explicit
release gates; local browser smoke is not production proof.

