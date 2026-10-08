# ADR-020: Native plugin isolation and local MCP approval

## Status

Partially accepted. Native Python/Node plugin code requires a real OS sandbox
and separate capability controls; implementation and runtime proof remain
incomplete. macOS stays unavailable until its required resource boundary is
verified.

The user confirmed on 2026-10-06 that local MCP stdio commands need explicit
approval, but do not need a mandatory OS sandbox. Such commands run with the
current user's normal machine permissions. Approval is not containment, and
the product must state this plainly before the user tests or enables a local
command. Server approval, testing the exact connection settings, and approval
of individual tool calls are separate decisions; the default remains approval
for every tool call, with only explicitly selected, unchanged read-only-hint
tools eligible for automatic use.

This decision supersedes the sandbox proposal below wherever it says to put
every local MCP stdio command behind an OS sandbox. The platform-specific
sandbox design and acceptance checklist are retained only as research for a
future native-code plugin worker; they are not requirements for user-approved
MCP stdio. AEGIS currently has its existing compute-only WASM boundary, but no
OS isolation for native Python/Node plugins or MCP stdio processes.

## Context

The product already exposes explicitly approved MCP stdio servers through
`McpStdioProvider`, and exposes compute-only WebAssembly extensions through
`ExtensionRegistry`. The current stdio provider starts its configured command
as a child process; approval and environment filtering do not create an OS
security boundary. Both the connection-test path and activation path currently
require a one-use test token bound to the server descriptor, workspace scope,
capability revision, and fingerprint of the submitted transport config. That
fingerprint binds the configured command, arguments, working directory, and
explicit environment values, but does not pin the resolved executable or its
runtime contents/version. It is a check that the tested configuration text was
not changed before activation, not executable identity verification or OS
containment. The standalone `aegis-sandbox` crate validates source policy but
does not execute or isolate native code.

The adjacent Rust resource runner is not a substitute for this boundary:
`ExecutionLanes::run_untrusted_process` checks reported memory/termination
capabilities, creates a scope, calls `Command::spawn`, and only then invokes
`ResourceController::apply_to_process`. The Linux backend adds the child PID
to `cgroup.procs` after spawn; the Windows backend calls
`AssignProcessToJobObject` after spawn. Arbitrary child instructions can run
before those post-spawn calls complete, and these resource controllers do not
provide the required filesystem or network isolation. Do not use this runner
for untrusted native plugins or MCP servers. Its unit test with the portable
controller verifies rejection when kernel controls are unavailable; it does
not verify a pre-execution containment boundary.

The active desktop MCP path is separate from that Rust runner: Tauri launches
the Python desktop sidecar in `desktop/src-tauri/src/main.rs`, and
`McpStdioProvider.start()` in `aegis_cognition/extensions.py` launches the
configured MCP command with `asyncio.create_subprocess_exec`. Per the accepted
approval-only decision above, that process is not OS-sandboxed. If a future
product decision adds sandboxing to MCP, a Rust-only control or the policy-only
`aegis-plugins/aegis-sandbox` crate would not protect this Python-owned launch
path; enforcement would have to cover the actual packaged process creation.

The confirmed product requirement is native Python/Node plugin support with
separate permissions, Windows/macOS/Linux coverage, bounded resource use, and
no unsandboxed fallback. The user also asked for higher performance and quality;
these are to be improved without weakening plugin isolation and must be measured
before claiming gains. MCP stdio is the transport for MCP servers, not an
already-approved execution contract for native plugins; a plugin worker API
must be selected from the repository's actual plugin requirements rather than
assumed to be MCP.

## Historical sandbox proposal (not selected for local MCP stdio)

1. Keep WebAssembly as the portable compute-only extension path. The earlier
   proposal to route every locally launched MCP stdio command through an OS
   sandbox is superseded by the user-confirmed approval-only policy above.
   Remote HTTP MCP remains on its host-controlled transport path. A future
   native-code plugin worker still requires a separately designed and verified
   OS boundary; MCP stdio approval is not a substitute for that boundary.
2. Bind the exact executable/arguments, runtime identity, environment policy,
   workspace scope, and granted capabilities to the approval reviewed by the
   user. A changed config or policy invalidates that approval/test before any
   worker starts. Apply the same check to test and activation paths. Never pass
   ambient provider keys, cookies, proxy credentials, or unrelated environment
   variables to a worker.
3. Default to no network and no workspace access. Permit only read-only runtime
   files and a private scratch area with enforced storage/file-count limits.
   Direct workspace access, when granted, is read-only; writes and deletes
   must use existing host tools so their approval policy remains authoritative.
   If network is later granted, use a host-mediated policy so the
   allowed-destination meaning is consistent across platforms.
4. Enforce wall-clock, memory, CPU, process-tree, scratch-storage, and
   captured-output limits.
   Probe capabilities at runtime and fail closed before executing plugin code
   whenever a required control cannot be established. Do not equate a source
   token scan, a language-level permission flag, or user approval with OS
   isolation.
