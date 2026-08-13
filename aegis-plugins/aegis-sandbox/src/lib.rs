use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::time::Duration;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum SandboxError {
    #[error("code violates sandbox policy: {0}")]
    Policy(String),
    #[error("invalid state key")]
    InvalidStateKey,
    #[error("state io failed: {0}")]
    StateIo(String),
    #[error("serialization failed: {0}")]
    Serde(String),
    #[error("execution backend failed: {0}")]
    Backend(String),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ResourceLimits {
    pub timeout_ms: u64,
    pub memory_bytes: u64,
    pub max_stdout_bytes: usize,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SandboxPolicy {
    pub allowed_imports: BTreeSet<String>,
    pub blocked_tokens: BTreeSet<String>,
    pub limits: ResourceLimits,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionRequest {
    pub code: String,
    pub entrypoint: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionReport {
    pub accepted: bool,
    pub stdout: String,
    pub stderr: String,
    pub report_hash: [u8; 32],
}

pub trait SandboxBackend {
    fn execute(
        &self,
        request: &ExecutionRequest,
        policy: &SandboxPolicy,
        state_root: &Path,
    ) -> Result<ExecutionReport, SandboxError>;
}

#[derive(Clone, Debug)]
pub struct PolicyOnlyBackend;

#[derive(Clone, Debug)]
pub struct SandboxRuntime<B> {
    policy: SandboxPolicy,
    state: SandboxStateStore,
    backend: B,
}

#[derive(Clone, Debug)]
pub struct SandboxStateStore {
    root: PathBuf,
}

impl Default for ResourceLimits {
    fn default() -> Self {
        Self {
            timeout_ms: 5_000,
            memory_bytes: 256 * 1024 * 1024,
            max_stdout_bytes: 256 * 1024,
        }
    }
}

impl Default for SandboxPolicy {
    fn default() -> Self {
        Self {
            allowed_imports: [
                "aegis_search_sdk",
                "aegis_browser",
                "json",
                "math",
                "statistics",
                "datetime",
                "re",
            ]
            .into_iter()
            .map(str::to_string)
            .collect(),
            blocked_tokens: [
                "__import__",
                "subprocess",
                "socket",
                "os.",
                "sys.",
                "open(",
                "eval(",
                "exec(",
                "compile(",
                "ctypes",
                "multiprocessing",
            ]
            .into_iter()
            .map(str::to_string)
            .collect(),
            limits: ResourceLimits::default(),
        }
    }
}

impl SandboxPolicy {
    pub fn validate_code(&self, code: &str) -> Result<(), SandboxError> {
        if code.trim().is_empty() {
            return Err(SandboxError::Policy("empty code".to_string()));
        }
        for token in &self.blocked_tokens {
            if code.contains(token) {
                return Err(SandboxError::Policy(format!("blocked token `{token}`")));
            }
        }
        for line in code.lines() {
            let trimmed = line.trim();
            if let Some(rest) = trimmed.strip_prefix("import ") {
                for import in rest.split(',').map(str::trim) {
                    let module = import.split_whitespace().next().unwrap_or_default();
                    self.validate_import(module)?;
                }
            }
            if let Some(rest) = trimmed.strip_prefix("from ") {
                let module = rest.split_whitespace().next().unwrap_or_default();
                self.validate_import(module)?;
            }
        }
        Ok(())
    }

    fn validate_import(&self, module: &str) -> Result<(), SandboxError> {
        let root = module.split('.').next().unwrap_or_default();
        if root.is_empty() || !self.allowed_imports.contains(root) {
            return Err(SandboxError::Policy(format!(
                "import `{module}` not allowed"
            )));
        }
        Ok(())
    }
}

impl<B: SandboxBackend> SandboxRuntime<B> {
    pub fn new(policy: SandboxPolicy, state: SandboxStateStore, backend: B) -> Self {
        Self {
            policy,
            state,
            backend,
        }
    }

    pub fn execute(&self, request: ExecutionRequest) -> Result<ExecutionReport, SandboxError> {
        self.policy.validate_code(&request.code)?;
        self.backend
            .execute(&request, &self.policy, self.state.root())
    }

    pub fn state(&self) -> &SandboxStateStore {
        &self.state
    }
}

impl SandboxBackend for PolicyOnlyBackend {
    fn execute(
        &self,
        request: &ExecutionRequest,
        policy: &SandboxPolicy,
        state_root: &Path,
    ) -> Result<ExecutionReport, SandboxError> {
        policy.validate_code(&request.code)?;
        let stdout = format!(
            "policy_validated=true timeout_ms={} state_root={}",
            policy.limits.timeout_ms,
            state_root.display()
        );
        Ok(ExecutionReport::new(true, stdout, String::new()))
    }
}

impl ExecutionReport {
    pub fn new(accepted: bool, stdout: String, stderr: String) -> Self {
        let mut report = Self {
            accepted,
            stdout,
            stderr,
            report_hash: [0; 32],
        };
        report.report_hash = report.compute_hash();
        report
    }

    pub fn compute_hash(&self) -> [u8; 32] {
        let mut hasher = blake3::Hasher::new();
        hasher.update(b"aegis-sandbox-execution-report-v1");
        hasher.update(&[u8::from(self.accepted)]);
        hasher.update(self.stdout.as_bytes());
        hasher.update(self.stderr.as_bytes());
        *hasher.finalize().as_bytes()
    }

    pub fn is_valid(&self) -> bool {
        self.report_hash == self.compute_hash() && self.report_hash.iter().any(|byte| *byte != 0)
    }
}

impl SandboxStateStore {
    pub fn new(root: impl Into<PathBuf>) -> Result<Self, SandboxError> {
        let root = root.into();
        fs::create_dir_all(&root).map_err(|error| SandboxError::StateIo(error.to_string()))?;
        Ok(Self { root })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn save_state<T: Serialize>(&self, key: &str, value: &T) -> Result<(), SandboxError> {
        let path = self.path_for_key(key)?;
        let payload = serde_json::to_vec_pretty(value)
            .map_err(|error| SandboxError::Serde(error.to_string()))?;
        fs::write(path, payload).map_err(|error| SandboxError::StateIo(error.to_string()))
    }

    pub fn load_state<T: for<'de> Deserialize<'de>>(&self, key: &str) -> Result<T, SandboxError> {
        let path = self.path_for_key(key)?;
        let payload = fs::read(path).map_err(|error| SandboxError::StateIo(error.to_string()))?;
        serde_json::from_slice(&payload).map_err(|error| SandboxError::Serde(error.to_string()))
    }

    pub fn list_states(&self) -> Result<Vec<String>, SandboxError> {
        let mut keys = Vec::new();
        for entry in
            fs::read_dir(&self.root).map_err(|error| SandboxError::StateIo(error.to_string()))?
        {
            let entry = entry.map_err(|error| SandboxError::StateIo(error.to_string()))?;
            if entry.path().extension().is_some_and(|ext| ext == "json") {
                if let Some(stem) = entry.path().file_stem().and_then(|stem| stem.to_str()) {
                    keys.push(stem.to_string());
                }
            }
        }
        keys.sort();
        Ok(keys)
    }

    pub fn cleanup_older_than(&self, _age: Duration) -> Result<usize, SandboxError> {
        Ok(0)
    }

    fn path_for_key(&self, key: &str) -> Result<PathBuf, SandboxError> {
        if key.is_empty()
            || key.contains(['/', '\\'])
            || Path::new(key)
                .components()
                .any(|component| !matches!(component, Component::Normal(_)))
        {
            return Err(SandboxError::InvalidStateKey);
        }
        Ok(self.root.join(format!("{key}.json")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    #[test]
    fn policy_rejects_blocked_imports_and_tokens() {
        let policy = SandboxPolicy::default();
        assert!(policy.validate_code("import json\nx = 1").is_ok());
        assert!(policy.validate_code("import os").is_err());
        assert!(policy.validate_code("open('secret')").is_err());
    }

    #[test]
    fn state_store_persists_json_without_path_escape() {
        let dir = tempfile::tempdir().unwrap();
        let store = SandboxStateStore::new(dir.path()).unwrap();
        let mut value = BTreeMap::new();
        value.insert("count", 3);
        store.save_state("turn_state", &value).unwrap();
        let loaded: BTreeMap<String, i32> = store.load_state("turn_state").unwrap();
        assert_eq!(loaded.get("count"), Some(&3));
        assert_eq!(store.list_states().unwrap(), vec!["turn_state".to_string()]);
        assert!(store.save_state("../escape", &value).is_err());
    }

    #[test]
    fn runtime_returns_hash_bound_policy_report() {
        let dir = tempfile::tempdir().unwrap();
        let runtime = SandboxRuntime::new(
            SandboxPolicy::default(),
            SandboxStateStore::new(dir.path()).unwrap(),
            PolicyOnlyBackend,
        );
        let report = runtime
            .execute(ExecutionRequest {
                code: "import json\nresult = {'ok': True}".to_string(),
                entrypoint: None,
            })
            .unwrap();
        assert!(report.accepted);
        assert!(report.is_valid());
    }
}
