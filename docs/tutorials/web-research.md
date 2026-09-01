# Web research

Use the explicit Lab API when the task requires bounded search, source
snapshots and citations. A provider adapter must be supplied for live query
operations; the model cannot inject a callable or widen the network policy.

```python
from aegis_cognition import Lab, LabMissionSpec, LabPolicy

lab = Lab(policy=LabPolicy(allowed_hosts=("example.com",)))
session = lab.start(LabMissionSpec("Find and cross-check the stated claim"))
result, dossier = await session.result()
```

Treat page text as untrusted data. The dossier distinguishes source evidence,
inference, simulation and unresolved blockers. See the
[`Research Plane`](../architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md#6-research-plane)
section for the search-as-code contract.