5. Start workers lazily on activation and reuse the active stdio process for
   its server lifetime; do not spawn a new interpreter for each tool call.
   Integrate worker admission with the existing host resource-admission owner
   rather than introducing another scheduler. Performance improvements remain
   unclaimed until measured against equivalent MCP workloads.

## Platform constraints

- **Windows:** use a per-plugin AppContainer/LPAC identity with least-privilege
  filesystem grants, no network capability by default, and a Job Object for
  resource limits and process-tree cleanup. Configure the Job before launch
  and include its handle in `PROC_THREAD_ATTRIBUTE_JOB_LIST` with
  `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES` and an explicit
  `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`. This assigns the child to its Job at
  creation and avoids a post-start assignment window. If the required creation
  attributes are unsupported or rejected, refuse to launch. Existing Job
  Object resource code is not wired to MCP workers and does not create an
  AppContainer.
- **Linux:** use Landlock for filesystem restrictions, syscall/network-family
  filtering for network denial, and a delegated cgroup v2 subtree for
  aggregate CPU, memory, process-count, and cleanup controls. Probe the actual
  Landlock ABI and cgroup controllers/delegation at runtime; kernel-version
  checks alone are insufficient. Landlock rights not included in a ruleset
  are not denied. TCP restrictions require ABI 4 and UDP restrictions require
  ABI 10; ABI 10 still does not replace a syscall policy for other socket
  families or inherited network-capable file descriptors. Review newly added
  ABI rights before claiming complete isolation. Create/configure the cgroup
  before launch and prefer `clone3(CLONE_INTO_CGROUP)` so the child is born
  inside it. Any fallback must establish cgroup membership and Landlock/seccomp
  before `execve`; migrating a running process afterward is too late. If a
  required control is unavailable, refuse native execution. Seccomp alone is
  not a sandbox.
- **macOS:** a normal child process is not a separate privilege boundary. The
  candidate design is a separately packaged and signed XPC worker with its own
  App Sandbox entitlements; pass user-selected file access deliberately and
  mediate destination-specific network access. Apple documents that a child
  created with `posix_spawn`/`NSTask` inherits the parent's sandbox, but does
  not provide the privilege separation of an XPC service. XPC does not by
  itself establish the required aggregate memory/process/scratch limits for a
  worker tree. Native execution must remain disabled until signing,
  entitlements, file grants, process-tree termination, and every required
  resource boundary are verified in the packaged app.

## Alternatives

- **WebAssembly only:** already provides the portable restricted compute path,
  but does not meet the requested native Python/Node compatibility.
- **Direct same-user process launch:** reuses the current stdio path but grants
  ambient user authority and is rejected as the native plugin sandbox.
- **External container runtime:** adds installation/runtime dependence and
  another operational boundary; it is not the default desktop path.
- **Anthropic Sandbox Runtime (`@anthropic-ai/sandbox-runtime`):** upstream
  demonstrates a useful MCP wrapper and cross-platform OS controls, but its
  tagged README identifies it as a beta research preview. At the 2026-10-06
  review, npm's latest published version `0.0.78` requires Node.js
  `>=20.11.0`; GitHub `main` reports `>=22.12.0` despite carrying the same
  version string, so compatibility must be checked against the exact published
  artifact rather than the moving branch. Linux uses Bubblewrap, macOS uses
  `sandbox-exec`, and Windows uses a dedicated local account plus a
  Windows Filtering Platform rule. Its documented default allows broad reads
  unless deny rules are configured. The published package does not establish
  the aggregate CPU, memory, and process limits required here. It is therefore
  a valuable design reference, not a drop-in AEGIS runtime.
- **Codex sandboxing crates:** the upstream Rust implementation is the closest
  architectural reference for platform-specific sandbox adapters, but its
  `codex-sandboxing` and Linux helper depend on multiple Codex workspace crates;
  they are not standalone crates that AEGIS can adopt without importing a much
  larger internal dependency graph. Reuse the documented patterns, not the
  monorepo implementation as a direct dependency.
- **Microsoft MXC (`microsoft/mxc`):** a relevant Rust-based, cross-platform
  candidate, but not an acceptable security boundary for this feature at the
  2026-10-06 review. Its own repository warns that the code is an early preview,
  generated policies can be overly permissive, and profiles must not yet be
  treated as security boundaries. The release page labels v0.9.0 a pre-release;
  the README also lists Windows 11 24H2+ as the supported ProcessContainer
  floor, macOS under schema `0.9.0-alpha`, Linux runtime dependencies, and a
  Node.js 24+ SDK. These statements coexist with documentation calling some
  one-shot backends stable, so the exact contract and release artifact matter.
  Do not use MXC to enforce native-plugin isolation unless its upstream warning
  is resolved and AEGIS independently proves the exact packaged policy on all
  supported OS versions. It remains a research reference, not an adopted
  dependency.

The supervisor/per-session reuse proposal below was written for MCP workers and
is not selected by this ADR's current MCP policy. It may be reconsidered only
for a separately specified native-plugin worker after its host API, lifecycle,
permissions, and cross-platform enforcement are established. No upstream
implementation is copied into AEGIS by this decision.

