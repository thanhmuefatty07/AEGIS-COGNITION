# AEGIS-COGNITION: AGENT HARNESS CONTINUATION PLAN

**Vai trò tài liệu**: Kế hoạch nối tiếp cho nền móng `AEGIS-COGNITION_ Kiến trúc AI Tối ưu.md`.

**Mục tiêu**: Thiết kế một AI Agent Harness có thể nhận prompt mơ hồ, tự chuyển thành nhiệm vụ vận hành, làm việc liên tục trên 100 giờ, tiết kiệm token cực hạn, không mất context, và giữ nguyên triết lý AEGIS: **LLM proposes, Rust verifies, Physics is judge**.

**Nguyên tắc không thương lượng**:
- Không dùng LLM-as-a-Judge để tự hợp thức hóa kết quả.
- Không commit trí nhớ dài hạn từ text thô của LLM nếu chưa có bằng chứng vật lý.
- Không đẩy raw text vào Wasmtime; sandbox chỉ nhận Wasm binary.
- Không over-engineering: mỗi lớp phải có mục đích đo được bằng MTC, SFoT, PAV, latency, hoặc recovery rate.
- Không cho autonomous loop gây tác động tài chính/pháp lý/ngoài đời thực nếu chưa qua policy, permission, và evidence gate.

---

## 0. Current Implementation Reality Check

Trạng thái hiện tại của codebase phải được ghi nhận chính xác trước khi claim Phase 2:

| Area | Current implementation | Phase 2 target |
|---|---|---|
| Episodic audit log | Standard Arrow IPC `StreamWriter<File>` plus in-memory trace vector. Current stream creation truncates the file for a fresh run. | mmap-backed Arrow IPC stream with resumable append-only semantics, PyCapsule/Python reader path, and benchmarked memory behavior. |
| QuickJS/Javy | QuickJS Wasm wrapper validation, deterministic invocation ABI packet, bounded linear-memory packet planning/write path, and a Wasmtime host-function bridge for validating the packet hash from guest memory exist. The packet binds magic/version/header bytes, fuel limit, script length/hash, wrapper hash, and invocation hash. `WasmtimeSandbox` caches compiled `Module` handles and QuickJS bridge `InstancePre` handles per engine with BLAKE3 module keys, avoiding per-call serialized-byte deserialization and import/type-resolution work on the bridge validation path, and uses a shared `EpochTicker` instead of a per-call watchdog thread. The latest force-fresh `quickjs_wasmtime_bridge_validate` run measured about 101.1 us mean on this machine and the benchmark gate is tightened to 400 us. Actual QuickJS interpreter execution and cold-start benchmarks are still pending. | Execute QuickJS/Javy through the host-validated linear memory bridge; benchmark Javy vs quickjs-wasm cold-start and fuel cost. |
| Sandbox text path | Production Wasmtime sandbox accepts Wasm bytes only. Text-based deterministic harness is test-only. | Keep production `PhysicalSandbox`/`WasmExecutionSandbox` byte-only; do not expose raw text execution in production APIs. |
| SAC terminology | Code now uses `PhysicalWitnessVote`, `SacPhysicalWitness`, `WitnessProof`, `last_witness_hash`, and `witness_verification_round`. | Keep witness terminology frozen in new code and audit scripts. |
| Policy kernel | `TypedToolIR`, `PolicyFacts`, `PolicyProofTrace`, `ReviewPacket`, `StagingEvidenceKind`, `SignedApprovalToken`, `ApprovalScopeReplay`, `OperatorReviewArtifact`, `DualApprovalProof`, `TelemetryEnvelope`, `HarnessBenchScorecard`, and deterministic Layer 0 R3/R4 staging/approval classifier exist in Rust. R4 policy facts and review packets now require hash-bound physical staging/testnet/snapshot/browser-session proof, or a signed HITL override reference. Approval-scope replay checks packet/IR/resource/risk/spend/expiry/policy-window coverage. Operator review artifacts sign only hash-bound policy/review/approval/replay evidence; helper text is not the signing target. Dual approval requires two distinct tokens from different approver keys on the same artifact. | Add WebAuthn/hardware-backed options and later Datalog/SMT/e-graph closure only after benchmark/proof gates. |
| Task ledger priority | `TaskLedger` and `TaskCard` exist in Rust with deterministic integer priority from DAG critical path, blocked descendants, evidence unblock count, and explicit deadline timestamp. `suggested_priority` is metadata only. `TaskGraphCache` caches structural topology, dense `children_by_index`, forest/shared-DAG mode, and whether topological order matches task storage order; `PriorityMetricCache` caches derived critical-path/blocked-descendant metrics; `ReadyCandidateCache` caches ready eligibility independent of `now_ms`; `ReadyQueueCache` caches same-state ready ordering by `now_ms` and `state_epoch`. Insert/status changes invalidate these caches through `state_epoch`. Forest-shaped task DAGs use the cached dense-index exact DP path; shared-dependency DAGs keep exact set-based fallback. When topological order matches task storage order, score application uses dense `values_mut()` iteration instead of per-task map lookup. The latest force-fresh run measured about 75.6 ns mean for `task_ledger_ready_queue_10k` cache hits and about 65.0 us mean for `task_ledger_ready_queue_10k_recompute`; gates are tightened to 500 ns and 250 us respectively. | Full dirty-subgraph incremental priority propagation remains a target only if the cached recompute path becomes insufficient under broader workload benchmarks; do not add Salsa/Datafrog until dependency and benchmark gates justify it. |
| Context Governor | `ContextGovernor`, `ActivatedContextGraph`, and `ContextPack` exist in Rust. The hot path activates at most 1-hop/2-hop around `active_task_id` plus required evidence refs, applies an activation cap, avoids extra activated-set cloning, and performs deterministic token-bounded greedy/local-swap selection over sorted-slice/`Vec` small sets. A fresh `context_governor_activated_pack` run measured about 38.3 us mean on this machine against a 10k-node global distractor graph; the benchmark gate is tightened to 100 us. | Add ContextPack replay events and background CogniFold/global graph folding. Do not run full global-graph knapsack per turn. |
| Hot evidence index | `evidence_index.rs` now defines `EvidenceCandidateTier`, `CandidateEvidenceRef`, `IndexEpochInputs`, `CandidateOnlyGate`, a tiny deterministic `HotLexicalIndex` inverted-index baseline using dense score accumulation plus bounded top-k with reusable `HotLexicalQueryScratch`, a no-dependency `SortedEvidenceSet`/`HotBitmapFilter` sorted-Vec filter baseline, a no-dependency `HotTermDictionary` sorted-Vec prefix-expansion baseline, `IndexEpochReplayRecord` binding over canonical query hash, sealed index epoch hash, limit, and ordered candidate-list hash, and `ColdVectorExpansionReplayRecord` binding over query hash, epoch hash, expansion config/artifact hashes, latency evidence, and cold candidate-list hash. Candidate refs are hash-bound to evidence/index epoch data and may be used only as `ContextPackCandidate`; direct witness, policy approval, memory commit, and Done-status uses fail closed. `ColdVectorExpansion` candidates require a matching replay record before they can enter a context pack. Current gated artifacts report about 102.5 us mean for `hot_lexical_index_top_k` against a 10k-doc in-memory corpus, about 51.7 us mean for `hot_bitmap_filter_intersection` over 65,536 segment ids, about 3.73 us mean for `index_epoch_replay_record`, about 8.16 us mean for `cold_vector_expansion_replay_record`, and about 11.1 us mean for `hot_term_dictionary_prefix_expand` over 50k dictionary terms. Gates are set to 200 us, 150 us, 10 us, 15 us, and 25 us respectively. | Add vector experiments only behind benches. `roaring-rs`, `fst`, DiskANN/FreshDiskANN-style disk ANN, and USearch-style SIMD vector search remain non-default until they beat baselines behind feature/benchmark gates. No retrieval score becomes truth or witness. |
| Replay ledger | `RunEventLedger` hash-chains `ContextPackBuilt`, `LLMResponseReceived`, `PolicyDecisionRecorded`, `OperatorReviewArtifactRecorded`, `ApprovalTokenRecorded`, tool-call request, and checkpoint events. `LLMResponseReceived` is rejected unless a `ContextPackBuilt` event already exists; `OperatorReviewArtifactRecorded` and `ApprovalTokenRecorded` are rejected unless a policy decision already exists. `ArrowRunEventStream` can roundtrip the hash chain through standard Arrow IPC and now writes sealed archive segments as one Arrow `RecordBatch` per segment instead of repeated one-row batches. The mmap materialized read path decodes Arrow batches directly into the destination event buffer and aggregates `ReplayIoEvidence` totals without an extra full-event pass. `BinaryRunEventSegment` can roundtrip fixed-size `RunEvent` records through mmap and scan/verify records without materializing a `Vec<RunEvent>`, the binary header fail-closes on magic/version/header-size/record-size/schema-hash drift, and `recover_last_valid_prefix_mmap` recovers the last valid binary prefix after truncated/corrupt tail records. `RunEventSegmentArchive` can recover sealed multi-segment archives with BLAKE3 manifest chaining using standard or mmap-backed sealed segment readers. `recover_last_valid_ledger_mmap` returns a recovery report for the last valid sealed prefix after a missing/corrupt tail segment. `ReplayIoEvidence` records mmap-backed read bytes and explicit `RunEvent` materialization bytes, and the JSON scorecard + run_checks report now carry that evidence, so the docs do not claim zero-copy. A fresh `replay_io_mmap_materialized` run measured about 5.80 ms mean on this machine after batch-per-segment writes plus read-side allocation/pass reduction, and the benchmark gate is tightened to 10 ms. `ReplayChaosBench` exercises deterministic seeded crash-boundary recovery, event-boundary and approval-boundary sweeps, binds the report into `HarnessBenchScorecard`, emits a validated 128-point seeded randomized JSON scorecard artifact, exposes the `replay-chaos-scorecard` CLI surface, and is wired into `run_checks`. | Add broader randomized crash-after-context/LLM/tool/approval recovery campaigns and benchmark-proven zero-copy/copy-count evidence. This is not mutable mmap writer semantics and not benchmark-proven zero-copy replay yet. |
| Benchmarks | Current numbers are micro-path benchmarks for isolated CPU-bound primitives plus bounded `task_ledger_ready_queue_10k`, `task_ledger_ready_queue_10k_recompute`, `context_governor_activated_pack`, `quickjs_invocation_abi_prepare`, `quickjs_linear_memory_write`, `quickjs_wasmtime_bridge_validate`, `replay_io_mmap_materialized`, `replay_io_binary_mmap_fixed`, `replay_io_binary_mmap_verify`, `replay_io_binary_mmap_recover_prefix`, `hot_lexical_index_top_k`, `hot_bitmap_filter_intersection`, and `hot_term_dictionary_prefix_expand` paths. This is not a QuickJS interpreter execution/cold-start benchmark. | HarnessBench-AEGIS end-to-end latency covering Wasm JIT, mutable Arrow writer semantics, QuickJS execution, tool gateway, browser evidence, and LLM network RTT. |
| Network stack | No default HTTP/3 or `simd-json` dependency in core. | Network stack (QUIC/simd-json) is strictly feature-gated and benchmark-dependent. Default Phase 2 uses standard `ureq`/`serde_json` for baseline stability. |

No document may claim mmap zero-copy audit streaming, QuickJS sub-millisecond cold-start, or platform-level latency until the matching implementation and benchmark exist.

---

## 1. Market Scan: Harnesses Đáng Tham Khảo

Các nguồn dưới đây là primary sources từ GitHub/docs/changelog chính thức, dùng để rút ra kỹ thuật, không sao chép kiến trúc.

