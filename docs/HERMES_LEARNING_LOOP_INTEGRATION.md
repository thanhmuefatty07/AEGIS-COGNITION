# Hermes Learning Loop Integration Report
## AEGIS-COGNITION — Self-Improving Cryptographic AI Harness

**Author:** AEGIS Principal AI Research Engineer
**Date:** 2026-06-12
**Model:** deepseek-ai/deepseek-v4-pro via Nvidia
**Status:** COMPLETE — All 5 phases executed

---

## 1. Executive Summary

### Top 5 Findings from Hermes Analysis

1. **Hermes skill self-improvement is LLM-driven**: skills are created from successful task completions, patched mid-session when errors are found, and auto-curated by a background Curator process that tracks usage, marks idle skills stale, archives old ones, and keeps tar.gz backups. The entire lifecycle is agentic — no human in the loop for skill creation or improvement.

2. **Memory is multi-layered**: Hermes has (a) in-session memory (the conversation), (b) persistent cross-session memory (user profile + personal notes stored as declarative facts in a database with periodic "nudge" reminders), (c) FTS5 full-text search over past session transcripts for cross-session recall, (d) external memory backends (Honcho, Mem0) for dialectic user modeling.

3. **Learning = Skills + Memory + Curator**: Hermes's learning loop isn't a single component — it's the synergy of skill_manage (create/patch/delete), memory (add/replace/remove), session_search (FTS5 discovery + scroll), and the Curator background process (stale detection, archival, backup).

4. **Trust model is agentic, not cryptographic**: Hermes trusts the LLM to decide what's worth saving. There's no BLAKE3 hash chain, no regression testing before skill admission, no CandidateOnlyGate. The Curator only acts on agent-created skills (not bundled/hub-installed) but still trusts the content.

5. **FTS5 session search is SQLite-native**: Session transcripts are stored in SQLite with FTS5 indexes. Discovery returns bookends + match-context windows. Scroll is paginated. This is lightweight and fast but provides candidates, not cryptographically-verified truth.

### Integration Approach Summary

AEGIS will absorb Hermes's learning primitives through a **cryptographically-hardened adapter layer** that wraps every learning event in BLAKE3 hash chains, enforces `CandidateOnlyGate` on session recall, requires regression testing before skill improvements, and respects `TrustLevel` gates (DEV auto-learn, STAGING requires review, PROD requires approval). The integration adds 5 new modules while preserving every AEGIS constraint.

### Expected Impact

| Capability | Before | After |
|-----------|--------|-------|
| Skill creation | Manual | Autonomous + crypto-audited |
| Skill improvement | None | LLM-assisted + regression-tested |
| Memory persistence | Manual | Periodic nudge + PAV-validated |
| Cross-session recall | None | FTS5-style + CandidateOnlyGate |
| User model | None | Trust-level-gated |

---

## 2. Hermes Learning Loop Analysis

### 2.1 Skill Creation Pipeline

Hermes creates skills via `skill_manage(action='create')` — the LLM itself decides when to save a skill. Trigger conditions (documented in the agent persona):

- Complex task succeeded (5+ tool calls)
- Errors overcome during task
- User-corrected approach worked
- Non-trivial workflow discovered
- User explicitly asks to remember a procedure

Skills are stored as SKILL.md files with YAML frontmatter and markdown body. Created skills go to `~/AppData/Local/hermes/skills/`. The `hermes-agent` skill documents the format: trigger conditions, numbered steps with exact commands, pitfalls section, verification steps.

**Key insight**: There's no pre-compilation, no WASM, no regression suite. Skills are prompt-engineered documents loaded into the model's context. This is fundamentally different from AEGIS's `SkillRegistry` which requires WASM compilation, regression testing, PAV watchdog approval, and BLAKE3 hash chains.

### 2.2 Skill Self-Improvement

Skills improve through `skill_manage(action='patch')` — the LLM patches existing skills when they're found outdated, incomplete, or wrong during use. The agent persona mandates: "When using a skill and finding it outdated, incomplete, or wrong, patch it immediately... don't wait to be asked."

