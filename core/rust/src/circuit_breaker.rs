use crate::llm::LLMResponse;
use blake3::Hasher;

pub const DEFAULT_LLM_LOOP_MAX_IDENTICAL_RESPONSES: u8 = 3;
pub const DEFAULT_LLM_LOOP_WINDOW_EVENTS: usize = 8;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LlmLoopCircuitBreakerConfig {
    pub max_identical_responses: u8,
    pub window_events: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CircuitBreakerDecisionKind {
    Allowed,
    Tripped,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CircuitBreakerDecision {
    pub kind: CircuitBreakerDecisionKind,
    pub session_id: u128,
    pub request_id: u128,
    pub response_fingerprint_hash: [u8; 32],
    pub repeated_response_count: u8,
    pub evidence_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LlmLoopObservation {
    session_id: u128,
    response_fingerprint_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LlmLoopCircuitBreaker {
    config: LlmLoopCircuitBreakerConfig,
    observations: Vec<LlmLoopObservation>,
}

impl LlmLoopCircuitBreakerConfig {
    pub fn bounded(
        max_identical_responses: u8,
        window_events: usize,
    ) -> Result<Self, &'static str> {
        let config = Self {
            max_identical_responses,
            window_events,
        };
        if config.is_valid() {
            Ok(config)
        } else {
            Err("invalid llm loop circuit breaker config")
        }
    }

    pub fn is_valid(&self) -> bool {
        self.max_identical_responses >= 2
            && self.window_events >= self.max_identical_responses as usize
            && self.window_events <= 256
    }
}

impl Default for LlmLoopCircuitBreakerConfig {
    fn default() -> Self {
        Self {
            max_identical_responses: DEFAULT_LLM_LOOP_MAX_IDENTICAL_RESPONSES,
            window_events: DEFAULT_LLM_LOOP_WINDOW_EVENTS,
        }
    }
}

impl CircuitBreakerDecision {
    pub fn is_tripped(&self) -> bool {
        self.kind == CircuitBreakerDecisionKind::Tripped
    }
}

impl LlmLoopCircuitBreaker {
    pub fn new(config: LlmLoopCircuitBreakerConfig) -> Result<Self, &'static str> {
        if !config.is_valid() {
            return Err("invalid llm loop circuit breaker config");
        }
        Ok(Self {
            config,
            observations: Vec::with_capacity(config.window_events),
        })
    }

    pub fn observe_response(
        &mut self,
        response: &LLMResponse,
    ) -> Result<CircuitBreakerDecision, &'static str> {
        if !response.is_valid() {
            return Err("invalid llm response for circuit breaker");
        }
        let response_fingerprint_hash = llm_response_fingerprint_hash(response);
        let repeated_response_count =
            self.repeated_count(response.session_id, response_fingerprint_hash);
        self.observations.push(LlmLoopObservation {
            session_id: response.session_id,
            response_fingerprint_hash,
        });
        if self.observations.len() > self.config.window_events {
            let overflow = self.observations.len() - self.config.window_events;
            self.observations.drain(0..overflow);
        }

        let kind = if repeated_response_count >= self.config.max_identical_responses {
            CircuitBreakerDecisionKind::Tripped
        } else {
            CircuitBreakerDecisionKind::Allowed
        };
        Ok(CircuitBreakerDecision {
            kind,
            session_id: response.session_id,
            request_id: response.request_id,
            response_fingerprint_hash,
            repeated_response_count,
            evidence_hash: circuit_breaker_decision_hash(
                response.session_id,
                response.request_id,
                response_fingerprint_hash,
                repeated_response_count,
                self.config.max_identical_responses,
                self.config.window_events,
                kind,
            ),
        })
    }

    pub fn window_len(&self) -> usize {
        self.observations.len()
    }

    fn repeated_count(&self, session_id: u128, response_fingerprint_hash: [u8; 32]) -> u8 {
        let prior_count = self
            .observations
            .iter()
            .filter(|observation| {
                observation.session_id == session_id
                    && observation.response_fingerprint_hash == response_fingerprint_hash
            })
            .count();
        prior_count.saturating_add(1).min(u8::MAX as usize) as u8
    }
}

impl Default for LlmLoopCircuitBreaker {
    fn default() -> Self {
        Self::new(LlmLoopCircuitBreakerConfig::default())
            .expect("default llm loop circuit breaker config is valid")
    }
}

pub fn llm_response_fingerprint_hash(response: &LLMResponse) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-llm-loop-response-fingerprint-v1");
    hasher.update(response.content.trim().as_bytes());
    hasher.update(&[u8::from(response.rejected)]);
    if let Some(error) = response.error {
        hasher.update(error.as_bytes());
    }
    *hasher.finalize().as_bytes()
}

pub fn circuit_breaker_decision_hash(
    session_id: u128,
    request_id: u128,
    response_fingerprint_hash: [u8; 32],
    repeated_response_count: u8,
    max_identical_responses: u8,
    window_events: usize,
    kind: CircuitBreakerDecisionKind,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-llm-loop-circuit-breaker-decision-v1");
    hasher.update(&session_id.to_le_bytes());
    hasher.update(&request_id.to_le_bytes());
    hasher.update(&response_fingerprint_hash);
    hasher.update(&[repeated_response_count, max_identical_responses]);
    hasher.update(&(window_events as u64).to_le_bytes());
    hasher.update(&[match kind {
        CircuitBreakerDecisionKind::Allowed => 0,
        CircuitBreakerDecisionKind::Tripped => 1,
    }]);
    *hasher.finalize().as_bytes()
}