## Acceptance evidence for any future sandboxed native-code worker

This checklist does not gate explicitly approved MCP stdio commands, which are
not sandboxed. It applies only if AEGIS implements native Python/Node plugin
execution behind an OS sandbox.

For each OS and supported architecture, run the packaged worker and prove:

- permitted runtime reads and explicitly granted workspace access work;
  canary reads/writes outside those grants fail;
- direct workspace writes/deletes are denied, and scratch storage, file count,
  and captured output stay within their configured limits;
- network is denied by default, including loopback and IPv4/IPv6 probes;
- Python and Node descendants inherit limits and are terminated on stop,
  timeout, or host exit;
- memory, CPU, process-count, wall-clock, and output limits behave as configured
  under stress without harming the host;
- only approved stdio handles and environment values reach the worker;
- changing the command, arguments, working directory, environment, runtime, or
  sandbox policy invalidates the reviewed config before test or activation;
- missing OS features, signing/entitlement errors, and policy setup failures
  prevent the worker's marker code from running;
- the normal MCP discovery, tool-call approval, cancellation, and shutdown
  journeys still pass through the existing registry/runtime owners.

Cross-platform build configuration is not runtime evidence. The feature must
remain unavailable on any platform whose enforcement tests have not passed.

Performance verification for a future native-plugin worker must compare the
same Python/Node runtime, plugin workload, hardware, and build in sandboxed and
baseline modes. Record cold worker activation latency, warm plugin-call
p50/p95/p99, throughput, CPU, and peak resident memory; separate one-time worker
startup from per-call cost. Reuse a long-lived worker only if the selected
plugin contract and isolation model support it. No speedup or acceptable-
overhead threshold is claimed until measurements and a workload-specific
budget are reviewed; isolation controls must not be weakened to improve a
benchmark.

## Migration, security, performance, operations, rollback

- **Migration:** The sandboxed native-plugin worker is not implemented. The
  existing MCP stdio path remains governed by the approval-only decision above;
  approval does not imply OS containment.
- **Security:** Do not enable native plugin execution through a worker until
  its process is contained before user code starts and the platform acceptance
  checks below pass. Missing controls must prevent launch; never fall back to
  an unsandboxed worker.
- **Performance:** No sandbox overhead or speedup has been measured. Compare
  equivalent packaged workloads before setting an overhead budget.
- **Operations:** Keep native worker execution unavailable on platforms where
  required controls, packaged permissions, or cleanup behavior have not been
  verified. Preserve the distinction between an approved MCP command and an
  OS-isolated native plugin.
- **Rollback:** If a future worker fails an enforcement or recovery check,
  disable that worker path and retain the existing MCP policy. Do not use the
  approval-only process launch as a fallback for native plugin execution.

## Evidence

The source review is recorded above: the Python MCP provider owns its current
subprocess launch, the Rust resource runner applies process controls after
spawn, and the desktop sidecar is launched separately. These facts do not
prove an OS sandbox. The proposed native worker is not implemented, and
packaged enforcement on Windows, Linux, and macOS remains **NOT VERIFIED**.
The acceptance checklist above defines the evidence required before enabling
that worker; this ADR does not claim the checklist has passed.

## Sources

- Microsoft, [AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer) and [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
- Microsoft, [process creation attributes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute) (`PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`, `PROC_THREAD_ATTRIBUTE_JOB_LIST`, and `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`).
- Linux kernel, [Landlock](https://docs.kernel.org/userspace-api/landlock.html), [seccomp](https://docs.kernel.org/userspace-api/seccomp_filter.html), [cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html), and [`clone3`/`CLONE_INTO_CGROUP`](https://man7.org/linux/man-pages/man2/clone.2.html).
- Apple, [App Sandbox and inheritance](https://developer.apple.com/library/archive/documentation/Miscellaneous/Reference/EntitlementKeyReference/Chapters/EnablingAppSandbox.html), [XPC privilege isolation](https://developer.apple.com/documentation/XPC?language=_11), and [sandbox violation guidance](https://developer.apple.com/documentation/security/discovering-and-diagnosing-app-sandbox-violations).
- Anthropic, [Sandbox Runtime README](https://github.com/anthropics/sandbox-runtime/blob/main/README.md) and [package manifest](https://raw.githubusercontent.com/anthropics/sandbox-runtime/main/package.json), reviewed 2026-10-06.
- OpenAI, [Codex sandboxing crate manifest](https://raw.githubusercontent.com/openai/codex/main/codex-rs/sandboxing/Cargo.toml) and [Linux sandbox helper manifest](https://raw.githubusercontent.com/openai/codex/main/codex-rs/linux-sandbox/Cargo.toml), reviewed 2026-10-06.
- Microsoft, [MXC repository and platform/security caveats](https://github.com/microsoft/mxc), [v0.9.0 release status](https://github.com/microsoft/mxc/releases), and [schema versioning](https://github.com/microsoft/mxc/blob/main/docs/versioning.md), reviewed 2026-10-06.