The **Curator** background process manages the lifecycle:
- Tracks usage via sidecar `~/.hermes/skills/.usage.json` (use_count, view_count, patch_count, last_activity_at, state, pinned)
- Marks idle skills stale (`min_idle_hours`)
- Archives stale skills (`archive_after_days`)
- Keeps pre-run tar.gz backups
- Only touches skills with `created_by: "agent"` provenance
- Pinned skills exempt from auto-transition
- **Never deletes** — max destructive action is archive

### 2.3 Memory Nudge System

The agent persona specifies memory rules in detail:
- "You have persistent memory across sessions"
- Save durable facts: user preferences, environment details, tool quirks, stable conventions
- Prioritize what reduces future user steering
- Do NOT save task progress, session outcomes, completed-work logs
- Write as declarative facts, not instructions
- Memory is injected into every turn

The nudge is implicit — the persona instructs the agent to be proactive about saving. There's no separate "nudge scheduler" — it's the persona prompting the LLM to use `memory(action='add')` at the right moments. Config controls: `memory.memory_enabled`, `memory.user_profile_enabled`, `memory.provider`.

Pluggable backends: built-in, Honcho (dialectic user modeling), Mem0, and more. `hermes memory setup/status/off` manages provider config.

### 2.4 Cross-Session Recall (FTS5)

Sessions stored in `~/.hermes/state.db` (SQLite + FTS5). The `session_search` tool provides three shapes:

1. **DISCOVERY** (`query`): FTS5 search, dedupes by session lineage, returns top N sessions with bookends (first 3 messages + last 3 messages) + match window (±5 messages around hit)
2. **SCROLL** (`session_id` + `around_message_id`): Paginated window reading for drilling into a session
3. **BROWSE** (no args): Recent sessions chronologically

FTS5 syntax: AND default, OR for broader recall, quoted phrases, boolean NOT, prefix wildcards. This is fast (SQLite FTS5) and returns candidates, not cryptographically verified facts.

### 2.5 User Model Building (Honcho)

Honcho is a dialectic user modeling system available as a plugin: `hermes honcho setup/status`. The agent persona says: "User profile — who the user is, their preferences, communication style." In AEGIS, the `USER PROFILE` section achieves this via the `memory` tool with `target='user'`.

Honcho builds a model across sessions by observing interaction patterns, preferences, corrections, and communication style. It's a separate service with its own storage — not the SQLite session DB.

---

## 3. AEGIS Integration Design

### 3.1 Learning Event Sourcing

**Design Decision**: Learning events live in a SEPARATE `LearningLedger`, NOT the `ReplayLedger`. Rationale:

- `ReplayLedger` is for deterministic replay of run events (MissionCompiled, ContextPackBuilt, LLMResponseReceived, etc.) — it must be byte-for-byte reproducible
- Learning events (skill created, memory persisted) are cross-session, non-deterministic by nature (LLM generates them)
- Mixing them would break replay determinism

**LearningLedger** uses the SAME BLAKE3 hash chain pattern as `ReplayLedger` and `SkillRegistry`:

```rust
// core/rust/src/learning/mod.rs (NEW)

pub enum LearningEventType {
    SkillCreated { task_hash: [u8; 32] },
    SkillImproved { usage_count: u32, improvement_hash: [u8; 32] },
    MemoryPersisted { memory_hash: [u8; 32] },
    UserModelUpdated { model_hash: [u8; 32] },
    SessionRecallIndexed { session_hash: [u8; 32] },
}

pub struct LearningEvent {
    pub event_type: LearningEventType,
    pub skill_id: Option<u128>,
    pub content_hash: [u8; 32],
    pub timestamp: u64,
    pub session_id: u128,
    pub previous_event_hash: [u8; 32],
    pub event_hash: [u8; 32],
}

pub struct LearningLedger {
    events: Vec<LearningEvent>,
    ledger_hash: [u8; 32],
}

impl LearningLedger {
    pub fn append(&mut self, event: LearningEvent) -> [u8; 32] {
        let event_hash = learning_event_domain_hash(&event);
        self.ledger_hash = blake3_chain_hash(self.ledger_hash, event_hash);
        self.events.push(event);
        event_hash
    }

    pub fn verify_integrity(&self) -> bool {
        let mut computed = [0u8; 32];
        for event in &self.events {
            computed = blake3_chain_hash(computed, learning_event_domain_hash(event));
        }
        computed == self.ledger_hash
    }
}
```

