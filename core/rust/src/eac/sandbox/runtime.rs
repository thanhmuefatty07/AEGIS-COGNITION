//! EaC Sandbox Runtime — Code-First Orchestration & Filesystem Serde.
//!
//! Graduated from `pocs/code_orchestration_poc`. Implements:
//! - Secure execution sandbox for generated search/retrieval code.
//! - `FilesystemSerde` API for atomic state persistence with BLAKE3-hashed
//!   turn state tracking.
//! - Path traversal and command injection guards.
//!
//! Integrates with [`EventBus`] to emit `SandboxExecution`, `StatePersisted`,
//! `StateLoaded`, and `SecurityViolation` events.

use crate::eac::events::{self, EventBus};
use blake3::Hasher;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use std::path::Path;

// ─────────────────────────────────────────────────────────────────────────────
// Skill Blueprint DSL
// ─────────────────────────────────────────────────────────────────────────────

/// Commands that can be executed within the sandbox.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub enum Command {
    Search { query: String, limit: usize },
    Filter { regex: String },
    Extract { css_selector: String },
}

/// A compiled skill program composed of ordered commands.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct SkillBlueprint {
    pub name: String,
    pub steps: Vec<Command>,
}

/// Cryptographic proof for a single execution step.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct StepProof {
    pub step_index: usize,
    pub cmd_hash: [u8; 32],
    pub output_hash: [u8; 32],
}

/// Complete execution trace for deterministic replay verification.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ProgramTrace {
    pub program_hash: [u8; 32],
    pub steps: Vec<StepProof>,
    pub aggregated_trace_hash: [u8; 32],
}

// ─────────────────────────────────────────────────────────────────────────────
// Security Guard
// ─────────────────────────────────────────────────────────────────────────────

/// Blocked patterns for path traversal and command injection prevention.
const BLOCKED_PATTERNS: &[&str] = &[
    "/etc/passwd",
    "..",
    "c:\\windows",
    "rm -rf",
    "sh ",
    "bash ",
    "cmd /c",
    "powershell",
    "del /f",
    "%systemroot%",
    "\\\\",
    "; rm",
    "| rm",
    "&& rm",
    "$(", // command substitution
];

/// Scan a command parameter for blocked patterns.
///
/// Returns `Err` with a description if a security violation is detected.
pub fn security_scan(input: &str, event_bus: Option<&EventBus>) -> Result<(), String> {
    let lowered = input.to_lowercase();
    for pattern in BLOCKED_PATTERNS {
        if lowered.contains(pattern) {
            if let Some(bus) = event_bus {
                events::emit_security_violation(bus, "BlockedPattern", pattern);
            }
            return Err(format!(
                "Security Violation: Blocked pattern '{}' detected in input.",
                pattern
            ));
        }
    }
    Ok(())
}

/// Scan all parameters within a Command.
fn security_scan_command(cmd: &Command, event_bus: Option<&EventBus>) -> Result<(), String> {
    let pattern = match cmd {
        Command::Search { query, .. } => query,
        Command::Filter { regex } => regex,
        Command::Extract { css_selector } => css_selector,
    };
    security_scan(pattern, event_bus)
}

// ─────────────────────────────────────────────────────────────────────────────
// Sandbox Runtime
// ─────────────────────────────────────────────────────────────────────────────

/// Fuel cost per command execution step.
const FUEL_PER_STEP: u32 = 10;

/// Secure sandbox for executing SkillBlueprint programs.
///
/// The runtime maintains a mock data store for testing. In production, the
/// data store would be backed by the actual evidence index.
pub struct SandboxRuntime {
    fuel_limit: u32,
    data_store: HashMap<String, Vec<String>>,
}

impl SandboxRuntime {
    /// Create a new sandbox with the given fuel limit and default test data.
    pub fn new(fuel_limit: u32) -> Self {
        let mut data_store = HashMap::new();
        data_store.insert(
            "CVE-2026".to_string(),
            vec![
                "CVE-2026-1001: Remote code execution in Aegis sandbox".to_string(),
                "CVE-2026-1002: Buffer overflow in Speculative decoder".to_string(),
                "CVE-2026-2001: Privilege escalation in QuickJS host binding".to_string(),
            ],
        );
        Self {
            fuel_limit,
            data_store,
        }
    }

