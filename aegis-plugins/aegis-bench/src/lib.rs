use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum BenchError {
    #[error("metric missing: {0}")]
    MissingMetric(String),
    #[error("performance gate failed: {0}")]
    GateFailed(String),
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BenchMetric {
    pub name: String,
    pub estimate_ns: f64,
    pub threshold_ns: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BenchReport {
    pub suite_name: String,
    pub metrics: Vec<BenchMetric>,
}

#[derive(Clone, Debug, Default)]
pub struct PerformanceGates {
    thresholds_ns: BTreeMap<String, f64>,
}

impl BenchMetric {
    pub fn passes(&self) -> bool {
        self.estimate_ns <= self.threshold_ns
    }
}

impl PerformanceGates {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_gate(mut self, name: impl Into<String>, threshold_ns: f64) -> Self {
        self.thresholds_ns.insert(name.into(), threshold_ns);
        self
    }

    pub fn plugin_defaults() -> Self {
        Self::new()
            .with_gate("search_single_operation_p95", 50_000_000.0)
            .with_gate("browser_dom_interaction_p95", 100_000_000.0)
            .with_gate("sandbox_startup_p95", 100_000_000.0)
            .with_gate("evidence_binding_verify_p95", 1_000_000.0)
            .with_gate("skill_injection_p95", 5_000_000.0)
    }

    pub fn evaluate(
        &self,
        measurements: &BTreeMap<String, f64>,
    ) -> Result<BenchReport, BenchError> {
        let mut metrics = Vec::new();
        for (name, threshold_ns) in &self.thresholds_ns {
            let estimate_ns = measurements
                .get(name)
                .copied()
                .ok_or_else(|| BenchError::MissingMetric(name.clone()))?;
            let metric = BenchMetric {
                name: name.clone(),
                estimate_ns,
                threshold_ns: *threshold_ns,
            };
            if !metric.passes() {
                return Err(BenchError::GateFailed(name.clone()));
            }
            metrics.push(metric);
        }
        Ok(BenchReport {
            suite_name: "AEGIS Plugin Gates".to_string(),
            metrics,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_gates_pass_with_bounded_measurements() {
        let gates = PerformanceGates::plugin_defaults();
        let measurements = BTreeMap::from([
            ("search_single_operation_p95".to_string(), 10_000_000.0),
            ("browser_dom_interaction_p95".to_string(), 10_000_000.0),
            ("sandbox_startup_p95".to_string(), 10_000_000.0),
            ("evidence_binding_verify_p95".to_string(), 500_000.0),
            ("skill_injection_p95".to_string(), 1_000_000.0),
        ]);
        let report = gates.evaluate(&measurements).unwrap();
        assert_eq!(report.metrics.len(), 5);
    }
}