**Integration**: `LearningLedger` is added to `CogniFoldStore` and `SkillRegistry` — every state-mutating learning operation appends an event. The ledger is persisted alongside CogniFold on disk.

### 3.2 Skill Self-Improvement Pipeline

**Design Decision**: Skill improvements go through the SAME admission pipeline as new skills — `SkillAdmissionRecord`, Wasmtime compilation, regression testing, PAV watchdog. The improvement adds an `improvement_hash` and `usage_count` to the manifest.

```rust
// ADD to skill_registry.rs

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SkillImprovementCandidate {
    pub skill_id: u128,
    pub usage_count: u32,
    pub success_rate: f32,
    pub failure_modes: Vec<String>,
    pub suggested_fixes: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SkillImprovementRecord {
    pub skill_id: u128,
    pub previous_package_hash: [u8; 32],
    pub previous_admission_hash: [u8; 32],
    pub usage_count: u32,
    pub success_rate: f32,
    pub improvement_hash: [u8; 32],
    pub regression_report_hash: [u8; 32],
    pub timestamp: u64,
    pub record_hash: [u8; 32],
}

impl SkillRegistry {
    /// Attempt to improve a skill based on usage statistics.
    /// Requires: usage_count >= 10, LLM-assisted improvement,
    /// regression tests pass, PAV accepted.
    pub fn improve_skill_from_usage(
        &mut self,
        skill_id: u128,
        usage_stats: &SkillUsageStats,
        learning_ledger: &mut LearningLedger,
    ) -> Result<SkillImprovementRecord, SkillAdmissionError> {
        // 1. Gate: minimum usage
        if usage_stats.usage_count < 10 {
            return Err(SkillAdmissionError::InsufficientUsage);
        }

        // 2. Extract improvement candidates from usage logs
        let candidates = self.extract_improvement_candidates(skill_id, usage_stats)?;

        // 3. LLM-assisted improvement (via ProviderRuntimeBudget)
        let improved_skill_md = self.generate_improved_skill_md(skill_id, &candidates)?;

        // 4. Compile to WASM
        let improved_wasm = self.compile_skill_to_wasm(&improved_skill_md)?;

        // 5. Build new manifest (version bump)
        let new_manifest = self.build_improved_manifest(skill_id, &improved_wasm)?;

        // 6. Run regression tests
        let regression_report = self.run_regression_tests(skill_id, &improved_wasm)?;
        if regression_report.failed_count > 0 {
            return Err(SkillAdmissionError::RegressionFailed);
        }

        // 7. PAV watchdog
        let sandbox_result = SandboxResult::from_wasm(&improved_wasm);
        let admission = SkillAdmissionRecord::from_wasmtime_and_regressions(
            &new_manifest, &sandbox_result, &regression_report, &self.watchdog,
        )?;

        // 8. Commit to registry
        self.commit_admitted_skill(&new_manifest, &admission, &regression_report)?;

        // 9. Record improvement
        let record = SkillImprovementRecord { /* ... */ };

        // 10. Append to learning ledger
        learning_ledger.append(LearningEvent {
            event_type: LearningEventType::SkillImproved {
                usage_count: usage_stats.usage_count,
                improvement_hash: record.improvement_hash,
            },
            skill_id: Some(skill_id),
            content_hash: record.improvement_hash,
            timestamp: record.timestamp,
            session_id: current_session_id(),
            previous_event_hash: learning_ledger.ledger_hash,
            event_hash: [0; 32],
        });

        Ok(record)
    }
}
```

**Key guarantee**: Skill improvement NEVER bypasses the admission pipeline. The improved skill is a NEW `SkillPackageManifest` with a new `package_hash` and new `admission_hash`. The old skill remains in the registry (epoch-chained). This preserves audit trail and enables rollback.

### 3.3 Memory Nudge System

**Design Decision**: Memory nudge becomes a background `MemoryNudgeSystem` that periodically scans recent sessions, extracts candidate facts via LLM summarization, validates through PAV watchdog, and commits to `CogniFold`.

