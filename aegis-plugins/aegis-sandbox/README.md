# aegis-sandbox

Policy checks and state-storage primitives; not an execution sandbox.

The current core checks a few Python import and token patterns and persists
explicit JSON state. These checks are not a Python parser or a security boundary.
`PolicyOnlyBackend` never executes submitted code (`code_executed` is false), and
this crate provides no OS-level filesystem, network, or resource isolation.
Native Python/Node plugin execution must stay unavailable until a platform
worker establishes and verifies those controls before the code starts.