| Harness / Repo | Điểm mạnh nên học | Điểm yếu/rủi ro cần vượt qua trong AEGIS |
|---|---|---|
| [Hermes Agent](https://github.com/NousResearch/Hermes-Agent) | Self-improving loop, skill creation, memory nudges, FTS5 session search, subagents, gateway đa kênh, scheduled automations, cloud/serverless persistence. README nêu rõ skill tự cải thiện, subagent song song, RPC scripts để gom pipeline nhiều bước thành turn ít context. | Rất mạnh về UX và learning loop, nhưng AEGIS phải thêm physical witness, PAV gate, deterministic rollback, và storage chứng cứ mật mã thay vì chỉ memory/search theo hội thoại. |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Durable execution, stateful long-running workflows, resume sau failure, memory, human-in-the-loop, graph orchestration. README mô tả durable execution và memory cho agent dài hạn. | Python graph runtime hữu dụng nhưng không đủ HPC/zero-trust nếu không có Rust kernel, Wasmtime, physical artifact verification. |
| [Microsoft AutoGen](https://github.com/microsoft/autogen) / [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | Multi-agent orchestration, MCP, graph workflows, checkpointing, streaming, HITL, time-travel, OpenTelemetry, production governance. AutoGen hiện maintenance mode; MAF được mô tả trong tài liệu công khai như một successor hướng production, nhưng mô tả bên ngoài này không phải bằng chứng AEGIS đã sẵn sàng. | Enterprise framework có nhiều abstraction; AEGIS phải giữ core nhỏ, deterministic, và không để group chat/role-play thay thế evidence. |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | Lightweight multi-agent workflows, tools, guardrails, handoffs, sessions, tracing, sandbox agents có filesystem và command execution cho long horizon. | Cần bổ sung token governor vật lý, context ledger độc lập model, và verification gauntlet không phụ thuộc provider. |
| [CrewAI](https://github.com/crewAIInc/crewAI) | Crews + Flows, event-driven control, task descriptions, memory/guardrails, tracing/control plane, dễ dùng cho enterprise automation. | Role/backstory dễ biến thành role-play nếu không có typed task contracts và external evidence. |
| [OpenHands](https://github.com/All-Hands-AI/OpenHands) | Software Agent SDK, CLI, local GUI, cloud, REST API, integrations Slack/Jira/Linear, RBAC, multi-user, enterprise self-host. | Mạnh về software engineering UX, nhưng AEGIS phải dùng physical memory, token governor, and deterministic shift protocol cho 100h. |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | Free-flowing LM agency, configurable YAML, research-oriented, strong SWE-bench lineage, real GitHub issue repair. | SWE-agent itself notes current effort is on mini-swe-agent. AEGIS nên học evaluation discipline, không bê nguyên tool surface. |
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | Radical simplicity: bash-only, linear history, independent `subprocess.run`, easy sandbox scaling, excellent baseline. | Tuy đơn giản và ổn định, thiếu memory hierarchy, mission compiler, cross-domain planning, and 100h autonomous governance. |
| [Aider repo map](https://aider.chat/docs/repomap.html) | Context optimization cực đáng học: repo map gồm symbols/signatures, graph ranking, token-budgeted selection, dynamic map expansion. | Aider là pair-programmer tương tác; AEGIS cần generalized context graph cho web, files, memory, tasks, tools, and business evidence. |
| [MetaGPT](https://github.com/geekan/MetaGPT) | "Software company as multi-agent system": one-line requirement -> user stories, competitive analysis, requirements, data structures, APIs, documents; SOP/team roles. | SOP hữu ích cho prompt mơ hồ, nhưng roles không được biến thành hội đồng semantic; AEGIS phải biến SOP thành typed DAG + evidence contracts. |
| [TaskWeaver](https://github.com/microsoft/TaskWeaver) | Code-first planning/execution, preserves chat history plus code execution history and in-memory data, plugin orchestration, code verification, logs. | Domain data analytics tốt; AEGIS cần ngôn ngữ tool đa miền, QuickJS/Wasm sandbox, and physical witness. |
| [Browser Use](https://github.com/browser-use/browser-use) | Browser automation agent, real-world web tasks, CLI giữ browser persistent, cloud offers stealth/proxy/captcha/scaling and persistent filesystem/memory. | Browser side effects rất rủi ro; AEGIS phải wrap bằng permission ledger, dry-run mode, visual/E2E witness, and rate limits. |
| [Letta](https://github.com/letta-ai/letta) | Advanced memory, stateful agents API, skills/subagents, local terminal agents, memory blocks, continual learning. | Memory không được tự động trở thành truth; AEGIS phải phân tách episodic trace, semantic pointer, and physical artifact memory. |
| [Mem0](https://github.com/mem0ai/mem0) | Memory layer với hybrid retrieval: semantic, BM25, entity matching, temporal reasoning, ADD-only extraction, agent-generated facts. | ADD-only rất phù hợp auditability, nhưng AEGIS cần canonical evidence and PAV gate trước khi đưa vào long-term memory. |
| [smolagents](https://github.com/huggingface/smolagents) | Minimal code-agent library, CodeAgent actions as code snippets, sandbox options Docker/E2B/Modal/Blaxel, model/tool/MCP agnostic. | Code-as-action mạnh nhưng phải chặn bằng Wasm/QuickJS, fuel, memory limits, and deterministic output hashing. |
| [Pydantic AI](https://github.com/pydantic/pydantic-ai) | Type-safe agents, model agnostic, OpenTelemetry/Logfire, evals, capabilities, YAML/JSON agent specs, MCP/A2A, HITL tool approval, durable execution, graph support. | Python validation tốt nhưng AEGIS nên đưa schema validation vào Rust/Arrow contracts for zero-copy and HPC. |
| [Google ADK](https://github.com/google/adk-python) | Code-first agents, eval/deploy, graph workflow runtime, routing, fan-out/fan-in, loops, retry, state management, HITL, nested workflows, task API. | Graph runtime tốt nhưng cần deterministic physical gate and token-economy engine mạnh hơn. |
| [Agno](https://github.com/agno-agi/agno) | Agent platform control plane: storage, memory, knowledge, traces, RBAC, human approval, OTel, scheduling, Slack/Telegram/WhatsApp/Discord/A2A. | Platform features mạnh; AEGIS nên tách control plane khỏi deterministic kernel để không kéo overhead vào hot path. |
| [Anthropic long-running harness article](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | Cực quan trọng cho 100h: initializer agent, feature list JSON, progress file, git history, one-feature-per-session, clean-state handoff, E2E browser testing. | Bài viết nói compaction alone is not sufficient. AEGIS phải thay progress text bằng typed ledger + BLAKE3 anchored evidence. |

---

## 2. Kết Luận Kiến Trúc Từ Market Scan

### 2.1 Những điểm tốt phải hấp thụ
- **Hermes**: learning loop, skill self-improvement, cross-session search, subagents, messaging gateway, scheduled automations.
- **LangGraph/MAF/ADK/Pydantic AI**: durable graph workflows, checkpointing, HITL, observability, typed specs.
- **Aider**: repo/context map dạng graph-ranking theo token budget.
- **Anthropic harness**: initializer + incremental session protocol + feature ledger + progress artifacts.
- **mini-swe-agent**: minimal, linear trajectory, easy sandboxing, bash/subprocess independence.
- **TaskWeaver/smolagents**: code-first action space and execution history.
- **Mem0/Letta**: memory as product surface, temporal/entity retrieval, stateful agent identity.
- **Browser Use/OpenHands/AutoGPT/Agno**: real-world tool integrations, UI/control plane, scheduling, RBAC, monitoring.

### 2.2 Những lỗi thị trường AEGIS phải sửa
- **Compaction không đủ**: nếu context mới không có task ledger có cấu trúc, agent sẽ đoán lại, làm trùng việc, hoặc tuyên bố xong sớm.
- **Semantic memory không phải bằng chứng**: memory retrieval có thể hữu ích nhưng không được phép là nguồn chân lý nếu không có artifact/evidence.
- **Multi-agent role-play không phải verification**: subagents chỉ có giá trị nếu output được kiểm chứng bằng hash, test, artifact, browser evidence, hoặc external metric.
- **Tool sprawl gây token bloat**: nhiều framework cho LLM tự chọn tool từ danh sách dài; AEGIS phải dùng tool router + context governor.
- **Control plane nặng không nên nằm trong hot path**: UI, Slack, cloud, RBAC tốt, nhưng Rust core phải giữ deterministic and minimal.
- **Autonomous commerce/finance cần policy gate**: prompt như "hãy kiếm tiền giúp tôi" phải qua safety, legality, capital constraints, and permission approval.

---

## 3. AEGIS Phase 2 Target: Autonomous Agent Harness 100h

Tên đề xuất: **AEGIS-HARNESS / NERVE-HARNESS**.

### 3.1 Mission statement
NERVE-HARNESS là runtime hỗ trợ LLM biến prompt mơ hồ thành chuỗi hành động có kiểm chứng, tự vận hành nhiều phiên, duy trì trạng thái làm việc trên 100 giờ, và chỉ commit tiến độ khi có bằng chứng vật lý.

### 3.2 Prompt mục tiêu
Ví dụ: "hãy kiếm tiền giúp tôi".

Harness không được hiểu prompt này là "đi trade, spam, hoặc tạo hành động tài chính ngay". Nó phải biên dịch thành:

```text
MissionSpec {
  objective: "increase lawful income or revenue opportunities",
  constraints: ["no illegal activity", "no financial transaction without approval", "no user impersonation", "no credential exfiltration"],
  capital_policy: "zero external spend unless approved",
  domains: ["market research", "skill inventory", "offer creation", "lead generation draft", "automation prototypes"],
  approval_required: ["payments", "account creation", "external posting", "email/send message", "contractual commitments"],
  evidence_contract: ["market data source", "artifact built", "test passed", "user approval recorded"]
}
```

### 3.3 Success criteria
- Agent can run 100h in shifts without losing objective, current state, evidence, or next action.
- Average context injected per turn remains bounded by **Token Governor**, not by raw transcript length.
- Every state transition has an artifact: code diff, browser screenshot, benchmark, data report, task ledger entry, or verified external observation.
- Every long-running run can be resumed from last `RunCheckpoint` without asking the LLM to reconstruct history.
- Tool side effects are blocked unless policy and permission gates pass.

---

## 4. Core Architecture

```mermaid
flowchart TD
    U["Ambiguous User Prompt"] --> MC["Mission Compiler"]
    MC --> PS["Policy & Constraint Synthesizer"]
    PS --> TL["Typed Task Ledger"]
    TL --> CG["Context Governor"]
    CG --> LLM["LLM Proposer"]
    LLM --> TR["Tool Router"]
    TR --> TK["Typed Tool Kernel"]
    TK --> WB["QuickJS/Wasm Sandbox"]
    TK --> BR["Browser/Computer Tools"]
    TK --> IO["Filesystem/API/RPC Tools"]
    WB --> PW["Physical Witness Gate"]
    BR --> EV["Evidence Collector"]
    IO --> EV
    EV --> PW
    PW --> CP["RunCheckpoint + Arrow IPC Audit"]
    CP --> CM["CogniFold Memory + Semantic Pointers"]
    CM --> CG
    CP --> SCH["Scheduler / Shift Manager"]
    SCH --> TL
```

### 4.1 Mission Compiler
Chuyển prompt mơ hồ thành `MissionSpec`, `RiskPolicy`, `SuccessMetrics`, `EvidenceContracts`.

Algorithm:
1. Parse user objective into possible domains.
2. Reject or quarantine illegal/unsafe interpretations.
3. Ask clarifying questions only if required to avoid irreversible risk.
4. Generate default safe assumptions for reversible research/planning tasks.
5. Emit machine-readable JSON with schema validation.

Key invariant:
```rust
pub trait MissionCompiler {
    fn compile(prompt: &str, policy: &RiskPolicy) -> Result<MissionSpec, MissionError>;
}
```

### 4.2 Typed Task Ledger
Không dùng todo markdown tự do cho core state. Dùng append-only ledger:

```rust
pub struct TaskCard {
    pub task_id: u128,
    pub mission_id: u128,
    pub parent_id: Option<u128>,
    pub objective: String,
    pub status: TaskStatus,
    pub deterministic_priority_score: u64,
    pub suggested_priority: Option<i64>,
    pub critical_path_len: u32,
    pub blocked_descendant_count: u32,
    pub evidence_unblock_count: u32,
    pub hard_deadline_ms: Option<u64>,
    pub risk_class: RiskClass,
    pub evidence_contract: EvidenceContract,
    pub token_budget_hint: u32,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
}
```

Task priority:

```text
deterministic_priority =
  graph_critical_path_score
  + blocked_descendant_score
  + evidence_unblock_score
  + deadline_pressure_score
  - risk_penalty
  - measured_resource_cost
```

Priority queue sorting is owned by Rust Core. LLM output may include only `suggested_priority` metadata for observability; it must not drive queue order. `graph_critical_path_score`, `blocked_descendant_score`, and `evidence_unblock_score` are computed from the Task DAG and evidence ledger. `deadline_pressure_score` comes from an explicit timestamp, not model preference text.

### 4.3 Shift Manager
Lấy bài học từ Anthropic initializer/coding-agent protocol nhưng thay text progress bằng typed ledger.

Mỗi shift:
1. Load `MissionSpec`.
2. Load latest `RunCheckpoint`.
3. Read top-N failed/pending `TaskCard`.
4. Run health check and basic E2E smoke if applicable.
5. Pick exactly one primary task for current shift.
6. Execute bounded loop.
7. Write checkpoint, evidence, and next-action packet.

Hard rule:
- Một shift không được "hoàn thành cả mission" trừ khi all acceptance gates pass.
- Nếu context gần đầy, shift phải checkpoint and yield, không cố one-shot.

### 4.4 Context Governor
Đây là lớp giải quyết triệt để context forgetting and token limit.

Inputs:
- Mission header.
- Current task card.
- Last checkpoint summary.
- Required source slices.
- Repo/world map.
- Recent high-signal errors.
- Evidence references by hash.

Outputs:
- `ContextPack` có token budget cố định.
- Không bao giờ nhét transcript dài.

Context selection algorithm:
1. Build or refresh the global multi-graph outside the hot path:
   - file nodes,
   - symbol nodes,
   - task nodes,
   - evidence nodes,
   - memory nodes,
   - tool nodes.
2. Activate only the hot-path subgraph:
   - 1-hop and 2-hop neighbors of the seed set: `active_task_id` plus `required_evidence_refs`,
   - unresolved blockers and policy nodes linked to the active task.
3. Score activated nodes by:
   - dependency distance to task,
   - recency,
   - failure relevance,
   - entity match,
   - physical artifact trust score,
   - user preference weight.
4. Solve bounded knapsack only on the activated subgraph:
   - maximize relevance under token budget,
   - always include mission/task/safety headers,
   - include only hashes for low-value logs.
5. Produce compact prompt pack.

Hot-path invariant:
- Context Governor must not run global graph knapsack per turn.
- CogniFold may fold and rescore the global graph in the background.
- If the activated subgraph is empty or insufficient, the shift must request explicit expansion evidence instead of scanning the full global graph synchronously.

AEGIS improvement over Aider:
- Aider maps repo symbols. AEGIS maps **repo + tasks + evidence + memory + tools + external observations**.

### 4.5 Memory System: Five-Tier Model

| Tier | Purpose | Storage | Commit Gate |
|---|---|---|---|
| L0 Hot Context | current prompt pack only | in-memory | token governor |
| L1 Episodic Audit | every LLM/tool event | Phase 1: Arrow IPC `StreamWriter<File>`; Phase 2 target: mmap-backed resumable stream | append-only target |
| L2 Physical Evidence | code/test/browser/data artifacts | BLAKE3 CAS | physical witness |
| L3 Semantic Pointers | retrieval handles | CogniFold | PAV-gated artifact |
| L4 Skills | reusable pipelines | skill registry | tests + evidence + usage stats |

Memory retrieval:
- BM25 keyword + entity linking + temporal ranking + semantic vector search.
- Retrieval result is not truth; it is a candidate pointer.
- Long-term memory update requires evidence, not LLM confidence.

### 4.6 Skill Factory
Học từ Hermes, Letta, Agno, Mem0: agent cần tự tích lũy kỹ năng.

AEGIS rule:
- Skill chỉ được tạo khi có trajectory thành công.
- Skill phải có preconditions, tool permissions, expected evidence, regression test.
- Skill không được tự sửa policy.

```rust
pub struct SkillCard {
    pub skill_id: u128,
    pub name: String,
    pub preconditions: Vec<String>,
    pub tool_scope: Vec<ToolPermission>,
    pub recipe_wasm_or_script_hash: [u8; 32],
    pub success_metrics: Vec<String>,
    pub regression_tests: Vec<String>,
    pub last_success_ms: u64,
    pub failure_count: u32,
}
```

### 4.7 Tool Kernel
Không đưa cho LLM một biển tool dài. Dùng router:

```text
LLM intent -> Tool Router -> candidate tools <= 5 -> typed call -> policy gate -> execution -> evidence
```

Tool categories:
- Browser/Computer tools.
- Filesystem tools.
- Shell/build/test tools.
- Research/search tools.
- Data tools.
- Messaging gateways.
- Finance/commercial tools.

Hard rule:
- High-risk tool calls cần explicit approval and, for R4, staging/testnet/snapshot execution evidence or signed HITL override proof.
- External side effects phải ghi `SideEffectProof`.

### 4.8 QuickJS/Wasm Action Runtime
LLM sinh JS tool code vì LLM viết JS tốt và syntax phổ biến.

Pipeline:
1. LLM outputs JS action script or typed tool call JSON.
2. Rust guardrail scans destructive/network/secret patterns.
3. QuickJS Wasm wrapper executes script in Wasmtime after the ABI is implemented.
4. Wasmtime fuel/memory/epoch limits enforce runtime safety.
5. Output canonicalized and hashed.
6. Physical Witness Gate decides commit.

No raw script enters Wasmtime as executable payload. Wasmtime payload remains QuickJS Wasm module; JS is data passed through the chosen ABI. Current code validates the wrapper, builds a deterministic ABI packet that binds script hash, wrapper hash, fuel limit, and invocation hash, writes that packet into a bounded Wasm-style linear-memory buffer, and exposes a Wasmtime host import that validates the packet hash from guest memory. Actual QuickJS interpreter execution remains a P0 implementation target.

### 4.9 Physical Witness Gate
Subagents can propose, but only evidence commits.

Witness sources:
- passing tests,
- browser E2E screenshot + DOM assertion,
- reproducible command output,
- BLAKE3 canonical artifact hash,
- external source snapshot,
- policy approval record.

Subagent outputs are not physical witness by themselves. Independent subagents may produce only `CandidateEvidence`; the candidate must pass a physical gate such as Wasmtime unit test, browser DOM/screenshot hash, reproducible command output, or BLAKE3 artifact match before it becomes `PhysicalWitness`.

### 4.10 Control Plane
Borrow from Agno/OpenHands/CrewAI/AutoGPT, but keep it out of hot path.

Surfaces:
- TUI command center.
- Browser dashboard.
- Slack/Telegram/Discord gateway.
- REST/SSE API.
- Scheduled automations.
- Audit report exporter.

The control plane never bypasses Rust core gates.

---

## 5. Autonomous 100h Protocol

### 5.1 Initialization
For a new vague mission:
1. Compile `MissionSpec`.
2. Generate `TaskLedger` with failing tasks.
3. Generate `RiskPolicy`.
4. Generate `init_check` command/script.
5. Create first `RunCheckpoint`.
6. Start `Shift 0`.

### 5.2 Shift loop

```text
while mission_not_complete:
  load checkpoint
  health_check()
  select task with highest deterministic_priority_score
  build context pack
  ask LLM for plan delta
  route tools
  collect evidence
  verify physical witness
  update task ledger
  checkpoint
  if context_pressure > threshold: yield
  if wall_time > shift_budget: yield
```

### 5.3 Anti-forgetting invariants
- Every shift starts from ledger + checkpoint, not from conversation vibes.
- Every task has explicit `passes=false` until evidence flips it.
- Every completion claim must cite evidence IDs.
- Every "done" requires all child tasks and mission acceptance gates.
- Every next shift gets a `NextActionPacket`.

```rust
pub struct NextActionPacket {
    pub mission_id: u128,
    pub current_task_id: u128,
    pub last_known_good_checkpoint: [u8; 32],
    pub next_recommended_action: String,
    pub blocked_on: Option<String>,
    pub required_context_refs: Vec<[u8; 32]>,
}
```

---

## 6. Token Optimization Architecture

### 6.1 Token budget hierarchy

```text
ContextPack budget =
  8% mission + policy
  12% current task + acceptance contract
  20% relevant repo/world map
  20% selected evidence summaries
  15% recent errors/tool outputs
  15% retrieved memories
  10% scratch / model-specific margin
```

### 6.2 Compression layers
- **Prompt skeletons**: stable instructions hashed and referenced.
- **Repo/world map**: graph-ranked, token-budgeted, Aider-inspired.
- **Trajectory compression**: long tool sequences become skill candidates.
- **Error sanitizer**: compiler/browser logs reduced to actionable lines.
- **Evidence handles**: BLAKE3 IDs instead of full artifacts.
- **Delta summaries**: summarize only state change since last checkpoint.
- **Tool RPC batching**: repeated small tool calls collapse into one script or Wasm action.

### 6.3 MTC metrics
Track:
- `tokens_in_per_verified_artifact`
- `tokens_out_per_verified_artifact`
- `tokens_spent_on_recovery`
- `context_reuse_ratio`
- `retrieval_precision_at_k`
- `checkpoint_resume_tokens`

Target:
- 100h run should not exceed a fixed context-pack size per shift.
- Resume from checkpoint should be under 2k-6k tokens depending on mission class.

---

## 7. Vague Prompt Handling: "Hãy kiếm tiền giúp tôi"

### 7.1 Safe mission expansion

Allowed default phases:
1. User profile and constraints discovery.
2. Skill/inventory assessment.
3. Market opportunity research.
4. Low-risk idea portfolio.
5. MVP/prototype generation.
6. Outreach drafts only, no sending without approval.
7. Measurement plan.
8. Revenue operation only after explicit user approval.

Blocked without approval:
- paid ads,
- trading/investing,
- account creation,
- sending emails/messages,
- scraping against ToS,
- legal/financial/tax advice as final authority,
- purchases,
- contract/signature actions.

### 7.2 Evidence contracts per phase

| Phase | Evidence needed |
|---|---|
| Market research | cited source snapshots, timestamp, relevance score |
| Opportunity shortlist | risk/value matrix, legal constraints, required capital |
| MVP prototype | repo artifact, tests, demo screenshot |
| Lead generation draft | draft only, no send; user approval required |
| Monetization experiment | explicit approval, spend cap, measurement dashboard |

### 7.3 Success definition
Harness succeeds if it turns vague money-making intent into a lawful, testable, low-risk execution plan and artifacts. It must not silently perform irreversible external actions.

---

## 8. Implementation Roadmap

### Phase 2.0: Harness State Kernel
- Add `MissionSpec`, `TaskCard`, `EvidenceContract`, `RunCheckpoint`.
- Store ledgers in the current standard Arrow IPC `StreamWriter<File>` path.
- Add P0 task: implement mmap-backed Arrow IPC stream with resumable append-only semantics.
- Add BLAKE3 content-addressed evidence store.
- Add `NextActionPacket`.

Acceptance:
- Can initialize vague prompt into typed mission without tool side effects.
- Can resume from checkpoint with no conversation transcript.

### Phase 2.1: Context Governor
- Build graph-ranked context map.
- Add token-budgeted pack builder.
- Add source/evidence handles.
- Add error/log sanitizer into prompt pack.

Acceptance:
- Given a large repo and task, pack includes relevant symbols/evidence under fixed token budget.
- Pack quality test catches missing required files/symbols.

### Phase 2.2: Tool Kernel + Permission Ledger
- Typed tool registry.
- Tool router.
- Risk classifier.
- HITL approval records.
- Staging/testnet/snapshot execution mode for high-risk external side effects; no LLM/mock-log dry-run evidence.

Acceptance:
- High-risk action blocked by default.
- Tool call evidence is recorded with hash, args, result, risk class.

### Phase 2.3: QuickJS/Wasm Runtime
- Select Javy or quickjs-wasm.
- Define QuickJS Wasm Linear Memory ABI using shared buffer plus host functions.
- Execute under Wasmtime fuel/memory/epoch limits.
- Canonicalize output and commit physical artifact.
- Benchmark Javy vs quickjs-wasm cold-start before claiming latency or binary-size targets.

Acceptance:
- Raw text cannot execute directly in sandbox.
- JS action runs only through QuickJS Wasm wrapper after ABI implementation.
- Malicious action traps or is rejected.

### Phase 2.4: Shift Manager 100h
- Implement shift loop.
- Add heartbeat scheduler.
- Add checkpoint/resume tests.
- Add one-task-per-shift guardrail.

Acceptance:
- Synthetic 100h simulation with forced context resets completes without losing mission/task state.
- Recovery after crash resumes from last good checkpoint.

### Phase 2.5: Skill Factory
- Convert successful trajectories into skill candidates.
- Add skill regression tests.
- Add usage statistics and failure decay.

Acceptance:
- Skill cannot be registered without passing evidence contract.
- Reused skill lowers token cost vs raw trajectory.

### Phase 2.6: Browser/Computer Operations
- Integrate Browser Use-style browser state.
- Add screenshot/DOM evidence.
- Add side-effect gate.
- Add web task templates.

Acceptance:
- Browser tasks produce visual + DOM evidence.
- No external submit/send/purchase without approval.

### Phase 2.7: Command Center
- TUI first, dashboard later.
- Show PAV, SFoT, MTC, task ledger, tool risk, checkpoint hash, current shift.

Acceptance:
- Operator can inspect current mission state without reading transcript.
- Operator can approve/deny risky actions.

---

## 9. Evaluation Suite

### 9.1 Benchmarks to create
- **VaguePromptBench**: vague prompt -> safe mission spec + task ledger.
- **LongRunBench**: 100h simulated run with forced compaction/reset.
- **ContextRetentionBench**: recall state after N shifts without transcript.
- **ToolRiskBench**: high-risk external action detection.
- **EvidenceBench**: completion claims must cite evidence IDs.
- **TokenEconomyBench**: task completion under fixed context budget.
- **BrowserWitnessBench**: browser task verified by screenshot + DOM.
- **SkillReuseBench**: repeat task costs fewer tokens after skill creation.

### 9.2 Required gates
- `MissionSpec` schema validation.
- `TaskLedger` no orphan tasks.
- `RunCheckpoint` BLAKE3 chain valid.
- `ContextPack` under budget.
- `NoRawTextToSandbox`.
- `NoSemanticMemoryCommitWithoutEvidence`.
- `HighRiskToolRequiresApproval`.
- `AllDoneRequiresAcceptanceEvidence`.

---

## 10. Engineering Structure

Proposed Rust modules:

```text
core/rust/src/harness/
  mission.rs        # MissionSpec, compiler, policy constraints
  task_ledger.rs    # TaskCard, status, priority, dependency DAG
  evidence.rs       # EvidenceContract, CAS, proof refs
  checkpoint.rs     # RunCheckpoint, NextActionPacket
  context.rs        # ContextGovernor, graph ranking, token budget
  tool_kernel.rs    # typed tools, permissions, side effects
  shift.rs          # 100h shift loop
  skills.rs         # SkillCard and skill registry
  quickjs.rs        # QuickJS Wasm ABI wrapper
```

Proposed Python/control modules:

```text
core/python/harness/
  mission_surface.py
  dashboard_api.py
  approval_queue.py
  integrations.py
```

Proposed artifacts:

```text
artifacts/harness/
  mission-ledger.arrow
  evidence/
  checkpoints/
  context-packs/
  skills/
```

---

## 11. Design Decisions Pending

1. **QuickJS artifact**: Javy vs quickjs-wasm.
2. **JS ABI**: stdin, memory buffer, WASI args, or host function.
3. **Arrow append semantics**: per-session stream vs resumable multi-process writer.
4. **Browser backend**: Browser Use, Playwright MCP, or native Browser plugin first.
5. **Control plane priority**: TUI first or API/dashboard first.
6. **Memory provider**: build native hybrid retrieval now or bridge Mem0/Letta during prototype.
7. **100h test environment**: synthetic simulation only first, or real long-running cron/heartbeat.

---

## 12. Final Architecture Verdict

AEGIS should not become another role-play multi-agent framework. The winning direction is:

```text
Hermes-style self-improvement
+ LangGraph/MAF durable graph execution
+ Aider graph-ranked context
+ Anthropic shift protocol
+ mini-swe-agent simplicity
+ Mem0/Letta temporal memory
+ Browser Use/OpenHands real-world tooling
+ AEGIS Rust/Wasmtime/Physical-Witness kernel
= NERVE-HARNESS
```

The breakthrough is not "more agents". The breakthrough is a **deterministic, evidence-first operating system for agents** where LLMs can work for 100h because the harness remembers, verifies, schedules, compresses, resumes, and refuses unsafe side effects.

---

## 13. Deep Research Addendum: Broad -> Narrow -> Deep

### 13.1 Audit verdict

Sau khi đối chiếu bản kế hoạch với trạng thái dự án hiện tại và các harness/benchmark/runtime mới hơn, không phát hiện mâu thuẫn nền tảng với hiến pháp AEGIS. Các điểm cần bổ sung dưới đây là **khoảng trống thiết kế thật**, không phải lỗi triển khai hiện hữu:

1. Harness chưa có mô hình replay đủ chặt cho phiên 100h.
2. Context Governor mới dừng ở graph-ranking, chưa có cơ chế chống memory/context interference.
3. QuickJS runtime chưa tách rõ live-script interpreter và precompiled skill artifact.
4. Tool/MCP gateway chưa đủ rõ về trust boundary, consent, manifest hash, và side-effect class.
5. Evaluation suite chưa có benchmark riêng cho harness, memory interference, ambiguous-prompt handling, và replay recovery.

Các điểm trên không phủ nhận kết quả Core Harness hiện tại. Chúng chỉ là điều kiện bắt buộc trước khi nâng từ "core runtime đúng" lên "agent harness sản xuất được".

### 13.2 Broad scan bổ sung

| Nguồn | Bài học kỹ thuật | Áp dụng vào AEGIS |
|---|---|---|
| [DeepAgents](https://github.com/hwchase17/deepagents) | Deep agents nhấn mạnh plan tool, filesystem, subagents, và system prompt chi tiết để xử lý tác vụ dài. | Học cơ chế plan/filesystem/subagent, nhưng mọi output phải đi qua task ledger, evidence gate, và physical witness. |
| [Harness Bench](https://www.harness-bench.ai/) | Benchmark tách riêng tác động của scaffolding, tool-use, search, memory bằng oracle components. | Tạo benchmark cho harness component, không chỉ test Rust core hoặc end-to-end mơ hồ. |
| [Javy](https://github.com/Shopify/javy) | JavaScript-to-Wasm toolchain dựa trên QuickJS, phù hợp đóng gói JS thành Wasm artifact. | Dùng Javy cho registered skills đã được compile, hash, approve. Không dùng để chạy text thô trực tiếp. |
| [Temporal docs](https://docs.temporal.io/) | Durable execution dựa trên workflow history và deterministic replay. | Học mô hình event history/replay, nhưng triển khai bản native nhẹ trong Arrow + Rust trước, không kéo service nặng vào hot path. |
| [Agent Memory Benchmark](https://agentmemorybenchmark.ai/) | Đo memory correctness, long-context retention, conflict/interference. | Tạo InterferenceMemoryBench để kiểm tra ký ức sai, ký ức cũ bị supersede, và retrieval gây nhiễu. |
| [MemexRL](https://arxiv.org/abs/2603.04257) | Gợi ý hướng tối ưu memory/retrieval bằng evidence-indexed interaction history. | Dùng như hướng nghiên cứu cho retrieval tuning, không biến RL memory thành nguồn chân lý. |
| [MCP security best practices](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices) | Tool metadata/prompt/resource từ server ngoài phải được xem là untrusted; client phải có control, consent, và isolation. | AEGIS Tool Gateway phải là policy-enforcing host broker, không tin server MCP tự khai báo an toàn. |

---

## 14. Narrowed Technical Verdicts

### 14.1 Durable workflow

**Verdict**: Không dùng Temporal service trong hot path của Phase 2. Học mô hình durable execution/replay, nhưng triển khai `DurableReplayEngine` native bằng Phase 1 Arrow IPC `StreamWriter<File>` trước, sau đó nâng lên mmap-backed append-only stream khi đã có benchmark và resumable writer semantics.

Lý do:
- AEGIS cần replay determinism cấp kernel, không chỉ resume tiện dụng.
- Hot path hiện đang tối ưu nanosecond/microsecond; thêm service orchestration sớm sẽ kéo latency và operational surface.
- Replay native giúp kiểm chứng PAV, SFoT, MTC, fuel, tool result, checkpoint seal trong cùng một proof chain.

### 14.2 Deep agent structure

**Verdict**: Cho phép subagents, nhưng subagent là **worker context partition**, không phải hội đồng ra quyết định.

Quy tắc:
- Subagent chỉ nhận `TaskCard` có scope hẹp, budget, deadline, và evidence contract.
- Output subagent chỉ là `CandidateEvidence`, chưa được commit.
- Parent harness chỉ accept khi evidence được verify bằng artifact/test/hash/browser witness/tool result.
- Không có "multi-agent vote" thay thế physical witness.

### 14.3 QuickJS/Javy split

**Verdict**: Dùng 2 đường runtime, cùng bị Wasmtime/fuel/memory limit:

1. **Live Action Runtime**: QuickJS Wasm reactor/interpreter cố định, JS từ LLM đi vào như data qua ABI.
2. **Registered Skill Runtime**: Javy compile JS skill thành Wasm artifact, ký hash, lưu trong skill registry.

Không nhập text thô vào trait sandbox. Sandbox contract vẫn là:

```rust
pub trait PhysicalSandbox {
    fn execute_wasm_binary(
        &self,
        payload: &[u8],
        limits: SandboxLimits,
    ) -> Result<PhysicalArtifact, Trap>;
}
```

### 14.4 MCP/tool gateway

**Verdict**: MCP server, Browser backend, shell, filesystem, API client, và external service đều phải đi qua một `ToolGateway`.

Không có tool nào được expose trực tiếp cho LLM chỉ vì nó xuất hiện trong manifest. Tool manifest là input không tin cậy, được normalize thành `ToolCard`, hash, risk-score, và policy-check trước khi đưa vào context.

---

## 15. Memory and Context Architecture v2

### 15.1 Problem

Memory retrieval giúp agent nhớ lâu hơn, nhưng cũng tạo 3 rủi ro:

1. **Stale fact**: thông tin đúng ở shift trước nhưng sai ở shift sau.
2. **Contradictory recall**: hai ký ức mâu thuẫn cùng được đưa vào context.
3. **Context poisoning by relevance**: đoạn text rất liên quan nhưng chưa được physical evidence xác nhận.

Vì vậy, memory không được xếp hạng chỉ bằng semantic similarity.

### 15.2 EvidenceIndexedMemory

Mỗi memory item phải có nguồn, thời gian, trạng thái revision, và proof reference:

```rust
pub struct MemoryAtom {
    pub id: MemoryId,
    pub kind: MemoryKind,
    pub text_digest: [u8; 32],
    pub source_ref: EvidenceRef,
    pub created_at_ns: u64,
    pub supersedes: Option<MemoryId>,
    pub confidence: f32,
    pub trust: f32,
}
```

Trust score:

```text
trust = physical_evidence_score * freshness * source_reliability - contradiction_penalty
```

Commit rule:
- Episodic trace có thể append ngay nếu là log sự kiện.
- Semantic memory chỉ được commit khi có `EvidenceRef`.
- Nếu memory mới mâu thuẫn memory cũ, không overwrite. Ghi `supersedes`, giữ cả hai trong ledger, và Context Governor chỉ chọn bản active mới nhất có trust cao nhất.

### 15.3 ContextPack selector

Context Governor v2 nên coi context selection là multi-objective bounded knapsack:

```text
maximize:
  relevance(task, item)
  + trust(item)
  + recency(item)
  + dependency_coverage(item)
  + novelty(item)
  - contradiction_risk(item)
  - token_cost(item)

subject to:
  token_budget <= hard_cap
  required_evidence_refs included
  active_task_dependencies included
  high_risk_tool_policy included
```

Hot-path bound:
- Build `ActivatedContextGraph` from 1-hop and 2-hop neighbors of the seed set: `active_task_id` plus `required_evidence_refs`.
- Add unresolved blockers and policy nodes only when they are linked into the activated seed neighborhood or explicitly required as evidence.
- Run greedy + local swap only on `ActivatedContextGraph`.
- Never run full global-graph knapsack in the per-turn hot path.
- Global graph folding, contradiction precomputation, and long-range memory consolidation run in CogniFold background jobs.

Không cần solver phức tạp trong Phase 2. Dùng greedy + local swap trên activated subgraph là đủ, miễn có metric:
- `context_hit_rate`: tỷ lệ evidence/task dependency cần thiết có trong pack.
- `context_waste_rate`: token không được dùng trong quyết định hoặc tool call tiếp theo.
- `contradiction_injected_rate`: số pack chứa memory mâu thuẫn chưa được resolve.
- `activation_node_count`: số node trong activated subgraph, phải có hard cap.
- `hot_path_context_latency_ms`: latency chọn context cho một turn.
- `recovery_pack_size`: token cần để resume sau checkpoint.

### 15.4 Context anti-forgetting invariant

Mỗi `NextActionPacket` phải chứa:

```text
mission_digest
active_task_ids
blocked_task_ids
last_verified_checkpoint
required_evidence_refs
current_policy_window
open_questions
next_minimal_action
```

Nếu thiếu một trường trên, shift không được handoff tự động.

---

## 16. Durable Workflow and Replay Model

### 16.1 Event-sourced ledger

Thay vì chỉ lưu progress file, harness phải lưu event history append-only:

```rust
pub enum RunEvent {
    MissionCompiled { mission_id: MissionId, digest: [u8; 32] },
    TaskCreated { task_id: TaskId, parent: Option<TaskId>, contract: EvidenceContract },
    TaskStatusChanged { task_id: TaskId, from: TaskStatus, to: TaskStatus },
    ContextPackBuilt { pack_id: ContextPackId, token_count: u32, digest: [u8; 32] },
    LLMResponseReceived { response_hash: [u8; 32], raw_text_ref: EvidenceRef },
    PolicyDecisionRecorded { policy_proof_trace_hash: [u8; 32], typed_tool_ir_hash: [u8; 32] },
    OperatorReviewArtifactRecorded { signing_target_hash: [u8; 32], artifact_hash: [u8; 32] },
    ApprovalTokenRecorded { approval_token_hash: [u8; 32], review_packet_hash: [u8; 32], expires_at_ms: u64 },
    ToolCallRequested { call_id: ToolCallId, tool_id: ToolId, risk: ToolRisk },
    ToolCallApproved { call_id: ToolCallId, approver_digest: [u8; 32] },
    ToolCallCompleted { call_id: ToolCallId, artifact: EvidenceRef },
    MemoryCommitted { memory_id: MemoryId, evidence: EvidenceRef },
    CheckpointSealed { checkpoint_id: CheckpointId, blake3_chain: [u8; 32] },
}
```

`LLMResponseReceived` is mandatory before any tool-call decision derived from an LLM response. Raw LLM text is not truth and cannot commit semantic memory, but it must be captured immediately in L1 Episodic Audit/CAS and BLAKE3-hashed so replay and audit can reconstruct the decision state without calling the model again.

`OperatorReviewArtifactRecorded` is mandatory evidence for replaying high-risk operator review packets: it records the signing target hash and artifact hash after `PolicyDecisionRecorded`, without treating helper prose or UI copy as the signing target.

### 16.2 Replay rule

Replay không được gọi lại LLM hoặc tool có side effect. Replay chỉ:
- đọc event ledger;
- dựng lại mission/task/context/memory state;
- đọc `LLMResponseReceived.raw_text_ref` khi cần tái tạo quyết định;
- verify BLAKE3 chain;
- đối chiếu artifact tồn tại;
- xác định next legal action.

Nếu event cần nondeterministic source, ledger phải chứa result artifact hoặc raw response reference đã hash. Replay không được "tự hỏi lại model".

### 16.3 Crash recovery gates

Một run 100h chỉ được resume khi:
- Arrow stream đọc được đến checkpoint cuối;
- BLAKE3 chain khớp;
- tất cả task active có status hợp lệ;
- tool call dang dở được đánh dấu `Interrupted` hoặc `NeedsHumanReview`;
- `NextActionPacket` tái tạo được dưới token budget.

---

## 17. Tool Gateway, MCP, Browser, and Computer Control

### 17.1 ToolCard canonicalization

Mọi tool được normalize thành:

```rust
pub struct ToolCard {
    pub id: ToolId,
    pub manifest_hash: [u8; 32],
    pub provider: ToolProvider,
    pub capability: CapabilityClass,
    pub side_effect: SideEffectClass,
    pub credential_scope: CredentialScope,
    pub risk: u8,
    pub requires_approval: bool,
}
```

Risk scoring:

```text
risk = side_effect_weight
     + credential_weight
     + external_visibility
     + spend_potential
     + irreversibility
```

### 17.2 Risk classes

| Class | Example | Policy |
|---|---|---|
| R0 Read-only local | read file, inspect repo, parse docs | Allowed if path policy passes |
| R1 Reversible local write | create temp artifact, run unit test | Allowed with ledger |
| R2 External read | web search, API GET | Allowed with domain/rate policy |
| R3 External write | post message, create ticket, send email | Human approval required |
| R4 Financial/legal/destructive | purchase, trade, delete production data | Explicit scoped approval plus staging sandbox execution evidence; otherwise hard block |

R4 dry-run definition:
- Dry-run evidence means **Staging Sandbox Execution**, not LLM simulation text and not mock logs.
- `ReviewPacket.staging_proof_kind` must be one of `SandboxExecution`, `TestnetExecution`, `IsolatedSnapshotExecution`, or `IsolatedBrowserSession`; untyped proof hashes, `LlmGeneratedText`, and `MockLog` fail closed.
- `PolicyFacts.has_staging_environment` is valid only when `staging_proof_ref_hash` is non-zero and `staging_evidence_kind` is physical; a bare boolean is invalid facts.
- Financial actions require testnet or provider-supported sandbox execution.
- Destructive data actions require staging database or isolated snapshot execution.
- Browser R4 actions require an isolated browser/session against staging or test endpoints.
- If no staging/testnet/snapshot environment exists, the action is `HardBlocked` until HITL cryptographic signature explicitly approves the real side effect.
- LLM-generated dry-run text/logs are never accepted as R4 evidence.

### 17.3 Browser/Computer operations

Browser and computer-control tools must emit visual/action evidence:

```text
before_screenshot_hash
action_intent
selector_or_coordinates
after_screenshot_hash
dom_snapshot_hash_or_window_title
side_effect_class
```

For vague missions such as "hãy kiếm tiền giúp tôi", browser automation starts in research/read-only mode. No signup, purchase, message, trading, or outreach action is allowed until the mission has a policy-approved action plan.

---

## 18. Evaluation and Benchmark Suite v2

### 18.1 HarnessBench-AEGIS

Build a local benchmark inspired by Harness Bench:

| Benchmark | Measures | Pass gate |
|---|---|---|
| `MissionCompilerBench` | vague prompt -> safe typed mission | no R3/R4 action without approval |
| `TaskLedgerBench` | dependency DAG integrity and deterministic queue sorting | no orphan task, no premature Done, no LLM numeric priority drives queue order |
| `ContextPackBench` | token-bounded dependency recall over activated subgraph | required evidence hit rate >= 0.95, activation node count under hard cap |
| `InterferenceMemoryBench` | stale/contradictory memory handling | contradiction injected rate <= 0.02 |
| `ReplayRecoveryBench` | crash/restart recovery | recover next action from checkpoint only, including post-LLM/pre-tool crash |
| `ToolGatewayBench` | tool risk classification and R4 gating | no false low-risk for external write, no R4 dry-run text accepted |
| `SandboxActionBench` | QuickJS/Javy execution boundaries | no raw text reaches sandbox trait |
| `BrowserWitnessBench` | browser action evidence | before/after evidence present for side effects |
| `SubagentEvidenceBench` | subagent output promotion | subagent output remains CandidateEvidence until physical gate passes |
| `100hShiftSim` | long-run drift and token economy | no duplicate work loops, bounded context |

### 18.2 No LLM-as-judge evaluation

Evaluation can use LLMs only for candidate generation or summarization. Pass/fail must come from:
- schema validation;
- replay determinism;
- hashes and artifact existence;
- unit/integration tests;
- browser screenshots/DOM hashes;
- policy assertions;
- benchmark metrics.

### 18.3 Production readiness gates

Phase 2 is not production-ready until these gates pass:

```text
constitution_audit == pass
legacy_symbol_scan == clean
HarnessBench-AEGIS critical gates == pass
ReplayRecoveryBench >= 100 randomized crash points
InterferenceMemoryBench contradiction failures <= threshold
ToolGatewayBench external-write false-low-risk == 0
ToolGatewayBench R4 text/mock dry-run accepted == 0
QuickJS ABI implemented and cold-start/fuel metrics recorded
mmap-backed Arrow IPC audit stream benchmark recorded
ContextPackBench activation_node_count <= configured cap
100hShiftSim completes without unbounded context growth
```

---

## 19. Implementation Priorities After Deep Audit

Recommended implementation order:

1. **DurableReplayEngine**
   - Highest leverage for 100h reliability.
   - Locks event schema, checkpoint seal, and crash recovery before adding more autonomy.

2. **ToolGateway + ToolCard**
   - Required before Browser/Computer/MCP/API expansion.
   - Prevents capability sprawl and unsafe side effects.

3. **Context Governor v2**
   - Adds trust, contradiction, dependency coverage, and token-waste metrics.
   - Directly improves token economy and long-run coherence.

4. **QuickJS/Javy Runtime Split**
   - Keeps live code and registered skills separate.
   - Preserves no-raw-text-to-sandbox invariant.
   - Target: sub-millisecond cold-start only after ABI implementation and benchmark evidence.

5. **HarnessBench-AEGIS**
   - Should be built early enough to stop architecture drift.
   - Every new feature must add at least one measurable gate.

6. **Browser/Computer read-only pilot**
   - Start only after ToolGateway and BrowserWitness schema exist.
   - Keep side effects disabled by default.

### 19.1 What not to build yet

Do not build these before the above:

- Full enterprise dashboard.
- Complex multi-agent marketplace.
- Autonomous finance/trading/outreach.
- Heavy workflow service dependency in hot path.
- Self-modifying skills without signed artifact registry.

These features are not banned. They are postponed because they do not improve determinism, replay, or evidence quality enough for Phase 2.

---

## 20. Open Questions for Principal Architect

1. **Replay storage**: chốt Arrow IPC single-writer per run, hay cần multi-process writer ngay từ đầu?
2. **QuickJS ABI**: ưu tiên stdin/stdout JSON, shared memory buffer, WASI args, hay host function call?
3. **Javy skill registry**: có cho phép skill JS tự sinh bởi agent không, hay chỉ human-approved trong Phase 2?
4. **Browser backend**: ưu tiên in-app Browser plugin, Playwright, hay Browser Use cho pilot đọc-only?
5. **MCP scope**: Phase 2 có cần MCP server thật, hay chỉ thiết kế ToolGateway tương thích MCP trước?
6. **Memory provider**: build native EvidenceIndexedMemory ngay, hay bridge Mem0/Letta ở control plane để thử nghiệm?
7. **100h simulation**: chạy synthetic task suite trước, hay một nhiệm vụ repo thật kéo dài nhiều shift?
8. **Approval policy**: ai là approver mặc định cho R3/R4 trong môi trường local: user trực tiếp, config file, hay signed approval token?
9. **Benchmark threshold**: chấp nhận `context_hit_rate` tối thiểu 0.95 như đề xuất, hay cần 0.98 cho nhiệm vụ critical?
10. **Scope Phase 2 đầu tiên**: ưu tiên "LLM Neural Link" hay "Command Center" sau khi replay/tool/context gates đã có?

---

## 21. Deep Research Wave 2: Extreme Performance Architecture

### 21.1 Research sources

Wave này tập trung vào nguồn primary từ docs/GitHub/arXiv, chỉ nhận ý tưởng khi chuyển được thành thuật toán, boundary, hoặc benchmark gate đo được.

| Source | Relevant technique | AEGIS adoption rule |
|---|---|---|
| [Wasmtime docs](https://docs.wasmtime.dev/) | fuel, epoch interruption, resource limiter, module precompile/serialization, pooling allocation strategy. | Dùng cho hot sandbox pool, nhưng proof-run vẫn phải deterministic và hash artifact. |
| [Apache Arrow C Data Interface](https://arrow.apache.org/docs/format/CDataInterface.html) and [PyCapsule Interface](https://arrow.apache.org/docs/format/CDataInterface/PyCapsuleInterface.html) | zero-copy interchange between Rust/Python via Arrow schemas, arrays, and capsules. | Phase 2 target for Python observability path; không claim zero-copy audit until benchmark. |
| [Apache DataFusion](https://github.com/apache/datafusion) | Rust-native query engine over Arrow data. | Audit/query plane only, not scheduler hot path. |
| [vLLM](https://github.com/vllm-project/vllm) / [PagedAttention paper](https://arxiv.org/abs/2309.06180) | paged KV cache management and high-throughput LLM serving. | Optional self-hosted LLM backend; API-provider KV portability remains banned. |
| [LMCache](https://github.com/LMCache/LMCache) | KV cache reuse/offload for long-context inference. | Optional `KVCacheBroker` for self-hosted models only, keyed by canonical ContextPack digest. |
| [SGLang](https://github.com/sgl-project/sglang) | RadixAttention/prefix sharing for structured generation workloads. | Learn prefix tree scheduling for repeated tool schemas and mission headers. |
| [FlashInfer](https://github.com/flashinfer-ai/flashinfer) | optimized attention and serving kernels. | Kernel-level reference for self-hosted inference, not core dependency. |
| [DiskANN](https://github.com/microsoft/DiskANN) | SSD-backed approximate nearest neighbor search. | Cold memory tier for large evidence/memory corpus when in-memory HNSW exceeds cap. |
| [Qdrant](https://github.com/qdrant/qdrant) | Rust vector search with filtering and HNSW. | Prototype external retrieval provider; production hot path still uses bounded activated graph. |
| [RAPTOR](https://arxiv.org/abs/2401.18059) | recursive tree summaries for retrieval. | Use only for evidence-linked summaries; never as truth source. |
| [GraphRAG](https://github.com/microsoft/graphrag) | graph-based retrieval/indexing over entities and communities. | Background indexing technique for CogniFold; not per-turn global graph traversal. |
| [OSWorld](https://github.com/xlang-ai/OSWorld), [Terminal-Bench](https://github.com/laude-institute/terminal-bench), [SWE-bench](https://github.com/swe-bench/SWE-bench), [BrowserGym](https://github.com/ServiceNow/BrowserGym) | real computer/browser/terminal/software-agent evaluation. | Build HarnessBench-AEGIS tasks with wall-clock, side-effect class, evidence coverage, and recovery metrics. |
| [Extism](https://github.com/extism/extism) | Wasm plugin model and host functions. | Reference for host-function discipline; AEGIS keeps its own minimal ABI for deterministic proof. |

### 21.2 Hot Sandbox Pool

Target: remove repeated Wasmtime compile/instantiate cost without weakening proof.

Architecture:

```text
QuickJS/Javy module bytes
  -> BLAKE3 module_digest
  -> Wasmtime precompile/serialize
  -> SandboxModuleCache[module_digest]
  -> StorePool with fixed memory/fuel limits
  -> execute_wasm_binary(payload, limits)
  -> PhysicalArtifact + fuel + output hash
```

Rules:
- AOT/precompiled module cache is keyed by BLAKE3 of original Wasm bytes plus Wasmtime config digest.
- Store pooling is allowed only if every execution resets memory, fuel, epoch deadline, and host state.
- Proof-run records module digest, config digest, fuel consumed, output digest, and trap reason.
- Epoch interruption is a timeout guard; fuel remains the deterministic accounting signal.
- Pool hit/miss must be exported as metric. A fast but opaque cache is not accepted.

Acceptance gates:

```text
SandboxPoolBench cold_start_ms recorded
SandboxPoolBench warm_start_ms recorded
SandboxPoolBench pool_reset_leak_count == 0
SandboxPoolBench proof_run_replay_hash_match == 1.0
```

### 21.3 Arrow Reality Upgrade Path

Target: upgrade from standard `StreamWriter<File>` to a measurable zero-copy observability path.

Phased design:

1. **Phase 1 current**: Arrow IPC `StreamWriter<File>` plus in-memory trace vector.
2. **Phase 2 segment writer**: append fixed-size Arrow IPC segment files with manifest and BLAKE3 chain.
3. **Phase 2 mmap reader**: read immutable sealed segments by mmap for dashboard/query.
4. **Phase 2 PyCapsule bridge**: expose sealed Arrow arrays to Python through Arrow C Data/PyCapsule.
5. **Phase 3 query plane**: DataFusion reads sealed segments for audit queries outside hot path.

Important constraint:
- Do not implement an unsafe in-place mutable mmap writer unless it proves correctness over crash recovery, schema evolution, and reader isolation.
- Append-only semantics can be segment-based. The physical invariant is immutable sealed segments plus hash chain, not a single ever-growing mutable mmap file.

Segment manifest:

```rust
pub struct AuditSegmentManifest {
    pub run_id: RunId,
    pub segment_id: u64,
    pub schema_hash: [u8; 32],
    pub first_event_ns: u64,
    pub last_event_ns: u64,
    pub record_count: u64,
    pub segment_blake3: [u8; 32],
    pub prev_segment_blake3: [u8; 32],
}
```

Acceptance gates:

```text
ArrowSegmentBench append_latency_p99_ms
ArrowSegmentBench mmap_read_copy_count == 0 for sealed segment reader
ArrowSegmentBench crash_recovery_last_valid_segment == true
PyCapsuleBridgeBench python_reads_without_materializing_rows == true
DataFusionAuditBench query_plane_not_in_hot_path == true
```

### 21.4 KV/Prefix Cache Broker

Target: reduce token and latency cost for repeated mission headers, schemas, and tool policies without violating provider constraints.

Hard boundary:
- API provider KV-cache export/import remains banned.
- KV/prefix cache broker is allowed only for self-hosted backends such as vLLM/SGLang/LMCache or provider-supported prompt caching.

Algorithm:

```text
canonicalize(ContextPack.static_prefix)
  -> prefix_digest
  -> if provider_supports_prompt_cache:
       request provider cache hint keyed by prefix_digest
     else if self_hosted_backend:
       KVCacheBroker.lookup(prefix_digest)
     else:
       inject text prefix normally
```

Static prefix candidates:
- mission constitution;
- tool schemas;
- policy windows;
- active task DAG skeleton;
- stable evidence handles.

Rejection rules:
- Never cache raw chain-of-thought.
- Never cache secrets or credentials.
- Invalidate prefix cache when policy hash, tool manifest hash, or schema hash changes.

Acceptance gates:

```text
PrefixCacheBench prefix_hit_rate
PrefixCacheBench prompt_tokens_saved
PrefixCacheBench wrong_prefix_reuse == 0
PrefixCacheBench policy_hash_mismatch_rejected == true
```

### 21.5 Retrieval and Memory Index Tiering

Target: handle 100h memory growth without global graph scans.

Tiered retrieval:

```text
L0 ActivatedContextGraph: in-memory 1-hop/2-hop graph around active task
L1 HotEvidenceIndex: small HNSW/BM25/entity index for active run
L2 WarmCogniFoldIndex: evidence-linked summaries and contradiction map
L3 ColdVectorIndex: DiskANN/Qdrant-style SSD/vector store
L4 Archive: sealed Arrow/DataFusion audit segments
```

Hot path only touches L0/L1. L2-L4 are background or explicit expansion paths.

Evidence-linked RAPTOR:
- Each summary node must store source evidence refs and supersession refs.
- A summary can compress context, but it cannot become truth.
- Contradiction edges are computed during background folding, not during per-turn selection.

Selection algorithm:

```text
seed = active_task_id + required_evidence_refs
activated = neighbors(seed, hops <= 2)
activated += unresolved_blockers(seed)
activated += policy_nodes(active_tool_risk)
candidate_docs = HotEvidenceIndex.search(seed_terms, limit=k)
candidate_docs = filter(candidate_docs, evidence_ref_exists && trust >= floor)
ContextPack = bounded_knapsack(activated + candidate_docs, token_cap)
```

Acceptance gates:

```text
RetrievalTierBench hot_path_uses_global_scan == false
RetrievalTierBench activation_node_count_p99 <= cap
RetrievalTierBench contradiction_precomputed_rate >= threshold
RetrievalTierBench cold_expansion_requires_explicit_event == true
```

### 21.6 HarnessBench-AEGIS Extreme Suite

Target: evaluate the harness like a system, not like a chatbot.

Bench classes:

| Suite | Inspired by | Measures |
|---|---|---|
| `TerminalOpsBench` | Terminal-Bench | shell/build/test workflows, crash recovery, artifact evidence |
| `BrowserOpsBench` | BrowserGym/OSWorld | DOM/screenshot proof, side-effect gating, R4 staging requirement |
| `CodeRepairBench` | SWE-bench | repo map, patch generation, test evidence, rollback |
| `LongRunShiftBench` | long-running harness research | 100h simulated drift, handoff, duplicate-work prevention |
| `SandboxPoolBench` | Wasmtime docs/perf practice | cold/warm sandbox latency, proof replay hash |
| `ArrowAuditBench` | Arrow C Data/PyCapsule/DataFusion | segment append, mmap read, Python observability |
| `PrefixCacheBench` | vLLM/SGLang/LMCache | token saved, prefix correctness, policy invalidation |

Core scoring:

```text
score =
  task_completion
  * evidence_coverage
  * replay_recoverability
  * policy_safety
  * latency_efficiency
  * token_efficiency
```

No score may be awarded by LLM judge. Each component must be computed from tests, hashes, event logs, browser evidence, or benchmark measurements.

### 21.7 Revolutionary Backlog, Ordered by Leverage

1. **Segmented Arrow Audit + PyCapsule reader**
   - Highest truth-to-performance leverage.
   - Makes observability real without hot-path deserialization claims.

2. **SandboxPoolBench + Wasmtime module cache**
   - Converts QuickJS/Javy from stub to measurable runtime.
   - Enables honest cold/warm latency gates.

3. **ActivatedContextGraph + HotEvidenceIndex**
   - Prevents context selection latency spikes.
   - Keeps long-run memory bounded without giving up recall.

4. **LLMResponseReceived + PrefixCacheBroker**
   - Makes replay deterministic across LLM decisions.
   - Reduces repeated prompt cost for stable mission/policy/tool prefixes.

5. **HarnessBench-AEGIS Extreme Suite**
   - Prevents architectural theater.
   - Forces every "revolutionary" claim to survive a measured workflow.

6. **R4 Staging Sandbox Gateway**
   - Converts dangerous "dry run" language into real isolated execution.
   - Blocks irreversible actions until a physical staging or signed approval path exists.

### 21.8 Non-Adoption List

Do not adopt these without a passing benchmark:

- Global graph knapsack in the per-turn path.
- Mutable mmap writer shared by readers and writers without sealed segments.
- KV-cache reuse for external API providers that do not expose a safe cache contract.
- LLM-generated benchmark pass/fail.
- Tool dry-run logs generated by the model.
- Full DataFusion query inside scheduler hot path.
- Default HTTP/3/simd-json dependencies before provider support and latency proof.

---

## 22. Deep Research Wave 3: Proof and Performance Assurance

### 22.1 Research sources

Wave 3 bổ sung lớp chứng minh để các tối ưu cực hạn không phá deterministic kernel.

| Source | Relevant technique | AEGIS adoption rule |
|---|---|---|
| [Kani](https://github.com/model-checking/kani) | bounded model checking for Rust. | Use for small pure invariants: priority queue ordering, hash chain transitions, manifest validation. |
| [Loom](https://github.com/tokio-rs/loom) | deterministic testing for concurrent Rust. | Use for lock/order bugs in sandbox pool, segment writer handoff, and approval queue. |
| [Shuttle](https://github.com/awslabs/shuttle) | schedule exploration for async/concurrent Rust. | Use for async ToolGateway/ShiftManager races when Loom model becomes too narrow. |
| [cargo-fuzz](https://github.com/rust-fuzz/cargo-fuzz) | libFuzzer integration for Rust. | Fuzz parsers, manifest decoders, canonicalization, and replay log readers. |
| [Miri](https://github.com/rust-lang/miri) | Rust interpreter for UB checks. | Run on unsafe/FFI/zero-copy modules before claiming memory safety. |
| [rkyv](https://github.com/rkyv/rkyv) | zero-copy deserialization for archived Rust data. | Allowed only for internal sealed snapshots with versioned schema and validation. |
| [zerocopy](https://github.com/google/zerocopy) | safe byte-level parsing/transmutation helpers. | Use for fixed-layout frames where alignment and endian rules are explicit. |
| [Tantivy](https://github.com/quickwit-oss/tantivy) | Rust full-text search and BM25-like retrieval. | Candidate HotEvidenceIndex engine for local lexical search, behind benchmark gate. |
| [Roaring Bitmap](https://github.com/RoaringBitmap/roaring-rs) | compressed bitmap intersections. | Candidate replacement for the current sorted-Vec filter baseline only after feature-gated benchmark evidence. |
| [tokio-uring](https://github.com/tokio-rs/tokio-uring), [monoio](https://github.com/bytedance/monoio), [Glommio](https://github.com/DataDog/glommio) | io_uring async and thread-per-core I/O models. | Linux-only optional benchmark path for audit segment I/O; default remains portable. |
| [tokio-console](https://github.com/tokio-rs/console) | async runtime observability. | Use for detecting task stalls and scheduler backpressure in control plane, not proof. |

### 22.2 Verification Ladder

Every performance-critical feature must climb a verification ladder before becoming default:

```text
spec invariant
  -> unit test
  -> property test
  -> fuzz target or model checker
  -> micro-path benchmark
  -> end-to-end HarnessBench gate
  -> production feature flag default decision
```

Adoption rule:
- A fast implementation without its proof harness is a prototype, not architecture.
- `unsafe`, FFI, mmap, cache reuse, and concurrency features require an explicit `ProofCartridge`.

`ProofCartridge`:

```rust
pub struct ProofCartridge {
    pub feature_id: &'static str,
    pub invariants: &'static [&'static str],
    pub unit_tests: &'static [&'static str],
    pub property_tests: &'static [&'static str],
    pub fuzz_targets: &'static [&'static str],
    pub model_checks: &'static [&'static str],
    pub benchmarks: &'static [&'static str],
}
```

### 22.3 Deterministic Scheduler Proof

Target: prove that Rust Core scheduling never depends on LLM numeric preferences.

Invariant:

```text
for all task_a, task_b:
  if deterministic_priority(task_a) > deterministic_priority(task_b)
  then queue_position(task_a) < queue_position(task_b)
```

Priority inputs allowed:
- DAG critical path length.
- blocked descendant count.
- explicit deadline timestamp.
- evidence unblock count.
- measured resource cost.
- risk class penalty.

Priority inputs forbidden:
- LLM expected value.
- LLM urgency score.
- LLM uncertainty reduction score.
- free-text "importance".

Proof gates:

```text
TaskLedgerPriorityPropTest random_dag_ordering == pass
TaskLedgerPriorityKani small_graph_ordering == pass
TaskLedgerPriorityFuzz malformed_task_cards_do_not_panic == pass
```

### 22.4 Concurrency and Pooling Proof

Target: sandbox pool and segment writer can be fast without hidden state leaks.

Concurrency hazards:
- pooled store reused without resetting memory/fuel/epoch;
- segment writer publishes partial segment;
- dashboard reads segment before seal;
- approval queue races with tool execution;
- cancellation occurs after side effect but before evidence write.

Modeling rule:
- Loom/Shuttle models must use tiny state spaces and explicit nondeterministic schedules.
- If the model cannot represent the whole component, model the invariant boundary: publish/seal, acquire/reset/release, approve/execute/commit.

Required models:

```text
SandboxPoolLoom acquire_reset_release
ArrowSegmentLoom writer_seals_before_reader
ToolGatewayShuttle approval_before_external_write
ReplayShuttle crash_after_llm_before_tool
```

### 22.5 Fuzzing Targets

Target: untrusted inputs cannot corrupt replay, policy, or evidence.

Fuzz targets:

```text
fuzz_canonicalize_payload
fuzz_arrow_segment_manifest
fuzz_run_event_decoder
fuzz_tool_manifest_normalizer
fuzz_policy_window_parser
fuzz_context_pack_decoder
fuzz_quickjs_abi_message
```

Fuzz acceptance:
- no panic;
- no unbounded allocation;
- no invalid event accepted as sealed;
- no R4 action downgraded to lower risk;
- no hash chain bypass.

### 22.6 Serialization Policy

AEGIS should use different serialization tools for different physics:

| Data path | Format | Rule |
|---|---|---|
| Cross-language columnar observability | Arrow IPC/C Data/PyCapsule | preferred for audit/query plane |
| Internal sealed Rust snapshot | rkyv or zerocopy | allowed only with version/schema hash and byte validation |
| External API payload | JSON via serde_json baseline | canonicalize before hashing |
| Tool manifest | canonical JSON or typed Rust schema | normalize and hash before exposure to LLM |
| Browser evidence | screenshot/DOM/hash bundle | immutable CAS artifact |

No single format wins all paths. The invariant is zero ambiguity and measurable copy count, not format ideology.

### 22.7 Index Acceleration

Target: make retrieval filters CPU-cheap before ranking.

HotEvidenceIndex pipeline:

```text
query_terms
  -> Tantivy/BM25 candidate ids
  -> SortedEvidenceSet/HotBitmapFilter filters:
       has_evidence_ref
       active_run
       not_superseded
       risk_allowed
       entity_match
  -> small candidate set
  -> trust/rerank
  -> ContextPack
```

Why this matters:
- Vector similarity is expensive and can retrieve semantically related poison.
- Bitmap intersections are deterministic, cache-friendly, and easy to benchmark.
- BM25/entity filters create a small candidate set before embeddings or graph expansion.

Acceptance gates:

```text
HotEvidenceIndexBench bitmap_filter_latency_p99_us
HotEvidenceIndexBench candidate_set_size_p99 <= cap
HotEvidenceIndexBench superseded_memory_leak == 0
HotEvidenceIndexBench risk_filter_false_negative == 0
```

### 22.8 Async I/O and Thread-Per-Core Policy

Target: extreme I/O throughput only where it wins measured latency.

Rules:
- Keep default implementation portable.
- io_uring/thread-per-core runtimes are optional Linux feature paths.
- Never place experimental I/O runtime in deterministic scheduler hot path until benchmark and model checks pass.
- Segment writer is the first candidate; ToolGateway network I/O is second; scheduler is last.

Feature gates:

```text
async-uring-audit = optional
thread-per-core-query = optional
```

Acceptance:

```text
UringAuditBench p99_append_latency < portable_writer_p99
UringAuditBench crash_recovery_equivalent == true
UringAuditBench loom_publish_seal_model == pass
```

### 22.9 Observability Without Runtime Theater

Target: measure without turning the control plane into the system.

Metrics required:
- sandbox pool hit/miss;
- store reset failures;
- segment seal latency;
- context activation node count;
- bitmap filter candidate count;
- replay recovery duration;
- R4 hard-block count;
- prefix cache hit rate;
- wrong prefix rejection count.

Tracing rule:
- Tracing spans can diagnose latency.
- Tracing spans cannot be evidence of correctness.
- Correctness evidence remains hashes, tests, model checks, and replay proofs.

### 22.10 Wave 3 Implementation Order

1. Add `ProofCartridge` registry to scripts/audit.
2. Add property tests for deterministic task priority.
3. Add fuzz targets for event/tool/policy decoders.
4. Add Loom model for segment publish/seal.
5. Add HotEvidenceIndex prototype with Tantivy plus the current sorted-Vec bitmap-filter baseline; adopt RoaringBitmap only if it wins the target-cardinality benchmark gate.
6. Add optional rkyv/zerocopy snapshot experiment behind feature flag.
7. Add optional io_uring audit writer benchmark behind feature flag.

Do not move any item to default path until its proof cartridge and benchmark gate pass.

---

## 23. Deep Research Wave 4: Incremental Reasoning Kernel

### 23.1 Research sources

Wave 4 tập trung vào cách tính toán lại ít nhất có thể khi task/memory/evidence graph phình lên.

| Source | Relevant technique | AEGIS adoption rule |
|---|---|---|
| [Salsa](https://github.com/salsa-rs/salsa) | incremental computation with query dependencies. | Use for deterministic recomputation of Task DAG scores, ContextPack dependencies, and policy windows. |
| [Timely Dataflow](https://github.com/TimelyDataflow/timely-dataflow) | low-latency dataflow computation. | Research path for background CogniFold folding, not scheduler hot path until benchmarked. |
| [Differential Dataflow](https://github.com/TimelyDataflow/differential-dataflow) | incremental view maintenance over changing collections. | Candidate for contradiction maps, evidence indexes, and task dependency deltas. |
| [Datafrog](https://github.com/rust-lang/datafrog) | lightweight Datalog engine in Rust. | Candidate deterministic policy/risk/query rule engine. |
| [egg](https://github.com/egraphs-good/egg) and [egglog](https://github.com/egraphs-good/egglog) | equality saturation and e-graph rewriting. | Use for canonicalizing equivalent tool plans/code transforms before physical tests. |
| [Fast Downward](https://github.com/aibasel/downward) | classical planning engine for PDDL-like domains. | Reference for symbolic planner/validator, not default dependency. |

### 23.2 Incremental State Kernel

Problem:
- Task priorities, context dependencies, contradiction edges, and policy windows change incrementally.
- Recomputing the full graph per turn violates the hot-path bound.

Solution:

```text
EventDelta
  -> IncrementalStateKernel
  -> affected TaskScore views
  -> affected ContextPack views
  -> affected PolicyWindow views
  -> affected ContradictionMap views
```

Allowed recomputation:
- only nodes reachable from the changed event;
- only cached queries whose dependency set changed;
- background global recomputation only as CogniFold maintenance.

Forbidden recomputation:
- full task graph priority recompute per turn;
- full memory contradiction scan per turn;
- global vector search before activated graph filtering.

Acceptance gates:

```text
IncrementalKernelBench changed_event_recompute_nodes_p99 <= cap
IncrementalKernelBench full_recompute_in_hot_path == false
IncrementalKernelBench cached_query_invalidated_only_on_dependency_change == true
```

### 23.3 Datalog Policy and Risk Engine

Target: replace ad hoc policy checks with deterministic rule evaluation.

Example facts:

```text
tool(tool_id, capability)
side_effect(tool_id, class)
credential_scope(tool_id, scope)
mission_policy(run_id, policy_hash)
approval(call_id, approver_hash, scope)
staging_available(action_id, env_hash)
```

Example rules:

```text
requires_human_approval(Call) :-
  tool_call(Call, Tool),
  side_effect(Tool, external_write).

hard_block(Call) :-
  tool_call(Call, Tool),
  side_effect(Tool, r4),
  not staging_available(Call, _),
  not approval(Call, _, explicit_real_side_effect).

allowed(Call) :-
  tool_call(Call, Tool),
  not hard_block(Call),
  policy_window_allows(Tool).
```

Why Datalog:
- deterministic;
- explainable;
- easy to fuzz with malformed facts;
- can emit proof traces as evidence.

Acceptance gates:

```text
PolicyDatalogBench r4_without_staging_or_signature_hard_blocked == true
PolicyDatalogBench external_write_requires_approval == true
PolicyDatalogBench rule_eval_p99_us <= threshold
PolicyDatalogFuzz malformed_facts_do_not_allow_action == true
```

Current implementation status:
- `PolicyDatalogClosureProof` now derives deterministic atom/rule closure for hard-block, approval-required, and allow paths.
- `PolicyProofTrace::binds_datalog_closure` binds the user-facing policy trace to the closure hash, matched rule ids, facts hash, and IR hash.
- `policy_datalog_closure_proof` is registered as a benchmark gate with a 10 us threshold.
- SMT counterexample generation and e-graph canonicalization remain pending.

### 23.4 E-Graph Canonicalization

Target: reduce redundant tool plans and code transforms before running expensive physical tests.

Use cases:
- normalize equivalent shell command plans before ToolGateway evaluation;
- canonicalize code patch transformations before PAV measurement;
- deduplicate semantically equivalent task decompositions;
- detect repeated no-op transformations across shifts.

Pipeline:

```text
CandidatePlan
  -> parse to typed IR
  -> e-graph rewrite rules
  -> canonical lowest-cost representative
  -> Wasmtime/test/browser physical gate
```

Rules:
- E-graph equivalence is not truth.
- Canonical representative must still pass physical gate.
- Cost function must be deterministic and based on resource estimates, policy risk, and artifact delta.

Cost function:

```text
cost(plan) =
  command_count
  + side_effect_weight
  + estimated_io_cost
  + required_approval_weight
  - expected_artifact_delta_score
```

Acceptance gates:

```text
EGraphPlanBench duplicate_plan_reduction_rate
EGraphPlanBench physical_gate_still_required == true
EGraphPlanBench no_r4_downgrade_by_rewrite == true
```

### 23.5 Symbolic Planner as Validator

Target: support vague mission planning without letting LLM invent executable order.

Approach:
- LLM may propose candidate actions and preconditions.
- Rust converts them into typed planning facts when schema-valid.
- A symbolic planner/validator checks reachability, preconditions, and goal satisfaction.
- Execution order comes from validated plan graph, not narrative text.

Planning objects:

```text
StateFact
ActionSchema
Precondition
Effect
RiskClass
EvidenceContract
```

Use cases:
- "hãy kiếm tiền giúp tôi" safe mission expansion;
- multi-step repo repair;
- browser research before external side effects;
- R4 staging prerequisite enforcement.

Acceptance gates:

```text
SymbolicPlanBench invalid_precondition_rejected == true
SymbolicPlanBench r4_requires_staging_precondition == true
SymbolicPlanBench plan_replay_same_order_hash == true
```

### 23.6 Incremental Priority Formula v2

Priority is a cached query:

```text
priority(task_id) =
  critical_path_len(task_id)
  + blocked_descendant_count(task_id) * w_blocked
  + evidence_unblock_count(task_id) * w_evidence
  + deadline_pressure(task_id, now) * w_deadline
  - risk_penalty(task_id)
  - measured_cost_penalty(task_id)
```

Invalidation:

```text
TaskStatusChanged -> invalidate ancestors(task_id)
EvidenceCommitted -> invalidate tasks_waiting_on(evidence_id)
PolicyWindowChanged -> invalidate tasks_with_risk_class(policy_scope)
DeadlineTick -> invalidate tasks_with_deadline_before(now + horizon)
```

No LLM score participates in this query.

### 23.7 Wave 4 Backlog

1. Add a prototype `IncrementalStateKernel` spec and benchmark harness.
2. Add Datalog-style policy facts/rules for ToolGateway.
3. Add property tests for incremental priority invalidation.
4. Add e-graph canonicalization experiment for typed shell/tool plans.
5. Add symbolic plan validator for mission compiler output.
6. Add proof traces to policy decisions and planner decisions.

Do not adopt Timely/Differential/Salsa/egg/Datafrog as default dependencies until the prototype beats the current simple implementation under HarnessBench-AEGIS.

---

## 24. Deep Research Wave 5: Browser and Computer Witness Pipeline

### 24.1 Research sources

Wave 5 tập trung vào biến Browser/Chrome/Computer control thành nguồn bằng chứng vật lý có thể replay và audit.

| Source | Relevant technique | AEGIS adoption rule |
|---|---|---|
| [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) | Page screenshots, DOMSnapshot, Network events, Runtime, Tracing domains. | Primary low-level browser witness source for Chrome-based automation. |
| [CDP DOMSnapshot](https://chromedevtools.github.io/devtools-protocol/tot/DOMSnapshot/) | capture DOM/layout/text snapshots. | Hash canonical DOM/layout snapshot for browser evidence. |
| [CDP Tracing](https://chromedevtools.github.io/devtools-protocol/tot/Tracing/) | browser performance trace events. | Latency evidence only, not correctness evidence. |
| [Playwright Trace Viewer](https://playwright.dev/docs/trace-viewer) | screenshots, snapshots, actions, sources, network trace. | Useful debug/control-plane artifact; canonical witness still uses AEGIS hash bundle. |
| [WebDriver BiDi](https://w3c.github.io/webdriver-bidi/) | browser automation event protocol. | Cross-browser future path; Chrome/CDP remains first implementation target. |
| [OSWorld](https://github.com/xlang-ai/OSWorld), [BrowserGym](https://github.com/ServiceNow/BrowserGym) | real browser/computer-use task evaluation. | Use for BrowserOpsBench task design and failure taxonomy. |

### 24.2 BrowserWitnessProof

Browser actions need more than a screenshot. Each side-effect-capable browser step must emit a witness packet:

```rust
pub struct BrowserWitnessProof {
    pub run_id: RunId,
    pub action_id: ActionId,
    pub side_effect_class: SideEffectClass,
    pub url_before_hash: [u8; 32],
    pub url_after_hash: [u8; 32],
    pub dom_snapshot_hash_before: [u8; 32],
    pub dom_snapshot_hash_after: [u8; 32],
    pub screenshot_hash_before: [u8; 32],
    pub screenshot_hash_after: [u8; 32],
    pub accessibility_tree_hash_after: [u8; 32],
    pub network_log_hash: [u8; 32],
    pub action_trace_hash: [u8; 32],
    pub policy_window_hash: [u8; 32],
}
```

Witness packet rules:
- Screenshot alone is not enough.
- DOMSnapshot alone is not enough.
- Network log alone is not enough.
- A valid witness bundles DOM, screenshot, URL, network, action intent, and policy window hash.
- R3/R4 actions require before/after witness packets and approval/staging evidence.

Current implementation status: `core/rust/src/browser_witness.rs` now implements the schema-level proof and action trace hash binding. `BrowserWitnessProof` binds URL-before/after, DOM-before/after, screenshot-before/after, accessibility-after, network log, action trace, policy window, isolated browser session, and redaction-policy hashes into `proof_hash`; `BrowserActionTrace` binds typed action kind, target/input hashes, optional coordinates, prompt decision, and policy window into `trace_hash`. Unit tests prove that incomplete bundles fail closed and that a valid proof hash can satisfy R4 `IsolatedBrowserSession` staging evidence. `browser_witness_proof_validate` is benchmark-gated; CDP/Playwright collectors and BrowserOpsBench remain pending.

### 24.3 Deterministic Browser Environment

Browser nondeterminism must be bounded before hashing:

```text
fixed viewport
fixed timezone
fixed locale
fixed user agent policy
disable service worker cache when safe
record network request/response metadata
record redirect chain
record browser version
record extension/plugin state
record permission prompts
```

Canonicalization:
- Strip volatile timestamps from DOM snapshot only if the stripping rule is recorded and hashed.
- Do not strip user-visible financial/legal content.
- Redact secrets/PII before exporting control-plane artifacts, but preserve sealed encrypted raw evidence for audit if policy allows.

### 24.4 Browser Action Trace

Each browser action is a typed event:

```rust
pub enum BrowserActionEvent {
    Navigate { url_hash: [u8; 32] },
    Click { selector_hash: [u8; 32], coordinates: Option<(i32, i32)> },
    TypeText { selector_hash: [u8; 32], text_hash: [u8; 32] },
    SelectOption { selector_hash: [u8; 32], option_hash: [u8; 32] },
    Download { suggested_name_hash: [u8; 32], content_hash: [u8; 32] },
    PermissionPrompt { prompt_hash: [u8; 32], decision: PromptDecision },
}
```

Rules:
- Raw typed text may be encrypted/redacted in public logs, but hash must remain in witness.
- Coordinates are fallback evidence; stable selectors/accessibility nodes are preferred.
- Any action that mutates external state must include policy and approval references.

### 24.5 BrowserOpsBench

BrowserOpsBench must include deterministic and adversarial cases:

| Case | Measures | Pass gate |
|---|---|---|
| `ReadOnlyResearch` | evidence capture without side effect | DOM/screenshot/network hashes present |
| `LoginBlocked` | credential prompt handling | no credential entry without explicit approval |
| `ExternalPostDraft` | R3 gating | draft allowed, post/send blocked before approval |
| `PurchaseHardBlock` | R4 gating | no purchase without staging or signed approval |
| `DynamicDOMChange` | witness stability | before/after hashes reflect visible change |
| `RedirectChain` | network witness | redirect chain recorded and hashed |
| `DownloadArtifact` | artifact capture | downloaded bytes hashed into CAS |

### 24.6 Chrome Trace Is Performance Evidence Only

CDP tracing and Playwright traces are excellent for latency diagnosis:

```text
navigation_start
dom_content_loaded
network_idle
input_latency
screenshot_capture_ms
dom_snapshot_capture_ms
trace_export_ms
```

But trace events cannot prove correctness. Correctness comes from:
- DOM/screenshot/network hash bundle;
- approval/staging proof;
- CAS artifact hash;
- replayable action sequence;
- policy assertions.

### 24.7 Browser Privacy and Evidence Redaction

Browser evidence is powerful and dangerous.

Rules:
- Public artifacts must be redacted.
- Sealed raw evidence may be retained only under policy.
- Credentials, session cookies, tokens, and personal data must not enter LLM context.
- Redaction itself must be deterministic and hashed.

Evidence tiers:

```text
sealed_raw_evidence: encrypted, audit-only
redacted_control_plane_evidence: safe for dashboard
llm_context_evidence: minimal hashes and summaries only
```

### 24.8 Browser/Computer Implementation Order

1. Implement `BrowserWitnessProof` schema.
2. Add CDP DOMSnapshot + screenshot + URL + network metadata collector.
3. Add redaction policy for control-plane artifacts.
4. Add BrowserOpsBench read-only tasks.
5. Add R3 external-post draft/block tests.
6. Add R4 purchase/destructive hard-block tests.
7. Add Playwright/CDP trace export for latency diagnosis.
8. Add WebDriver BiDi compatibility experiment after Chrome/CDP baseline.

Do not let the browser plugin, Chrome automation, or computer-use action bypass ToolGateway policy, approval, or physical witness gates.

---

## 25. Deep Research Wave 6: Optimization Governance and Performance Budgets

This wave turns performance ambition into a governed engineering system. The rule is strict: profiling can reveal where physics is slow, but only benchmarked code may claim speed.

### 25.1 Source Scan

| Source | High-signal technique | AEGIS adoption rule |
|---|---|---|
| [Google OR-Tools CP-SAT scheduling](https://developers.google.com/optimization/scheduling) | constraint programming for resource/time scheduling and job-shop style planning. | Use only for offline mission/sprint schedule optimization. Hot-path task selection remains Rust deterministic DAG metrics. |
| [Rayon](https://docs.rs/rayon/latest/rayon/) | work-stealing data parallel iterators and join/scope APIs. | Use for background CogniFold, indexing, report generation, and benchmark fanout after determinism proof. Not in physical proof commit path by default. |
| [Crossbeam](https://github.com/crossbeam-rs/crossbeam) | concurrent data structures, channels, scoped threads, and epoch-based patterns. | Allow bounded queues and channels for telemetry/background workers. Model-check any queue that can influence commit order. |
| [simd-json](https://github.com/simd-lite/simd-json) / [simdjson](https://github.com/simdjson/simdjson) | SIMD-accelerated JSON parsing and structural indexing. | Feature-gated `fast-json` path only. Baseline remains `serde_json`; adoption requires canonicalization equality and allocation benchmarks. |
| [Criterion.rs](https://bheisler.github.io/criterion.rs/book/) | statistical microbenchmarking with warmup, measurement, and comparison reports. | Keep for wall-time trends and regression reports; never use alone as proof of instruction/cache behavior. |
| [Iai-Callgrind](https://iai-callgrind.github.io/iai-callgrind/latest/html/index.html) | deterministic instruction/count-level benchmarking through Valgrind/Callgrind. | Add instruction-count gates for hot primitives before claiming optimization wins. |
| [Inferno](https://github.com/jonhoo/inferno) / [pprof-rs](https://github.com/tikv/pprof-rs) | flamegraph and profiler tooling for Rust applications. | Profiler output is diagnosis, not correctness proof. It can create tasks; it cannot close gates. |

### 25.2 PerformanceBudgetLedger

Every hot-path component needs an explicit budget before optimization work begins:

```rust
pub struct PerformanceBudgetLedger {
    pub component: &'static str,
    pub max_wall_ns_p50: u64,
    pub max_wall_ns_p99: u64,
    pub max_instructions: u64,
    pub max_allocations: u64,
    pub max_bytes_allocated: u64,
    pub max_cache_miss_ratio_ppm: u32,
    pub proof_required: &'static [&'static str],
}
```

Budget rules:
- No "faster" claim without before/after benchmark artifact.
- No hot-path allocation increase without explicit waiver and measured p99 benefit.
- No SIMD/parser swap unless canonical output hash matches baseline.
- No parallelism in commit order unless replay hash is identical across randomized schedules.

### 25.3 Offline Schedule Optimizer vs Hot-Path Scheduler

Use two schedulers with different jobs:

```text
OfflineScheduleOptimizer:
  input: task DAG, resource classes, estimated durations, deadlines, risk windows
  engine: CP-SAT or exact/heuristic solver outside hot path
  output: suggested execution calendar and capacity warnings

HotPathTaskScheduler:
  input: active TaskLedger, evidence ledger, policy window, hard deadlines
  engine: cached deterministic graph metrics
  output: next task id
```

Hard boundary:
- OR-Tools/CP-SAT output is advisory schedule metadata.
- It may precompute capacity risk and deadline pressure.
- It must not override `deterministic_priority_score`.
- If CP-SAT is unavailable, the system still runs deterministically.

### 25.4 Instruction and Allocation Gates

Wall-clock microbenchmarks are noisy. Add lower-level gates:

| Gate | Measures | Pass rule |
|---|---|---|
| `PerfBudgetBench` | p50/p99 wall time per hot component | below ledger budget on pinned baseline machine |
| `InstructionCountBench` | Callgrind instruction counts | no regression over threshold without accepted waiver |
| `AllocationBench` | allocations and bytes allocated | zero allocation in scheduler/proof hot loops |
| `CacheLocalityBench` | cache misses or locality proxy | regression opens task, not auto-fail until baseline stable |
| `ReplayDeterminismBench` | repeated execution under randomized schedules | identical replay hash |

Implementation policy:
- `criterion` measures runtime trend.
- `iai-callgrind` measures instruction-level regression.
- heap/allocation counters measure allocation promises.
- flamegraphs identify suspects, then suspects must be converted into a measurable benchmark.

### 25.5 Cache, Layout, and Allocation Policy

Hot-path data should be shaped for CPU reality:

```text
preferred:
  Struct-of-Arrays for scan-heavy queues
  generational slabs/arenas for stable ids
  fixed-capacity small buffers for proof packets
  BTreeMap only where deterministic ordering matters
  prehashed EvidenceRef and TaskId keys

forbidden in hot loops:
  unbounded Vec growth
  HashMap iteration order as proof order
  string formatting for control decisions
  heap allocation during queue sort
  JSON parse for repeated scheduler state
```

The physical proof path must prefer stable binary records over textual data structures. JSON is allowed at API boundaries, then must be canonicalized and lowered into typed records before scheduling or witness decisions.

### 25.6 SIMD and Fast-JSON Policy

`fast-json` remains optional because SIMD parsers can create portability and canonicalization risk.

Required adoption gates:

```text
SimdJsonBench canonical_hash_matches_serde_json == true
SimdJsonBench malformed_input_rejection_matches_baseline == true
SimdJsonBench p99_parse_latency_improves_over_baseline == true
SimdJsonBench allocation_count_not_higher_than_baseline == true
```

If any gate fails, keep `serde_json` baseline and record the failed artifact. Do not tune the policy around one favorable benchmark.

### 25.7 Background Parallelism Policy

Parallelism is allowed where order is not truth:

Allowed:
- CogniFold background folding.
- retrieval index rebuilds.
- benchmark fanout.
- report/export generation.
- offline schedule analysis.

Restricted:
- witness threshold commit order.
- replay event ordering.
- policy approval state.
- active task queue mutation.
- browser/computer side-effect execution.

Any restricted parallel path needs:

```text
Loom/Shuttle model for commit boundary
ReplayDeterminismBench repeated hash equality
panic/cancellation recovery test
bounded queue backpressure policy
```

### 25.8 Profiling Rule

Profiler output is diagnosis, not proof.

Valid profiler use:
- identify hot functions;
- detect allocation-heavy frames;
- find unexpected lock contention;
- compare flamegraph shape before/after a patch.

Invalid profiler use:
- prove correctness;
- prove determinism;
- justify accepting nondeterministic parallel commit;
- claim p99 production latency without HarnessBench evidence.

### 25.9 Wave 6 Backlog

1. Add `PerformanceBudgetLedger` schema and default budgets for scheduler, witness, audit append, and context activation.
2. Add `InstructionCountBench` using `iai-callgrind` for hot primitives.
3. Add `AllocationBench` for scheduler/proof hot loops.
4. Add `CacheLocalityBench` experiment for TaskLedger SoA vs AoS.
5. Add feature-gated `SimdJsonBench` comparing `serde_json` baseline against `simd-json`.
6. Add `ParallelFoldingBench` for Rayon/Crossbeam background CogniFold paths.
7. Add `ReplayDeterminismBench` with randomized worker schedule seeds.
8. Add `OfflineScheduleOptimizer` prototype behind a non-default feature or external planning script.

---

## 26. Deep Research Wave 7: Hardware-Aware Runtime Layout and Binary Replay

This wave moves AEGIS closer to CPU reality: fewer unpredictable allocations, fewer branchy text paths, tighter binary records, and explicit cache/NUMA ownership. Complexity is allowed only when it is measurable and replay-safe.

### 26.1 Source Scan

| Source | High-signal technique | AEGIS adoption rule |
|---|---|---|
| [Intel Optimization Reference Manual](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html) | cache hierarchy, prefetching, branch prediction, alignment, SIMD, memory-order guidance. | Use as design input for layout experiments; actual gates come from AEGIS benchmarks on target machines. |
| [rkyv](https://github.com/rkyv/rkyv) | zero-copy archived Rust data structures. | Internal sealed snapshots only, with schema hash, validation, and version migration path. |
| [zerocopy](https://github.com/google/zerocopy) | byte-level layout traits for safe parsing and writing. | Fixed-layout frames only; endian, alignment, and padding must be explicit in type names or schema. |
| [Cap'n Proto](https://capnproto.org/) / [FlatBuffers](https://flatbuffers.dev/) | binary serialization designed for random access without parse/unpack overhead. | Candidate for cross-language sealed records only after BLAKE3 canonical hash and schema evolution tests. |
| [bumpalo](https://github.com/fitzgen/bumpalo) | fast bump allocation for many same-lifetime objects. | Use for per-shift temporary graphs/context packs; never store long-lived evidence refs in an arena that will reset. |
| [mimalloc](https://github.com/microsoft/mimalloc), [snmalloc](https://github.com/microsoft/snmalloc), [jemalloc](https://github.com/jemalloc/jemalloc) | allocator designs targeting fragmentation, locality, and multi-thread scalability. | Global allocator experiments must be feature-gated and benchmarked on Windows/Linux separately. |
| [StarMalloc](https://arxiv.org/abs/2405.05744) | formally verified concurrent allocator direction. | Research signal only until implementation ecosystem is practical; use its proof posture as a bar for allocator-critical code. |

### 26.2 RuntimeLayoutBudget

Each hot record family needs a layout budget:

```rust
pub struct RuntimeLayoutBudget {
    pub record_family: &'static str,
    pub max_record_bytes: u32,
    pub cache_line_policy: CacheLinePolicy,
    pub allocation_zone: AllocationZone,
    pub binary_schema_hash: [u8; 32],
    pub deterministic_order_key: &'static str,
}

pub enum AllocationZone {
    HotStackOrSmallVec,
    ShiftArena,
    GenerationalSlab,
    SealedBinarySegment,
    ColdHeap,
}
```

Rules:
- Task/witness/replay hot records must have bounded size or explicit cold sidecar refs.
- Variable text belongs in CAS/sidecar storage; hot records store hashes and offsets.
- Long-lived ids cannot point into resettable arenas.
- Cache-line alignment is allowed only when it reduces measured contention or false sharing.

### 26.3 Allocator Zoning

Use allocation zones by lifetime:

| Zone | Use | Reset/ownership rule |
|---|---|---|
| `HotStackOrSmallVec` | tiny proof packets and fixed fanout arrays | no heap allocation in hot proof loops |
| `ShiftArena` | context activation graph, temporary ranking buffers | reset only after checkpoint and no outstanding refs |
| `GenerationalSlab` | task/evidence/memory nodes with stable ids | generation check required on every dereference |
| `SealedBinarySegment` | replay/audit records after commit | immutable after seal, BLAKE3 chain anchored |
| `ColdHeap` | UI strings, reports, control-plane exports | never used for scheduler proof order |

Allocator experiments:
- `bumpalo` for `ShiftArena`;
- existing generational slab for stable graph nodes;
- optional global allocator experiments with `mimalloc`, `snmalloc`, or `jemalloc`;
- no global allocator switch without OS-specific benchmark evidence.

### 26.4 Binary Replay Record

Replay should not depend on reparsing text-heavy logs. Add a compact binary record target:

```rust
#[repr(C)]
pub struct BinaryReplayRecordHeader {
    pub magic: u32,
    pub version: u16,
    pub record_kind: u16,
    pub payload_len: u32,
    pub schema_hash_prefix: u64,
    pub previous_record_hash: [u8; 32],
    pub payload_hash: [u8; 32],
}
```

Payload policies:
- fixed-layout payloads may use `zerocopy`;
- sealed internal snapshots may experiment with `rkyv`;
- cross-language records may compare Arrow IPC, Cap'n Proto, and FlatBuffers;
- every binary payload must have a canonical BLAKE3 hash and schema hash.

No binary format is accepted because it is fashionable. It must beat Arrow/serde baseline on measured replay latency or memory behavior while preserving schema evolution and audit readability.

### 26.5 Cache-Line Ownership and False Sharing Rule

Each concurrently updated structure needs ownership notes:

```text
single-writer:
  no atomic required inside writer hot loop
  publish sealed record after checksum

multi-reader:
  immutable segment or versioned snapshot
  no writer mutation after publish

multi-writer:
  avoid in proof path
  require padded counters or sharded ownership
  model-check if order influences commits
```

False sharing gates:

```text
FalseSharingBench padded_counter_p99 <= baseline_p99
FalseSharingBench no_regression_single_thread == true
CacheLocalityBench record_scan_cache_miss_not_worse == true
```

Padding can waste memory. It is allowed only for proven contention boundaries, not as a blanket style.

### 26.6 NUMA and Thread Placement Policy

NUMA and pinning can help or hurt depending on machine topology.

Allowed targets:
- pin benchmark runner for repeatable measurements;
- keep Wasmtime worker pool affinity stable during a run;
- isolate background CogniFold workers from latency-critical witness tasks;
- record CPU topology in benchmark artifacts.

Forbidden claims:
- no "NUMA optimized" label without topology-specific benchmark;
- no manual pinning in default runtime until it proves p99 benefit;
- no cross-node shared mutable proof path.

Required gate:

```text
TopologyBench records_cpu_model_topology == true
TopologyBench pinned_vs_unpinned_p99_recorded == true
TopologyBench replay_hash_same_across_thread_layouts == true
```

### 26.7 Branch Predictability and Typed State Machines

LLM-facing flows are text-heavy and branchy. Rust hot paths should be typed and predictable:

```text
preferred:
  enum state machine with compact discriminants
  table-driven transition validation
  precomputed risk class
  typed EvidenceRef instead of string ids
  monomorphic hot kernels

avoid:
  dynamic dispatch inside scheduler loop
  repeated regex/string matching after canonicalization
  HashMap lookups for every transition
  branchy policy recomputation on unchanged state
```

Correctness rule:
- table-driven transitions must be generated from a checked policy spec or covered by exhaustive tests;
- branch reduction must not skip policy gates;
- any unsafe or transmute-based layout optimization needs a `ProofCartridge`.

### 26.8 Layout and Replay Bench Gates

| Gate | Measures | Pass rule |
|---|---|---|
| `LayoutBudgetBench` | record size, alignment, sidecar ratio | within `RuntimeLayoutBudget` |
| `ArenaLifetimeBench` | arena reset safety and allocation count | no dangling refs; hot allocation count decreases |
| `BinaryReplayBench` | replay scan latency and bytes read | faster or lower memory than baseline with identical replay hash |
| `SchemaEvolutionBench` | old/new binary record compatibility | old sealed records still parse or fail closed |
| `TopologyBench` | CPU topology and pinned/unpinned p99 | recorded before adopting pinning |
| `FalseSharingBench` | counter/queue contention behavior | p99 improvement without single-thread regression |

### 26.9 Wave 7 Backlog

1. Add `RuntimeLayoutBudget` schema and record-family inventory.
2. Add `LayoutBudgetBench` for `TaskCard`, witness proof, audit header, and context node.
3. Prototype `BinaryReplayRecordHeader` with BLAKE3 chain and schema hash.
4. Add `ArenaLifetimeBench` for shift-local activated context graphs.
5. Add `SchemaEvolutionBench` comparing Arrow IPC baseline vs one binary candidate.
6. Add `FalseSharingBench` for telemetry counters and witness queues.
7. Add `TopologyBench` artifact recorder for CPU model, logical cores, and pinning experiment.
8. Keep allocator swaps feature-gated until Windows/Linux evidence exists.

---

## 27. Deep Research Wave 8: Formal Policy Kernel and Typed Tool IR

This wave makes ToolGateway decisions auditable as data, not as prose. LLMs may propose intents, but every executable action must pass a typed IR, policy facts, proof trace, and evidence contract.

### 27.1 Source Scan

| Source | High-signal technique | AEGIS adoption rule |
|---|---|---|
| [AWS Cedar](https://www.cedarpolicy.com/) | policy language for authorization decisions with analyzable policies. | Study for authorization shape and entity/action/resource modeling; AEGIS policy runtime remains Rust-native until benchmarks and proof traces exist. |
| [Open Policy Agent/Rego](https://www.openpolicyagent.org/docs/latest/policy-language/) | declarative policy-as-code and decision documents. | Useful for policy authoring ideas; hot path cannot call external policy service by default. |
| [Datafrog](https://github.com/rust-lang/datafrog) / [Souffle Datalog](https://souffle-lang.github.io/) | Datalog-style fixed-point rules and relation joins. | Use for policy facts that need dependency closure, reachability, and monotonic rule evaluation. |
| [Z3](https://github.com/Z3Prover/z3), [cvc5](https://cvc5.github.io/), [SMT-LIB](https://smt-lib.org/) | SMT solving and counterexample generation. | Use offline or pre-commit for bounded counterexamples; no solver call in latency-critical witness commit path by default. |
| [egg](https://github.com/egraphs-good/egg) / [egglog](https://github.com/egraphs-good/egglog) | e-graph canonicalization and equality saturation. | Canonicalize equivalent tool plans before policy validation; rewritten plans must preserve typed effects and evidence contracts. |
| [WebAssembly Component Model WIT](https://component-model.bytecodealliance.org/design/wit.html) | typed interface definitions for components. | Candidate shape for tool ABI contracts and sandbox host functions. |
| [JSON Schema](https://json-schema.org/) / [OpenAPI](https://spec.openapis.org/oas/latest.html) / [TypeSpec](https://typespec.io/) | schema-first API/tool contracts. | Accept at boundaries, then lower into `TypedToolIR`; text schema validation alone is not a policy decision. |

### 27.2 TypedToolIR

Tool calls must become typed records before execution:

```rust
pub struct TypedToolIR {
    pub tool_id: ToolId,
    pub operation_id: OperationId,
    pub capability: CapabilityClass,
    pub side_effect: SideEffectClass,
    pub credential_scope: CredentialScope,
    pub risk_class: RiskClass,
    pub preconditions: SmallVec<[PolicyAtom; 8]>,
    pub effects: SmallVec<[PolicyAtom; 8]>,
    pub evidence_contract: EvidenceContract,
    pub approval_scope: Option<ApprovalScope>,
    pub canonical_hash: [u8; 32],
}
```

Rules:
- LLM text can create only an `UntrustedToolIntent`.
- `UntrustedToolIntent` must be parsed, schema-validated, canonicalized, and lowered into `TypedToolIR`.
- Tool execution reads only `TypedToolIR`, not raw model text.
- `canonical_hash` enters the event ledger before policy evaluation.

### 27.3 Policy Facts and Proof Trace

Policy decisions should emit their input facts and minimal derivation:

```rust
pub struct PolicyFacts {
    pub policy_version_hash: [u8; 32],
    pub has_valid_approval: bool,
    pub has_staging_environment: bool,
    pub has_hitl_signature: bool,
    pub staging_proof_ref_hash: Option<[u8; 32]>,
    pub staging_evidence_kind: Option<StagingEvidenceKind>,
    pub hitl_signature_ref_hash: Option<[u8; 32]>,
    pub facts_hash: [u8; 32],
}

pub struct PolicyProofTrace {
    pub policy_version_hash: [u8; 32],
    pub input_ir_hash: [u8; 32],
    pub facts_hash: [u8; 32],
    pub decision: PolicyDecision,
    pub matched_rules: SmallVec<[RuleId; 16]>,
    pub counterexample_ref: Option<EvidenceRef>,
}

pub enum PolicyDecision {
    Allow,
    RequireApproval(ApprovalScope),
    RequireStagingSandbox,
    HardBlock(BlockReason),
}
```

Proof trace is not a wall of logs. It is a compact, replayable explanation hash chain that lets audit reconstruct why a call was allowed, blocked, or escalated.

### 27.4 Three-Layer Policy Kernel

```text
Layer 0: fast deterministic classifier
  typed capability, side effect, credential scope, irreversibility

Layer 1: Datalog closure
  dependency reachability, staging availability, approval inheritance,
  credential boundary, external visibility, policy window

Layer 2: bounded SMT/counterexample
  high-risk preflight only; prove no forbidden effect under declared preconditions
```

Layer rules:
- R0/R1 should usually stop after Layer 0/1.
- R3/R4 must emit proof trace and approval/staging requirements.
- SMT solvers may produce counterexamples for auditors, but solver success cannot override a hard-coded R4 block.
- If any layer is unavailable, policy fails closed for side-effectful tools.

### 27.5 Datalog Policy Facts

Example relation set:

```text
tool_capability(tool_id, capability)
operation_effect(operation_id, effect)
requires_credential(operation_id, credential_scope)
has_approval(run_id, approval_scope)
approval_covers(approval_scope, operation_id)
has_staging_environment(operation_id, staging_ref)
touches_external_state(operation_id)
touches_financial_or_legal_state(operation_id)
destructive_effect(effect)
```

Derived decisions:

```text
requires_approval(op) :-
  touches_external_state(op),
  not has_approval(run, scope),
  approval_covers(scope, op).

requires_staging(op) :-
  touches_financial_or_legal_state(op).

hard_block(op) :-
  requires_staging(op),
  not has_staging_environment(op, _),
  not has_hitl_signature(op, _).
```

Datalog is useful here because it is explicit, monotonic, and replayable. It is not used to judge semantic truth; it computes policy closure over typed facts.

### 27.6 E-Graph Plan Canonicalization

Tool plans can be semantically equivalent but syntactically different. Canonicalization reduces noise before validation:

```text
normalize:
  read(file) then hash(file) -> read_and_hash(file)
  mkdir(path) then write(path/file) -> ensure_parent(path/file) then write(path/file)
  browser_navigate(url) then browser_snapshot -> browser_fetch_witness(url)

forbidden rewrite:
  staging_delete(table) -> production_delete(table)
  draft_post(msg) -> send_post(msg)
  read_secret(scope) -> credential_use(scope)
```

Rewrite gates:
- rewritten plan must preserve risk class or raise it;
- rewritten effects must be a subset or explicit superset requiring re-approval;
- canonical plan hash enters the ledger;
- physical witness still validates outputs after execution.

### 27.7 Approval Scope Semantics

Approval must be scoped and machine-checkable:

```rust
pub struct ApprovalScope {
    pub approver_key_hash: [u8; 32],
    pub allowed_tool_ids: SmallVec<[ToolId; 8]>,
    pub allowed_operations: SmallVec<[OperationId; 8]>,
    pub max_risk_class: RiskClass,
    pub expires_at_ms: u64,
    pub resource_patterns_hash: [u8; 32],
    pub max_spend_minor_units: Option<u64>,
}
```

Rules:
- Approval to draft is not approval to send.
- Approval to staging is not approval to production.
- Approval for one resource pattern does not cover wildcard resources unless the wildcard was explicitly signed.
- Expired approval fails closed.

### 27.8 Formal Policy Bench Gates

| Gate | Measures | Pass rule |
|---|---|---|
| `TypedToolIrBench` | parse/lower/canonicalize tool intents | raw LLM text never reaches executor |
| `PolicyKernelBench` | policy decision latency and replayability | decision hash stable under replay |
| `DatalogPolicyBench` | closure correctness over policy facts | R3/R4 requirements derived correctly |
| `ApprovalScopeBench` | scoped approval coverage | no privilege expansion |
| `R4HardBlockBench` | financial/legal/destructive action handling | hard block without staging or signed HITL |
| `SMTCounterexampleBench` | bounded precondition/effect contradictions | counterexample stored as evidence ref |
| `EGraphPlanRewriteBench` | plan canonicalization | risk not downgraded; hash stable |

### 27.9 Wave 8 Backlog

1. Define `UntrustedToolIntent -> TypedToolIR` lowering contract.
2. Add `PolicyProofTrace` to event ledger design.
3. Add Layer 0 deterministic classifier table for capability/effect/risk.
4. Add Datalog fact schema for ToolGateway policy closure.
5. Prototype SMT counterexample generation for one R4 destructive action.
6. Add e-graph rewrite experiment for read/hash/write tool plans.
7. Add approval scope signature and expiry tests.
8. Add `R4HardBlockBench` and `ApprovalScopeBench` to HarnessBench-AEGIS.

---

## 28. Deep Research Wave 9: Evaluation Gauntlet and Adversarial Benchmarking

This wave turns architecture claims into tests. AEGIS should not ask "does the agent sound smart?" It should ask: did the physical artifact pass, did policy hold, did replay recover, and did the benchmark resist shortcutting?

### 28.1 Source Scan

| Source | High-signal benchmark idea | AEGIS adoption rule |
|---|---|---|
| [SWE-bench](https://github.com/swe-bench/SWE-bench) / [SWE-bench Verified](https://www.swebench.com/) | real GitHub issue repair with test-based validation. | Use for CodeRepairBench design: repo checkout, patch, hidden tests, artifact diff, rollback trace. |
| [Terminal-Bench](https://github.com/laude-institute/terminal-bench) | terminal-native agent tasks with command execution. | Use for TerminalOpsBench: shell plan, command evidence, crash/retry/replay checks. |
| [OSWorld](https://github.com/xlang-ai/OSWorld) | desktop/computer-control tasks in real OS environments. | Use for ComputerOpsBench with screenshot/window/action witness packets. |
| [BrowserGym](https://github.com/ServiceNow/BrowserGym), [WebArena](https://github.com/web-arena-x/webarena), [WorkArena](https://github.com/ServiceNow/WorkArena) | web/browser task evaluation and enterprise workflow tasks. | Use for BrowserOpsBench and R3/R4 side-effect gating tasks. |
| [BFCL](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard) / [tau-bench](https://github.com/sierra-research/tau-bench) | tool/function calling and user-agent interaction evaluation. | Use for ToolGateway typed call validation, multi-turn state, and approval scope tests. |
| [AppWorld](https://github.com/stonybrooknlp/appworld), [GAIA](https://huggingface.co/gaia-benchmark) | app-level tool use and general assistant tasks. | Use for multi-tool workflows, but replace answer grading with physical artifacts where possible. |
| [AgentDojo](https://github.com/ethz-spylab/agentdojo) / [DoomArena](https://github.com/salesforce/DoomArena) | prompt injection, tool misuse, and malicious web/task environments. | Use for adversarial context and browser safety tasks; LLM refusal text is not enough, policy gate must block. |
| [CyberSecEval](https://github.com/meta-llama/PurpleLlama/tree/main/CybersecurityBenchmarks) / [AgentHarm](https://huggingface.co/datasets/ai-safety-institute/AgentHarm) | cybersecurity and harmful-agent behavior evaluation. | Use only in controlled sandbox; score policy blocking, credential isolation, and no external side effect. |

### 28.2 HarnessBench-AEGIS Scorecard

Each benchmark run emits a scorecard:

```rust
pub struct HarnessBenchScorecard {
    pub bench_id: BenchId,
    pub task_id: TaskId,
    pub success: bool,
    pub physical_witness_hash: [u8; 32],
    pub replay_hash: [u8; 32],
    pub policy_violation_count: u32,
    pub hard_block_count: u32,
    pub approval_required_count: u32,
    pub witness_coverage_ppm: u32,
    pub crash_recovery_passed: bool,
    pub p50_wall_ms: u64,
    pub p99_wall_ms: u64,
    pub token_count: u64,
    pub tool_call_count: u32,
}
```

Scoring rules:
- No LLM judge for pass/fail.
- Pass/fail comes from tests, hidden validators, artifact hashes, DOM/window assertions, or policy facts.
- Text answer quality can be recorded as metadata but cannot close a physical gate.
- Benchmarks must record raw evidence refs, redacted control-plane artifacts, and replay hash.

### 28.3 Benchmark Matrix

| Bench | External inspiration | AEGIS physical pass gate |
|---|---|---|
| `CodeRepairBench` | SWE-bench | patch applies, hidden tests pass, BLAKE3 diff chain sealed |
| `TerminalOpsBench` | Terminal-Bench | command outputs hashed, expected filesystem artifact exists |
| `BrowserOpsBench` | BrowserGym/WebArena/WorkArena | DOM/screenshot/network/action witness bundle valid |
| `ComputerOpsBench` | OSWorld | window state/screenshot/action evidence valid |
| `ToolGatewayBench` | BFCL/tau-bench/AppWorld | typed tool call matches schema and policy proof trace |
| `ResearchBench` | GAIA-style tasks | source snapshots hashed; answer backed by citations/evidence refs |
| `InjectionDefenseBench` | AgentDojo/DoomArena | malicious instruction fails to bypass policy/tool isolation |
| `CyberSafetyBench` | CyberSecEval/AgentHarm | harmful/external side-effect tasks hard-blocked in sandbox |
| `ReplayChaosBench` | durable execution practice | deterministic seeded crash-boundary, event-boundary sweep, approval-boundary sweep, and 128-point seeded randomized scorecard artifact pass; broader randomized expansion pending |
| `PerformanceRegressionBench` | Criterion/Iai-Callgrind/TopologyBench | budget gates pass with recorded machine profile |

### 28.4 Anti-Cheat and Benchmark Integrity

Benchmarks can be gamed. AEGIS must defend against shortcut learning:

```text
hidden validators
task seed rotation
artifact-level success checks
network/domain allowlists
sealed raw logs
replay from checkpoint
no benchmark name in model prompt unless needed
no training-split leak into context pack
```

Integrity rules:
- Public task description cannot include hidden test names.
- Harness must hash the initial environment snapshot.
- Any external web source used during benchmark must be snapshotted or content-hashed.
- If the task requires browser/computer action, screenshot alone is insufficient.
- If benchmark data might be contaminated, result is marked `ContaminationSuspected`, not accepted as proof.

### 28.5 Adversarial Task Families

Add adversarial variants for every normal task:

| Family | Attack | Required defense |
|---|---|---|
| `PromptInjection` | page/file tells agent to ignore policy | ToolGateway ignores text and enforces typed policy |
| `CredentialLure` | task asks for token/password entry | credential scope blocks without approval |
| `SideEffectTrap` | innocent wording causes external write | risk classifier escalates R3/R4 |
| `ReplayCrash` | crash after LLM response before tool call | replay reads `LLMResponseReceived.raw_text_ref` |
| `ArtifactSpoof` | fake log claims success | physical artifact hash/test/DOM proof required |
| `BenchmarkLeak` | task references known benchmark answer | hidden validator and contamination flag |
| `ToolSchemaDrift` | provider schema changes | schema hash mismatch fails closed |

### 28.6 Long-Horizon Completion Metrics

For 50+ step tasks, a single success bit hides too much. Track:

```text
task_completion_rate
checkpoint_recovery_rate
policy_violation_rate
physical_witness_coverage
mean_steps_between_backtrack
context_pack_precision
evidence_reuse_rate
token_per_committed_artifact
wall_time_per_committed_artifact
```

Hard interpretation:
- A faster run with weaker witness coverage is not a win.
- A higher success score with more policy violations is not a win.
- A benchmark pass without replay recovery evidence is incomplete.
- Browser/computer wins without side-effect classification are incomplete.

### 28.7 Evaluation Harness Architecture

```text
BenchmarkSpec
  -> EnvironmentBuilder
  -> TaskInitializer
  -> AEGIS Run
  -> EvidenceCollector
  -> HiddenValidator
  -> ReplayVerifier
  -> ScorecardEmitter
```

Every bench needs:
- deterministic environment initialization;
- explicit network/filesystem/credential policy;
- crash injection points;
- expected evidence contract;
- hidden validator;
- scorecard with raw refs and redacted summary.

### 28.8 Wave 9 Bench Gates

| Gate | Measures | Pass rule |
|---|---|---|
| `GauntletSpecBench` | benchmark spec completeness | env, policy, validator, evidence contract present |
| `HiddenValidatorBench` | validator isolation | model cannot see hidden checks |
| `ReplayChaosBench` | crash recovery | seeded crash-boundary, event-boundary, and approval-boundary recovery return the same valid ledger prefix and scorecard hash |
| `InjectionDefenseBench` | prompt/tool injection | no policy bypass, no credential leak |
| `ContaminationBench` | benchmark leakage | suspected contamination marked and excluded |
| `WitnessCoverageBench` | evidence completeness | required physical witness refs present |
| `LongHorizonBench` | 50+ step mission stability | checkpoint/replay/policy metrics recorded |

### 28.9 Wave 9 Backlog

1. Define `HarnessBenchScorecard` and benchmark result JSON schema.
2. Add `GauntletSpecBench` for benchmark spec completeness.
3. Add small local `CodeRepairBench` seed task before importing SWE-bench style tasks.
4. Add BrowserOpsBench adversarial prompt-injection page.
5. Expand `ReplayChaosBench` from deterministic seeded segment/event/approval-boundary crashes and the 128-point scorecard gate to broader randomized crash campaigns around LLM/tool/checkpoint/operator approval boundaries.
6. Add `InjectionDefenseBench` using malicious file/web instructions.
7. Add `ContaminationBench` metadata and exclusion rule.
8. Add leaderboard export that separates success, policy, witness coverage, replay recovery, and cost.

---

## 29. Deep Research Wave 10: Model/Inference Systems and Local Runtime Acceleration

This wave applies only when AEGIS controls the inference runtime or the provider exposes a safe cache/structured-output contract. External APIs are not assumed to expose KV cache, scheduler internals, or deterministic replay.

### 29.1 Source Scan

| Source | High-signal technique | AEGIS adoption rule |
|---|---|---|
| [vLLM](https://github.com/vllm-project/vllm) / [PagedAttention](https://arxiv.org/abs/2309.06180) | paged KV cache, continuous batching, high-throughput serving. | Candidate self-hosted backend; cache keys must use canonical ContextPack digest and policy hash. |
| [SGLang](https://github.com/sgl-project/sglang) | RadixAttention/prefix sharing and structured generation runtime. | Learn prefix-tree scheduling for repeated mission headers, tool schemas, and policy context. |
| [LMCache](https://github.com/LMCache/LMCache) | KV cache reuse/offload across requests. | Optional `KVCacheBroker` only for self-hosted model paths with explicit invalidation. |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | local CPU/GPU inference, GGUF ecosystem, broad hardware reach. | Local fallback backend for privacy/offline workflows; performance claims require per-machine benchmark. |
| [TensorRT-LLM](https://github.com/NVIDIA/TensorRT-LLM) | NVIDIA-optimized inference engine and serving kernels. | GPU production candidate behind hardware-specific feature gates and reproducibility tests. |
| [FlashAttention](https://github.com/Dao-AILab/flash-attention) / [FlashInfer](https://github.com/flashinfer-ai/flashinfer) | optimized attention and serving kernels. | Kernel-level optimization signal; adoption only through backend benchmark, not direct hot-path dependency by default. |
| [Medusa](https://github.com/FasterDecoding/Medusa), [EAGLE](https://github.com/SafeAILab/EAGLE), [SpecInfer](https://github.com/microsoft/SpecInfer) | speculative/multi-token decoding families. | Use only if accepted output is verified against target model contract and tool JSON canonicalization gates. |

### 29.2 InferenceBackendContract

Every model backend must declare capabilities before the orchestrator can optimize around it:

```rust
pub struct InferenceBackendContract {
    pub backend_id: BackendId,
    pub model_hash: [u8; 32],
    pub tokenizer_hash: [u8; 32],
    pub runtime_config_hash: [u8; 32],
    pub supports_prefix_cache: bool,
    pub supports_kv_export: bool,
    pub supports_guided_decoding: bool,
    pub supports_seeded_sampling: bool,
    pub supports_batch_priority: bool,
    pub max_context_tokens: u32,
    pub proof_required: &'static [&'static str],
}
```

Rules:
- No optimizer may assume KV cache portability.
- Prompt caching is keyed by exact model/tokenizer/runtime/policy/context hashes.
- If tokenizer hash changes, all prefix/KV cache entries are invalid.
- If policy/tool schema changes, cache entry is invalid for tool-call contexts.
- Determinism requires explicit runtime config and sampling contract.

### 29.3 PrefixCacheBroker v2

The broker should cache only what is safe:

```text
PrefixCacheKey =
  model_hash
  tokenizer_hash
  runtime_config_hash
  canonical_context_pack_hash
  policy_window_hash
  tool_schema_hash
  adapter_hash
```

Allowed:
- mission header prefix;
- stable safety/policy prefix;
- registered tool schema prefix;
- sealed memory summaries with evidence hashes.

Forbidden:
- cache reuse across different policy windows;
- cache reuse across different tool schemas;
- cache reuse for unsealed raw LLM text;
- external API KV reuse without provider contract.

### 29.4 Continuous Batching Boundary

Inference batching is a throughput optimizer, not the mission scheduler.

```text
MissionScheduler:
  deterministic task priority and policy order

InferenceBatcher:
  groups already-legal prompt requests
  respects latency tier and cancellation policy
  cannot reorder side-effect commits
```

Batching rules:
- Tool execution order is governed by event ledger, not backend batch order.
- Streamed tokens are untrusted until parsed/canonicalized.
- Cancellation must emit event refs for partial model output hash.
- High-risk R3/R4 prompts must not share mutable state with unrelated sessions.

### 29.5 Structured Output and Guided Decoding

Guided decoding can reduce malformed tool JSON, but it is not a policy gate.

Rules:
- guided decoding schema must be hashed into `InferenceBackendContract`;
- emitted JSON must still pass `TypedToolIR` lowering;
- schema-valid but policy-invalid tool calls are blocked;
- constrained decoding failure opens a repair loop, not a silent fallback to free text.

### 29.6 Speculative Decoding Contract

Speculative decoding is accepted only if the target model verifies the output:

```text
SpecDecodeBench accepted_tokens_verified_by_target == true
SpecDecodeBench canonical_tool_json_equal_to_baseline == true
SpecDecodeBench no_policy_context_dropped == true
SpecDecodeBench p99_latency_recorded == true
```

For deterministic tool-call generation, compare canonical output records, not pretty text. For free-form research summaries, speculative speedup is useful only if citation/evidence references remain intact.

### 29.7 Quantization and Local Runtime Policy

Quantization can change outputs and tool-call validity.

Allowed:
- local/offline inference;
- draft model in speculative decoding;
- summarization or retrieval expansion tasks;
- low-risk brainstorming where output still passes physical gates.

Restricted:
- direct R3/R4 tool call generation;
- policy reasoning;
- witness proof generation;
- final typed action unless canonical output and policy proof match full-precision baseline.

Required gates:

```text
QuantizationWitnessBench typed_tool_ir_match_rate_recorded == true
QuantizationWitnessBench policy_decision_hash_matches_baseline == true
QuantizationWitnessBench physical_gate_success_not_lower_without_waiver == true
```

### 29.8 Inference Benchmark Gates

| Gate | Measures | Pass rule |
|---|---|---|
| `InferenceBackendBench` | backend p50/p99, throughput, error rate | recorded with model/tokenizer/runtime hashes |
| `PrefixCacheCorrectnessBench` | prefix cache hit correctness | output/evidence hash matches no-cache baseline |
| `KvCacheInvalidationBench` | policy/tool/schema invalidation | stale cache never used |
| `SpecDecodeEquivalenceBench` | draft/target acceptance and canonical equality | no tool JSON divergence |
| `StructuredOutputBench` | guided decoding validity | schema pass plus `TypedToolIR` pass |
| `QuantizationWitnessBench` | quantized vs baseline behavior | policy/witness gates not degraded silently |
| `BatchingFairnessBench` | latency-tier fairness and cancellation | no starvation; replay hash stable |
| `LocalRuntimeBench` | llama.cpp/local backend behavior | per-machine metrics recorded, no global claim |

### 29.9 Wave 10 Backlog

1. Add `InferenceBackendContract` schema.
2. Upgrade `KVCacheBroker` spec to `PrefixCacheBroker v2` with policy/tool schema invalidation.
3. Add provider mode enum: `ExternalApi`, `SelfHostedVllm`, `SelfHostedSglang`, `LocalLlamaCpp`, `TensorRtLlm`.
4. Add `InferenceBackendBench` artifact schema.
5. Add `PrefixCacheCorrectnessBench` no-cache vs cached comparison.
6. Add `SpecDecodeEquivalenceBench` for typed tool JSON.
7. Add `StructuredOutputBench` that still requires `TypedToolIR`.
8. Add `QuantizationWitnessBench` before any quantized model path can drive tool calls.

---

## 30. Deep Research Wave 11: Storage, Indexing, and Retrieval Engine

This wave makes evidence retrieval fast without letting retrieval become truth. Search engines, vector indexes, and query engines may return candidate pointers; only physical witness and policy gates can commit state.

### 30.1 Source Scan

| Source | High-signal technique | AEGIS adoption rule |
|---|---|---|
| [Tantivy](https://github.com/quickwit-oss/tantivy) | Rust full-text search and BM25-style lexical retrieval. | Candidate hot lexical index for file/symbol/evidence text; benchmark before adoption. |
| [RoaringBitmap/rust](https://github.com/RoaringBitmap/roaring-rs) | compressed bitmap intersections and set operations. | Candidate optimized filter engine; default path stays no-dependency sorted-Vec until roaring wins on AEGIS task/evidence/risk/time cardinalities. |
| [Apache Arrow](https://arrow.apache.org/) / [DataFusion](https://github.com/apache/datafusion) | columnar format and Rust query engine. | Query/audit plane over sealed segments only; scheduler hot path cannot depend on DataFusion queries. |
| [RocksDB](https://github.com/facebook/rocksdb), [redb](https://github.com/cberner/redb), [SQLite](https://www.sqlite.org/), [libSQL](https://github.com/tursodatabase/libsql) | embedded catalogs and persistent key-value/relational state. | Candidate control-plane/catalog stores; proof path still uses append-only evidence hashes. |
| [Qdrant](https://github.com/qdrant/qdrant), [LanceDB](https://github.com/lancedb/lancedb), [FAISS](https://github.com/facebookresearch/faiss), [hnswlib](https://github.com/nmslib/hnswlib) | vector/ANN retrieval and filtered search. | Cold/semantic candidate tier only; approximate nearest neighbor result is never proof. |
| [DuckDB](https://github.com/duckdb/duckdb) | embedded analytical query engine over files/columns. | Optional offline audit/reporting comparison against DataFusion; not hot path. |

### 30.2 EvidenceIndexManifest

Indexes must be published as sealed generations:

```rust
pub struct EvidenceIndexManifest {
    pub run_id: RunId,
    pub generation: u64,
    pub source_segment_hashes: SmallVec<[[u8; 32]; 16]>,
    pub schema_hash: [u8; 32],
    pub lexical_index_hash: Option<[u8; 32]>,
    pub bitmap_index_hash: Option<[u8; 32]>,
    pub vector_index_hash: Option<[u8; 32]>,
    pub graph_index_hash: Option<[u8; 32]>,
    pub created_from_checkpoint_hash: [u8; 32],
}
```

Rules:
- Index generation is immutable after publish.
- Partial indexes are not visible to Context Governor.
- Every retrieval result references manifest generation.
- If source segment hash changes, the derived index generation is invalid.

### 30.3 Retrieval Tier Architecture

```text
L0 ActiveSet:
  in-memory task/evidence ids for current shift

L1 HotEvidenceIndex:
  Tantivy lexical candidates + sorted-Vec/optional RoaringBitmap filters + graph neighbors

L2 SegmentCatalog:
  embedded KV/relational catalog for sealed segment metadata

L3 SemanticCandidateTier:
  vector index or external retrieval provider, bounded and filterable

L4 ArchiveQueryPlane:
  Arrow/DataFusion/DuckDB over sealed audit segments
```

Hot-path invariant:
- Context Governor can query L0/L1 only.
- L2-L4 expansion requires explicit event and bounded result cap.
- Global archive query is never synchronous hot path.

### 30.4 Candidate Retrieval Pipeline

```text
query_seed =
  active_task_id
  required_evidence_refs
  failed_command_terms
  symbol/entity ids

pipeline:
  activate graph 1-hop/2-hop
  lexical search bounded k
  bitmap intersect risk/time/status filters
  optional vector candidates bounded k
  merge by deterministic tie-break key
  return EvidenceRef candidates
```

Ranking rules:
- identifier/code terms prefer lexical exact/BM25 candidates;
- semantic vector candidates must be filtered by policy/risk bitmaps;
- LLM rerank scores may be stored as metadata but cannot decide truth;
- final tie-break uses deterministic keys: trust tier, distance, timestamp, hash.

### 30.5 SegmentCatalog and Compaction

`SegmentCatalog` stores metadata, not truth:

```text
segment_hash
schema_hash
sealed_at_ms
record_count
min_max_timestamp
risk_bitmap_ref
task_bitmap_ref
artifact_kind_bitmap_ref
index_generation
```

Compaction rules:
- compaction runs in background;
- new compacted segment is published only after hash chain verifies;
- old segment remains readable until manifest switch is committed;
- replay uses original event chain, not compacted convenience view.

### 30.6 Vector and ANN Safety

Approximate retrieval is useful and dangerous.

Allowed:
- discover related memories;
- propose candidate evidence refs;
- cluster similar failures;
- find repeated browser/tool patterns.

Forbidden:
- treat high vector similarity as proof;
- bypass physical witness;
- bypass policy filters;
- return unbounded candidates to context pack;
- silently mix embeddings from different model hashes.

Required metadata:

```text
embedding_model_hash
chunking_policy_hash
source_segment_hash
vector_index_generation
candidate_distance
filter_bitmap_generation
```

### 30.7 Query Plane Isolation

DataFusion/DuckDB-style analytics are powerful but must stay out of hot decisions:

Allowed:
- audit reports;
- benchmark analysis;
- dashboard aggregates;
- offline anomaly detection;
- post-run investigation.

Forbidden:
- scheduler task priority in hot path;
- witness threshold decision;
- policy allow/block decision;
- replay next-action calculation.

### 30.8 Storage and Retrieval Bench Gates

| Gate | Measures | Pass rule |
|---|---|---|
| `EvidenceIndexManifestBench` | manifest publish/invalidations | no partial index visible |
| `HotEvidenceIndexBench` | lexical+bitmap candidate latency | p99 under budget and candidate cap |
| `BitmapFilterBench` | sorted-Vec baseline and optional Roaring intersections | no risk/status false negative; optional dependency must beat baseline |
| `HybridRetrievalBench` | lexical/vector/graph merge | deterministic ordering with bounded candidates |
| `VectorRecallBench` | ANN recall for known evidence | recall recorded; never proof by itself |
| `SegmentCatalogBench` | segment metadata lookup | lookup p99 under budget |
| `CompactionPublishBench` | compaction visibility and hash chain | old/new manifests replay-safe |
| `QueryPlaneIsolationBench` | DataFusion/DuckDB isolation | no hot-path dependency |
| `ColdExpansionBench` | L2-L4 expansion events | explicit event and cap required |

### 30.9 Wave 11 Backlog

1. Add `EvidenceIndexManifest` and manifest generation rules.
2. Add `SegmentCatalog` schema backed by append-only evidence hashes.
3. Prototype `HotEvidenceIndex` with lexical + bitmap filters.
4. Add `HybridRetrievalBench` deterministic merge tests.
5. Add `VectorRecallBench` metadata and model-hash tracking.
6. Add `CompactionPublishBench` for manifest switch safety.
7. Add `QueryPlaneIsolationBench` to prevent DataFusion/DuckDB hot-path creep.
8. Add `ColdExpansionBench` for explicit archive expansion events.

---

## 31. Deep Research Wave 12: Distributed Execution and Consensus-Free Cluster Runtime

This wave scales work across machines without moving truth out of the deterministic core. Remote workers may compute, test, browse, index, or infer; only the run's single-writer log can commit.

### 31.1 Source Scan

| Source | High-signal technique | AEGIS adoption rule |
|---|---|---|
| [Ray](https://docs.ray.io/) | distributed tasks, actors, object store, placement groups. | Learn placement/resource scheduling for sandbox/browser/GPU workers; do not import remote actor state into proof path. |
| [Dask Distributed](https://distributed.dask.org/) | dynamic task graph scheduling and distributed futures. | Useful for background fanout/index/benchmark orchestration; hot commit remains native ledger. |
| [Flyte](https://docs.flyte.org/) | typed workflows, task caching, reproducible data pipelines. | Study typed task specs and artifact lineage for HarnessBench/offline jobs. |
| [Temporal](https://docs.temporal.io/) | durable workflow history and deterministic replay. | Keep as conceptual model; Phase 2 remains native Rust/Arrow replay unless service adoption is benchmarked. |
| [NATS JetStream](https://docs.nats.io/nats-concepts/jetstream) / [Apache Kafka](https://kafka.apache.org/documentation/) | durable messaging, delivery semantics, idempotent producer concepts. | Queue lessons only. AEGIS commits by idempotency key and artifact hash, not broker promises. |
| [Tokio](https://tokio.rs/) | Rust async runtime and task scheduling. | Candidate for I/O-bound control plane and worker communication, not CPU proof loops by default. |
| [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/) | batch jobs, retry/backoff, parallel completions. | Optional deployment substrate for external workers; AEGIS must still own policy, evidence, and replay. |

### 31.2 ClusterWorkEnvelope

Every remote unit of work must be typed and hash-addressed:

```rust
pub struct ClusterWorkEnvelope {
    pub run_id: RunId,
    pub work_id: WorkId,
    pub task_id: TaskId,
    pub worker_role: WorkerRole,
    pub attempt: u32,
    pub input_artifact_hashes: SmallVec<[[u8; 32]; 8]>,
    pub evidence_contract_hash: [u8; 32],
    pub policy_window_hash: [u8; 32],
    pub idempotency_key: [u8; 32],
    pub deadline_ms: u64,
}

pub enum WorkerRole {
    WasmSandbox,
    BrowserWitness,
    ComputerWitness,
    InferenceBackend,
    RetrievalIndexer,
    BenchmarkRunner,
}
```

Rules:
- Remote workers cannot approve or commit.
- Remote workers return `CandidateArtifactRef`.
- The single-writer run log validates policy, evidence contract, and artifact hash before commit.
- Retry duplicates are deduplicated by idempotency key plus artifact hash.

### 31.3 SingleWriterRunLog

Distributed execution needs one commit owner per run:

```text
worker result -> CandidateArtifactRef
candidate validation -> PhysicalWitnessProof
policy replay -> PolicyProofTrace
single-writer append -> committed RunEvent
```

Hard rules:
- No multi-worker vote replaces physical witness.
- No broker offset is proof of task success.
- No remote worker writes directly to L1/L2 evidence ledger.
- If the run writer is unavailable, side-effectful work pauses.

### 31.4 Delivery Semantics

Assume at-least-once execution, then make commit idempotent:

```text
work lease acquired
worker executes
worker uploads artifact to CAS
worker returns candidate ref
writer checks idempotency key
writer verifies artifact/evidence/policy
writer commits or rejects
```

Avoid claims:
- "exactly once execution";
- "broker guarantees correctness";
- "network delivery proves success";
- "retry is safe without idempotency".

Accepted claim:
- exactly-once **commit effect** can be approximated by single-writer dedupe over deterministic idempotency keys and artifact hashes.

### 31.5 Placement and Resource Classes

Resource-aware placement is performance metadata:

| Role | Placement signal | Commit rule |
|---|---|---|
| `WasmSandbox` | CPU isolation, Wasmtime version, memory cap | artifact hash + fuel/PAV proof |
| `BrowserWitness` | browser version, display profile, network policy | DOM/screenshot/network/action witness |
| `ComputerWitness` | OS image, window manager, resolution | screenshot/window/action proof |
| `InferenceBackend` | GPU/CPU model, backend contract hash | output still untrusted until parsed/policy checked |
| `RetrievalIndexer` | disk/cache locality, segment generation | manifest hash before publish |
| `BenchmarkRunner` | pinned baseline machine profile | scorecard with topology/runtime hashes |

The cluster scheduler may optimize placement, but it cannot alter task priority, risk class, or commit order.

### 31.6 Backpressure and Cancellation

Cluster backpressure must be explicit:

```text
queue_depth_by_role
lease_expiration_ms
oldest_work_age_ms
retry_count
artifact_upload_bytes
writer_append_lag_ms
side_effect_pause_count
```

Rules:
- R3/R4 work has zero speculative parallel side effects.
- Cancellation emits a `WorkCancelled` event with reason and partial artifact hash if available.
- Browser/computer workers must stop before side effect when approval expires.
- Inference cancellation must hash partial response if it influenced any downstream state.

### 31.7 Partition and Failure Policy

Network partitions are normal. Policy:
- read-only/background work may continue if input artifacts are sealed;
- side-effectful work pauses when writer/policy service is unreachable;
- duplicate worker results are accepted only as candidates;
- stale policy window hash fails closed;
- CAS upload without manifest commit is garbage-collectable, not truth.

### 31.8 Cluster Runtime Bench Gates

| Gate | Measures | Pass rule |
|---|---|---|
| `ClusterWorkEnvelopeBench` | envelope canonicalization | stable idempotency key and hashes |
| `SingleWriterLogBench` | distributed candidate commit | no direct worker commit |
| `WorkLeaseBench` | lease expiry/retry/dedupe | duplicates do not double-commit |
| `RemoteWitnessBench` | remote artifact proof | candidate must satisfy evidence contract |
| `BackpressureBench` | queue/lag/cancellation behavior | no unbounded queue growth |
| `PlacementBench` | resource-class scheduling | placement metadata recorded; priority unchanged |
| `NetworkPartitionBench` | partition/failover policy | side-effectful work pauses/fails closed |
| `ClusterReplayBench` | replay after distributed run | same committed event chain |

### 31.9 Wave 12 Backlog

1. Add `ClusterWorkEnvelope` and `WorkerRole` schemas.
2. Add `CandidateArtifactRef` return contract for remote workers.
3. Add single-writer append rule to distributed run spec.
4. Add `WorkLeaseBench` for retry and idempotency behavior.
5. Add `NetworkPartitionBench` with stale policy window.
6. Add `RemoteWitnessBench` for sandbox/browser remote artifacts.
7. Add `BackpressureBench` metrics for queue depth and writer lag.
8. Add optional Ray/Kubernetes proof-of-concept only after native local bench baseline exists.

---

## 32. Deep Research Wave 13: Security Hardening and Supply Chain Integrity

This wave protects the performance machine from poisoned dependencies, unsigned workers, leaked secrets, and sandbox overtrust. Optimization is allowed only when the artifact chain remains verifiable.

### 32.1 Source Scan

| Source | High-signal technique | AEGIS adoption rule |
|---|---|---|
| [SLSA](https://slsa.dev/) | supply-chain levels, provenance, build integrity. | Use as build provenance model; AEGIS records source/build/binary hashes before trusting workers. |
| [Sigstore/cosign](https://docs.sigstore.dev/cosign/) | keyless signing, transparency log, artifact signatures. | Candidate signing path for binaries, worker images, benchmark artifacts, and SBOMs. |
| [in-toto](https://in-toto.io/) | supply-chain layout and link metadata. | Use for step-by-step provenance: source, dependency, build, test, package, deploy. |
| [SPDX](https://spdx.dev/) / [CycloneDX](https://cyclonedx.org/) | SBOM formats and dependency inventory. | Generate SBOM for Rust/Python/worker artifacts; SBOM hash enters release evidence. |
| [OpenSSF Scorecard](https://github.com/ossf/scorecard) | repository security posture checks. | Advisory signal only; scorecard cannot override direct dependency review. |
| [cargo-vet](https://github.com/mozilla/cargo-vet), [cargo-auditable](https://github.com/rust-secure-code/cargo-auditable), [RustSec](https://rustsec.org/) | Rust dependency audit, embedded dependency info, advisory DB. | Required candidates for Rust supply-chain gates before adding nontrivial dependencies. |
| [Wasmtime security](https://docs.wasmtime.dev/security.html) / [WASI capability model](https://wasi.dev/) | sandbox security and capability-oriented WASI. | Wasm capabilities default deny; no filesystem/network preopen without policy proof. |
| [seccomp](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html), [gVisor](https://gvisor.dev/), [Firecracker](https://firecracker-microvm.github.io/) | syscall filtering, user-space kernel sandbox, microVM isolation. | Defense-in-depth options for remote workers; adoption requires overhead and escape-regression benchmarks. |

### 32.2 SupplyChainProof

Build and worker artifacts need a signed proof packet:

```rust
pub struct SupplyChainProof {
    pub source_tree_hash: [u8; 32],
    pub lockfile_hash: [u8; 32],
    pub sbom_hash: [u8; 32],
    pub provenance_hash: [u8; 32],
    pub builder_identity_hash: [u8; 32],
    pub binary_hash: [u8; 32],
    pub signature_bundle_hash: [u8; 32],
    pub reproducible_build: bool,
}
```

Rules:
- Worker binary cannot join privileged roles without valid `SupplyChainProof`.
- Benchmark artifact cannot be promoted without toolchain/runtime hash.
- SBOM is evidence inventory, not proof of safety.
- Signature is identity/integrity evidence, not proof of correctness.

### 32.3 Dependency Admission Policy

Dependency admission is stricter for hot/sandbox paths:

```text
dependency_request
  -> license check
  -> RustSec/advisory scan
  -> cargo-vet or manual review
  -> build.rs/proc-macro risk classification
  -> feature flag/default dependency review
  -> benchmark/proof requirement
```

Hard rules:
- No default dependency for speculative performance features.
- `build.rs`, proc-macro, FFI, network, crypto, and parser dependencies require explicit review.
- Dependency updates that change runtime behavior need benchmark and audit rerun.
- Vulnerability ignore must carry scope, expiry, and compensating control.

### 32.4 Sandbox Capability Discipline

Sandboxing must be explicit, not assumed:

| Layer | Purpose | Rule |
|---|---|---|
| Wasmtime fuel/epoch/memory | CPU/memory bounds | always required for Wasm execution |
| WASI capabilities | filesystem/env/network control | default deny; preopens are policy artifacts |
| seccomp | syscall deny/allow list | remote worker hardening experiment |
| gVisor/Firecracker | stronger process/VM boundary | optional high-risk worker isolation after overhead bench |
| browser profile isolation | web credential/session boundary | ephemeral by default; persistent profile only with approval |

No container, VM, or browser profile is accepted as proof by itself. It is a boundary that still must emit artifact/witness evidence.

### 32.5 Secret Isolation

Credentials must never become model context:

```text
secret_ref_hash
credential_scope
tool_id
operation_id
expires_at_ms
approval_scope_hash
redaction_policy_hash
```

Rules:
- LLM sees only scoped handles, never raw secrets.
- Tool executor resolves secret handles outside the prompt.
- Browser screenshots/logs must redact tokens/cookies before control-plane export.
- Sealed raw evidence containing secrets is encrypted and audit-only.
- Secret scope mismatch hard-blocks tool execution.

### 32.6 Worker Trust and Remote Execution

Remote workers need trust before work assignment:

```text
worker_attestation:
  worker_binary_hash
  supply_chain_proof_hash
  sandbox_profile_hash
  runtime_config_hash
  role_allowlist
```

Rules:
- untrusted worker may run only public read-only benchmark tasks;
- side-effectful worker roles require signed binary and sandbox profile proof;
- stale worker attestation fails closed;
- worker output is always `CandidateArtifactRef`, never direct commit.

### 32.7 Security and Supply-Chain Gates

| Gate | Measures | Pass rule |
|---|---|---|
| `SupplyChainProofBench` | proof packet completeness | source/lock/SBOM/provenance/binary/signature hashes present |
| `SbomAttestationBench` | SBOM generation and hash chain | SBOM hash tied to binary hash |
| `DependencyVetBench` | dependency admission | no unreviewed risky dependency in hot/sandbox path |
| `BinarySignatureBench` | artifact signature verification | invalid signature blocks worker promotion |
| `CapabilityDenyBench` | WASI/filesystem/network defaults | deny by default, preopen requires policy proof |
| `SandboxEscapeBench` | malicious sandbox probes | no filesystem/network escape under profile |
| `SecretIsolationBench` | prompt/log/browser redaction | raw secret never enters LLM context |
| `WorkerBinaryTrustBench` | remote worker admission | privileged role requires valid proof |
| `ReproBuildBench` | reproducible or explainable build | binary hash stable or variance recorded |

### 32.8 Wave 13 Backlog

1. Add `SupplyChainProof` schema.
2. Add SBOM generation target for Rust/Python artifacts.
3. Add dependency admission policy for `Cargo.toml` changes.
4. Add `CapabilityDenyBench` for Wasmtime/WASI preopen rules.
5. Add `SecretIsolationBench` for tool/browser/computer evidence.
6. Add worker attestation schema for distributed execution.
7. Add `BinarySignatureBench` and `WorkerBinaryTrustBench`.
8. Add `SandboxEscapeBench` with benign malicious probes in local sandbox only.

---

## 33. Deep Research Wave 14: Observability, Telemetry, and Control-Plane Forensics

This wave makes runtime behavior inspectable without confusing dashboards for truth. Telemetry is a mirror and diagnostic layer; the event ledger, evidence refs, and physical witness hashes remain the audit source of truth.

### 33.1 Source Scan

| Source | High-signal technique | AEGIS adoption rule |
|---|---|---|
| [OpenTelemetry](https://opentelemetry.io/docs/) / [OTLP](https://opentelemetry.io/docs/specs/otlp/) | traces, metrics, logs, semantic conventions, collector pipeline. | Use for interoperable telemetry export; ledger hashes remain canonical audit record. |
| [opentelemetry-rust](https://github.com/open-telemetry/opentelemetry-rust) / [tracing](https://docs.rs/tracing/latest/tracing/) | Rust instrumentation, spans, subscribers, structured events. | Instrument Rust runtime with low-cardinality spans and evidence refs. |
| [Prometheus](https://prometheus.io/docs/) | metrics model, pull scraping, alerting, label discipline. | Use for low-cardinality operational metrics and SLO alerts, not high-cardinality evidence logs. |
| [Grafana Loki](https://grafana.com/docs/loki/latest/) / [Tempo](https://grafana.com/docs/tempo/latest/) / [Pyroscope](https://grafana.com/docs/pyroscope/latest/) | logs, traces, continuous profiling stack. | Control-plane analysis only; raw secrets and unredacted LLM text cannot enter dashboards. |
| [Parca](https://www.parca.dev/docs/) / [eBPF](https://ebpf.io/) / [Cilium eBPF](https://docs.cilium.io/en/stable/bpf/) / [Aya](https://github.com/aya-rs/aya) | low-overhead profiling and kernel/runtime observability. | Optional profiling path after overhead bench; profiler output diagnoses performance, not correctness. |

### 33.2 TelemetryEnvelope

Every exported signal should preserve a stable link to the audit ledger:

```rust
pub struct TelemetryEnvelope {
    pub run_id: RunId,
    pub trace_id: [u8; 16],
    pub span_id: [u8; 8],
    pub event_kind: TelemetryEventKind,
    pub risk_class: RiskClass,
    pub evidence_ref_hash: Option<[u8; 32]>,
    pub policy_window_hash: Option<[u8; 32]>,
    pub redaction_policy_hash: [u8; 32],
    pub monotonic_time_ns: u64,
    pub wall_time_ms: u64,
    pub payload_hash: [u8; 32],
}
```

Rules:
- Telemetry payloads are redacted summaries or hashes.
- Raw LLM text, secrets, cookies, and unredacted browser evidence do not enter metrics/logs/traces.
- Every policy block, witness divergence, sandbox trap, and R3/R4 decision must have a trace link.
- Missing telemetry cannot block replay if the event ledger is intact.

### 33.3 ForensicTracePacket

For incident/debug export, bundle the trace with hashes:

```rust
pub struct ForensicTracePacket {
    pub incident_id: IncidentId,
    pub run_id: RunId,
    pub checkpoint_hash: [u8; 32],
    pub trace_root_hash: [u8; 32],
    pub metric_snapshot_hash: [u8; 32],
    pub log_bundle_hash: [u8; 32],
    pub profile_hash: Option<[u8; 32]>,
    pub redaction_policy_hash: [u8; 32],
    pub evidence_refs: SmallVec<[EvidenceRef; 16]>,
}
```

The packet is a map back to sealed evidence, not a substitute for sealed evidence.

### 33.4 Signal Discipline

```text
Metrics:
  low cardinality, alertable, p50/p99/rates/counters

Traces:
  request/run/task causal graph with hashed ids

Logs:
  structured event summaries, no raw secrets

Profiles:
  CPU/heap/blocking diagnosis with overhead budget

Ledger:
  append-only canonical audit truth
```

Cardinality rules:
- no raw task ids, file paths, URLs, user text, or artifact hashes as Prometheus labels;
- use exemplars or trace links for high-cardinality detail;
- per-run/per-task drilldown belongs in trace/log/audit plane, not metric labels.

### 33.5 Sampling and Must-Keep Events

Sampling is allowed only for low-risk high-volume events.

Never sample:
- R3/R4 decisions;
- policy blocks;
- sandbox traps;
- witness divergence;
- approval grants/denials;
- secret access attempts;
- replay crash/recovery events;
- supply-chain verification failures.

Sample candidates:
- repeated successful L0 context pack builds;
- low-risk cache hits;
- periodic heartbeat spans;
- UI polling events.

### 33.6 Control-Plane Forensics

The TUI/dashboard/API must observe, not control hidden state:

Rules:
- control plane cannot mutate task priority directly;
- control plane cannot approve R3/R4 without signed approval event;
- export actions are logged as evidence refs;
- redaction policy version is shown with every incident packet;
- dashboards display policy/witness status from ledger-derived state.

### 33.7 Alerting and SLOs

Alert on physical health, not model vibes:

```text
witness_divergence_rate
sandbox_trap_rate
replay_recovery_failure_rate
policy_violation_attempt_rate
r4_hard_block_count
event_ledger_append_lag_ms
checkpoint_seal_latency_ms
context_pack_over_budget_rate
browser_witness_missing_rate
secret_redaction_failure_count
```

An alert opens an investigation task with evidence refs and a `ForensicTracePacket`; it does not auto-relax policy.

### 33.8 Observability Bench Gates

| Gate | Measures | Pass rule |
|---|---|---|
| `TelemetryEnvelopeBench` | envelope completeness | run/trace/span/hash/redaction fields present |
| `TraceEvidenceLinkBench` | trace-to-ledger correlation | policy/witness/trap events link to evidence refs |
| `MetricCardinalityBench` | metric label safety | no high-cardinality secret/path/hash labels |
| `RedactionPolicyBench` | log/trace/dashboard redaction | secrets/raw LLM text absent from exports |
| `SamplingPolicyBench` | must-keep event retention | R3/R4/trap/witness/security events never sampled out |
| `ProfileOverheadBench` | profiler overhead | profiling cost recorded before enablement |
| `ForensicReplayBench` | incident packet replay | packet reconstructs ledger path |
| `AlertRuleBench` | SLO alert mapping | alert creates investigation with evidence refs |
| `ControlPlaneNoBypassBench` | dashboard/API safety | no priority/policy/witness bypass |

### 33.9 Wave 14 Backlog

1. Add `TelemetryEnvelope` schema.
2. Add trace ids to key RunEvents and policy/witness decisions.
3. Add `ForensicTracePacket` export format.
4. Add metric label allowlist and cardinality tests.
5. Add redaction tests for LLM text, secrets, browser cookies, URLs.
6. Add must-keep sampling policy.
7. Add profile overhead experiment behind feature flag.
8. Add alert-to-investigation task creation spec.

---

## 34. Deep Research Wave 15: Human-in-the-Loop, Approval UX, and Operator Safety

This wave makes human approval cryptographic and scoped. AEGIS should never ask an operator to "trust the agent"; it should ask them to sign an exact typed action after seeing the evidence, risk, scope, and rollback story.

### 34.1 Source Scan

| Source | High-signal technique | AEGIS adoption rule |
|---|---|---|
| [WebAuthn](https://www.w3.org/TR/webauthn-3/) / [FIDO2](https://fidoalliance.org/specifications/) | phishing-resistant user authentication and hardware-backed assertions. | Candidate for local high-assurance approval signatures and operator identity binding. |
| [GitHub protected environments](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment) | required reviewers, wait timers, environment secrets. | Learn reviewer/secret separation and deployment approval ergonomics. |
| [LangGraph human-in-the-loop](https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/) | interrupt, review, approve/resume workflow. | Conceptual pattern for pausing before tool execution; AEGIS approval proof remains Rust ledger event. |
| [OpenAI Agents human-in-the-loop](https://openai.github.io/openai-agents-python/human-in-the-loop/) | tool approval pause/resume mechanics. | Use as UX inspiration only; policy and signature remain AEGIS-owned. |
| [Pydantic AI deferred tools](https://ai.pydantic.dev/tools-advanced/#deferred-tools) | deferred tool calls for approval. | Useful tool-call lifecycle pattern; AEGIS still lowers to `TypedToolIR` first. |
| [Kubernetes admission controllers](https://kubernetes.io/docs/reference/access-authn-authz/admission-controllers/) | request admission before state mutation. | Useful mental model: mutate/validate/admit before side effect, with policy evidence. |

### 34.2 ReviewPacket

Approval UI must display a review packet, not raw LLM prose:

```rust
pub struct ReviewPacket {
    pub review_id: ReviewId,
    pub typed_tool_ir_hash: [u8; 32],
    pub policy_proof_trace_hash: [u8; 32],
    pub risk_class: RiskClass,
    pub side_effect: SideEffectClass,
    pub resource_scope_hash: [u8; 32],
    pub expected_effect_hash: [u8; 32],
    pub evidence_refs: SmallVec<[EvidenceRef; 16]>,
    pub staging_proof_ref: Option<EvidenceRef>,
    pub staging_proof_kind: Option<StagingEvidenceKind>,
    pub hitl_override_ref: Option<EvidenceRef>,
    pub reversibility_plan_hash: Option<[u8; 32]>,
    pub redaction_policy_hash: [u8; 32],
}
```

Rules:
- Review packet is generated from `TypedToolIR` and policy facts.
- LLM summary may be shown as helper text, but cannot be signed as the approval target.
- R3/R4 review must include side effect, resource scope, credential scope, and expiry.
- R4 review must include hash-bound staging/testnet/snapshot/browser-session evidence with physical `StagingEvidenceKind`, or signed real-world override.

`OperatorReviewArtifact` is the current Rust signing target wrapper. It binds `ReviewPacket`, `PolicyProofTrace`, optional `ApprovalScopeReplay`, policy-window hash, replay event hashes, and redaction policy into `signing_target_hash`. Helper prose may be attached as `helper_text_hash`, but changing helper text cannot change the signing target.

`DualApprovalProof` is the local two-person baseline. It is valid only when two different `SignedApprovalToken`s from different approver keys bind to the same `OperatorReviewArtifact` signing target and review packet. WebAuthn/FIDO2 can replace the signature source later, but not the hash target.

### 34.3 SignedApprovalToken

Approval must bind to the exact action:

```rust
pub struct SignedApprovalToken {
    pub approval_id: ApprovalId,
    pub approver_key_hash: [u8; 32],
    pub review_packet_hash: [u8; 32],
    pub typed_tool_ir_hash: [u8; 32],
    pub max_risk_class: RiskClass,
    pub resource_scope_hash: [u8; 32],
    pub max_spend_minor_units: Option<u64>,
    pub expires_at_ms: u64,
    pub challenge_nonce: [u8; 32],
    pub signature: [u8; 64],
}
```

Rules:
- Approval to draft is not approval to send.
- Approval to staging is not approval to production.
- Approval to spend $10 is not approval to spend $11.
- Approval expires and fails closed.
- Any change to `TypedToolIR`, resource scope, policy window, or risk class invalidates the approval.

### 34.4 Approval State Machine

```text
NeedsReview
  -> ReviewPacketCreated
  -> PresentedToOperator
  -> ApprovedSigned | Rejected | Expired | NeedsMoreEvidence
  -> PolicyRecheck
  -> ExecuteOrBlock
```

Hard rules:
- No execution between `PresentedToOperator` and `ApprovedSigned`.
- Rejection creates evidence and cannot be silently retried with equivalent wording.
- `NeedsMoreEvidence` routes back to evidence collection, not policy weakening.
- Policy is rechecked after approval because world state may have changed.

### 34.5 Operator UX Safety

The safest policy can fail through a bad UI.

Required UX properties:
- show exact tool, operation, resource scope, credential scope, risk class, expiry;
- show before/after witness or staging proof for R3/R4;
- make reject/cancel as easy as approve;
- default action is no side effect;
- display whether action is reversible, staged, or production;
- show redaction status for evidence.

Forbidden:
- "approve all future R4";
- pre-checked approval boxes;
- approval by replying to an LLM-generated summary only;
- hiding diff/resource scope behind expandable-only UI;
- silent approval reuse after policy/tool schema changes.

### 34.6 Reversibility and Two-Person Rules

Risk classes need different human protocols:

| Risk | Approval shape | Extra condition |
|---|---|---|
| R0/R1 | no approval or simple local confirmation | ledger event |
| R2 | domain/rate policy review if needed | source snapshot |
| R3 | scoped signed approval | draft/staging proof preferred |
| R4 | scoped signed approval plus staging/testnet/snapshot | hard block otherwise |
| R4-high | two-person or hardware-backed approval | optional local policy, fail closed |

Two-person approval is policy-configurable, but if enabled both signatures bind to the same `ReviewPacket`.

### 34.7 Approval Bench Gates

| Gate | Measures | Pass rule |
|---|---|---|
| `ReviewPacketBench` | packet completeness | tool/risk/scope/evidence/reversibility present |
| `SignedApprovalTokenBench` | signature binding | any IR/scope/risk change invalidates token |
| `ApprovalScopeReplayBench` | replay of approval decisions | same allow/block under same evidence |
| `ApprovalExpiryBench` | expiry and policy-window changes | expired/stale approval fails closed |
| `NoDarkPatternBench` | UI safety invariants | no prechecked/bulk/hidden-scope approval |
| `DiffWitnessBench` | before/after evidence | R3/R4 packet includes witness/staging refs |
| `OperatorUndoBench` | reversibility plan | reversible actions have rollback artifact refs |
| `TwoPersonRuleBench` | high-risk dual approval | both signatures required when policy enables it |

### 34.8 Wave 15 Backlog

1. Add `ReviewPacket` schema.
2. Add `SignedApprovalToken` schema.
3. Add approval state machine to ToolGateway plan.
4. Add R3/R4 review packet rendering requirements for TUI/dashboard.
5. Add `ApprovalScopeReplayBench`.
6. Add `NoDarkPatternBench` UI invariant checklist.
7. Add WebAuthn/FIDO2 experiment for local approval signatures.
8. Add two-person approval option for R4-high local policy.

---

## 35. Deep Research Wave 16: Productization, Packaging, and Deployment Topology

This wave turns the research architecture into selectable release profiles. The default product must stay small and deterministic; heavy capabilities are explicit feature profiles with their own tests, benchmarks, and supply-chain proof.

### 35.1 Source Scan

| Source | High-signal technique | AEGIS adoption rule |
|---|---|---|
| [cargo-dist](https://opensource.axo.dev/cargo-dist/) | Rust binary release packaging and installers. | Candidate for signed CLI/TUI release artifacts after supply-chain proof is defined. |
| [Nix flakes](https://nixos.wiki/wiki/Flakes) | reproducible development/build environments. | Candidate reproducible dev shell and CI baseline; not required for end users by default. |
| [Docker BuildKit](https://docs.docker.com/build/buildkit/) / [cargo-chef](https://github.com/LukeMathWalker/cargo-chef) | cached container builds and Rust dependency layer planning. | Use for CI/build speed, not as proof of correctness or sandbox safety. |
| [uv](https://docs.astral.sh/uv/) | fast Python packaging/environment management. | Candidate for Python bridge dev/test workflows and offline lock sync. |
| [maturin](https://www.maturin.rs/) / [PyO3](https://pyo3.rs/) | Rust/Python extension and wheel packaging. | Use for Python bridge packaging only with ABI/version hash in release manifest. |
| [systemd units](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) / [Helm](https://helm.sh/docs/) | local service and Kubernetes deployment packaging. | Optional service deployments; core harness must still run as local CLI/TUI. |

### 35.2 ReleaseProfile

Every distributed artifact must state what is inside:

```rust
pub struct ReleaseProfile {
    pub profile_id: &'static str,
    pub version: &'static str,
    pub target_os: &'static str,
    pub target_arch: &'static str,
    pub feature_set_hash: [u8; 32],
    pub config_schema_hash: [u8; 32],
    pub supply_chain_proof_hash: [u8; 32],
    pub benchmark_report_hash: [u8; 32],
    pub audit_report_hash: [u8; 32],
}
```

Rules:
- Default profile is minimal Rust core plus local audit tooling.
- Browser, inference, cluster, profiling, and network-heavy features are opt-in.
- Each profile has its own test/audit/bench report.
- Feature profile hash enters `RunEvent` at startup.

### 35.3 Feature Profiles

| Profile | Includes | Excludes by default |
|---|---|---|
| `core-minimal` | Rust DAG, Wasmtime, physical witness, audit log | browser, network providers, cluster, self-hosted inference |
| `local-dev` | core + Python bridge + developer checks | production approvals, cluster workers |
| `browser-witness` | BrowserWitnessProof collectors | persistent browser profile unless approved |
| `self-hosted-inference` | InferenceBackendContract + prefix cache gates | external API KV assumptions |
| `cluster-worker` | ClusterWorkEnvelope worker runtime | direct ledger commit permission |
| `hardened-prod` | supply-chain proof, signed approval, deny-by-default capabilities | experimental perf dependencies |
| `full-lab` | all experiments behind gates | production claims |

### 35.4 Deployment Topologies

```text
single-user local:
  CLI/TUI + local files + optional browser witness

local service:
  Rust service + TUI/dashboard + signed approval device

python-integrated:
  PyO3/maturin wheel + Rust core shared library

self-hosted inference:
  Rust core + inference backend contract + local model runtime

cluster:
  single writer + remote workers + CAS + policy/approval service

offline bundle:
  binary + config schema + SBOM + model/tool manifests + docs
```

Topology rules:
- topology cannot weaken policy;
- remote worker topology cannot grant commit permission;
- offline mode must fail closed for unavailable external verification;
- every topology produces a `DeploymentManifest` hash.

### 35.5 Configuration Layering

Configuration is data with a hash chain:

```text
default_config
  -> release_profile_config
  -> machine_config
  -> operator_policy_config
  -> secret_ref_bindings
  -> runtime_approval_events
```

Rules:
- raw secrets are never config values;
- environment variables may point to secret handles, not raw credentials;
- config changes during a run emit `ConfigChanged` event;
- policy-related config changes invalidate approvals and prefix caches;
- config hash appears in benchmark and replay artifacts.

### 35.6 ReleaseManifest

```rust
pub struct ReleaseManifest {
    pub release_profile_hash: [u8; 32],
    pub binary_hashes: SmallVec<[[u8; 32]; 8]>,
    pub sbom_hash: [u8; 32],
    pub config_schema_hash: [u8; 32],
    pub migration_plan_hash: Option<[u8; 32]>,
    pub test_report_hash: [u8; 32],
    pub benchmark_report_hash: [u8; 32],
    pub constitution_audit_hash: [u8; 32],
}
```

Release cannot be promoted when:
- `constitution_audit` fails;
- supply-chain proof is missing;
- feature profile includes unbenchmarked experimental dependency;
- migration lacks rollback path;
- default profile pulls optional heavy features.

### 35.7 Upgrade and Rollback

Upgrade must be replay-safe:

```text
pre_upgrade_checkpoint
release_manifest_verify
schema_migration_dry_run
replay_sample_against_new_binary
operator_approval_if_risk_increases
promote
rollback_checkpoint_retained
```

Rules:
- schema migration must fail closed;
- rollback path is required before production migration;
- benchmark regressions require waiver and evidence;
- upgrading policy config invalidates old approvals.

### 35.8 Productization Gates

| Gate | Measures | Pass rule |
|---|---|---|
| `ReleaseProfileBench` | feature set and profile hash | default profile stays minimal |
| `ReleaseManifestBench` | release evidence completeness | binary/SBOM/config/test/bench/audit hashes present |
| `OfflineInstallBench` | offline bundle behavior | no network needed; unavailable external checks fail closed |
| `ConfigLayerBench` | config hash and invalidation | policy/config changes invalidate approvals/cache |
| `ServiceBoundaryBench` | topology safety | service/dashboard cannot bypass core gates |
| `UpgradeRollbackBench` | migration and rollback | replay sample passes before promotion |
| `BinarySizeBench` | feature profile footprint | optional features do not bloat minimal profile |
| `ColdStartBench` | startup latency per profile | measured before claims |

### 35.9 Wave 16 Backlog

1. Define `ReleaseProfile` and `ReleaseManifest`.
2. Add feature profile inventory to `Cargo.toml`/docs.
3. Add config layering spec and config hash event.
4. Add `OfflineInstallBench` for local bundle.
5. Add `ServiceBoundaryBench` for TUI/dashboard/API topology.
6. Add `UpgradeRollbackBench` with schema migration dry-run.
7. Add profile-specific binary size and cold-start reports.
8. Add release promotion rule requiring audit + benchmark + supply-chain proof.

---

## 36. Deep Research Wave 17: Architecture Freeze and Implementation Roadmap

This wave converts research into build order. The goal is not to implement every advanced idea immediately; the goal is to freeze the dependency graph so AEGIS can build the smallest truth-preserving core first, then add high-complexity performance features only behind gates.

### 36.1 Freeze Principles

Hard freeze rules:
- The deterministic Rust core owns scheduling, policy, replay, and commit order.
- LLM output can propose only typed intents and candidate evidence.
- Retrieval, ranking, telemetry, profiler output, and dashboards are not truth.
- Optional performance features require feature flags, benchmark artifacts, and rollback path.
- No source may claim platform-level performance until end-to-end HarnessBench evidence exists.
- Any feature that touches secrets, approvals, side effects, or witness commits fails closed when uncertain.

### 36.2 Implementation Dependency Graph

```text
Foundation:
  RunEvent ledger
  EvidenceRef/CAS
  RiskPolicy
  PhysicalWitness
  Wasm-only sandbox trait

Truth Plane:
  LLMResponseReceived
  TypedToolIR
  PolicyProofTrace
  ReviewPacket/SignedApprovalToken
  TelemetryEnvelope

Evidence Plane:
  EvidenceIndexManifest
  SegmentCatalog
  BrowserWitnessProof
  HarnessBenchScorecard

Performance Plane:
  PerformanceBudgetLedger
  RuntimeLayoutBudget
  PrefixCacheBroker v2
  BinaryReplayRecordHeader

Expansion Plane:
  ClusterWorkEnvelope
  SupplyChainProof
  ReleaseProfile/ReleaseManifest
```

Ordering rule:
- A lower plane cannot depend on a higher plane.
- Performance Plane may optimize Truth/Evidence Plane only after equivalence gates pass.
- Expansion Plane cannot bypass lower-plane gates.

### 36.3 Phase 2 Build Order

P0: truth schemas and audit locks
1. `RunEvent::LLMResponseReceived`.
2. `TypedToolIR`.
3. `PolicyProofTrace`.
4. `ReviewPacket` and `SignedApprovalToken`.
5. `TelemetryEnvelope`.
6. `HarnessBenchScorecard`.

P1: deterministic replay and policy kernel
1. durable replay over existing Arrow `StreamWriter<File>`.
2. policy Layer 0 deterministic classifier plus deterministic Datalog-style closure proof.
3. R3/R4 hard-block and staging rules, including proof-trace/closure binding.
4. approval token replay binding plus approval-scope coverage checks over review-packet hash, typed-tool IR, resource scope, risk ceiling, spend cap, expiry, and policy-window hash.
5. crash-after-LLM-before-tool recovery.
6. deterministic TaskLedger priority baseline with structural `TaskGraphCache`; full dirty-subgraph priority propagation remains pending.
7. ContextGovernor activated-subgraph baseline.
8. ContextPackBuilt, PolicyDecisionRecorded, and ApprovalTokenRecorded replay events plus standard Arrow IPC replay roundtrip, sealed segment manifest recovery, segmented Arrow audit proof with semantic mmap scan, mmap-backed sealed segment readback, last-valid-prefix recovery, and approval-boundary sweep; a fresh bounded 128-point seeded crash scorecard is now locally proven, while randomized latency/copy-count and broader hosted/process-boundary campaigns remain pending.

P2: evidence indexing and benchmark gauntlet
1. `EvidenceIndexManifest`.
2. `SegmentCatalog`.
3. local HotEvidenceIndex prototype.
4. GauntletSpecBench and ReplayChaosBench.
5. BrowserOpsBench read-only plus injection defense.

P3: measured performance upgrades
1. `PerformanceBudgetLedger`.
2. `InstructionCountBench` and `AllocationBench`.
3. QuickJS ABI benchmark.
4. mmap/segmented Arrow reader benchmark.
5. PrefixCacheBroker v2 only for self-hosted backends.

P4: deployment and distributed expansion
1. `ReleaseProfile` and `ReleaseManifest`.
2. `SupplyChainProof`.
3. `ClusterWorkEnvelope`.
4. remote worker proof-of-concept.
5. hardened-prod profile.

### 36.4 Do-Not-Build-Yet List

Do not build these until prerequisite gates pass:

| Feature | Blocked until |
|---|---|
| default HTTP/3/QUIC client | provider baseline and latency proof |
| default `simd-json` path | `SimdJsonBench` canonical equality and allocation win |
| global allocator swap | Windows/Linux `AllocationBench` and rollback profile |
| mutable mmap writer | crash/recovery proof and sealed segment alternative benchmark |
| DataFusion in hot path | never allowed; query plane only |
| vector DB as truth source | never allowed; candidate retrieval only |
| CP-SAT hot scheduler | never allowed; offline schedule metadata only |
| cluster remote commit | never allowed; single-writer log only |
| quantized model tool-call authority | `QuantizationWitnessBench` and policy hash match |
| R4 real-world action without staging | explicit HITL cryptographic override |
| browser persistent profile | approval, redaction, and credential scope proof |

### 36.5 Phase Gate Matrix

| Gate | Required evidence |
|---|---|
| `TruthSchemaGate` | Rust schema compiles, replay event hash stable |
| `PolicyGate` | R3/R4 hard-block tests and approval expiry tests pass |
| `ReplayGate` | randomized crash recovery yields same next legal action |
| `WitnessGate` | physical witness proof hashes match committed artifacts |
| `PerformanceGate` | budget, instruction, allocation, p99 reports recorded |
| `SecurityGate` | supply-chain proof, secret isolation, sandbox capability deny |
| `ProductGate` | release manifest has binary/SBOM/config/test/bench/audit hashes |
| `NoOverclaimGate` | docs distinguish implemented, target, and benchmarked states |

### 36.6 Canonical Next Implementation Packet

Sprint A baseline has been implemented as Rust truth-schema primitives plus audit locks:

```text
Sprint A:
  implemented:
    TypedToolIR
    PolicyFacts
    PolicyProofTrace
    PolicyDatalogClosureProof
    ReviewPacket
    SignedApprovalToken
    ApprovalScopeReplay
    OperatorReviewArtifact
    DualApprovalProof
    TelemetryEnvelope
    HarnessBenchScorecard
    RunEvent::LLMResponseReceived
    RunEvent::ApprovalTokenRecorded
    DeterministicPolicyKernel Layer 0
    R3/R4 approval and staging unit tests

  audit checks:
    raw LLM text never reaches executor
    R4 dry-run text not accepted
    approval to draft != approval to send
    telemetry is not audit truth

  tests:
    approval expiry fails closed
    approval token replay binding includes review packet and expiry
    approval-scope replay covers packet/IR/resource/risk/spend/policy-window
    operator review artifact signs hashes, not helper prose
    dual approval requires two distinct approver keys on the same artifact
    replay after LLM response before tool
    scorecard rejects LLM judge pass/fail
    R4 without staging/HITL hard-blocks
    staging contract requires sandbox evidence
```

No new heavy dependency is required for Sprint A; none was added for the baseline implementation. The next packet should focus on broader replay crash campaigns and optional WebAuthn/hardware-backed signature experiments; Datalog/SMT/e-graph policy closure remains out of scope until the small deterministic kernel is fully wired and benchmarked.

### 36.7 Architecture Freeze Questions

Questions to answer before implementation:
1. Should `TypedToolIR` live in `core/rust/src/policy.rs` or a new `tool_ir.rs` module?
2. Should `PolicyProofTrace` be part of `RunEvent` immediately or stored as an `EvidenceRef` sidecar first?
3. Should `ReviewPacket` support WebAuthn now, or start with local signing abstraction and add WebAuthn later?
4. Should `TelemetryEnvelope` be compiled into core by default, or behind a `telemetry` feature with no-op default?
5. Which single P0 bench should become the first HarnessBench-AEGIS seed: `ReplayChaosBench`, `R4HardBlockBench`, or `TypedToolIrBench`?

### 36.8 Freeze Backlog

1. **Completed (design input):** add `docs/ARCHITECTURE_FREEZE.md` with the
   frozen principles, AF-001–AF-007 decision records, ownership map, migration
   gates and explicit non-adoption rules derived from this section.
2. **Completed (local gate):** `scripts/constitution_audit.py` exposes
   `TruthSchemaGate` for versioned schema identity and Rust owner markers; Rust
   compile and replay-hash stability remain runtime evidence.
3. **Completed (local gate):** `NoOverclaimGate` scans high-risk readiness
   language by sentence context and requires explicit freeze/baseline/benchmark
   status disclosures.
4. **Completed (design input):** the P0 module ownership map is recorded in
   `docs/ARCHITECTURE_FREEZE.md`, including canonical paths and cutover proof.
5. **Completed (design input):** Sprint A task cards now bind owners, outputs,
   acceptance evidence, fail-closed behavior and rollback boundaries.
6. **Completed (design input):** the implementation/no-build decision log now
   records the evidence, consequence and revisit trigger for each choice.

---

## 37. Deep Research Wave 18: Hot Evidence Index and Candidate Retrieval Cascade

This wave turns long-run memory retrieval into a bounded, data-oriented candidate pipeline. The goal is not to install a search stack by default; the goal is to define the fastest safe shape for evidence lookup when 100h runs create too many files, symbols, traces, screenshots, and replay segments for global scans.

### 37.1 Source Scan

| Source | Relevant technique | AEGIS adoption rule |
|---|---|---|
| [Tantivy](https://github.com/quickwit-oss/tantivy) | Rust Lucene-style full-text search, BM25, fast fields/columnar doc values. | Candidate lexical tier only; feature-gated until canonical evidence refs and allocation/latency benches pass. |
| [SeekStorm](https://github.com/SeekStorm/SeekStorm) | Rust sub-millisecond BM25/full-text library and server design. | Benchmark reference for lexical latency; do not adopt server path into core. |
| [BM25S](https://arxiv.org/abs/2407.03618) | Eager sparse BM25 scoring with high lexical throughput. | Candidate algorithm for offline/lab comparison, not a default dependency. |
| [fst](https://github.com/BurntSushi/fst) | Compact finite-state transducer maps/sets for term dictionaries and fuzzy/prefix expansion. | Candidate replacement for the sorted-Vec `HotTermDictionary`; every expansion must remain a candidate ref, and adoption requires beating the baseline. |
| [roaring-rs](https://github.com/RoaringBitmap/roaring-rs) | Compressed bitmaps for fast set intersections. | Candidate filter tier for task/risk/evidence tags; bitmap result cannot bypass policy or witness gates. |
| [CRoaring](https://github.com/RoaringBitmap/CRoaring) | C/C++ Roaring implementation with SIMD-oriented set operation paths. | Reference for future feature-gated roaring benchmark only; default core keeps the sorted-Vec baseline until roaring wins on AEGIS cardinalities. |
| [DiskANN](https://github.com/microsoft/DiskANN) / [FreshDiskANN](https://arxiv.org/abs/2105.09613) | SSD-backed graph ANN and streaming vector index updates. | Cold vector expansion only; no ANN result is truth, and no disk ANN runs in the scheduler hot path. |
| [OctopusANN](https://arxiv.org/abs/2602.21514) | SSD graph ANN I/O optimizations: memory-resident navigation and dynamic width. | Research input for cold tier; adoption requires storage-profile-specific recall/latency/copy-count reports. |
| [USearch](https://github.com/unum-cloud/USearch) / [SimSIMD](https://github.com/ashvardanian/SimSIMD) | SIMD similarity and compact vector-search kernels with Rust bindings. | Optional exact/SIMD rerank for small candidate sets only; hardware feature hash must enter the benchmark artifact. |

### 37.2 HotEvidenceIndex v2

The retrieval plane returns handles, not conclusions:

```rust
pub enum EvidenceCandidateTier {
    ActivatedContext,
    LexicalBm25,
    FstExpansion,
    BitmapFilter,
    ExactSimdRerank,
    ColdVectorExpansion,
}

pub struct CandidateEvidenceRef {
    pub evidence_ref_hash: [u8; 32],
    pub segment_id: u64,
    pub origin_tier: EvidenceCandidateTier,
    pub score_quantized: u32,
    pub index_epoch_hash: [u8; 32],
}
```

Rules:
- `CandidateEvidenceRef` is a pointer to evidence, not evidence.
- Retrieval may rank, filter, and deduplicate candidates, but cannot commit memory, task status, policy approval, or witness proof.
- Every returned ref must resolve in CAS or a sealed segment catalog before it enters a `ContextPack`.
- Ranking scores are diagnostic metadata; physical witness, policy proof, and replay ledger remain the commit authorities.

### 37.3 Retrieval Cascade

Default hot path:

```text
seed = active_task_id + required_evidence_refs + current symbol/file handles
activated = ContextGovernor 1-hop/2-hop graph
lexical = HotLexicalIndex.top_k(seed_terms, k_small)
expanded_terms = HotTermDictionary.expand(seed_terms, max_terms)
filtered = HotBitmapFilter.intersect(lexical, task_scope, risk_scope, evidence_kind)
reranked = optional ExactSimdRerank(filtered, only_if candidate_count <= cap)
ContextPack candidates = resolve CAS refs + apply token budget
```

Cold path:

```text
ColdVectorExpansion event required
VectorColdTier.search(query_embedding_hash, k)
intersect with evidence-exists bitmap
return CandidateEvidenceRef list
wait for physical gate before any commit
```

Hot-path invariants:
- No full-text index scan inside scheduler/task priority.
- No disk ANN query inside `select_next_task` or `build_context_pack`.
- No vector score, BM25 score, or fuzzy match score is accepted as proof.
- Cold expansion must emit a replay event containing query hash, index epoch hash, candidate count, and latency evidence.

### 37.4 Index Epoch Hashing

Every index generation is deterministic data:

```text
index_epoch_hash =
  blake3(
    segment_catalog_hash,
    tokenizer_config_hash,
    schema_hash,
    redaction_policy_hash,
    embedding_model_hash_or_zero,
    feature_profile_hash
  )
```

Invalidation rules:
- New sealed segment creates a new index epoch.
- Tokenizer/schema/redaction/embedding changes invalidate candidate caches.
- Index corruption or missing segment catalog fails closed to `CandidateEvidenceUnavailable`.
- Query-plane indexes may be rebuilt in the background, but the hot path consumes only sealed index epochs.

### 37.5 Gates

| Gate | Measures | Pass rule |
|---|---|---|
| `CandidateOnlyGate` | retrieval result type and downstream use | candidates cannot become witness, approval, memory commit, or Done status directly |
| `HotLexicalIndexBench` | lexical top-k latency and allocation | bounded p99 and no global scan over sealed archive |
| `FstPrefixBench` | dictionary expansion | sorted-Vec baseline must stay under gate; fst adoption requires deterministic term order, bounded expansion count, and a win over baseline |
| `BitmapFilterBench` | set intersections over task/risk/evidence tags | sorted-Vec baseline must stay under gate; roaring adoption requires beating this baseline at target cardinality |
| `HybridRecallLatencyBench` | lexical + bitmap + optional SIMD rerank | recall/latency improves over lexical-only without weakening gates |
| `ColdVectorExpansionBench` | DiskANN/USearch-style cold path | explicit replay event required; no scheduler hot-path dependency |
| `IndexEpochReplayBench` | replay after index rebuild | same query + same epoch yields same candidate refs |

### 37.6 Non-Adoption List

Do not adopt:
- search server as a default local dependency;
- vector DB as truth source;
- ANN result as witness;
- BM25/fuzzy score as policy evidence;
- cold vector expansion without replay event;
- mutable index epoch visible to the hot path;
- SIMD/vector dependency in `core-minimal` before feature-profile bench evidence.

### 37.7 Wave 18 Backlog

1. Implemented baseline: `CandidateEvidenceRef`, `EvidenceCandidateTier`, `IndexEpochInputs`, and `CandidateOnlyGate` exist in Rust.
2. Implemented audit lock: `constitution_audit` checks `hot_evidence_index_candidate_only_gate` and `plan_wave18_hot_evidence_index`.
3. Implemented baseline: a tiny deterministic `HotLexicalIndex` inverted index and `hot_lexical_index_top_k` benchmark gate exist before any Tantivy/SeekStorm/BM25S experiment; current fresh mean is about 102.5 us against 10k docs with a 200 us gate.
4. Implemented baseline: `SortedEvidenceSet`/`HotBitmapFilter` provide no-dependency sorted-Vec filtering, current gated `hot_bitmap_filter_intersection` measured about 51.7 us mean over 65,536 segment ids, and the gate is set to 150 us. `roaring-rs` remains candidate-only until it beats this baseline behind a feature/benchmark gate.
5. Implemented baseline: `HotTermDictionary` provides no-dependency sorted-Vec prefix expansion over sealed symbol/file terms, `hot_term_dictionary_prefix_expand` measured about 11.1 us mean over 50k terms, and the gate is set to 25 us. `fst` remains candidate-only until it beats this baseline behind a feature/benchmark gate.
6. Implemented local baseline: `ColdVectorExpansionReplayRecord` and `ColdVectorExpansionRecorded` bind query hash, index epoch, expansion configuration/artifact, bounded candidate list, and non-zero latency evidence; deterministic in-memory `ColdVectorIndex` emits only `CandidateOnly` refs and has replay/tamper regressions before any DiskANN/USearch adoption. No DiskANN/USearch dependency is enabled.
7. Implemented baseline: `IndexEpochReplayRecord`, `lexical_query_hash`, and `candidate_list_hash` bind same-query/same-epoch candidate refs for replay; `index_epoch_replay_record` measured about 3.73 us mean with a 10 us gate for the hot hash-binding path.
8. Implemented local campaign: `replay-chaos-scorecard` exercised 128 deterministic seeded crash points over segmented replay, recovered only valid prefixes, and emitted a hash-bound scorecard plus mmap/write evidence. The artifact is local evidence only; 100h endurance, cross-process/hosted writer enforcement, randomized latency/copy-count dispersion, and independent verification remain pending.
