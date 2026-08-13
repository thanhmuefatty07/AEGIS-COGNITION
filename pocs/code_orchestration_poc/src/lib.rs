use blake3::Hasher;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub enum Command {
    Search { query: String, limit: usize },
    Filter { regex: String },
    Extract { css_selector: String },
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct SkillBlueprint {
    pub name: String,
    pub steps: Vec<Command>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct StepProof {
    pub step_index: usize,
    pub cmd_hash: [u8; 32],
    pub output_hash: [u8; 32],
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ProgramTrace {
    pub program_hash: [u8; 32],
    pub steps: Vec<StepProof>,
    pub aggregated_trace_hash: [u8; 32],
}

pub struct SandboxRuntime {
    fuel_limit: u32,
    mock_db: HashMap<String, Vec<String>>,
}

impl SandboxRuntime {
    pub fn new(fuel_limit: u32) -> Self {
        let mut mock_db = HashMap::new();
        mock_db.insert(
            "CVE-2026".to_string(),
            vec![
                "CVE-2026-1001: Remote code execution in Aegis sandbox".to_string(),
                "CVE-2026-1002: Buffer overflow in Speculative decoder".to_string(),
                "CVE-2026-2001: Privilege escalation in QuickJS host binding".to_string(),
            ],
        );
        Self {
            fuel_limit,
            mock_db,
        }
    }

    pub fn execute(
        &self,
        blueprint: &SkillBlueprint,
    ) -> Result<(Vec<String>, ProgramTrace), String> {
        let mut fuel_remaining = self.fuel_limit;
        let mut current_data: Vec<String> = Vec::new();
        let mut steps_proofs = Vec::new();

        let blueprint_bytes = serde_json::to_vec(blueprint).unwrap_or_default();
        let mut bp_hasher = Hasher::new();
        bp_hasher.update(&blueprint_bytes);
        let program_hash = *bp_hasher.finalize().as_bytes();

        for (idx, cmd) in blueprint.steps.iter().enumerate() {
            // Fuel consumption check
            if fuel_remaining < 10 {
                return Err("Out of fuel in Wasm Sandbox".to_string());
            }
            fuel_remaining -= 10;

            // Security injection check
            Self::security_scan_command(cmd)?;

            // Execute command
            let output = match cmd {
                Command::Search { query, limit } => {
                    let mut found = Vec::new();
                    for (k, v) in &self.mock_db {
                        if k.contains(query) || query.contains(k) {
                            found.extend(v.clone());
                        }
                    }
                    found.truncate(*limit);
                    found
                }
                Command::Filter { regex } => {
                    let mut filtered = Vec::new();
                    for item in &current_data {
                        if item.contains(regex) {
                            filtered.push(item.clone());
                        }
                    }
                    filtered
                }
                Command::Extract { css_selector } => {
                    // Simulating DOM text extraction
                    current_data
                        .iter()
                        .map(|item| format!("[{}] {}", css_selector, item))
                        .collect()
                }
            };

            current_data = output.clone();

            // Hash command inputs and outputs
            let cmd_bytes = serde_json::to_vec(cmd).unwrap_or_default();
            let mut cmd_hasher = Hasher::new();
            cmd_hasher.update(&cmd_bytes);
            let cmd_hash = *cmd_hasher.finalize().as_bytes();

            let out_bytes = serde_json::to_vec(&current_data).unwrap_or_default();
            let mut out_hasher = Hasher::new();
            out_hasher.update(&out_bytes);
            let output_hash = *out_hasher.finalize().as_bytes();

            steps_proofs.push(StepProof {
                step_index: idx,
                cmd_hash,
                output_hash,
            });
        }

        // Aggregate trace hash
        let mut trace_hasher = Hasher::new();
        trace_hasher.update(&program_hash);
        for proof in &steps_proofs {
            trace_hasher.update(&proof.cmd_hash);
            trace_hasher.update(&proof.output_hash);
        }
        let aggregated_trace_hash = *trace_hasher.finalize().as_bytes();

        Ok((
            current_data,
            ProgramTrace {
                program_hash,
                steps: steps_proofs,
                aggregated_trace_hash,
            },
        ))
    }

    fn security_scan_command(cmd: &Command) -> Result<(), String> {
        let pattern = match cmd {
            Command::Search { query, .. } => query,
            Command::Filter { regex } => regex,
            Command::Extract { css_selector } => css_selector,
        };

        // Guard against file traversal and shell injections
        let blocked = ["/etc/passwd", "..", "c:\\windows", "rm -rf", "sh ", "bash "];
        for b in blocked {
            if pattern.to_lowercase().contains(b) {
                return Err(format!(
                    "Security Violation: Blocked word '{}' detected in program parameter.",
                    b
                ));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_valid_program_execution() {
        let runtime = SandboxRuntime::new(100);
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

        let result = runtime.execute(&blueprint);
        assert!(result.is_ok());
        let (output, trace) = result.unwrap();

        assert_eq!(output.len(), 1);
        assert_eq!(
            output[0],
            "[div.vuln] CVE-2026-1001: Remote code execution in Aegis sandbox"
        );

        // Assert that the trace contains correct number of steps and a valid trace hash
        assert_eq!(trace.steps.len(), 3);
        assert!(trace.aggregated_trace_hash != [0; 32]);
    }

    #[test]
    fn test_security_violation() {
        let runtime = SandboxRuntime::new(100);
        let blueprint = SkillBlueprint {
            name: "malicious_lookup".to_string(),
            steps: vec![Command::Search {
                query: "../../../etc/passwd".to_string(),
                limit: 5,
            }],
        };

        let result = runtime.execute(&blueprint);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Security Violation"));
    }

    #[test]
    fn test_out_of_fuel() {
        // Only 15 fuel, which isn't enough for 2 commands costing 10 fuel each
        let runtime = SandboxRuntime::new(15);
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

        let result = runtime.execute(&blueprint);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("Out of fuel"));
    }
}