    /// Create a sandbox with custom data store.
    pub fn with_data(fuel_limit: u32, data_store: HashMap<String, Vec<String>>) -> Self {
        Self {
            fuel_limit,
            data_store,
        }
    }

    /// Execute a SkillBlueprint program within the sandbox.
    ///
    /// Returns the final output data and a cryptographic execution trace.
    /// Emits a `SandboxExecution` event to the EventBus on success, or
    /// a `SecurityViolation` event on blocked input.
    pub fn execute(
        &self,
        blueprint: &SkillBlueprint,
        event_bus: Option<&EventBus>,
    ) -> Result<(Vec<String>, ProgramTrace), String> {
        let mut fuel_remaining = self.fuel_limit;
        let mut current_data: Vec<String> = Vec::new();
        let mut step_proofs = Vec::new();

        // Hash the entire program for the trace root.
        let blueprint_bytes = serde_json::to_vec(blueprint).unwrap_or_default();
        let program_hash = *Hasher::new().update(&blueprint_bytes).finalize().as_bytes();

        for (idx, cmd) in blueprint.steps.iter().enumerate() {
            // Fuel gate
            if fuel_remaining < FUEL_PER_STEP {
                return Err("Out of fuel in Sandbox Runtime".to_string());
            }
            fuel_remaining -= FUEL_PER_STEP;

            // Security gate
            security_scan_command(cmd, event_bus)?;

            // Execute command
            let output = match cmd {
                Command::Search { query, limit } => {
                    let mut found = Vec::new();
                    for (k, v) in &self.data_store {
                        if k.contains(query) || query.contains(k) {
                            found.extend(v.clone());
                        }
                    }
                    found.truncate(*limit);
                    found
                }
                Command::Filter { regex } => current_data
                    .iter()
                    .filter(|item| item.contains(regex))
                    .cloned()
                    .collect(),
                Command::Extract { css_selector } => current_data
                    .iter()
                    .map(|item| format!("[{}] {}", css_selector, item))
                    .collect(),
            };

            current_data = output;

            // Hash command + output for the step proof.
            let cmd_bytes = serde_json::to_vec(cmd).unwrap_or_default();
            let cmd_hash = *Hasher::new().update(&cmd_bytes).finalize().as_bytes();

            let out_bytes = serde_json::to_vec(&current_data).unwrap_or_default();
            let output_hash = *Hasher::new().update(&out_bytes).finalize().as_bytes();

            step_proofs.push(StepProof {
                step_index: idx,
                cmd_hash,
                output_hash,
            });
        }

        // Aggregate trace hash: chain program_hash → step hashes.
        let mut trace_hasher = Hasher::new();
        trace_hasher.update(&program_hash);
        for proof in &step_proofs {
            trace_hasher.update(&proof.cmd_hash);
            trace_hasher.update(&proof.output_hash);
        }
        let aggregated_trace_hash = *trace_hasher.finalize().as_bytes();

        let fuel_consumed = self.fuel_limit - fuel_remaining;

        // Emit event
        if let Some(bus) = event_bus {
            events::emit_sandbox_execution(
                bus,
                blueprint.name.clone(),
                program_hash,
                step_proofs.len(),
                fuel_consumed,
                aggregated_trace_hash,
            );
        }

        Ok((
            current_data,
            ProgramTrace {
                program_hash,
                steps: step_proofs,
                aggregated_trace_hash,
            },
        ))
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Filesystem Serde
// ─────────────────────────────────────────────────────────────────────────────

/// Turn state for filesystem-based persistence.
///
/// Replaces in-memory REPL state with atomic filesystem writes and
/// BLAKE3-hashed turn state tracking.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct TurnState {
    /// Monotonically increasing turn index.
    pub turn_index: u64,
    /// The namespace (evidence_binding_hash hex) for this turn.
    pub namespace: String,
    /// Serialized key-value state data. Uses BTreeMap for deterministic
    /// serialization order to ensure BLAKE3 hash stability.
    pub data: BTreeMap<String, serde_json::Value>,
    /// BLAKE3 hash of the serialized state (hex).
    pub state_hash: String,
    /// Hash of the previous turn's state for chain integrity.
    pub prev_state_hash: Option<String>,
}

/// Filesystem-backed state serialization with atomic writes and BLAKE3
/// chain integrity.
#[derive(Debug)]
pub struct FilesystemSerde {
    base_dir: String,
}

impl FilesystemSerde {
    pub fn new(base_dir: &str) -> Result<Self, String> {
        // Security: block path traversal in the base directory itself.
        security_scan(base_dir, None)?;

        std::fs::create_dir_all(base_dir)
            .map_err(|e| format!("Failed to create state directory: {}", e))?;

        Ok(Self {
            base_dir: base_dir.to_string(),
        })
    }

