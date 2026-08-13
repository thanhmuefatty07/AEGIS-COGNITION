# aegis-sandbox

Policy-first sandbox runtime primitives.

The current portable core validates Python snippets, enforces import and token
policies, and persists explicit JSON state under a sandbox root. OS-level
namespace/seccomp/cgroup backends are exposed as future host adapters rather than
claimed by tests on unsupported systems.