```rust
// core/rust/src/memory/nudge.rs (NEW)

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MemoryCandidate {
    pub content: String,
    pub content_hash: [u8; 32],
    pub relevance_score: f32,
    pub source_session_id: u128,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MemoryNudge {
    pub nudge_id: u128,
    pub session_id: u128,
    pub candidates: Vec<MemoryCandidate>,
    pub candidate_list_hash: [u8; 32],
    pub nudge_hash: [u8; 32],
    pub timestamp: u64,
}

pub struct MemoryNudgeSystem {
    cognifold: CogniFoldStore,
    learning_ledger: LearningLedger,
    nudge_threshold: f32,  // default: 0.7
    pav_watchdog: PhysicalWatchdog,
}

impl MemoryNudgeSystem {
    pub fn periodic_nudge(
        &mut self,
        session_id: u128,
    ) -> Result<MemoryNudge, MemoryError> {
        // 1. Extract candidate memories from session transcript
        let raw_candidates = self.cognifold.extract_session_memories(session_id)?;

        // 2. Score by relevance (LLM-assisted)
        let scored = self.score_candidates(raw_candidates)?;

        // 3. Filter by threshold
        let high_relevance: Vec<_> = scored
            .into_iter()
            .filter(|c| c.relevance_score >= self.nudge_threshold)
            .collect();

        // 4. Build nudge with hash
        let nudge = MemoryNudge::new(session_id, &high_relevance)?;

        // 5. Append to learning ledger
        self.learning_ledger.append(LearningEvent {
            event_type: LearningEventType::MemoryPersisted {
                memory_hash: nudge.nudge_hash,
            },
            skill_id: None,
            content_hash: nudge.nudge_hash,
            timestamp: nudge.timestamp,
            session_id,
            previous_event_hash: self.learning_ledger.ledger_hash,
            event_hash: [0; 32],
        });

        Ok(nudge)
    }

    pub fn commit_nudged_memories(
        &mut self,
        nudge: &MemoryNudge,
    ) -> Result<Vec<ToolMemoryCommitProof>, MemoryError> {
        let mut proofs = Vec::new();

        for candidate in &nudge.candidates {
            // PAV watchdog validation
            let artifact = PhysicalArtifact::from_bytes(candidate.content.as_bytes());
            if self.pav_watchdog.accepts(&artifact).is_err() {
                continue; // Skip if PAV rejects
            }

            // Commit to CogniFold
            let proof = self.cognifold.commit_memory(
                candidate.content.clone(),
                MemoryOrigin::Nudge(nudge.nudge_id),
            )?;
            proofs.push(proof);
        }

        Ok(proofs)
    }
}
```

**Key guarantee**: Memory commits pass PAV watchdog. No byte enters CogniFold without `PhysicalWatchdog.accepts()`. Nudge itself is recorded in `LearningLedger` for audit.

### 3.4 Cross-Session Recall

**Design Decision**: Build a `SessionSearchIndex` that wraps AEGIS's existing `HotLexicalIndex` (the same index used for evidence retrieval) and enforces `CandidateOnlyGate`.

```rust
// core/rust/src/memory/session_search.rs (NEW)

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionDocument {
    pub session_id: u128,
    pub content_hash: [u8; 32],
    pub timestamp: u64,
}

pub struct SessionSearchIndex {
    documents: BTreeMap<u128, SessionDocument>,
    inverted_index: HotLexicalIndex,
    indexed_count: u64,
}

impl SessionSearchIndex {
    pub fn index_session(
        &mut self,
        session_id: u128,
        content: &str,
    ) -> [u8; 32] {
        let content_hash = blake3_domain_hash(b"aegis-session-document-v1", content.as_bytes());
        let doc = SessionDocument { session_id, content_hash, timestamp: now() };

        // Use HotLexicalIndex (same engine as evidence retrieval)
        self.inverted_index.insert_document(session_id, content);
        self.documents.insert(session_id, doc);
        self.indexed_count += 1;

        content_hash
    }

    pub fn search(
        &self,
        query: &str,
        top_k: usize,
    ) -> Vec<CandidateEvidenceRef> {
        let query_hash = blake3_domain_hash(b"aegis-session-query-v1", query.as_bytes());
        let hits = self.inverted_index.search(query, top_k);

        hits.into_iter().map(|hit| {
            CandidateEvidenceRef::new(
                hit.doc_id,
                EvidenceCandidateTier::ColdVectorExpansion,
                hit.score,
                self.documents[&hit.doc_id].content_hash,
                query_hash,
            )
        }).collect()
    }
}
```

**Integration with ContextGovernor**:

