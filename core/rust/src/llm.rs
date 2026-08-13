use blake3::Hasher;

#[derive(Clone, Copy)]
pub struct ProviderConfig {
    pub provider_id: &'static str,
    pub model_name: &'static str,
    pub endpoint: &'static str,
    pub timeout_ms: u64,
    pub retry_limit: u8,
    pub preferred_for_tools: bool,
    pub cost_rank: u8,
    pub availability_rank: u8,
}

#[derive(Clone)]
pub struct LLMRequest {
    pub request_id: u128,
    pub session_id: u128,
    pub prompt: String,
    pub system_context: Option<String>,
    pub model_hint: Option<&'static str>,
    pub tool_hint: Option<&'static str>,
    pub prefer_low_latency: bool,
    pub prefer_low_cost: bool,
    pub require_tool_use: bool,
    pub require_reliability: bool,
}

pub struct LLMResponse {
    pub request_id: u128,
    pub session_id: u128,
    pub content: String,
    pub token_usage: usize,
    pub latency_ms: u64,
    pub confidence: f32,
    pub rejected: bool,
    pub error: Option<&'static str>,
}

#[derive(Clone, Copy)]
pub struct RouterDecision {
    pub provider_id: &'static str,
    pub model_name: &'static str,
    pub fallback_used: bool,
}

impl RouterDecision {
    pub fn is_valid(&self) -> bool {
        !self.provider_id.is_empty() && !self.model_name.is_empty()
    }
}

#[derive(Clone, Copy)]
pub struct LLMCheckpoint {
    pub session_id: u128,
    pub request_id: u128,
    pub provider_id: &'static str,
    pub model_name: &'static str,
    pub token_usage: usize,
    pub confidence: f32,
    pub latency_ms: u64,
}

#[derive(Clone, Copy)]
pub struct InferenceBackendContract {
    pub provider_id: &'static str,
    pub model_name: &'static str,
    pub backend_version: &'static str,
    pub tokenizer_hash: [u8; 32],
    pub config_hash: [u8; 32],
    pub endpoint_class_hash: [u8; 32],
    pub context_window_tokens: u32,
    pub max_output_tokens: u32,
    pub supports_tools: bool,
    pub supports_guided_decoding: bool,
    pub supports_speculative_decoding: bool,
    pub contract_hash: [u8; 32],
}