    /// Build the file path for a given turn index.
    fn turn_path(&self, turn_index: u64) -> std::path::PathBuf {
        Path::new(&self.base_dir).join(format!("turn_{:08}.json", turn_index))
    }

    /// Persist state to disk using atomic write (write-to-temp + rename).
    ///
    /// Computes a BLAKE3 hash of the serialized data and stores it in the
    /// `TurnState` for chain integrity verification.
    pub fn persist(
        &self,
        turn_index: u64,
        namespace: &str,
        data: BTreeMap<String, serde_json::Value>,
        prev_state_hash: Option<String>,
        event_bus: Option<&EventBus>,
    ) -> Result<TurnState, String> {
        // Compute hash of the data payload.
        let data_bytes =
            serde_json::to_vec(&data).map_err(|e| format!("Serialization failed: {}", e))?;
        let state_hash_bytes = *Hasher::new().update(&data_bytes).finalize().as_bytes();
        let state_hash = blake3::Hash::from(state_hash_bytes).to_hex().to_string();

        let turn_state = TurnState {
            turn_index,
            namespace: namespace.to_string(),
            data,
            state_hash: state_hash.clone(),
            prev_state_hash,
        };

        let serialized = serde_json::to_string_pretty(&turn_state)
            .map_err(|e| format!("Serialization failed: {}", e))?;

        let target_path = self.turn_path(turn_index);
        let temp_path = target_path.with_extension("json.tmp");

        // Atomic write: temp file → rename.
        std::fs::write(&temp_path, &serialized)
            .map_err(|e| format!("Failed to write temp file: {}", e))?;

        // On Windows, rename can fail if target exists. Handle gracefully.
        if std::fs::rename(&temp_path, &target_path).is_err() {
            let _ = std::fs::remove_file(&target_path);
            if std::fs::rename(&temp_path, &target_path).is_err() {
                std::fs::write(&target_path, &serialized)
                    .map_err(|e| format!("Failed to write state file: {}", e))?;
            }
        }

        let bytes_written = serialized.len();

        if let Some(bus) = event_bus {
            events::emit_state_persisted(
                bus,
                state_hash_bytes,
                turn_index,
                target_path.to_string_lossy().to_string(),
                bytes_written,
            );
        }

        Ok(turn_state)
    }

    /// Load state from disk for a given turn index.
    pub fn load(&self, turn_index: u64, event_bus: Option<&EventBus>) -> Result<TurnState, String> {
        let path = self.turn_path(turn_index);
        let contents = std::fs::read_to_string(&path)
            .map_err(|e| format!("Failed to read state file: {}", e))?;

        let turn_state: TurnState = serde_json::from_str(&contents)
            .map_err(|e| format!("Deserialization failed: {}", e))?;

        // Verify hash integrity.
        let data_bytes = serde_json::to_vec(&turn_state.data)
            .map_err(|e| format!("Re-serialization failed: {}", e))?;
        let computed_hash = blake3::hash(&data_bytes).to_hex().to_string();

        if computed_hash != turn_state.state_hash {
            return Err(format!(
                "State hash mismatch: expected {}, computed {}",
                turn_state.state_hash, computed_hash
            ));
        }

        if let Some(bus) = event_bus {
            let hash_bytes: [u8; 32] = *blake3::hash(&data_bytes).as_bytes();
            events::emit_state_loaded(
                bus,
                hash_bytes,
                turn_index,
                path.to_string_lossy().to_string(),
            );
        }

        Ok(turn_state)
    }