```rust
impl ContextGovernor {
    pub fn inject_session_recall(
        &self,
        active_task_id: ContextNodeId,
        query: &str,
        top_k: usize,
        session_index: &SessionSearchIndex,
    ) -> Result<(ContextPack, ContextPackCandidateProof), ContextGovernorError> {
        // Search returns CandidateEvidenceRefs
        let candidates = session_index.search(query, top_k);

        // Build context pack WITH CandidateOnlyGate enforcement
        // (candidates are validated against replay records)
        self.build_candidate_bound_context_pack(
            active_task_id,
            &candidates,
            None, // cold_vector_record
        )
    }
}
```

**Key guarantee**: Session search results are `CandidateEvidenceRef`s, NOT truth. `CandidateOnlyGate` ensures they can only be used as context pack candidates, never as committed facts. The `ContextPackCandidateProof` chains the candidate list hash with the context pack digest.

### 3.5 User Model Building

**Design Decision**: User model stored in `CogniFold` as a memory segment with `TrustLevel`-gated writes.

```rust
// core/rust/src/memory/user_model.rs (NEW)

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct UserModel {
    pub user_id: u128,
    pub preferences: BTreeMap<String, String>,
    pub interaction_patterns: Vec<InteractionPattern>,
    pub model_hash: [u8; 32],
    pub last_updated: u64,
    pub trust_level_snapshot: TrustLevel,
}

impl UserModelStore {
    pub fn update_user_model(
        &mut self,
        user_id: u128,
        interaction: Interaction,
        trust_level: TrustLevel,
        learning_ledger: &mut LearningLedger,
    ) -> Result<UserModel, UserModelError> {
        // Trust level gate
        match trust_level {
            TrustLevel::Dev => { /* auto-update */ }
            TrustLevel::Staging => {
                // Requires review flag
                return Err(UserModelError::ReviewRequired);
            }
            TrustLevel::Prod => {
                // Requires explicit approval
                return Err(UserModelError::ApprovalRequired);
            }
        }

        // Extract pattern
        let pattern = InteractionPattern::from_interaction(&interaction);

        // Update model
        let mut model = self.get_or_create(user_id);
        model.interaction_patterns.push(pattern);
        model.last_updated = now();
        model.model_hash = model.compute_hash();

        // Commit to CogniFold (with PAV)
        let proof = self.cognifold.commit_object(
            &model,
            MemoryOrigin::UserModel(user_id),
        )?;

        // Append to learning ledger
        learning_ledger.append(LearningEvent {
            event_type: LearningEventType::UserModelUpdated {
                model_hash: model.model_hash,
            },
            content_hash: model.model_hash,
            timestamp: model.last_updated,
            session_id: current_session_id(),
            previous_event_hash: learning_ledger.ledger_hash,
            event_hash: [0; 32],
            skill_id: None,
        });

        Ok(model)
    }
}
```

**Key guarantee**: Trust level enforcement is non-negotiable. DEV auto-learns, STAGING flags for review, PROD requires explicit approval. User model commits go through PAV watchdog.

---

## 4. Module-by-Module Upgrade Plan

| Priority | Module | Action | New/Modify | Effort |
|----------|--------|--------|-----------|--------|
| P0 | `skill_registry.rs` | Add `improve_skill_from_usage()` | Modify | 8h |
| P0 | `memory/nudge.rs` | New MemoryNudgeSystem | NEW | 6h |
| P1 | `memory/session_search.rs` | New SessionSearchIndex | NEW | 4h |
| P1 | `memory/user_model.rs` | New UserModelStore | NEW | 4h |
| P2 | `learning/mod.rs` | New LearningLedger + LearningEvent | NEW | 6h |
| P2 | `context.rs` | Add `inject_session_recall()` | Modify | 3h |

### 4.1 skill_registry.rs Enhancement

**Add after line 567 (end of `impl SkillRegistry`)**: Methods `improve_skill_from_usage`, `extract_improvement_candidates`, `generate_improved_skill_md`, `compile_skill_to_wasm`, `build_improved_manifest`.

**Add new types**: `SkillUsageStats`, `SkillImprovementCandidate`, `SkillImprovementRecord`.

**Add new error variant**: `SkillAdmissionError::InsufficientUsage`.

### 4.2 memory/nudge.rs (NEW MODULE)

