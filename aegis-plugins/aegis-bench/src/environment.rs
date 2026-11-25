use crate::BenchError;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EnvironmentFingerprintV3 {
    pub git_sha: String,
    pub worktree_clean: bool,
    pub cpu_model: String,
    pub cpu_governor: String,
    pub pinned_cores: String,
    pub logical_cpus: usize,
    pub physical_cpus: usize,
    pub ram_bytes: u64,
    pub os_kernel: String,
    pub rustc_version: String,
    pub python_version: String,
    pub allocator: String,
    pub locale: String,
    pub network_policy: String,
    pub pre_run_load_avg: f64,
    pub post_run_load_avg: f64,
    pub baseline_cpu_mhz: f64,
    pub measured_cpu_mhz: f64,
    pub fs_leak_detected: bool,
    pub sandbox_leak_detected: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ContaminationVerdict {
    Clean,
    Contaminated,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct EnvironmentCheckReport {
    pub verdict: ContaminationVerdict,
    pub cpu_frequency_drop_ratio: f64,
    pub load_threshold: f64,
    pub reasons: Vec<String>,
}

impl EnvironmentFingerprintV3 {
    pub fn evaluate_contamination(&self, enforce_clean_worktree: bool) -> EnvironmentCheckReport {
        let mut reasons = Vec::new();
        if self.physical_cpus == 0 || self.logical_cpus == 0 {
            reasons.push("invalid_cpu_topology".to_string());
        }
        let load_threshold = 0.25 * (self.physical_cpus.max(1) as f64);
        if !self.pre_run_load_avg.is_finite()
            || self.pre_run_load_avg < 0.0
            || self.pre_run_load_avg > load_threshold
        {
            reasons.push("pre_run_load_avg_exceeded".to_string());
        }
        if !self.post_run_load_avg.is_finite()
            || self.post_run_load_avg < 0.0
            || self.post_run_load_avg > load_threshold
        {
            reasons.push("post_run_load_avg_exceeded".to_string());
        }
        let freq_drop = if self.baseline_cpu_mhz > 0.0 && self.measured_cpu_mhz.is_finite() {
            ((self.baseline_cpu_mhz - self.measured_cpu_mhz) / self.baseline_cpu_mhz).max(0.0)
        } else {
            0.0
        };
        if freq_drop > 0.10 {
            reasons.push("cpu_thermal_throttling_gt_10pct".to_string());
        }
        if self.fs_leak_detected {
            reasons.push("fs_leak_detected".to_string());
        }
        if self.sandbox_leak_detected {
            reasons.push("sandbox_leak_detected".to_string());
        }
        if enforce_clean_worktree && !self.worktree_clean {
            reasons.push("dirty_worktree_in_strict_run".to_string());
        }
        for (field_name, value) in [
            ("git_sha", self.git_sha.as_str()),
            ("cpu_model", self.cpu_model.as_str()),
            ("os_kernel", self.os_kernel.as_str()),
            ("rustc_version", self.rustc_version.as_str()),
            ("allocator", self.allocator.as_str()),
            ("network_policy", self.network_policy.as_str()),
        ] {
            if value.trim().is_empty() {
                reasons.push(format!("missing_{field_name}"));
            }
        }
        EnvironmentCheckReport {
            verdict: if reasons.is_empty() {
                ContaminationVerdict::Clean
            } else {
                ContaminationVerdict::Contaminated
            },
            cpu_frequency_drop_ratio: freq_drop,
            load_threshold,
            reasons,
        }
    }

    pub fn require_clean(&self, enforce_clean_worktree: bool) -> Result<EnvironmentCheckReport, BenchError> {
        let report = self.evaluate_contamination(enforce_clean_worktree);
        if report.verdict == ContaminationVerdict::Contaminated {
            return Err(BenchError::Contaminated(report.reasons.join(",")));
        }
        Ok(report)
    }

    pub fn capture_local_best_effort(network_policy: impl Into<String>) -> Self {
        let logical_cpus = std::thread::available_parallelism()
            .map(std::num::NonZeroUsize::get)
            .unwrap_or(1);
        let physical_cpus = (logical_cpus / 2).max(1);
        let os_kernel = format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH);
        Self {
            git_sha: option_env!("AEGIS_GIT_SHA").unwrap_or("unknown").to_string(),
            worktree_clean: false,
            cpu_model: std::env::var("PROCESSOR_IDENTIFIER")
                .unwrap_or_else(|_| std::env::consts::ARCH.to_string()),
            cpu_governor: "unknown".to_string(),
            pinned_cores: "unpinned".to_string(),
            logical_cpus,
            physical_cpus,
            ram_bytes: 0,
            os_kernel,
            rustc_version: option_env!("AEGIS_RUSTC_VERSION")
                .unwrap_or("rustc-workspace")
                .to_string(),
            python_version: "unknown".to_string(),
            allocator: "system".to_string(),
            locale: std::env::var("LANG").unwrap_or_else(|_| "C".to_string()),
            network_policy: network_policy.into(),
            pre_run_load_avg: 0.0,
            post_run_load_avg: 0.0,
            baseline_cpu_mhz: 0.0,
            measured_cpu_mhz: 0.0,
            fs_leak_detected: false,
            sandbox_leak_detected: false,
        }
    }
}