    /// Verify chain integrity across a range of turns.
    pub fn verify_chain(&self, from_turn: u64, to_turn: u64) -> Result<(), String> {
        if from_turn > to_turn {
            return Err("Invalid turn range".to_string());
        }

        let mut prev_hash: Option<String> = None;
        for idx in from_turn..=to_turn {
            let state = self.load(idx, None)?;

            if state.prev_state_hash != prev_hash {
                return Err(format!(
                    "Chain break at turn {}: expected prev_hash={:?}, got {:?}",
                    idx, prev_hash, state.prev_state_hash
                ));
            }

            prev_hash = Some(state.state_hash);
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── Sandbox Tests ────────────────────────────────────────────────────

    #[test]
    fn test_valid_program_execution() {
        let runtime = SandboxRuntime::new(100);
        let bus = EventBus::new();
        let blueprint = SkillBlueprint {
            name: "cve_lookup".to_string(),
            steps: vec![
                Command::Search {
                    query: "CVE-2026".to_string(),
                    limit: 5,
                },
                Command::Filter {
                    regex: "Aegis".to_string(),
                },
                Command::Extract {
                    css_selector: "div.vuln".to_string(),
                },
            ],
        };

        let result = runtime.execute(&blueprint, Some(&bus));
        assert!(result.is_ok());
        let (output, trace) = result.unwrap();

        assert_eq!(output.len(), 1);
        assert_eq!(
            output[0],
            "[div.vuln] CVE-2026-1001: Remote code execution in Aegis sandbox"
        );
        assert_eq!(trace.steps.len(), 3);
        assert!(trace.aggregated_trace_hash != [0; 32]);

        // Verify event was emitted
        let events = bus.drain();
        assert_eq!(events.len(), 1);
        match &events[0] {
            crate::eac::events::EacEvent::SandboxExecution {
                program_name,
                steps_executed,
                fuel_consumed,
                ..
            } => {
                assert_eq!(program_name, "cve_lookup");
                assert_eq!(*steps_executed, 3);
                assert_eq!(*fuel_consumed, 30);
            }
            other => panic!("Expected SandboxExecution, got {:?}", other),
        }
    }

    #[test]
    fn test_security_violation_path_traversal() {
        let runtime = SandboxRuntime::new(100);
        let bus = EventBus::new();
        let blueprint = SkillBlueprint {
            name: "malicious".to_string(),
            steps: vec![Command::Search {
                query: "../../../etc/passwd".to_string(),
                limit: 5,
            }],
        };

        let result = runtime.execute(&blueprint, Some(&bus));
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Security Violation"));

        // Verify security event was emitted
        let events = bus.drain();
        assert_eq!(events.len(), 1);
        match &events[0] {
            crate::eac::events::EacEvent::SecurityViolation {
                violation_kind,
                // blocked_pattern removed in hardening Phase 1A (2026-06-15) — unused.
                ..
            } => {
                // The input may match multiple blocked patterns; just verify
                // that the violation was recorded.
                assert_eq!(violation_kind, "BlockedPattern");
            }
            other => panic!("Expected SecurityViolation, got {:?}", other),
        }
    }

    #[test]
    fn test_security_violation_command_injection() {
        let runtime = SandboxRuntime::new(100);
        let blueprint = SkillBlueprint {
            name: "injection".to_string(),
            steps: vec![Command::Filter {
                regex: "rm -rf /".to_string(),
            }],
        };

        let result = runtime.execute(&blueprint, None);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Security Violation"));
    }

    #[test]
    fn test_out_of_fuel() {
        let runtime = SandboxRuntime::new(15); // 15 fuel, 2 commands × 10 = 20 needed
        let blueprint = SkillBlueprint {
            name: "insufficient_fuel".to_string(),
            steps: vec![
                Command::Search {
                    query: "CVE-2026".to_string(),
                    limit: 5,
                },
                Command::Filter {
                    regex: "Aegis".to_string(),
                },
            ],
        };

        let result = runtime.execute(&blueprint, None);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Out of fuel"));
    }

    #[test]
    fn test_deterministic_trace_hash() {
        let runtime = SandboxRuntime::new(100);
        let blueprint = SkillBlueprint {
            name: "deterministic".to_string(),
            steps: vec![Command::Search {
                query: "CVE-2026".to_string(),
                limit: 2,
            }],
        };

        let (_, trace1) = runtime.execute(&blueprint, None).unwrap();
        let (_, trace2) = runtime.execute(&blueprint, None).unwrap();

        assert_eq!(trace1.program_hash, trace2.program_hash);
        assert_eq!(trace1.aggregated_trace_hash, trace2.aggregated_trace_hash);
    }

    // ── FilesystemSerde Tests ────────────────────────────────────────────

    #[test]
    fn test_persist_and_load_state() {
        let temp_dir = std::env::temp_dir().join("aegis_fs_serde_test_1");
        let _ = std::fs::remove_dir_all(&temp_dir);

        let serde = FilesystemSerde::new(temp_dir.to_str().unwrap()).unwrap();
        let bus = EventBus::new();

        let mut data = BTreeMap::new();
        data.insert("key1".to_string(), serde_json::json!("value1"));
        data.insert("key2".to_string(), serde_json::json!(42));

        let saved = serde
            .persist(0, "ns1", data.clone(), None, Some(&bus))
            .unwrap();

        assert_eq!(saved.turn_index, 0);
        assert_eq!(saved.namespace, "ns1");
        assert!(saved.prev_state_hash.is_none());

        let loaded = serde.load(0, Some(&bus)).unwrap();
        assert_eq!(loaded.state_hash, saved.state_hash);
        assert_eq!(loaded.data, data);

        // Verify events
        let events = bus.drain();
        assert_eq!(events.len(), 2); // persist + load

        // Cleanup
        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_chain_integrity() {
        let temp_dir = std::env::temp_dir().join("aegis_fs_serde_test_2");
        let _ = std::fs::remove_dir_all(&temp_dir);

        let serde = FilesystemSerde::new(temp_dir.to_str().unwrap()).unwrap();

        let mut data0 = BTreeMap::new();
        data0.insert("turn".to_string(), serde_json::json!(0));
        let turn0 = serde.persist(0, "ns", data0, None, None).unwrap();

        let mut data1 = BTreeMap::new();
        data1.insert("turn".to_string(), serde_json::json!(1));
        let _turn1 = serde
            .persist(1, "ns", data1, Some(turn0.state_hash), None)
            .unwrap();

        // Chain should be valid
        assert!(serde.verify_chain(0, 1).is_ok());

        // Cleanup
        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_filesystem_serde_blocks_traversal() {
        let result = FilesystemSerde::new("../../../etc/evil");
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Security Violation"));
    }

    #[test]
    fn test_state_hash_mismatch_detection() {
        let temp_dir = std::env::temp_dir().join("aegis_fs_serde_test_3");
        let _ = std::fs::remove_dir_all(&temp_dir);

        let serde = FilesystemSerde::new(temp_dir.to_str().unwrap()).unwrap();

        let mut data = BTreeMap::new();
        data.insert("key".to_string(), serde_json::json!("value"));
        serde.persist(0, "ns", data, None, None).unwrap();

        // Tamper with the file
        let path = serde.turn_path(0);
        let contents = std::fs::read_to_string(&path).unwrap();
        let tampered = contents.replace("\"value\"", "\"tampered\"");
        std::fs::write(&path, tampered).unwrap();

        // Load should detect mismatch
        let result = serde.load(0, None);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("hash mismatch"));

        // Cleanup
        let _ = std::fs::remove_dir_all(&temp_dir);
    }
}