Creates `core/rust/src/memory/nudge.rs` with `MemoryCandidate`, `MemoryNudge`, `MemoryNudgeSystem`. Registered in `memory/mod.rs`.

### 4.3 memory/session_search.rs (NEW MODULE)

Creates `core/rust/src/memory/session_search.rs` with `SessionDocument`, `SessionSearchIndex`. Uses existing `HotLexicalIndex`. Registered in `memory/mod.rs`.

### 4.4 memory/user_model.rs (NEW MODULE)

Creates `core/rust/src/memory/user_model.rs` with `UserModel`, `InteractionPattern`, `UserModelStore`. Registered in `memory/mod.rs`.

### 4.5 learning/mod.rs (NEW MODULE)

Creates `core/rust/src/learning/mod.rs` with `LearningEventType`, `LearningEvent`, `LearningLedger`. Registered in `lib.rs` as `pub mod learning;`.

### 4.6 context.rs Enhancement

Add method `inject_session_recall` to `ContextGovernor` that takes a `SessionSearchIndex` and returns `(ContextPack, ContextPackCandidateProof)`.

---

## 5. Performance & Security Validation

### 5.1 Performance Budget

| Operation | Target | Hot path? |
|-----------|--------|-----------|
| `improve_skill_from_usage` | < 500ms (LLM call) | NO — background |
| `periodic_nudge` | < 50ms (LLM call async) | NO — background |
| `session_search` | < 5ms | NO |
| `update_user_model` | < 10ms | NO |
| `LearningLedger.append` | < 2µs | NO |
| BLAKE3 hot path | < 5µs | YES — UNCHANGED |

**None of the learning operations touch the hot path.** The hot path is BLAKE3 hashing during evidence commit — learning events are appended asynchronously.

### 5.2 Security Gates

| Gate | Enforcement |
|------|-------------|
| Skill improvement → regression tests | `SkillAdmissionRecord::from_wasmtime_and_regressions` checks `failed_count == 0` |
| Skill improvement → PAV watchdog | `watchdog.accepts(&artifact)` before admission |
| Memory nudge → PAV watchdog | Each candidate checked before CogniFold commit |
| Session search → CandidateOnlyGate | Results are `CandidateEvidenceRef`s, validated with replay records |
| User model → TrustLevel | DEV auto, STAGING review flag, PROD error |
| Learning ledger → BLAKE3 chain | Hash chain verified on read |

### 5.3 Integration Verification

```
cargo test -p aegis-nerve --lib  # Existing 80+ tests
cargo test -p aegis-nerve --tests # Integration tests
cargo bench --bench learning_ops  # NEW: learning benchmarks
```

---

## 6. Comparison Matrix

| Feature | Hermes | AEGIS (Before) | AEGIS (After) |
|---------|--------|---------------|---------------|
| Skill creation | LLM-driven, SKILL.md | Manual, WASM + BLAKE3 | Autonomous + Crypto-audited |
| Skill improvement | LLM patches in-session | None | LLM + Regression + PAV |
| Memory persistence | LLM decides, declarative | Manual CogniFold writes | Periodic nudge + PAV-validated |
| Session search | SQLite FTS5 | None | HotLexicalIndex + CandidateOnlyGate |
| User model | Honcho dialectic | None | TrustLevel-gated |
| Replay determinism | Not applicable | BLAKE3 chains, event sourcing | PRESERVED |
| Cryptographic integrity | Not applicable | Every artifact hashed | EXTENDED to learning events |
| Trust model | Agentic trust | DEV/STAGING/PROD | EXTENDED to learning ops |

---

## 7. Implementation Roadmap

### Sprint 1: Core Learning Ledger (Days 1-3)
- [ ] Create `core/rust/src/learning/mod.rs` — `LearningLedger`
- [ ] Add `learning` module to `lib.rs`
- [ ] Wire `LearningLedger` into `SkillRegistry` and `CogniFoldStore`
- [ ] Tests: hash chain integrity, event append, verify

### Sprint 2: Skill Self-Improvement (Days 4-5)
- [ ] Add `SkillUsageStats`, `SkillImprovementRecord` to `skill_registry.rs`
- [ ] Implement `improve_skill_from_usage()` with full admission pipeline
- [ ] Integration test: create → use → improve → verify BLAKE3 chain