#[derive(Clone, Copy)]
pub struct InferenceRouteProof {
    pub request_hash: [u8; 32],
    pub selected_contract_hash: [u8; 32],
    pub fallback_contract_hash: Option<[u8; 32]>,
    pub provider_ordering_hash: [u8; 32],
    pub route_policy_hash: [u8; 32],
    pub decision_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProviderBudgetError {
    InvalidProvider,
    InvalidBudget,
    InvalidRequest,
    InvalidFeedback,
    NoProviderAvailable,
    RouteMismatch,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProviderRuntimeFeedbackKind {
    Http429,
    TransportFailure,
    Success,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ProviderRuntimeFeedback {
    pub provider_id: &'static str,
    pub model_name: &'static str,
    pub feedback_kind: ProviderRuntimeFeedbackKind,
    pub observed_at_ms: u64,
    pub retry_after_ms: u64,
    pub feedback_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ProviderRuntimeBudget {
    pub provider_id: &'static str,
    pub model_name: &'static str,
    pub remaining_requests: u32,
    pub remaining_tokens: u32,
    pub reset_epoch_ms: u64,
    pub budget_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProviderBudgetLedger {
    budgets: Vec<ProviderRuntimeBudget>,
    pub ledger_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProviderRouteAdmissionProof {
    pub request_hash: [u8; 32],
    pub required_tokens: u32,
    pub selected_provider_hash: [u8; 32],
    pub throttled_provider_hashes: Vec<[u8; 32]>,
    pub admitted_provider_ordering_hash: [u8; 32],
    pub budget_ledger_hash: [u8; 32],
    pub route_policy_hash: [u8; 32],
    pub decision_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DynamicProviderFallbackProof {
    pub request_hash: [u8; 32],
    pub feedback_hash: [u8; 32],
    pub previous_ledger_hash: [u8; 32],
    pub updated_ledger_hash: [u8; 32],
    pub selected_provider_hash: [u8; 32],
    pub throttled_provider_hashes: Vec<[u8; 32]>,
    pub admission_proof_hash: [u8; 32],
    pub fallback_used: bool,
    pub downgraded_model: bool,
    pub decision_latency_ns: u64,
    pub decision_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Copy)]
pub struct InferenceResponseProof {
    pub request_hash: [u8; 32],
    pub contract_hash: [u8; 32],
    pub response_fingerprint_hash: [u8; 32],
    pub checkpoint_hash: [u8; 32],
    pub token_usage: usize,
    pub latency_ms: u64,
    pub rejected: bool,
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PrefixCacheError {
    InvalidCapacity,
    InvalidRequest,
    InvalidContract,
    InvalidRouteProof,
    InvalidScope,
    InvalidPrefix,
    CacheMiss,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PrefixCacheHandle {
    pub cache_scope_hash: [u8; 32],
    pub contract_hash: [u8; 32],
    pub tokenizer_hash: [u8; 32],
    pub config_hash: [u8; 32],
    pub endpoint_class_hash: [u8; 32],
    pub route_policy_hash: [u8; 32],
    pub prefix_hash: [u8; 32],
    pub prefix_byte_len: u32,
    pub prefix_token_count: u32,
    pub cache_key_hash: [u8; 32],
    pub handle_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PrefixCacheEntry {
    pub handle: PrefixCacheHandle,
    pub admitted_epoch: u64,
    pub last_used_epoch: u64,
    pub hit_count: u32,
    pub entry_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PrefixCachePortabilityProof {
    pub request_hash: [u8; 32],
    pub contract_hash: [u8; 32],
    pub route_proof_hash: [u8; 32],
    pub cache_key_hash: [u8; 32],
    pub handle_hash: [u8; 32],
    pub prefix_hash: [u8; 32],
    pub cache_scope_hash: [u8; 32],
    pub prefix_byte_len: u32,
    pub prefix_token_count: u32,
    pub admitted_epoch: u64,
    pub last_used_epoch: u64,
    pub hit_count: u32,
    pub proof_hash: [u8; 32],
}

pub struct PrefixCacheBroker {
    entries: Vec<PrefixCacheEntry>,
    capacity: usize,
    epoch: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StructuredOutputKind {
    String,
    Integer,
    Boolean,
    Hash32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StructuredOutputError {
    InvalidSchema,
    InvalidResponse,
    InvalidContract,
    GuidedDecodingUnsupported,
    InvalidResponseProof,
    InvalidJson,
    MissingField,
    UnexpectedField,
    TypeMismatch,
    HashMismatch,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StructuredOutputFieldSpec {
    pub name: &'static str,
    pub kind: StructuredOutputKind,
    pub required: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StructuredOutputSchema {
    pub schema_id: &'static str,
    pub version: u32,
    pub fields: &'static [StructuredOutputFieldSpec],
    pub allow_extra_fields: bool,
    pub schema_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StructuredOutputProof {
    pub request_hash: [u8; 32],
    pub contract_hash: [u8; 32],
    pub response_proof_hash: [u8; 32],
    pub schema_hash: [u8; 32],
    pub decoder_contract_hash: [u8; 32],
    pub canonical_payload_hash: [u8; 32],
    pub response_fingerprint_hash: [u8; 32],
    pub field_presence_hash: [u8; 32],
    pub guided_decoding_required: bool,
    pub truth_claim: bool,
    pub proof_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ContinuousBatchError {
    InvalidPolicy,
    InvalidRequest,
    InvalidContract,
    InvalidRouteProof,
    InvalidCandidate,
    EmptyQueue,
    TokenBudgetExceeded,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ContinuousBatchPolicy {
    pub max_batch_items: u32,
    pub max_prompt_tokens: u32,
    pub max_output_tokens: u32,
    pub max_wait_ms: u64,
    pub policy_hash: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ContinuousBatchCandidate {
    pub request_hash: [u8; 32],
    pub contract_hash: [u8; 32],
    pub route_proof_hash: [u8; 32],
    pub prompt_token_count: u32,
    pub max_output_tokens: u32,
    pub arrival_index: u64,
    pub enqueued_at_ms: u64,
    pub candidate_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContinuousBatchFairnessProof {
    pub batch_id: u128,
    pub now_ms: u64,
    pub policy_hash: [u8; 32],
    pub target_contract_hash: [u8; 32],
    pub selected_candidate_hashes: Vec<[u8; 32]>,
    pub deferred_candidate_hashes: Vec<[u8; 32]>,
    pub selected_prompt_tokens: u32,
    pub selected_output_tokens: u32,
    pub oldest_wait_ms: u64,
    pub oldest_deferred_wait_ms: u64,
    pub selected_list_hash: [u8; 32],
    pub deferred_list_hash: [u8; 32],
    pub fairness_order_hash: [u8; 32],
    pub fairness_bound_satisfied: bool,
    pub proof_hash: [u8; 32],
}

pub struct ContinuousBatchPlanner;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SpeculativeDecodeError {
    InvalidRequest,
    InvalidDraftContract,
    InvalidTargetContract,
    SpeculativeUnsupported,
    InvalidRouteProof,
    InvalidTokenBatch,
    EmptyAcceptedPrefix,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SpeculativeTokenBatch {
    pub request_hash: [u8; 32],
    pub contract_hash: [u8; 32],
    pub tokens: Vec<u32>,
    pub token_count: u32,
    pub token_hash: [u8; 32],
    pub batch_hash: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SpeculativeDecodeEquivalenceProof {
    pub request_hash: [u8; 32],
    pub route_proof_hash: [u8; 32],
    pub draft_contract_hash: [u8; 32],
    pub target_contract_hash: [u8; 32],
    pub draft_batch_hash: [u8; 32],
    pub target_batch_hash: [u8; 32],
    pub accepted_prefix_len: u32,
    pub accepted_prefix_hash: [u8; 32],
    pub rejected_suffix_hash: [u8; 32],
    pub verifier_policy_hash: [u8; 32],
    pub accepted_token_budget: u32,
    pub proof_hash: [u8; 32],
}

pub struct SpeculativeDecodeVerifier;

pub trait ProviderAdapter {
    fn provider_id(&self) -> &'static str;
    fn model_name(&self) -> &'static str;
    fn supports(&self, request: &LLMRequest) -> bool;
    fn reliability_score(&self) -> u8 {
        1
    }
    fn send(&self, request: &LLMRequest) -> Result<LLMResponse, &'static str>;
}

pub struct AdapterRegistry<'a> {
    pub adapters: Vec<&'a dyn ProviderAdapter>,
}

impl<'a> AdapterRegistry<'a> {
    pub fn new(adapters: Vec<&'a dyn ProviderAdapter>) -> Self {
        Self { adapters }
    }

    pub fn select(&self, request: &LLMRequest) -> Option<&'a dyn ProviderAdapter> {
        let mut best: Option<(&'a dyn ProviderAdapter, u8)> = None;
        for adapter in self.adapters.iter().copied() {
            if !adapter.supports(request) {
                continue;
            }
            let score = adapter.reliability_score();
            if best.is_none_or(|(_, best_score)| score > best_score) {
                best = Some((adapter, score));
            }
        }
        best.map(|(adapter, _)| adapter)
    }

    pub fn select_provider(
        &self,
        provider_id: &str,
        model_name: &str,
        request: &LLMRequest,
    ) -> Option<&'a dyn ProviderAdapter> {
        self.adapters
            .iter()
            .copied()
            .filter(|adapter| {
                adapter.provider_id() == provider_id
                    && adapter.model_name() == model_name
                    && adapter.supports(request)
            })
            .max_by_key(|adapter| adapter.reliability_score())
    }
}

impl ProviderConfig {
    pub fn is_valid(&self) -> bool {
        !self.provider_id.is_empty()
            && !self.model_name.is_empty()
            && !self.endpoint.is_empty()
            && self.timeout_ms > 0
            && self.retry_limit > 0
            && self.availability_rank > 0
    }
}

impl LLMRequest {
    pub fn is_valid(&self) -> bool {
        self.request_id > 0 && self.session_id > 0 && !self.prompt.trim().is_empty()
    }
}

impl LLMResponse {
    pub fn is_valid(&self) -> bool {
        self.request_id > 0
            && self.session_id > 0
            && (self.rejected || !self.content.trim().is_empty())
    }
}

impl LLMCheckpoint {
    pub fn is_valid(&self) -> bool {
        self.session_id > 0
            && self.request_id > 0
            && !self.provider_id.is_empty()
            && !self.model_name.is_empty()
    }
}

impl InferenceBackendContract {
    pub fn from_provider(
        provider: &ProviderConfig,
        backend_version: &'static str,
        tokenizer_hash: [u8; 32],
        config_hash: [u8; 32],
        endpoint_class_hash: [u8; 32],
        context_window_tokens: u32,
        max_output_tokens: u32,
        supports_guided_decoding: bool,
        supports_speculative_decoding: bool,
    ) -> Option<Self> {
        if !provider.is_valid()
            || backend_version.is_empty()
            || tokenizer_hash == [0; 32]
            || config_hash == [0; 32]
            || endpoint_class_hash == [0; 32]
            || context_window_tokens == 0
            || max_output_tokens == 0
        {
            return None;
        }
        let contract_hash = inference_backend_contract_hash(
            provider.provider_id,
            provider.model_name,
            backend_version,
            tokenizer_hash,
            config_hash,
            endpoint_class_hash,
            context_window_tokens,
            max_output_tokens,
            provider.preferred_for_tools,
            supports_guided_decoding,
            supports_speculative_decoding,
        );
        Some(Self {
            provider_id: provider.provider_id,
            model_name: provider.model_name,
            backend_version,
            tokenizer_hash,
            config_hash,
            endpoint_class_hash,
            context_window_tokens,
            max_output_tokens,
            supports_tools: provider.preferred_for_tools,
            supports_guided_decoding,
            supports_speculative_decoding,
            contract_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        !self.provider_id.is_empty()
            && !self.model_name.is_empty()
            && !self.backend_version.is_empty()
            && self.tokenizer_hash != [0; 32]
            && self.config_hash != [0; 32]
            && self.endpoint_class_hash != [0; 32]
            && self.context_window_tokens > 0
            && self.max_output_tokens > 0
            && self.contract_hash
                == inference_backend_contract_hash(
                    self.provider_id,
                    self.model_name,
                    self.backend_version,
                    self.tokenizer_hash,
                    self.config_hash,
                    self.endpoint_class_hash,
                    self.context_window_tokens,
                    self.max_output_tokens,
                    self.supports_tools,
                    self.supports_guided_decoding,
                    self.supports_speculative_decoding,
                )
    }

    pub fn matches_decision(&self, decision: &RouterDecision) -> bool {
        self.provider_id == decision.provider_id && self.model_name == decision.model_name
    }
}

impl InferenceRouteProof {
    pub fn build(
        request: &LLMRequest,
        providers: &[ProviderConfig],
        contracts: &[InferenceBackendContract],
    ) -> Option<Self> {
        let (primary, fallback) = route_with_fallback(request, providers)?;
        let selected = find_contract_for_decision(contracts, &primary)?;
        let fallback_contract_hash = match fallback {
            Some(fallback_decision) => {
                Some(find_contract_for_decision(contracts, &fallback_decision)?.contract_hash)
            }
            None => None,
        };
        let request_hash = llm_request_hash(request);
        let provider_ordering_hash = provider_ordering_hash(providers);
        let route_policy_hash = route_policy_hash(request);
        let decision_hash = inference_route_decision_hash(
            request_hash,
            selected.contract_hash,
            fallback_contract_hash,
            provider_ordering_hash,
            route_policy_hash,
        );
        let proof_hash = inference_route_proof_hash(
            request_hash,
            selected.contract_hash,
            fallback_contract_hash,
            provider_ordering_hash,
            route_policy_hash,
            decision_hash,
        );
        Some(Self {
            request_hash,
            selected_contract_hash: selected.contract_hash,
            fallback_contract_hash,
            provider_ordering_hash,
            route_policy_hash,
            decision_hash,
            proof_hash,
        })
    }

    pub fn is_valid(
        &self,
        request: &LLMRequest,
        providers: &[ProviderConfig],
        contracts: &[InferenceBackendContract],
    ) -> bool {
        Self::build(request, providers, contracts).is_some_and(|expected| {
            self.request_hash == expected.request_hash
                && self.selected_contract_hash == expected.selected_contract_hash
                && self.fallback_contract_hash == expected.fallback_contract_hash
                && self.provider_ordering_hash == expected.provider_ordering_hash
                && self.route_policy_hash == expected.route_policy_hash
                && self.decision_hash == expected.decision_hash
                && self.proof_hash == expected.proof_hash
        })
    }

    pub fn hashes_are_consistent(&self) -> bool {
        self.decision_hash
            == inference_route_decision_hash(
                self.request_hash,
                self.selected_contract_hash,
                self.fallback_contract_hash,
                self.provider_ordering_hash,
                self.route_policy_hash,
            )
            && self.proof_hash
                == inference_route_proof_hash(
                    self.request_hash,
                    self.selected_contract_hash,
                    self.fallback_contract_hash,
                    self.provider_ordering_hash,
                    self.route_policy_hash,
                    self.decision_hash,
                )
    }
}

impl ProviderRuntimeBudget {
    pub fn new(
        provider_id: &'static str,
        model_name: &'static str,
        remaining_requests: u32,
        remaining_tokens: u32,
        reset_epoch_ms: u64,
    ) -> Result<Self, ProviderBudgetError> {
        if provider_id.is_empty() || model_name.is_empty() {
            return Err(ProviderBudgetError::InvalidBudget);
        }
        let budget_hash = provider_runtime_budget_hash(
            provider_id,
            model_name,
            remaining_requests,
            remaining_tokens,
            reset_epoch_ms,
        );
        Ok(Self {
            provider_id,
            model_name,
            remaining_requests,
            remaining_tokens,
            reset_epoch_ms,
            budget_hash,
        })
    }

    pub fn can_serve(&self, required_tokens: u32) -> bool {
        self.remaining_requests > 0 && self.remaining_tokens >= required_tokens
    }

    pub fn matches_provider(&self, provider: &ProviderConfig) -> bool {
        self.provider_id == provider.provider_id && self.model_name == provider.model_name
    }

    pub fn is_valid(&self) -> bool {
        !self.provider_id.is_empty()
            && !self.model_name.is_empty()
            && self.budget_hash
                == provider_runtime_budget_hash(
                    self.provider_id,
                    self.model_name,
                    self.remaining_requests,
                    self.remaining_tokens,
                    self.reset_epoch_ms,
                )
    }
}

impl ProviderRuntimeFeedbackKind {
    pub fn code(self) -> u8 {
        match self {
            Self::Http429 => 1,
            Self::TransportFailure => 2,
            Self::Success => 3,
        }
    }
}

impl ProviderRuntimeFeedback {
    pub fn new(
        provider_id: &'static str,
        model_name: &'static str,
        feedback_kind: ProviderRuntimeFeedbackKind,
        observed_at_ms: u64,
        retry_after_ms: u64,
    ) -> Result<Self, ProviderBudgetError> {
        if provider_id.is_empty() || model_name.is_empty() || observed_at_ms == 0 {
            return Err(ProviderBudgetError::InvalidFeedback);
        }
        let feedback_hash = provider_runtime_feedback_hash(
            provider_id,
            model_name,
            feedback_kind,
            observed_at_ms,
            retry_after_ms,
        );
        Ok(Self {
            provider_id,
            model_name,
            feedback_kind,
            observed_at_ms,
            retry_after_ms,
            feedback_hash,
        })
    }

    pub fn matches_provider(&self, provider: &ProviderConfig) -> bool {
        self.provider_id == provider.provider_id && self.model_name == provider.model_name
    }

    pub fn is_throttle_signal(&self) -> bool {
        matches!(
            self.feedback_kind,
            ProviderRuntimeFeedbackKind::Http429 | ProviderRuntimeFeedbackKind::TransportFailure
        )
    }

    pub fn is_valid(&self) -> bool {
        !self.provider_id.is_empty()
            && !self.model_name.is_empty()
            && self.observed_at_ms > 0
            && self.feedback_hash
                == provider_runtime_feedback_hash(
                    self.provider_id,
                    self.model_name,
                    self.feedback_kind,
                    self.observed_at_ms,
                    self.retry_after_ms,
                )
    }
}

impl ProviderBudgetLedger {
    pub fn new(budgets: &[ProviderRuntimeBudget]) -> Result<Self, ProviderBudgetError> {
        if budgets.is_empty() || budgets.iter().any(|budget| !budget.is_valid()) {
            return Err(ProviderBudgetError::InvalidBudget);
        }
        let mut ordered = budgets.to_vec();
        ordered.sort_by(|left, right| {
            left.provider_id
                .cmp(right.provider_id)
                .then_with(|| left.model_name.cmp(right.model_name))
        });
        let ledger_hash = provider_budget_ledger_hash(&ordered);
        Ok(Self {
            budgets: ordered,
            ledger_hash,
        })
    }

    pub fn budgets(&self) -> &[ProviderRuntimeBudget] {
        &self.budgets
    }

    pub fn is_valid(&self) -> bool {
        !self.budgets.is_empty()
            && self.budgets.iter().all(ProviderRuntimeBudget::is_valid)
            && self.ledger_hash == provider_budget_ledger_hash(&self.budgets)
    }

    pub fn admitted_providers(
        &self,
        providers: &[ProviderConfig],
        required_tokens: u32,
    ) -> Vec<ProviderConfig> {
        providers
            .iter()
            .copied()
            .filter(|provider| {
                provider.is_valid()
                    && self.budgets.iter().any(|budget| {
                        budget.matches_provider(provider) && budget.can_serve(required_tokens)
                    })
            })
            .collect()
    }

    pub fn throttled_hashes_for(
        &self,
        providers: &[ProviderConfig],
        required_tokens: u32,
    ) -> Vec<[u8; 32]> {
        self.budgets
            .iter()
            .filter(|budget| {
                providers
                    .iter()
                    .any(|provider| provider.is_valid() && budget.matches_provider(provider))
                    && !budget.can_serve(required_tokens)
            })
            .map(|budget| budget.budget_hash)
            .collect()
    }

    pub fn apply_feedback(
        &self,
        feedback: &ProviderRuntimeFeedback,
    ) -> Result<Self, ProviderBudgetError> {
        if !self.is_valid() || !feedback.is_valid() {
            return Err(ProviderBudgetError::InvalidFeedback);
        }
        let mut matched = false;
        let updated = self
            .budgets
            .iter()
            .map(|budget| {
                if budget.provider_id == feedback.provider_id
                    && budget.model_name == feedback.model_name
                {
                    matched = true;
                    if feedback.is_throttle_signal() {
                        ProviderRuntimeBudget::new(
                            budget.provider_id,
                            budget.model_name,
                            0,
                            0,
                            feedback
                                .observed_at_ms
                                .saturating_add(feedback.retry_after_ms),
                        )
                    } else {
                        ProviderRuntimeBudget::new(
                            budget.provider_id,
                            budget.model_name,
                            budget.remaining_requests,
                            budget.remaining_tokens,
                            budget.reset_epoch_ms,
                        )
                    }
                } else {
                    ProviderRuntimeBudget::new(
                        budget.provider_id,
                        budget.model_name,
                        budget.remaining_requests,
                        budget.remaining_tokens,
                        budget.reset_epoch_ms,
                    )
                }
            })
            .collect::<Result<Vec<_>, _>>()?;
        if !matched {
            return Err(ProviderBudgetError::InvalidFeedback);
        }
        Self::new(&updated)
    }
}

impl ProviderRouteAdmissionProof {
    pub fn build(
        request: &LLMRequest,
        providers: &[ProviderConfig],
        ledger: &ProviderBudgetLedger,
        required_tokens: u32,
    ) -> Result<(RouterDecision, Self), ProviderBudgetError> {
        if !request.is_valid() || required_tokens == 0 {
            return Err(ProviderBudgetError::InvalidRequest);
        }
        if providers.is_empty() || providers.iter().any(|provider| !provider.is_valid()) {
            return Err(ProviderBudgetError::InvalidProvider);
        }
        if !ledger.is_valid() {
            return Err(ProviderBudgetError::InvalidBudget);
        }
        let admitted = ledger.admitted_providers(providers, required_tokens);
        let decision =
            route_request(request, &admitted).ok_or(ProviderBudgetError::NoProviderAvailable)?;
        let selected_budget = ledger
            .budgets()
            .iter()
            .find(|budget| {
                budget.provider_id == decision.provider_id
                    && budget.model_name == decision.model_name
            })
            .ok_or(ProviderBudgetError::RouteMismatch)?;
        let throttled_provider_hashes = ledger.throttled_hashes_for(providers, required_tokens);
        let admitted_provider_ordering_hash = provider_ordering_hash(&admitted);
        let request_hash = llm_request_hash(request);
        let route_policy_hash = route_policy_hash(request);
        let decision_hash = provider_route_admission_decision_hash(
            request_hash,
            required_tokens,
            selected_budget.budget_hash,
            &throttled_provider_hashes,
            admitted_provider_ordering_hash,
            ledger.ledger_hash,
            route_policy_hash,
        );
        let proof_hash = provider_route_admission_proof_hash(
            request_hash,
            required_tokens,
            selected_budget.budget_hash,
            &throttled_provider_hashes,
            admitted_provider_ordering_hash,
            ledger.ledger_hash,
            route_policy_hash,
            decision_hash,
        );
        let proof = Self {
            request_hash,
            required_tokens,
            selected_provider_hash: selected_budget.budget_hash,
            throttled_provider_hashes,
            admitted_provider_ordering_hash,
            budget_ledger_hash: ledger.ledger_hash,
            route_policy_hash,
            decision_hash,
            proof_hash,
        };
        Ok((decision, proof))
    }

    pub fn is_valid_for(
        &self,
        request: &LLMRequest,
        providers: &[ProviderConfig],
        ledger: &ProviderBudgetLedger,
        decision: &RouterDecision,
    ) -> bool {
        Self::build(request, providers, ledger, self.required_tokens).is_ok_and(
            |(expected_decision, expected)| {
                expected_decision.provider_id == decision.provider_id
                    && expected_decision.model_name == decision.model_name
                    && expected_decision.fallback_used == decision.fallback_used
                    && self == &expected
            },
        )
    }
}

impl DynamicProviderFallbackProof {
    pub fn build(
        request: &LLMRequest,
        providers: &[ProviderConfig],
        previous_ledger: &ProviderBudgetLedger,
        feedback: &ProviderRuntimeFeedback,
        required_tokens: u32,
        decision_latency_ns: u64,
    ) -> Result<(RouterDecision, ProviderBudgetLedger, Self), ProviderBudgetError> {
        if !request.is_valid()
            || providers.is_empty()
            || providers.iter().any(|provider| !provider.is_valid())
            || !previous_ledger.is_valid()
            || !feedback.is_valid()
            || required_tokens == 0
            || decision_latency_ns == 0
        {
            return Err(ProviderBudgetError::InvalidRequest);
        }
        if !providers
            .iter()
            .any(|provider| feedback.matches_provider(provider))
        {
            return Err(ProviderBudgetError::InvalidFeedback);
        }
        let updated_ledger = previous_ledger.apply_feedback(feedback)?;
        let (decision, admission) =
            route_request_with_budget(request, providers, &updated_ledger, required_tokens)?;
        let selected_budget = updated_ledger
            .budgets()
            .iter()
            .find(|budget| {
                budget.provider_id == decision.provider_id
                    && budget.model_name == decision.model_name
            })
            .ok_or(ProviderBudgetError::RouteMismatch)?;
        let request_hash = llm_request_hash(request);
        let fallback_used = decision.provider_id != feedback.provider_id
            || decision.model_name != feedback.model_name;
        let downgraded_model = fallback_used
            && feedback.model_name != decision.model_name
            && providers.iter().any(|provider| {
                provider.provider_id == decision.provider_id
                    && provider.model_name == decision.model_name
                    && provider.cost_rank
                        >= providers
                            .iter()
                            .find(|candidate| feedback.matches_provider(candidate))
                            .map(|candidate| candidate.cost_rank)
                            .unwrap_or(0)
            });
        let decision_hash = dynamic_provider_fallback_decision_hash(
            request_hash,
            feedback.feedback_hash,
            previous_ledger.ledger_hash,
            updated_ledger.ledger_hash,
            selected_budget.budget_hash,
            &admission.throttled_provider_hashes,
            admission.proof_hash,
            fallback_used,
            downgraded_model,
            decision_latency_ns,
        );
        let proof_hash = dynamic_provider_fallback_proof_hash(
            request_hash,
            feedback.feedback_hash,
            previous_ledger.ledger_hash,
            updated_ledger.ledger_hash,
            selected_budget.budget_hash,
            &admission.throttled_provider_hashes,
            admission.proof_hash,
            fallback_used,
            downgraded_model,
            decision_latency_ns,
            decision_hash,
        );
        let proof = Self {
            request_hash,
            feedback_hash: feedback.feedback_hash,
            previous_ledger_hash: previous_ledger.ledger_hash,
            updated_ledger_hash: updated_ledger.ledger_hash,
            selected_provider_hash: selected_budget.budget_hash,
            throttled_provider_hashes: admission.throttled_provider_hashes.clone(),
            admission_proof_hash: admission.proof_hash,
            fallback_used,
            downgraded_model,
            decision_latency_ns,
            decision_hash,
            proof_hash,
        };
        Ok((decision, updated_ledger, proof))
    }

    pub fn is_valid_for(
        &self,
        request: &LLMRequest,
        providers: &[ProviderConfig],
        previous_ledger: &ProviderBudgetLedger,
        feedback: &ProviderRuntimeFeedback,
        required_tokens: u32,
    ) -> bool {
        Self::build(
            request,
            providers,
            previous_ledger,
            feedback,
            required_tokens,
            self.decision_latency_ns,
        )
        .is_ok_and(|(_, _, expected)| self == &expected)
    }
}

impl InferenceResponseProof {
    pub fn build(
        request: &LLMRequest,
        response: &LLMResponse,
        contract: &InferenceBackendContract,
    ) -> Option<Self> {
        if !validate_response_chain(request, response) || !contract.is_valid() {
            return None;
        }
        let checkpoint = build_response_checkpoint(
            request,
            response,
            contract.provider_id,
            contract.model_name,
        )?;
        let request_hash = llm_request_hash(request);
        let checkpoint_hash = llm_checkpoint_hash(&checkpoint);
        let response_fingerprint_hash = llm_response_fingerprint_hash(response);
        let proof_hash = inference_response_proof_hash(
            request_hash,
            contract.contract_hash,
            response_fingerprint_hash,
            checkpoint_hash,
            response.token_usage,
            response.latency_ms,
            response.rejected,
        );
        Some(Self {
            request_hash,
            contract_hash: contract.contract_hash,
            response_fingerprint_hash,
            checkpoint_hash,
            token_usage: response.token_usage,
            latency_ms: response.latency_ms,
            rejected: response.rejected,
            proof_hash,
        })
    }

    pub fn is_valid(
        &self,
        request: &LLMRequest,
        response: &LLMResponse,
        contract: &InferenceBackendContract,
    ) -> bool {
        Self::build(request, response, contract).is_some_and(|expected| {
            self.request_hash == expected.request_hash
                && self.contract_hash == expected.contract_hash
                && self.response_fingerprint_hash == expected.response_fingerprint_hash
                && self.checkpoint_hash == expected.checkpoint_hash
                && self.token_usage == expected.token_usage
                && self.latency_ms == expected.latency_ms
                && self.rejected == expected.rejected
                && self.proof_hash == expected.proof_hash
        })
    }
}

impl PrefixCacheHandle {
    pub fn build(
        request: &LLMRequest,
        contract: &InferenceBackendContract,
        route_proof: &InferenceRouteProof,
        cache_scope_hash: [u8; 32],
        prefix_byte_len: u32,
        prefix_token_count: u32,
    ) -> Result<Self, PrefixCacheError> {
        validate_prefix_cache_inputs(
            request,
            contract,
            route_proof,
            cache_scope_hash,
            prefix_byte_len,
            prefix_token_count,
        )?;
        let prefix_hash =
            llm_prefix_hash(request, prefix_byte_len).ok_or(PrefixCacheError::InvalidPrefix)?;
        let cache_key_hash = prefix_cache_key_hash(
            cache_scope_hash,
            contract.contract_hash,
            contract.tokenizer_hash,
            contract.config_hash,
            contract.endpoint_class_hash,
            route_proof.route_policy_hash,
            prefix_hash,
            prefix_byte_len,
            prefix_token_count,
        );
        let handle_hash = prefix_cache_handle_hash(
            cache_scope_hash,
            contract.contract_hash,
            contract.tokenizer_hash,
            contract.config_hash,
            contract.endpoint_class_hash,
            route_proof.route_policy_hash,
            prefix_hash,
            prefix_byte_len,
            prefix_token_count,
            cache_key_hash,
        );
        Ok(Self {
            cache_scope_hash,
            contract_hash: contract.contract_hash,
            tokenizer_hash: contract.tokenizer_hash,
            config_hash: contract.config_hash,
            endpoint_class_hash: contract.endpoint_class_hash,
            route_policy_hash: route_proof.route_policy_hash,
            prefix_hash,
            prefix_byte_len,
            prefix_token_count,
            cache_key_hash,
            handle_hash,
        })
    }

    pub fn is_valid_for(
        &self,
        request: &LLMRequest,
        contract: &InferenceBackendContract,
        route_proof: &InferenceRouteProof,
        cache_scope_hash: [u8; 32],
    ) -> bool {
        Self::build(
            request,
            contract,
            route_proof,
            cache_scope_hash,
            self.prefix_byte_len,
            self.prefix_token_count,
        )
        .is_ok_and(|expected| {
            self.cache_scope_hash == expected.cache_scope_hash
                && self.contract_hash == expected.contract_hash
                && self.tokenizer_hash == expected.tokenizer_hash
                && self.config_hash == expected.config_hash
                && self.endpoint_class_hash == expected.endpoint_class_hash
                && self.route_policy_hash == expected.route_policy_hash
                && self.prefix_hash == expected.prefix_hash
                && self.cache_key_hash == expected.cache_key_hash
                && self.handle_hash == expected.handle_hash
        })
    }
}

impl PrefixCacheEntry {
    fn new(handle: PrefixCacheHandle, epoch: u64) -> Self {
        let entry_hash = prefix_cache_entry_hash(handle.handle_hash, epoch, epoch, 0);
        Self {
            handle,
            admitted_epoch: epoch,
            last_used_epoch: epoch,
            hit_count: 0,
            entry_hash,
        }
    }

    fn record_hit(&mut self, epoch: u64) {
        self.last_used_epoch = epoch;
        self.hit_count = self.hit_count.saturating_add(1);
        self.entry_hash = prefix_cache_entry_hash(
            self.handle.handle_hash,
            self.admitted_epoch,
            self.last_used_epoch,
            self.hit_count,
        );
    }

    pub fn is_valid(&self) -> bool {
        self.handle.handle_hash != [0; 32]
            && self.admitted_epoch > 0
            && self.last_used_epoch >= self.admitted_epoch
            && self.entry_hash
                == prefix_cache_entry_hash(
                    self.handle.handle_hash,
                    self.admitted_epoch,
                    self.last_used_epoch,
                    self.hit_count,
                )
    }
}

impl PrefixCachePortabilityProof {
    fn from_entry(
        request: &LLMRequest,
        contract: &InferenceBackendContract,
        route_proof: &InferenceRouteProof,
        entry: &PrefixCacheEntry,
    ) -> Option<Self> {
        if !entry.is_valid()
            || !entry.handle.is_valid_for(
                request,
                contract,
                route_proof,
                entry.handle.cache_scope_hash,
            )
        {
            return None;
        }
        let request_hash = llm_request_hash(request);
        let proof_hash = prefix_cache_portability_proof_hash(
            request_hash,
            contract.contract_hash,
            route_proof.proof_hash,
            entry.handle.cache_key_hash,
            entry.handle.handle_hash,
            entry.handle.prefix_hash,
            entry.handle.cache_scope_hash,
            entry.handle.prefix_byte_len,
            entry.handle.prefix_token_count,
            entry.admitted_epoch,
            entry.last_used_epoch,
            entry.hit_count,
        );
        Some(Self {
            request_hash,
            contract_hash: contract.contract_hash,
            route_proof_hash: route_proof.proof_hash,
            cache_key_hash: entry.handle.cache_key_hash,
            handle_hash: entry.handle.handle_hash,
            prefix_hash: entry.handle.prefix_hash,
            cache_scope_hash: entry.handle.cache_scope_hash,
            prefix_byte_len: entry.handle.prefix_byte_len,
            prefix_token_count: entry.handle.prefix_token_count,
            admitted_epoch: entry.admitted_epoch,
            last_used_epoch: entry.last_used_epoch,
            hit_count: entry.hit_count,
            proof_hash,
        })
    }

    pub fn is_valid_for(
        &self,
        request: &LLMRequest,
        contract: &InferenceBackendContract,
        route_proof: &InferenceRouteProof,
        handle: &PrefixCacheHandle,
    ) -> bool {
        handle.is_valid_for(request, contract, route_proof, self.cache_scope_hash)
            && self.request_hash == llm_request_hash(request)
            && self.contract_hash == contract.contract_hash
            && self.route_proof_hash == route_proof.proof_hash
            && self.cache_key_hash == handle.cache_key_hash
            && self.handle_hash == handle.handle_hash
            && self.prefix_hash == handle.prefix_hash
            && self.prefix_byte_len == handle.prefix_byte_len
            && self.prefix_token_count == handle.prefix_token_count
            && self.admitted_epoch > 0
            && self.last_used_epoch >= self.admitted_epoch
            && self.hit_count > 0
            && self.proof_hash
                == prefix_cache_portability_proof_hash(
                    self.request_hash,
                    self.contract_hash,
                    self.route_proof_hash,
                    self.cache_key_hash,
                    self.handle_hash,
                    self.prefix_hash,
                    self.cache_scope_hash,
                    self.prefix_byte_len,
                    self.prefix_token_count,
                    self.admitted_epoch,
                    self.last_used_epoch,
                    self.hit_count,
                )
    }
}

impl PrefixCacheBroker {
    pub fn new(capacity: usize) -> Result<Self, PrefixCacheError> {
        if capacity == 0 {
            return Err(PrefixCacheError::InvalidCapacity);
        }
        Ok(Self {
            entries: Vec::with_capacity(capacity),
            capacity,
            epoch: 0,
        })
    }

    pub fn admit(
        &mut self,
        request: &LLMRequest,
        contract: &InferenceBackendContract,
        route_proof: &InferenceRouteProof,
        cache_scope_hash: [u8; 32],
        prefix_byte_len: u32,
        prefix_token_count: u32,
    ) -> Result<PrefixCacheHandle, PrefixCacheError> {
        let handle = PrefixCacheHandle::build(
            request,
            contract,
            route_proof,
            cache_scope_hash,
            prefix_byte_len,
            prefix_token_count,
        )?;
        self.epoch = self.epoch.saturating_add(1).max(1);
        if let Some(entry) = self
            .entries
            .iter_mut()
            .find(|entry| entry.handle.cache_key_hash == handle.cache_key_hash)
        {
            entry.handle = handle;
            entry.last_used_epoch = self.epoch;
            entry.entry_hash = prefix_cache_entry_hash(
                entry.handle.handle_hash,
                entry.admitted_epoch,
                entry.last_used_epoch,
                entry.hit_count,
            );
            return Ok(entry.handle);
        }
        if self.entries.len() == self.capacity {
            self.evict_one();
        }
        self.entries.push(PrefixCacheEntry::new(handle, self.epoch));
        Ok(handle)
    }

    pub fn lookup(
        &mut self,
        request: &LLMRequest,
        contract: &InferenceBackendContract,
        route_proof: &InferenceRouteProof,
        cache_scope_hash: [u8; 32],
        prefix_byte_len: u32,
        prefix_token_count: u32,
    ) -> Result<PrefixCachePortabilityProof, PrefixCacheError> {
        let handle = PrefixCacheHandle::build(
            request,
            contract,
            route_proof,
            cache_scope_hash,
            prefix_byte_len,
            prefix_token_count,
        )?;
        let index = self
            .entries
            .iter()
            .position(|entry| entry.handle.cache_key_hash == handle.cache_key_hash)
            .ok_or(PrefixCacheError::CacheMiss)?;
        self.epoch = self.epoch.saturating_add(1).max(1);
        self.entries[index].record_hit(self.epoch);
        PrefixCachePortabilityProof::from_entry(
            request,
            contract,
            route_proof,
            &self.entries[index],
        )
        .ok_or(PrefixCacheError::InvalidRouteProof)
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn epoch(&self) -> u64 {
        self.epoch
    }

    fn evict_one(&mut self) {
        if let Some(index) = self
            .entries
            .iter()
            .enumerate()
            .min_by(|(_, left), (_, right)| {
                left.last_used_epoch
                    .cmp(&right.last_used_epoch)
                    .then_with(|| left.handle.cache_key_hash.cmp(&right.handle.cache_key_hash))
            })
            .map(|(index, _)| index)
        {
            self.entries.swap_remove(index);
        }
    }
}

impl StructuredOutputKind {
    fn code(self) -> u8 {
        match self {
            StructuredOutputKind::String => 1,
            StructuredOutputKind::Integer => 2,
            StructuredOutputKind::Boolean => 3,
            StructuredOutputKind::Hash32 => 4,
        }
    }
}

impl StructuredOutputSchema {
    pub fn new(
        schema_id: &'static str,
        version: u32,
        fields: &'static [StructuredOutputFieldSpec],
        allow_extra_fields: bool,
    ) -> Option<Self> {
        if schema_id.is_empty() || version == 0 || fields.is_empty() {
            return None;
        }
        let mut previous = "";
        for field in fields {
            if field.name.is_empty() || (!previous.is_empty() && field.name <= previous) {
                return None;
            }
            previous = field.name;
        }
        let schema_hash =
            structured_output_schema_hash(schema_id, version, fields, allow_extra_fields);
        Some(Self {
            schema_id,
            version,
            fields,
            allow_extra_fields,
            schema_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        Self::new(
            self.schema_id,
            self.version,
            self.fields,
            self.allow_extra_fields,
        )
        .is_some_and(|expected| expected.schema_hash == self.schema_hash)
    }
}

impl StructuredOutputProof {
    pub fn build(
        request: &LLMRequest,
        response: &LLMResponse,
        contract: &InferenceBackendContract,
        response_proof: &InferenceResponseProof,
        schema: &StructuredOutputSchema,
        guided_decoding_required: bool,
    ) -> Result<Self, StructuredOutputError> {
        if !request.is_valid() || !response.is_valid() {
            return Err(StructuredOutputError::InvalidResponse);
        }
        if !contract.is_valid() {
            return Err(StructuredOutputError::InvalidContract);
        }
        if guided_decoding_required && !contract.supports_guided_decoding {
            return Err(StructuredOutputError::GuidedDecodingUnsupported);
        }
        if !schema.is_valid() {
            return Err(StructuredOutputError::InvalidSchema);
        }
        if !response_proof.is_valid(request, response, contract) {
            return Err(StructuredOutputError::InvalidResponseProof);
        }
        let parsed: serde_json::Value = serde_json::from_str(response.content.trim())
            .map_err(|_| StructuredOutputError::InvalidJson)?;
        let object = parsed
            .as_object()
            .ok_or(StructuredOutputError::InvalidJson)?;
        if !schema.allow_extra_fields {
            for key in object.keys() {
                if !schema.fields.iter().any(|field| field.name == key) {
                    return Err(StructuredOutputError::UnexpectedField);
                }
            }
        }
        for field in schema.fields {
            match object.get(field.name) {
                Some(value) => validate_structured_field(field, value)?,
                None if field.required => return Err(StructuredOutputError::MissingField),
                None => {}
            }
        }
        let canonical_payload_hash = structured_output_payload_hash(schema, object)?;
        let field_presence_hash = structured_output_field_presence_hash(schema, object);
        let decoder_contract_hash = guided_decoder_contract_hash(
            contract.contract_hash,
            schema.schema_hash,
            guided_decoding_required,
        );
        let proof_hash = structured_output_proof_hash(
            response_proof.request_hash,
            contract.contract_hash,
            response_proof.proof_hash,
            schema.schema_hash,
            decoder_contract_hash,
            canonical_payload_hash,
            response_proof.response_fingerprint_hash,
            field_presence_hash,
            guided_decoding_required,
            false,
        );
        Ok(Self {
            request_hash: response_proof.request_hash,
            contract_hash: contract.contract_hash,
            response_proof_hash: response_proof.proof_hash,
            schema_hash: schema.schema_hash,
            decoder_contract_hash,
            canonical_payload_hash,
            response_fingerprint_hash: response_proof.response_fingerprint_hash,
            field_presence_hash,
            guided_decoding_required,
            truth_claim: false,
            proof_hash,
        })
    }

    pub fn is_valid_for(
        &self,
        request: &LLMRequest,
        response: &LLMResponse,
        contract: &InferenceBackendContract,
        response_proof: &InferenceResponseProof,
        schema: &StructuredOutputSchema,
    ) -> bool {
        Self::build(
            request,
            response,
            contract,
            response_proof,
            schema,
            self.guided_decoding_required,
        )
        .is_ok_and(|expected| {
            !self.truth_claim
                && self.request_hash == expected.request_hash
                && self.contract_hash == expected.contract_hash
                && self.response_proof_hash == expected.response_proof_hash
                && self.schema_hash == expected.schema_hash
                && self.decoder_contract_hash == expected.decoder_contract_hash
                && self.canonical_payload_hash == expected.canonical_payload_hash
                && self.response_fingerprint_hash == expected.response_fingerprint_hash
                && self.field_presence_hash == expected.field_presence_hash
                && self.proof_hash == expected.proof_hash
        })
    }
}

impl ContinuousBatchPolicy {
    pub fn new(
        max_batch_items: u32,
        max_prompt_tokens: u32,
        max_output_tokens: u32,
        max_wait_ms: u64,
    ) -> Option<Self> {
        if max_batch_items == 0
            || max_prompt_tokens == 0
            || max_output_tokens == 0
            || max_wait_ms == 0
        {
            return None;
        }
        let policy_hash = continuous_batch_policy_hash(
            max_batch_items,
            max_prompt_tokens,
            max_output_tokens,
            max_wait_ms,
        );
        Some(Self {
            max_batch_items,
            max_prompt_tokens,
            max_output_tokens,
            max_wait_ms,
            policy_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        Self::new(
            self.max_batch_items,
            self.max_prompt_tokens,
            self.max_output_tokens,
            self.max_wait_ms,
        )
        .is_some_and(|expected| expected.policy_hash == self.policy_hash)
    }
}

impl ContinuousBatchCandidate {
    pub fn build(
        request: &LLMRequest,
        contract: &InferenceBackendContract,
        route_proof: &InferenceRouteProof,
        prompt_token_count: u32,
        max_output_tokens: u32,
        arrival_index: u64,
        enqueued_at_ms: u64,
    ) -> Result<Self, ContinuousBatchError> {
        if !request.is_valid() {
            return Err(ContinuousBatchError::InvalidRequest);
        }
        if !contract.is_valid() {
            return Err(ContinuousBatchError::InvalidContract);
        }
        if !route_proof.hashes_are_consistent()
            || route_proof.request_hash != llm_request_hash(request)
            || route_proof.selected_contract_hash != contract.contract_hash
        {
            return Err(ContinuousBatchError::InvalidRouteProof);
        }
        if prompt_token_count == 0
            || max_output_tokens == 0
            || prompt_token_count > contract.context_window_tokens
            || max_output_tokens > contract.max_output_tokens
        {
            return Err(ContinuousBatchError::InvalidCandidate);
        }
        let request_hash = route_proof.request_hash;
        let candidate_hash = continuous_batch_candidate_hash(
            request_hash,
            contract.contract_hash,
            route_proof.proof_hash,
            prompt_token_count,
            max_output_tokens,
            arrival_index,
            enqueued_at_ms,
        );
        Ok(Self {
            request_hash,
            contract_hash: contract.contract_hash,
            route_proof_hash: route_proof.proof_hash,
            prompt_token_count,
            max_output_tokens,
            arrival_index,
            enqueued_at_ms,
            candidate_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.request_hash != [0; 32]
            && self.contract_hash != [0; 32]
            && self.route_proof_hash != [0; 32]
            && self.prompt_token_count > 0
            && self.max_output_tokens > 0
            && self.candidate_hash
                == continuous_batch_candidate_hash(
                    self.request_hash,
                    self.contract_hash,
                    self.route_proof_hash,
                    self.prompt_token_count,
                    self.max_output_tokens,
                    self.arrival_index,
                    self.enqueued_at_ms,
                )
    }
}

impl ContinuousBatchFairnessProof {
    pub fn is_valid_for(
        &self,
        batch_id: u128,
        now_ms: u64,
        policy: &ContinuousBatchPolicy,
        candidates: &[ContinuousBatchCandidate],
    ) -> bool {
        ContinuousBatchPlanner::build_proof(batch_id, now_ms, policy, candidates).is_ok_and(
            |expected| {
                self.batch_id == expected.batch_id
                    && self.now_ms == expected.now_ms
                    && self.policy_hash == expected.policy_hash
                    && self.target_contract_hash == expected.target_contract_hash
                    && self.selected_candidate_hashes == expected.selected_candidate_hashes
                    && self.deferred_candidate_hashes == expected.deferred_candidate_hashes
                    && self.selected_prompt_tokens == expected.selected_prompt_tokens
                    && self.selected_output_tokens == expected.selected_output_tokens
                    && self.oldest_wait_ms == expected.oldest_wait_ms
                    && self.oldest_deferred_wait_ms == expected.oldest_deferred_wait_ms
                    && self.selected_list_hash == expected.selected_list_hash
                    && self.deferred_list_hash == expected.deferred_list_hash
                    && self.fairness_order_hash == expected.fairness_order_hash
                    && self.fairness_bound_satisfied == expected.fairness_bound_satisfied
                    && self.proof_hash == expected.proof_hash
            },
        )
    }
}

impl ContinuousBatchPlanner {
    pub fn build_proof(
        batch_id: u128,
        now_ms: u64,
        policy: &ContinuousBatchPolicy,
        candidates: &[ContinuousBatchCandidate],
    ) -> Result<ContinuousBatchFairnessProof, ContinuousBatchError> {
        if !policy.is_valid() {
            return Err(ContinuousBatchError::InvalidPolicy);
        }
        if candidates.is_empty() {
            return Err(ContinuousBatchError::EmptyQueue);
        }
        if candidates.iter().any(|candidate| !candidate.is_valid()) {
            return Err(ContinuousBatchError::InvalidCandidate);
        }
        if candidates.iter().any(|candidate| {
            candidate.prompt_token_count > policy.max_prompt_tokens
                || candidate.max_output_tokens > policy.max_output_tokens
        }) {
            return Err(ContinuousBatchError::TokenBudgetExceeded);
        }
        let mut ordered: Vec<ContinuousBatchCandidate> = candidates.to_vec();
        ordered.sort_by(|left, right| {
            left.arrival_index
                .cmp(&right.arrival_index)
                .then_with(|| left.enqueued_at_ms.cmp(&right.enqueued_at_ms))
                .then_with(|| left.candidate_hash.cmp(&right.candidate_hash))
        });
        let oldest = ordered.first().ok_or(ContinuousBatchError::EmptyQueue)?;
        let target_contract_hash = oldest.contract_hash;
        let mut selected = Vec::new();
        let mut deferred = Vec::new();
        let mut selected_prompt_tokens = 0u32;
        let mut selected_output_tokens = 0u32;

        for candidate in ordered {
            let same_contract = candidate.contract_hash == target_contract_hash;
            let has_item_room = (selected.len() as u32) < policy.max_batch_items;
            let prompt_room = selected_prompt_tokens.saturating_add(candidate.prompt_token_count)
                <= policy.max_prompt_tokens;
            let output_room = selected_output_tokens.saturating_add(candidate.max_output_tokens)
                <= policy.max_output_tokens;
            if same_contract && has_item_room && prompt_room && output_room {
                selected_prompt_tokens =
                    selected_prompt_tokens.saturating_add(candidate.prompt_token_count);
                selected_output_tokens =
                    selected_output_tokens.saturating_add(candidate.max_output_tokens);
                selected.push(candidate);
            } else {
                deferred.push(candidate);
            }
        }

        if selected.is_empty() {
            return Err(ContinuousBatchError::EmptyQueue);
        }
        let selected_candidate_hashes: Vec<_> = selected
            .iter()
            .map(|candidate| candidate.candidate_hash)
            .collect();
        let deferred_candidate_hashes: Vec<_> = deferred
            .iter()
            .map(|candidate| candidate.candidate_hash)
            .collect();
        let selected_list_hash = continuous_batch_candidate_list_hash(&selected_candidate_hashes);
        let deferred_list_hash = continuous_batch_candidate_list_hash(&deferred_candidate_hashes);
        let oldest_wait_ms = selected
            .iter()
            .map(|candidate| now_ms.saturating_sub(candidate.enqueued_at_ms))
            .max()
            .unwrap_or(0);
        let oldest_deferred_wait_ms = deferred
            .iter()
            .map(|candidate| now_ms.saturating_sub(candidate.enqueued_at_ms))
            .max()
            .unwrap_or(0);
        let fairness_order_hash =
            continuous_batch_fairness_order_hash(target_contract_hash, &selected, &deferred);
        let fairness_bound_satisfied = oldest_wait_ms <= policy.max_wait_ms
            || oldest_deferred_wait_ms == 0
            || oldest_wait_ms >= oldest_deferred_wait_ms;
        let proof_hash = continuous_batch_fairness_proof_hash(
            batch_id,
            now_ms,
            policy.policy_hash,
            target_contract_hash,
            selected_list_hash,
            deferred_list_hash,
            fairness_order_hash,
            selected_prompt_tokens,
            selected_output_tokens,
            oldest_wait_ms,
            oldest_deferred_wait_ms,
            fairness_bound_satisfied,
        );
        Ok(ContinuousBatchFairnessProof {
            batch_id,
            now_ms,
            policy_hash: policy.policy_hash,
            target_contract_hash,
            selected_candidate_hashes,
            deferred_candidate_hashes,
            selected_prompt_tokens,
            selected_output_tokens,
            oldest_wait_ms,
            oldest_deferred_wait_ms,
            selected_list_hash,
            deferred_list_hash,
            fairness_order_hash,
            fairness_bound_satisfied,
            proof_hash,
        })
    }
}

impl SpeculativeTokenBatch {
    pub fn build(
        request: &LLMRequest,
        contract: &InferenceBackendContract,
        tokens: &[u32],
    ) -> Result<Self, SpeculativeDecodeError> {
        if !request.is_valid() {
            return Err(SpeculativeDecodeError::InvalidRequest);
        }
        if !contract.is_valid() {
            return Err(SpeculativeDecodeError::InvalidTargetContract);
        }
        if tokens.is_empty() || tokens.len() > u32::MAX as usize {
            return Err(SpeculativeDecodeError::InvalidTokenBatch);
        }
        let token_count = tokens.len() as u32;
        if token_count > contract.max_output_tokens {
            return Err(SpeculativeDecodeError::InvalidTokenBatch);
        }
        let request_hash = llm_request_hash(request);
        let token_hash = speculative_token_hash(tokens);
        let batch_hash = speculative_token_batch_hash(
            request_hash,
            contract.contract_hash,
            token_hash,
            token_count,
        );
        Ok(Self {
            request_hash,
            contract_hash: contract.contract_hash,
            tokens: tokens.to_vec(),
            token_count,
            token_hash,
            batch_hash,
        })
    }

    pub fn is_valid_for(&self, request: &LLMRequest, contract: &InferenceBackendContract) -> bool {
        Self::build(request, contract, &self.tokens).is_ok_and(|expected| {
            self.request_hash == expected.request_hash
                && self.contract_hash == expected.contract_hash
                && self.token_count == expected.token_count
                && self.token_hash == expected.token_hash
                && self.batch_hash == expected.batch_hash
        })
    }
}

impl SpeculativeDecodeEquivalenceProof {
    pub fn is_valid_for(
        &self,
        request: &LLMRequest,
        route_proof: &InferenceRouteProof,
        draft_contract: &InferenceBackendContract,
        target_contract: &InferenceBackendContract,
        draft: &SpeculativeTokenBatch,
        target: &SpeculativeTokenBatch,
    ) -> bool {
        SpeculativeDecodeVerifier::build_proof(
            request,
            route_proof,
            draft_contract,
            target_contract,
            draft,
            target,
            self.accepted_token_budget,
        )
        .is_ok_and(|expected| {
            self.request_hash == expected.request_hash
                && self.route_proof_hash == expected.route_proof_hash
                && self.draft_contract_hash == expected.draft_contract_hash
                && self.target_contract_hash == expected.target_contract_hash
                && self.draft_batch_hash == expected.draft_batch_hash
                && self.target_batch_hash == expected.target_batch_hash
                && self.accepted_prefix_len == expected.accepted_prefix_len
                && self.accepted_prefix_hash == expected.accepted_prefix_hash
                && self.rejected_suffix_hash == expected.rejected_suffix_hash
                && self.verifier_policy_hash == expected.verifier_policy_hash
                && self.accepted_token_budget == expected.accepted_token_budget
                && self.proof_hash == expected.proof_hash
        })
    }
}

impl SpeculativeDecodeVerifier {
    pub fn build_proof(
        request: &LLMRequest,
        route_proof: &InferenceRouteProof,
        draft_contract: &InferenceBackendContract,
        target_contract: &InferenceBackendContract,
        draft: &SpeculativeTokenBatch,
        target: &SpeculativeTokenBatch,
        accepted_token_budget: u32,
    ) -> Result<SpeculativeDecodeEquivalenceProof, SpeculativeDecodeError> {
        if !request.is_valid() {
            return Err(SpeculativeDecodeError::InvalidRequest);
        }
        if !draft_contract.is_valid() {
            return Err(SpeculativeDecodeError::InvalidDraftContract);
        }
        if !target_contract.is_valid() {
            return Err(SpeculativeDecodeError::InvalidTargetContract);
        }
        if !draft_contract.supports_speculative_decoding
            || !target_contract.supports_speculative_decoding
        {
            return Err(SpeculativeDecodeError::SpeculativeUnsupported);
        }
        let request_hash = llm_request_hash(request);
        if !route_proof.hashes_are_consistent()
            || route_proof.request_hash != request_hash
            || route_proof.selected_contract_hash != target_contract.contract_hash
        {
            return Err(SpeculativeDecodeError::InvalidRouteProof);
        }
        if !draft.is_valid_for(request, draft_contract)
            || !target.is_valid_for(request, target_contract)
        {
            return Err(SpeculativeDecodeError::InvalidTokenBatch);
        }
        if draft_contract.tokenizer_hash != target_contract.tokenizer_hash
            || draft_contract.config_hash != target_contract.config_hash
            || draft_contract.endpoint_class_hash != target_contract.endpoint_class_hash
        {
            return Err(SpeculativeDecodeError::InvalidDraftContract);
        }
        if accepted_token_budget == 0
            || accepted_token_budget > target_contract.max_output_tokens
            || accepted_token_budget > draft.token_count
            || accepted_token_budget > target.token_count
        {
            return Err(SpeculativeDecodeError::InvalidTokenBatch);
        }
        let accepted_prefix_len = draft
            .tokens
            .iter()
            .zip(target.tokens.iter())
            .take(accepted_token_budget as usize)
            .take_while(|(draft_token, target_token)| draft_token == target_token)
            .count();
        if accepted_prefix_len == 0 {
            return Err(SpeculativeDecodeError::EmptyAcceptedPrefix);
        }
        let accepted_prefix_len = accepted_prefix_len as u32;
        let accepted_prefix_hash = speculative_accepted_prefix_hash(
            request_hash,
            &target.tokens[..accepted_prefix_len as usize],
        );
        let rejected_suffix_hash = speculative_rejected_suffix_hash(
            request_hash,
            &draft.tokens[accepted_prefix_len as usize..],
            &target.tokens[accepted_prefix_len as usize..],
        );
        let verifier_policy_hash = speculative_verifier_policy_hash(
            draft_contract.contract_hash,
            target_contract.contract_hash,
            draft_contract.tokenizer_hash,
            draft_contract.config_hash,
            draft_contract.endpoint_class_hash,
            accepted_token_budget,
        );
        let proof_hash = speculative_decode_equivalence_proof_hash(
            request_hash,
            route_proof.proof_hash,
            draft_contract.contract_hash,
            target_contract.contract_hash,
            draft.batch_hash,
            target.batch_hash,
            accepted_prefix_len,
            accepted_prefix_hash,
            rejected_suffix_hash,
            verifier_policy_hash,
            accepted_token_budget,
        );
        Ok(SpeculativeDecodeEquivalenceProof {
            request_hash,
            route_proof_hash: route_proof.proof_hash,
            draft_contract_hash: draft_contract.contract_hash,
            target_contract_hash: target_contract.contract_hash,
            draft_batch_hash: draft.batch_hash,
            target_batch_hash: target.batch_hash,
            accepted_prefix_len,
            accepted_prefix_hash,
            rejected_suffix_hash,
            verifier_policy_hash,
            accepted_token_budget,
            proof_hash,
        })
    }
}

pub fn normalize_response(
    request: &LLMRequest,
    content: &str,
    token_usage: usize,
    latency_ms: u64,
    confidence: f32,
) -> LLMResponse {
    LLMResponse {
        request_id: request.request_id,
        session_id: request.session_id,
        content: content.trim().to_string(),
        token_usage,
        latency_ms,
        confidence: confidence.clamp(0.0, 1.0),
        rejected: false,
        error: None,
    }
}

pub fn rejected_response(request: &LLMRequest, error: &'static str) -> LLMResponse {
    LLMResponse {
        request_id: request.request_id,
        session_id: request.session_id,
        content: String::new(),
        token_usage: 0,
        latency_ms: 0,
        confidence: 0.0,
        rejected: true,
        error: Some(error),
    }
}

pub fn validate_response_chain(request: &LLMRequest, response: &LLMResponse) -> bool {
    request.is_valid()
        && response.is_valid()
        && request.request_id == response.request_id
        && request.session_id == response.session_id
}

pub fn route_request(request: &LLMRequest, providers: &[ProviderConfig]) -> Option<RouterDecision> {
    if !request.is_valid() {
        return None;
    }

    let chosen = providers
        .iter()
        .filter(|provider| provider.is_valid())
        .min_by_key(|provider| route_score(request, provider))?;
    Some(RouterDecision {
        provider_id: chosen.provider_id,
        model_name: chosen.model_name,
        fallback_used: false,
    })
}

fn route_score(request: &LLMRequest, provider: &ProviderConfig) -> u8 {
    let mut score = provider.cost_rank;
    if request.prefer_low_latency {
        score = score.saturating_add(1);
    }
    if request.prefer_low_cost {
        score = score.saturating_add(provider.cost_rank);
    }
    if request.require_tool_use && !provider.preferred_for_tools {
        score = score.saturating_add(10);
    }
    if request.require_reliability {
        score = score.saturating_add(provider.availability_rank);
    }
    if let Some(model_hint) = request.model_hint {
        if model_hint != provider.model_name {
            score = score.saturating_add(5);
        }
    }
    score
}

pub fn route_with_fallback(
    request: &LLMRequest,
    providers: &[ProviderConfig],
) -> Option<(RouterDecision, Option<RouterDecision>)> {
    let primary = route_request(request, providers)?;
    let fallback = providers
        .iter()
        .filter(|provider| provider.is_valid() && provider.provider_id != primary.provider_id)
        .min_by_key(|provider| provider.cost_rank)
        .map(|provider| RouterDecision {
            provider_id: provider.provider_id,
            model_name: provider.model_name,
            fallback_used: true,
        });
    Some((primary, fallback))
}

pub fn route_request_with_budget(
    request: &LLMRequest,
    providers: &[ProviderConfig],
    ledger: &ProviderBudgetLedger,
    required_tokens: u32,
) -> Result<(RouterDecision, ProviderRouteAdmissionProof), ProviderBudgetError> {
    ProviderRouteAdmissionProof::build(request, providers, ledger, required_tokens)
}

pub fn route_request_after_provider_feedback(
    request: &LLMRequest,
    providers: &[ProviderConfig],
    previous_ledger: &ProviderBudgetLedger,
    feedback: &ProviderRuntimeFeedback,
    required_tokens: u32,
) -> Result<
    (
        RouterDecision,
        ProviderBudgetLedger,
        DynamicProviderFallbackProof,
    ),
    ProviderBudgetError,
> {
    let started = std::time::Instant::now();
    let updated_ledger = previous_ledger.apply_feedback(feedback)?;
    let _ = route_request_with_budget(request, providers, &updated_ledger, required_tokens)?;
    let decision_latency_ns = started.elapsed().as_nanos().clamp(1, u64::MAX as u128) as u64;
    DynamicProviderFallbackProof::build(
        request,
        providers,
        previous_ledger,
        feedback,
        required_tokens,
        decision_latency_ns,
    )
}

pub fn build_response_checkpoint(
    request: &LLMRequest,
    response: &LLMResponse,
    provider_id: &'static str,
    model_name: &'static str,
) -> Option<LLMCheckpoint> {
    if !validate_response_chain(request, response) {
        return None;
    }
    checkpoint_from_response(provider_id, model_name, response)
}

pub fn session_bridge_key(request: &LLMRequest) -> (u128, u128) {
    (request.request_id, request.session_id)
}

pub fn build_llm_request(
    request_id: u128,
    session_id: u128,
    prompt: &str,
    model_hint: Option<&'static str>,
) -> LLMRequest {
    LLMRequest {
        request_id,
        session_id,
        prompt: prompt.to_string(),
        system_context: None,
        model_hint,
        tool_hint: None,
        prefer_low_latency: true,
        prefer_low_cost: false,
        require_tool_use: false,
        require_reliability: true,
    }
}

pub fn validate_provider_chain(providers: &[ProviderConfig]) -> bool {
    !providers.is_empty() && providers.iter().all(ProviderConfig::is_valid)
}

pub fn checkpoint_from_response(
    provider_id: &'static str,
    model_name: &'static str,
    response: &LLMResponse,
) -> Option<LLMCheckpoint> {
    if !response.is_valid() {
        return None;
    }
    Some(LLMCheckpoint {
        session_id: response.session_id,
        request_id: response.request_id,
        provider_id,
        model_name,
        token_usage: response.token_usage,
        confidence: response.confidence,
        latency_ms: response.latency_ms,
    })
}

pub fn llm_request_hash(request: &LLMRequest) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-llm-request-v1");
    update_u128(&mut hasher, request.request_id);
    update_u128(&mut hasher, request.session_id);
    update_bytes(&mut hasher, request.prompt.trim().as_bytes());
    update_optional_str(&mut hasher, request.system_context.as_deref());
    update_optional_str(&mut hasher, request.model_hint);
    update_optional_str(&mut hasher, request.tool_hint);
    update_bool(&mut hasher, request.prefer_low_latency);
    update_bool(&mut hasher, request.prefer_low_cost);
    update_bool(&mut hasher, request.require_tool_use);
    update_bool(&mut hasher, request.require_reliability);
    hasher.finalize().into()
}

pub fn provider_config_hash(provider: &ProviderConfig) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-provider-config-v1");
    update_str(&mut hasher, provider.provider_id);
    update_str(&mut hasher, provider.model_name);
    update_str(&mut hasher, provider.endpoint);
    update_u64(&mut hasher, provider.timeout_ms);
    hasher.update(&[provider.retry_limit]);
    update_bool(&mut hasher, provider.preferred_for_tools);
    hasher.update(&[provider.cost_rank, provider.availability_rank]);
    hasher.finalize().into()
}

pub fn provider_ordering_hash(providers: &[ProviderConfig]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-provider-ordering-v1");
    update_u64(&mut hasher, providers.len().min(u64::MAX as usize) as u64);
    for provider in providers {
        hasher.update(&provider_config_hash(provider));
    }
    hasher.finalize().into()
}

pub fn provider_runtime_budget_hash(
    provider_id: &str,
    model_name: &str,
    remaining_requests: u32,
    remaining_tokens: u32,
    reset_epoch_ms: u64,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-provider-runtime-budget-v1");
    update_str(&mut hasher, provider_id);
    update_str(&mut hasher, model_name);
    update_u32(&mut hasher, remaining_requests);
    update_u32(&mut hasher, remaining_tokens);
    update_u64(&mut hasher, reset_epoch_ms);
    hasher.finalize().into()
}

pub fn provider_runtime_feedback_hash(
    provider_id: &str,
    model_name: &str,
    feedback_kind: ProviderRuntimeFeedbackKind,
    observed_at_ms: u64,
    retry_after_ms: u64,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-provider-runtime-feedback-v1");
    update_str(&mut hasher, provider_id);
    update_str(&mut hasher, model_name);
    hasher.update(&[feedback_kind.code()]);
    update_u64(&mut hasher, observed_at_ms);
    update_u64(&mut hasher, retry_after_ms);
    hasher.finalize().into()
}

pub fn provider_budget_ledger_hash(budgets: &[ProviderRuntimeBudget]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-provider-budget-ledger-v1");
    update_u64(&mut hasher, budgets.len().min(u64::MAX as usize) as u64);
    for budget in budgets {
        hasher.update(&budget.budget_hash);
    }
    hasher.finalize().into()
}

#[allow(clippy::too_many_arguments)]
pub fn dynamic_provider_fallback_decision_hash(
    request_hash: [u8; 32],
    feedback_hash: [u8; 32],
    previous_ledger_hash: [u8; 32],
    updated_ledger_hash: [u8; 32],
    selected_provider_hash: [u8; 32],
    throttled_provider_hashes: &[[u8; 32]],
    admission_proof_hash: [u8; 32],
    fallback_used: bool,
    downgraded_model: bool,
    decision_latency_ns: u64,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-dynamic-provider-fallback-decision-v1");
    hasher.update(&request_hash);
    hasher.update(&feedback_hash);
    hasher.update(&previous_ledger_hash);
    hasher.update(&updated_ledger_hash);
    hasher.update(&selected_provider_hash);
    update_hash_slice(&mut hasher, throttled_provider_hashes);
    hasher.update(&admission_proof_hash);
    update_bool(&mut hasher, fallback_used);
    update_bool(&mut hasher, downgraded_model);
    update_u64(&mut hasher, decision_latency_ns);
    hasher.finalize().into()
}

#[allow(clippy::too_many_arguments)]
pub fn dynamic_provider_fallback_proof_hash(
    request_hash: [u8; 32],
    feedback_hash: [u8; 32],
    previous_ledger_hash: [u8; 32],
    updated_ledger_hash: [u8; 32],
    selected_provider_hash: [u8; 32],
    throttled_provider_hashes: &[[u8; 32]],
    admission_proof_hash: [u8; 32],
    fallback_used: bool,
    downgraded_model: bool,
    decision_latency_ns: u64,
    decision_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-dynamic-provider-fallback-proof-v1");
    hasher.update(&request_hash);
    hasher.update(&feedback_hash);
    hasher.update(&previous_ledger_hash);
    hasher.update(&updated_ledger_hash);
    hasher.update(&selected_provider_hash);
    update_hash_slice(&mut hasher, throttled_provider_hashes);
    hasher.update(&admission_proof_hash);
    update_bool(&mut hasher, fallback_used);
    update_bool(&mut hasher, downgraded_model);
    update_u64(&mut hasher, decision_latency_ns);
    hasher.update(&decision_hash);
    hasher.finalize().into()
}

pub fn provider_route_admission_decision_hash(
    request_hash: [u8; 32],
    required_tokens: u32,
    selected_provider_hash: [u8; 32],
    throttled_provider_hashes: &[[u8; 32]],
    admitted_provider_ordering_hash: [u8; 32],
    budget_ledger_hash: [u8; 32],
    route_policy_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-provider-route-admission-decision-v1");
    hasher.update(&request_hash);
    update_u32(&mut hasher, required_tokens);
    hasher.update(&selected_provider_hash);
    update_hash_slice(&mut hasher, throttled_provider_hashes);
    hasher.update(&admitted_provider_ordering_hash);
    hasher.update(&budget_ledger_hash);
    hasher.update(&route_policy_hash);
    hasher.finalize().into()
}

pub fn provider_route_admission_proof_hash(
    request_hash: [u8; 32],
    required_tokens: u32,
    selected_provider_hash: [u8; 32],
    throttled_provider_hashes: &[[u8; 32]],
    admitted_provider_ordering_hash: [u8; 32],
    budget_ledger_hash: [u8; 32],
    route_policy_hash: [u8; 32],
    decision_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-provider-route-admission-proof-v1");
    hasher.update(&request_hash);
    update_u32(&mut hasher, required_tokens);
    hasher.update(&selected_provider_hash);
    update_hash_slice(&mut hasher, throttled_provider_hashes);
    hasher.update(&admitted_provider_ordering_hash);
    hasher.update(&budget_ledger_hash);
    hasher.update(&route_policy_hash);
    hasher.update(&decision_hash);
    hasher.finalize().into()
}

pub fn route_policy_hash(request: &LLMRequest) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-inference-route-policy-v1");
    update_bool(&mut hasher, request.prefer_low_latency);
    update_bool(&mut hasher, request.prefer_low_cost);
    update_bool(&mut hasher, request.require_tool_use);
    update_bool(&mut hasher, request.require_reliability);
    update_optional_str(&mut hasher, request.model_hint);
    update_optional_str(&mut hasher, request.tool_hint);
    hasher.finalize().into()
}

pub fn inference_backend_contract_hash(
    provider_id: &str,
    model_name: &str,
    backend_version: &str,
    tokenizer_hash: [u8; 32],
    config_hash: [u8; 32],
    endpoint_class_hash: [u8; 32],
    context_window_tokens: u32,
    max_output_tokens: u32,
    supports_tools: bool,
    supports_guided_decoding: bool,
    supports_speculative_decoding: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-inference-backend-contract-v1");
    update_str(&mut hasher, provider_id);
    update_str(&mut hasher, model_name);
    update_str(&mut hasher, backend_version);
    hasher.update(&tokenizer_hash);
    hasher.update(&config_hash);
    hasher.update(&endpoint_class_hash);
    update_u32(&mut hasher, context_window_tokens);
    update_u32(&mut hasher, max_output_tokens);
    update_bool(&mut hasher, supports_tools);
    update_bool(&mut hasher, supports_guided_decoding);
    update_bool(&mut hasher, supports_speculative_decoding);
    hasher.finalize().into()
}

pub fn inference_route_decision_hash(
    request_hash: [u8; 32],
    selected_contract_hash: [u8; 32],
    fallback_contract_hash: Option<[u8; 32]>,
    provider_ordering_hash: [u8; 32],
    route_policy_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-inference-route-decision-v1");
    hasher.update(&request_hash);
    hasher.update(&selected_contract_hash);
    update_optional_hash(&mut hasher, fallback_contract_hash);
    hasher.update(&provider_ordering_hash);
    hasher.update(&route_policy_hash);
    hasher.finalize().into()
}

pub fn inference_route_proof_hash(
    request_hash: [u8; 32],
    selected_contract_hash: [u8; 32],
    fallback_contract_hash: Option<[u8; 32]>,
    provider_ordering_hash: [u8; 32],
    route_policy_hash: [u8; 32],
    decision_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-inference-route-proof-v1");
    hasher.update(&request_hash);
    hasher.update(&selected_contract_hash);
    update_optional_hash(&mut hasher, fallback_contract_hash);
    hasher.update(&provider_ordering_hash);
    hasher.update(&route_policy_hash);
    hasher.update(&decision_hash);
    hasher.finalize().into()
}

pub fn llm_response_fingerprint_hash(response: &LLMResponse) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-llm-response-fingerprint-v1");
    update_bytes(&mut hasher, response.content.trim().as_bytes());
    update_bool(&mut hasher, response.rejected);
    update_optional_str(&mut hasher, response.error);
    hasher.finalize().into()
}

pub fn llm_checkpoint_hash(checkpoint: &LLMCheckpoint) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-llm-checkpoint-v1");
    update_u128(&mut hasher, checkpoint.session_id);
    update_u128(&mut hasher, checkpoint.request_id);
    update_str(&mut hasher, checkpoint.provider_id);
    update_str(&mut hasher, checkpoint.model_name);
    update_u64(
        &mut hasher,
        checkpoint.token_usage.min(u64::MAX as usize) as u64,
    );
    update_u32(&mut hasher, checkpoint.confidence.to_bits());
    update_u64(&mut hasher, checkpoint.latency_ms);
    hasher.finalize().into()
}

pub fn inference_response_proof_hash(
    request_hash: [u8; 32],
    contract_hash: [u8; 32],
    response_fingerprint_hash: [u8; 32],
    checkpoint_hash: [u8; 32],
    token_usage: usize,
    latency_ms: u64,
    rejected: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-inference-response-proof-v1");
    hasher.update(&request_hash);
    hasher.update(&contract_hash);
    hasher.update(&response_fingerprint_hash);
    hasher.update(&checkpoint_hash);
    update_u64(&mut hasher, token_usage.min(u64::MAX as usize) as u64);
    update_u64(&mut hasher, latency_ms);
    update_bool(&mut hasher, rejected);
    hasher.finalize().into()
}

pub fn llm_prefix_hash(request: &LLMRequest, prefix_byte_len: u32) -> Option<[u8; 32]> {
    if !request.is_valid() {
        return None;
    }
    let prefix_len = prefix_byte_len as usize;
    let prompt = request.prompt.as_bytes();
    if prefix_len == 0 || prefix_len > prompt.len() {
        return None;
    }
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-llm-prefix-v1");
    update_u128(&mut hasher, request.session_id);
    update_bytes(&mut hasher, &prompt[..prefix_len]);
    update_optional_str(&mut hasher, request.system_context.as_deref());
    update_optional_str(&mut hasher, request.model_hint);
    update_optional_str(&mut hasher, request.tool_hint);
    Some(hasher.finalize().into())
}

pub fn prefix_cache_key_hash(
    cache_scope_hash: [u8; 32],
    contract_hash: [u8; 32],
    tokenizer_hash: [u8; 32],
    config_hash: [u8; 32],
    endpoint_class_hash: [u8; 32],
    route_policy_hash: [u8; 32],
    prefix_hash: [u8; 32],
    prefix_byte_len: u32,
    prefix_token_count: u32,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-prefix-cache-key-v1");
    hasher.update(&cache_scope_hash);
    hasher.update(&contract_hash);
    hasher.update(&tokenizer_hash);
    hasher.update(&config_hash);
    hasher.update(&endpoint_class_hash);
    hasher.update(&route_policy_hash);
    hasher.update(&prefix_hash);
    update_u32(&mut hasher, prefix_byte_len);
    update_u32(&mut hasher, prefix_token_count);
    hasher.finalize().into()
}

pub fn prefix_cache_handle_hash(
    cache_scope_hash: [u8; 32],
    contract_hash: [u8; 32],
    tokenizer_hash: [u8; 32],
    config_hash: [u8; 32],
    endpoint_class_hash: [u8; 32],
    route_policy_hash: [u8; 32],
    prefix_hash: [u8; 32],
    prefix_byte_len: u32,
    prefix_token_count: u32,
    cache_key_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-prefix-cache-handle-v1");
    hasher.update(&cache_scope_hash);
    hasher.update(&contract_hash);
    hasher.update(&tokenizer_hash);
    hasher.update(&config_hash);
    hasher.update(&endpoint_class_hash);
    hasher.update(&route_policy_hash);
    hasher.update(&prefix_hash);
    update_u32(&mut hasher, prefix_byte_len);
    update_u32(&mut hasher, prefix_token_count);
    hasher.update(&cache_key_hash);
    hasher.finalize().into()
}

pub fn prefix_cache_entry_hash(
    handle_hash: [u8; 32],
    admitted_epoch: u64,
    last_used_epoch: u64,
    hit_count: u32,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-prefix-cache-entry-v1");
    hasher.update(&handle_hash);
    update_u64(&mut hasher, admitted_epoch);
    update_u64(&mut hasher, last_used_epoch);
    update_u32(&mut hasher, hit_count);
    hasher.finalize().into()
}

pub fn prefix_cache_portability_proof_hash(
    request_hash: [u8; 32],
    contract_hash: [u8; 32],
    route_proof_hash: [u8; 32],
    cache_key_hash: [u8; 32],
    handle_hash: [u8; 32],
    prefix_hash: [u8; 32],
    cache_scope_hash: [u8; 32],
    prefix_byte_len: u32,
    prefix_token_count: u32,
    admitted_epoch: u64,
    last_used_epoch: u64,
    hit_count: u32,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-prefix-cache-portability-proof-v1");
    hasher.update(&request_hash);
    hasher.update(&contract_hash);
    hasher.update(&route_proof_hash);
    hasher.update(&cache_key_hash);
    hasher.update(&handle_hash);
    hasher.update(&prefix_hash);
    hasher.update(&cache_scope_hash);
    update_u32(&mut hasher, prefix_byte_len);
    update_u32(&mut hasher, prefix_token_count);
    update_u64(&mut hasher, admitted_epoch);
    update_u64(&mut hasher, last_used_epoch);
    update_u32(&mut hasher, hit_count);
    hasher.finalize().into()
}

pub fn structured_output_schema_hash(
    schema_id: &str,
    version: u32,
    fields: &[StructuredOutputFieldSpec],
    allow_extra_fields: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-structured-output-schema-v1");
    update_str(&mut hasher, schema_id);
    update_u32(&mut hasher, version);
    update_u64(&mut hasher, fields.len().min(u64::MAX as usize) as u64);
    for field in fields {
        update_str(&mut hasher, field.name);
        hasher.update(&[field.kind.code(), u8::from(field.required)]);
    }
    update_bool(&mut hasher, allow_extra_fields);
    hasher.finalize().into()
}

pub fn guided_decoder_contract_hash(
    contract_hash: [u8; 32],
    schema_hash: [u8; 32],
    guided_decoding_required: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-guided-decoder-contract-v1");
    hasher.update(&contract_hash);
    hasher.update(&schema_hash);
    update_bool(&mut hasher, guided_decoding_required);
    hasher.finalize().into()
}

pub fn structured_output_field_presence_hash(
    schema: &StructuredOutputSchema,
    object: &serde_json::Map<String, serde_json::Value>,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-structured-output-field-presence-v1");
    hasher.update(&schema.schema_hash);
    for field in schema.fields {
        hasher.update(&[u8::from(object.contains_key(field.name))]);
    }
    hasher.finalize().into()
}

pub fn structured_output_payload_hash(
    schema: &StructuredOutputSchema,
    object: &serde_json::Map<String, serde_json::Value>,
) -> Result<[u8; 32], StructuredOutputError> {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-structured-output-payload-v1");
    hasher.update(&schema.schema_hash);
    for field in schema.fields {
        if let Some(value) = object.get(field.name) {
            update_str(&mut hasher, field.name);
            hasher.update(&[field.kind.code()]);
            update_canonical_json_value(&mut hasher, field.kind, value)?;
        } else {
            update_str(&mut hasher, field.name);
            hasher.update(&[0]);
        }
    }
    if schema.allow_extra_fields {
        let mut extras: Vec<_> = object
            .iter()
            .filter(|(key, _)| !schema.fields.iter().any(|field| field.name == key.as_str()))
            .collect();
        extras.sort_by(|(left, _), (right, _)| left.cmp(right));
        for (key, value) in extras {
            update_str(&mut hasher, key);
            update_bytes(&mut hasher, value.to_string().as_bytes());
        }
    }
    Ok(hasher.finalize().into())
}

pub fn structured_output_proof_hash(
    request_hash: [u8; 32],
    contract_hash: [u8; 32],
    response_proof_hash: [u8; 32],
    schema_hash: [u8; 32],
    decoder_contract_hash: [u8; 32],
    canonical_payload_hash: [u8; 32],
    response_fingerprint_hash: [u8; 32],
    field_presence_hash: [u8; 32],
    guided_decoding_required: bool,
    truth_claim: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-structured-output-proof-v1");
    hasher.update(&request_hash);
    hasher.update(&contract_hash);
    hasher.update(&response_proof_hash);
    hasher.update(&schema_hash);
    hasher.update(&decoder_contract_hash);
    hasher.update(&canonical_payload_hash);
    hasher.update(&response_fingerprint_hash);
    hasher.update(&field_presence_hash);
    update_bool(&mut hasher, guided_decoding_required);
    update_bool(&mut hasher, truth_claim);
    hasher.finalize().into()
}

pub fn continuous_batch_policy_hash(
    max_batch_items: u32,
    max_prompt_tokens: u32,
    max_output_tokens: u32,
    max_wait_ms: u64,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-continuous-batch-policy-v1");
    update_u32(&mut hasher, max_batch_items);
    update_u32(&mut hasher, max_prompt_tokens);
    update_u32(&mut hasher, max_output_tokens);
    update_u64(&mut hasher, max_wait_ms);
    hasher.finalize().into()
}

pub fn continuous_batch_candidate_hash(
    request_hash: [u8; 32],
    contract_hash: [u8; 32],
    route_proof_hash: [u8; 32],
    prompt_token_count: u32,
    max_output_tokens: u32,
    arrival_index: u64,
    enqueued_at_ms: u64,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-continuous-batch-candidate-v1");
    hasher.update(&request_hash);
    hasher.update(&contract_hash);
    hasher.update(&route_proof_hash);
    update_u32(&mut hasher, prompt_token_count);
    update_u32(&mut hasher, max_output_tokens);
    update_u64(&mut hasher, arrival_index);
    update_u64(&mut hasher, enqueued_at_ms);
    hasher.finalize().into()
}

pub fn continuous_batch_candidate_list_hash(candidate_hashes: &[[u8; 32]]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-continuous-batch-candidate-list-v1");
    update_u64(
        &mut hasher,
        candidate_hashes.len().min(u64::MAX as usize) as u64,
    );
    for candidate_hash in candidate_hashes {
        hasher.update(candidate_hash);
    }
    hasher.finalize().into()
}

pub fn continuous_batch_fairness_order_hash(
    target_contract_hash: [u8; 32],
    selected: &[ContinuousBatchCandidate],
    deferred: &[ContinuousBatchCandidate],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-continuous-batch-fairness-order-v1");
    hasher.update(&target_contract_hash);
    update_u64(&mut hasher, selected.len().min(u64::MAX as usize) as u64);
    for candidate in selected {
        hasher.update(&candidate.candidate_hash);
        update_u64(&mut hasher, candidate.arrival_index);
        update_u64(&mut hasher, candidate.enqueued_at_ms);
    }
    update_u64(&mut hasher, deferred.len().min(u64::MAX as usize) as u64);
    for candidate in deferred {
        hasher.update(&candidate.candidate_hash);
        update_u64(&mut hasher, candidate.arrival_index);
        update_u64(&mut hasher, candidate.enqueued_at_ms);
    }
    hasher.finalize().into()
}

pub fn continuous_batch_fairness_proof_hash(
    batch_id: u128,
    now_ms: u64,
    policy_hash: [u8; 32],
    target_contract_hash: [u8; 32],
    selected_list_hash: [u8; 32],
    deferred_list_hash: [u8; 32],
    fairness_order_hash: [u8; 32],
    selected_prompt_tokens: u32,
    selected_output_tokens: u32,
    oldest_wait_ms: u64,
    oldest_deferred_wait_ms: u64,
    fairness_bound_satisfied: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-continuous-batch-fairness-proof-v1");
    update_u128(&mut hasher, batch_id);
    update_u64(&mut hasher, now_ms);
    hasher.update(&policy_hash);
    hasher.update(&target_contract_hash);
    hasher.update(&selected_list_hash);
    hasher.update(&deferred_list_hash);
    hasher.update(&fairness_order_hash);
    update_u32(&mut hasher, selected_prompt_tokens);
    update_u32(&mut hasher, selected_output_tokens);
    update_u64(&mut hasher, oldest_wait_ms);
    update_u64(&mut hasher, oldest_deferred_wait_ms);
    update_bool(&mut hasher, fairness_bound_satisfied);
    hasher.finalize().into()
}

pub fn speculative_token_hash(tokens: &[u32]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-speculative-token-hash-v1");
    update_u64(&mut hasher, tokens.len().min(u64::MAX as usize) as u64);
    for token in tokens {
        update_u32(&mut hasher, *token);
    }
    hasher.finalize().into()
}

pub fn speculative_token_batch_hash(
    request_hash: [u8; 32],
    contract_hash: [u8; 32],
    token_hash: [u8; 32],
    token_count: u32,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-speculative-token-batch-v1");
    hasher.update(&request_hash);
    hasher.update(&contract_hash);
    hasher.update(&token_hash);
    update_u32(&mut hasher, token_count);
    hasher.finalize().into()
}

pub fn speculative_accepted_prefix_hash(request_hash: [u8; 32], tokens: &[u32]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-speculative-accepted-prefix-v1");
    hasher.update(&request_hash);
    hasher.update(&speculative_token_hash(tokens));
    update_u64(&mut hasher, tokens.len().min(u64::MAX as usize) as u64);
    hasher.finalize().into()
}

pub fn speculative_rejected_suffix_hash(
    request_hash: [u8; 32],
    draft_suffix: &[u32],
    target_suffix: &[u32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-speculative-rejected-suffix-v1");
    hasher.update(&request_hash);
    hasher.update(&speculative_token_hash(draft_suffix));
    hasher.update(&speculative_token_hash(target_suffix));
    update_u64(
        &mut hasher,
        draft_suffix.len().min(u64::MAX as usize) as u64,
    );
    update_u64(
        &mut hasher,
        target_suffix.len().min(u64::MAX as usize) as u64,
    );
    hasher.finalize().into()
}

pub fn speculative_verifier_policy_hash(
    draft_contract_hash: [u8; 32],
    target_contract_hash: [u8; 32],
    tokenizer_hash: [u8; 32],
    config_hash: [u8; 32],
    endpoint_class_hash: [u8; 32],
    accepted_token_budget: u32,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-speculative-verifier-policy-v1");
    hasher.update(&draft_contract_hash);
    hasher.update(&target_contract_hash);
    hasher.update(&tokenizer_hash);
    hasher.update(&config_hash);
    hasher.update(&endpoint_class_hash);
    update_u32(&mut hasher, accepted_token_budget);
    hasher.finalize().into()
}

pub fn speculative_decode_equivalence_proof_hash(
    request_hash: [u8; 32],
    route_proof_hash: [u8; 32],
    draft_contract_hash: [u8; 32],
    target_contract_hash: [u8; 32],
    draft_batch_hash: [u8; 32],
    target_batch_hash: [u8; 32],
    accepted_prefix_len: u32,
    accepted_prefix_hash: [u8; 32],
    rejected_suffix_hash: [u8; 32],
    verifier_policy_hash: [u8; 32],
    accepted_token_budget: u32,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-speculative-decode-equivalence-proof-v1");
    hasher.update(&request_hash);
    hasher.update(&route_proof_hash);
    hasher.update(&draft_contract_hash);
    hasher.update(&target_contract_hash);
    hasher.update(&draft_batch_hash);
    hasher.update(&target_batch_hash);
    update_u32(&mut hasher, accepted_prefix_len);
    hasher.update(&accepted_prefix_hash);
    hasher.update(&rejected_suffix_hash);
    hasher.update(&verifier_policy_hash);
    update_u32(&mut hasher, accepted_token_budget);
    hasher.finalize().into()
}

fn validate_structured_field(
    field: &StructuredOutputFieldSpec,
    value: &serde_json::Value,
) -> Result<(), StructuredOutputError> {
    match field.kind {
        StructuredOutputKind::String => value
            .as_str()
            .map(|_| ())
            .ok_or(StructuredOutputError::TypeMismatch),
        StructuredOutputKind::Integer => value
            .as_i64()
            .map(|_| ())
            .ok_or(StructuredOutputError::TypeMismatch),
        StructuredOutputKind::Boolean => value
            .as_bool()
            .map(|_| ())
            .ok_or(StructuredOutputError::TypeMismatch),
        StructuredOutputKind::Hash32 => {
            let text = value.as_str().ok_or(StructuredOutputError::TypeMismatch)?;
            if is_hex_hash32(text) {
                Ok(())
            } else {
                Err(StructuredOutputError::HashMismatch)
            }
        }
    }
}

fn update_canonical_json_value(
    hasher: &mut Hasher,
    kind: StructuredOutputKind,
    value: &serde_json::Value,
) -> Result<(), StructuredOutputError> {
    match kind {
        StructuredOutputKind::String | StructuredOutputKind::Hash32 => {
            update_str(
                hasher,
                value.as_str().ok_or(StructuredOutputError::TypeMismatch)?,
            );
        }
        StructuredOutputKind::Integer => {
            hasher.update(
                &value
                    .as_i64()
                    .ok_or(StructuredOutputError::TypeMismatch)?
                    .to_le_bytes(),
            );
        }
        StructuredOutputKind::Boolean => {
            update_bool(
                hasher,
                value.as_bool().ok_or(StructuredOutputError::TypeMismatch)?,
            );
        }
    }
    Ok(())
}

fn is_hex_hash32(value: &str) -> bool {
    value.len() == 64 && value.as_bytes().iter().all(u8::is_ascii_hexdigit)
}

fn validate_prefix_cache_inputs(
    request: &LLMRequest,
    contract: &InferenceBackendContract,
    route_proof: &InferenceRouteProof,
    cache_scope_hash: [u8; 32],
    prefix_byte_len: u32,
    prefix_token_count: u32,
) -> Result<(), PrefixCacheError> {
    if !request.is_valid() {
        return Err(PrefixCacheError::InvalidRequest);
    }
    if !contract.is_valid() {
        return Err(PrefixCacheError::InvalidContract);
    }
    if !route_proof.hashes_are_consistent()
        || route_proof.request_hash != llm_request_hash(request)
        || route_proof.selected_contract_hash != contract.contract_hash
    {
        return Err(PrefixCacheError::InvalidRouteProof);
    }
    if cache_scope_hash == [0; 32] {
        return Err(PrefixCacheError::InvalidScope);
    }
    if prefix_byte_len == 0
        || prefix_token_count == 0
        || prefix_byte_len as usize > request.prompt.len()
        || prefix_token_count > contract.context_window_tokens
    {
        return Err(PrefixCacheError::InvalidPrefix);
    }
    Ok(())
}

fn find_contract_for_decision<'a>(
    contracts: &'a [InferenceBackendContract],
    decision: &RouterDecision,
) -> Option<&'a InferenceBackendContract> {
    contracts
        .iter()
        .find(|contract| contract.is_valid() && contract.matches_decision(decision))
}

fn update_optional_hash(hasher: &mut Hasher, hash: Option<[u8; 32]>) {
    match hash {
        Some(hash) => {
            hasher.update(&[1]);
            hasher.update(&hash);
        }
        None => {
            hasher.update(&[0]);
        }
    };
}

fn update_hash_slice(hasher: &mut Hasher, hashes: &[[u8; 32]]) {
    update_u64(hasher, hashes.len().min(u64::MAX as usize) as u64);
    for hash in hashes {
        hasher.update(hash);
    }
}

fn update_optional_str(hasher: &mut Hasher, value: Option<&str>) {
    match value {
        Some(value) => {
            hasher.update(&[1]);
            update_str(hasher, value);
        }
        None => {
            hasher.update(&[0]);
        }
    };
}

fn update_str(hasher: &mut Hasher, value: &str) {
    update_bytes(hasher, value.as_bytes());
}

fn update_bytes(hasher: &mut Hasher, bytes: &[u8]) {
    update_u64(hasher, bytes.len().min(u64::MAX as usize) as u64);
    hasher.update(bytes);
}

fn update_bool(hasher: &mut Hasher, value: bool) {
    hasher.update(&[u8::from(value)]);
}

fn update_u128(hasher: &mut Hasher, value: u128) {
    hasher.update(&value.to_le_bytes());
}

fn update_u64(hasher: &mut Hasher, value: u64) {
    hasher.update(&value.to_le_bytes());
}

fn update_u32(hasher: &mut Hasher, value: u32) {
    hasher.update(&value.to_le_bytes());
}