### Sprint 3: Memory Nudge + Session Search (Days 6-8)
- [ ] Create `memory/nudge.rs` — `MemoryNudgeSystem`
- [ ] Create `memory/session_search.rs` — `SessionSearchIndex`
- [ ] Wire `inject_session_recall()` into `ContextGovernor`
- [ ] Tests: PAV enforcement, CandidateOnlyGate enforcement

### Sprint 4: User Model + Validation (Days 9-10)
- [ ] Create `memory/user_model.rs` — `UserModelStore`
- [ ] Implement TrustLevel gating
- [ ] Full-system integration test: skill creation → improvement → memory nudge → session recall
- [ ] Benchmark: verify hot path < 5µs
- [ ] Security audit: regression enforcement, PAV enforcement, CandidateOnlyGate enforcement

---

## 8. Appendices

### A: Key Architecture Patterns Preserved

1. **BLAKE3 Domain Hasher**: All new hashes use `domain_hasher(b"aegis-<component>-v<N>")` — the same pattern as every existing hash function in `skill_registry.rs`.
2. **Hash Chain**: `LearningLedger` uses the same chain pattern as `SkillRegistry` epoch commits: `chain_hash = hash(previous || event)`.
3. **CandidateOnlyGate**: Session recall uses the EXACT SAME gate as evidence retrieval — candidates cannot be promoted to truth.
4. **PAV Watchdog**: Memory nudge and skill improvement both pass through `PhysicalWatchdog.accepts()`.
5. **TrustLevel**: Already exists in `hot_engine.rs` as `Dev`/`Staging`/`Prod`. Reused verbatim.

### B: Hermes Skill Document (Loaded for Analysis)

The `hermes-agent` skill (version 2.1.0, 600+ lines) was loaded via `skill_view(name='hermes-agent')`. Key sections analyzed: Skill self-improvement mechanism, Memory nudge and persistence (agent persona rules), FTS5 session search (SQLite), Honcho user modeling, Curator background process. Full skill content available at skill path: `autonomous-ai-agents/hermes-agent/SKILL.md`.

### C: AEGIS Module Map

```
core/rust/src/
├── skill_registry.rs    (1109 lines) — Skill admission, activation, execution, BLAKE3 chains
├── memory/
│   ├── fold.rs          (869 lines)  — GenerationalSlab, RuntimeLayoutBudget, 64-byte alignment
│   ├── frame.rs         — MemoryFrame, MemoryGraph, SemanticNode
│   ├── fidelity.rs      — Reinforcement logic
│   ├── pool.rs          — Memory pooling
│   └── mod.rs
├── context.rs           (1249 lines) — ContextGovernor, CandidateOnlyGate, context packs
├── replay.rs            (8413 lines) — ReplayLedger, SegmentedArrowAuditStream, event sourcing
├── policy.rs            (1518 lines) — PolicyKernel, TypedToolIR, RiskClass, EvidenceContract
├── hot_engine.rs        (697 lines)  — TrustLevel, InMemoryEvidenceArena, ShadowSealer
├── evidence_index.rs    (3519 lines) — CandidateOnlyGate, HotLexicalIndex, CandidateEvidenceRef
├── physical.rs          — PhysicalWatchdog, PhysicalArtifact, BacktrackSignal
├── sandbox.rs           — Wasmtime sandbox, SandboxBackendKind
├── tool_gateway.rs      — ToolExecutionGateway, ToolExecutionReceipt
└── lib.rs               (47 lines)   — Module registry
```

### D: BLAKE3 Domain Hashes Used in AEGIS

All existing domain hashes follow the pattern `aegis-<component>-v<N>`:
- `aegis-skill-package-v1`
- `aegis-skill-regression-case-v1`
- `aegis-skill-regression-suite-v1`
- `aegis-skill-regression-report-v1`
- `aegis-skill-pav-acceptance-v1`
- `aegis-skill-admission-record-v1`
- `aegis-skill-registry-commit-v1`
- `aegis-skill-execution-proof-v1`
- `aegis-skill-execution-handoff-v1`

New domain hashes follow the same convention:
- `aegis-learning-event-v1`
- `aegis-skill-improvement-record-v1`
- `aegis-memory-nudge-v1`
- `aegis-session-document-v1`
- `aegis-session-query-v1`
- `aegis-user-model-v1`