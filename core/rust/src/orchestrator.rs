use crate::circuit_breaker::{
    CircuitBreakerDecision, LlmLoopCircuitBreaker, LlmLoopCircuitBreakerConfig,
};
use crate::ipc::{message_to_zero_copy, validate_zero_copy};
use crate::llm::{
    checkpoint_from_response, route_request, route_request_with_budget, route_with_fallback,
    session_bridge_key, AdapterRegistry, LLMCheckpoint, LLMRequest, LLMResponse,
    ProviderBudgetLedger, ProviderConfig, ProviderRouteAdmissionProof,
};
use crate::memory::fold::CogniFoldStore;
use crate::message::MessageFrame;
use crate::physical::{BacktrackSignal, PhysicalArtifact, PhysicalWatchdog};
use crate::replay::{RunEvent, RunEventLedger};
use crate::sac::{verify_physical_witness, PhysicalWitnessVote, SacAnchor};
use crate::schema::NERVE_SCHEMA;
use arrow::array::{ArrayRef, BinaryArray, BooleanArray, UInt64Array};
use arrow::datatypes::{DataType, Field, Schema, SchemaRef};
use arrow::ipc::writer::StreamWriter;
use arrow::record_batch::RecordBatch;
use blake3::Hasher;
use std::fs::{File, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::Arc;

const PAYLOAD_VOTE_CACHE_LIMIT: usize = 64;
pub const EPISODIC_AUDIT_SCHEMA_VERSION: u64 = 2;

#[derive(Clone, Debug, PartialEq)]
pub struct EpisodicTrace {
    pub request_id: u128,
    pub session_id: u128,
    pub content: String,
    pub content_len: usize,
    pub content_blake3: [u8; 32],
    pub raw_text_ref_hash: [u8; 32],
    pub token_usage: usize,
    pub latency_ms: u64,
    pub rejected: bool,
    pub audit_record_hash: [u8; 32],
}

pub struct EpisodicAuditLog {
    traces: Vec<EpisodicTrace>,
    arrow_stream: Option<ArrowIpcAuditStream>,
}

struct ArrowIpcAuditStream {
    path: PathBuf,
    schema: SchemaRef,
    writer: StreamWriter<File>,
}

impl EpisodicAuditLog {
    pub fn new() -> Self {
        Self {
            traces: Vec::new(),
            arrow_stream: None,
        }
    }

    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            traces: Vec::with_capacity(capacity),
            arrow_stream: None,
        }
    }

    pub fn arrow_ipc_stream(path: impl AsRef<Path>) -> Result<Self, &'static str> {
        Self::with_capacity_and_arrow_ipc_stream(0, path)
    }

    pub fn with_capacity_and_arrow_ipc_stream(
        capacity: usize,
        path: impl AsRef<Path>,
    ) -> Result<Self, &'static str> {
        Ok(Self {
            traces: Vec::with_capacity(capacity),
            arrow_stream: Some(ArrowIpcAuditStream::create(path)?),
        })
    }

    pub fn append_llm_response(&mut self, response: &LLMResponse) -> Result<(), &'static str> {
        if !response.is_valid() {
            return Err("invalid LLM response");
        }
        let content_len = response.content.len();
        let content_blake3 = crate::physical::blake3_digest(response.content.as_bytes());
        let raw_text_ref_hash = episodic_audit_raw_text_ref_hash(
            response.request_id,
            response.session_id,
            content_len,
            content_blake3,
            response.rejected,
        );
        let audit_record_hash = episodic_audit_record_hash(
            response.request_id,
            response.session_id,
            content_len,
            content_blake3,
            raw_text_ref_hash,
            response.token_usage,
            response.latency_ms,
            response.rejected,
        );
        let trace = EpisodicTrace {
            request_id: response.request_id,
            session_id: response.session_id,
            content: response.content.clone(),
            content_len,
            content_blake3,
            raw_text_ref_hash,
            token_usage: response.token_usage,
            latency_ms: response.latency_ms,
            rejected: response.rejected,
            audit_record_hash,
        };

        if let Some(stream) = self.arrow_stream.as_mut() {
            stream.append(&trace)?;
        }
        self.traces.push(trace);
        Ok(())
    }

    pub fn latest(&self) -> Option<&EpisodicTrace> {
        self.traces.last()
    }

    pub fn len(&self) -> usize {
        self.traces.len()
    }

    pub fn is_empty(&self) -> bool {
        self.traces.is_empty()
    }

    pub fn arrow_ipc_path(&self) -> Option<&Path> {
        self.arrow_stream
            .as_ref()
            .map(|stream| stream.path.as_path())
    }
}

impl Default for EpisodicAuditLog {
    fn default() -> Self {
        Self::new()
    }
}

impl ArrowIpcAuditStream {
    fn create(path: impl AsRef<Path>) -> Result<Self, &'static str> {
        let path = path.as_ref().to_path_buf();
        let file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&path)
            .map_err(|_| "failed to open arrow ipc audit stream")?;
        let schema = Arc::new(Self::schema());
        let writer = StreamWriter::try_new(file, schema.as_ref())
            .map_err(|_| "failed to initialize arrow ipc audit stream")?;
        Ok(Self {
            path,
            schema,
            writer,
        })
    }

    fn append(&mut self, trace: &EpisodicTrace) -> Result<(), &'static str> {
        let (request_id_hi, request_id_lo) = split_u128(trace.request_id);
        let (session_id_hi, session_id_lo) = split_u128(trace.session_id);
        let batch = RecordBatch::try_new(
            Arc::clone(&self.schema),
            vec![
                Arc::new(UInt64Array::from_iter_values([
                    EPISODIC_AUDIT_SCHEMA_VERSION,
                ])) as ArrayRef,
                Arc::new(UInt64Array::from_iter_values([request_id_hi])) as ArrayRef,
                Arc::new(UInt64Array::from_iter_values([request_id_lo])) as ArrayRef,
                Arc::new(UInt64Array::from_iter_values([session_id_hi])) as ArrayRef,
                Arc::new(UInt64Array::from_iter_values([session_id_lo])) as ArrayRef,
                Arc::new(UInt64Array::from_iter_values([trace.content_len as u64])) as ArrayRef,
                Arc::new(BinaryArray::from_vec(vec![trace.content_blake3.as_slice()])) as ArrayRef,
                Arc::new(BinaryArray::from_vec(vec![trace
                    .raw_text_ref_hash
                    .as_slice()])) as ArrayRef,
                Arc::new(UInt64Array::from_iter_values([trace.token_usage as u64])) as ArrayRef,
                Arc::new(UInt64Array::from_iter_values([trace.latency_ms])) as ArrayRef,
                Arc::new(BooleanArray::from(vec![trace.rejected])) as ArrayRef,
                Arc::new(BinaryArray::from_vec(vec![trace
                    .audit_record_hash
                    .as_slice()])) as ArrayRef,
            ],
        )
        .map_err(|_| "failed to encode arrow ipc audit batch")?;
        self.writer
            .write(&batch)
            .map_err(|_| "failed to write arrow ipc audit batch")?;
        self.writer
            .flush()
            .map_err(|_| "failed to flush arrow ipc audit stream")?;
        Ok(())
    }

    fn schema() -> Schema {
        Schema::new(vec![
            Field::new("schema_version", DataType::UInt64, false),
            Field::new("request_id_hi", DataType::UInt64, false),
            Field::new("request_id_lo", DataType::UInt64, false),
            Field::new("session_id_hi", DataType::UInt64, false),
            Field::new("session_id_lo", DataType::UInt64, false),
            Field::new("content_len", DataType::UInt64, false),
            Field::new("content_blake3", DataType::Binary, false),
            Field::new("raw_text_ref_hash", DataType::Binary, false),
            Field::new("token_usage", DataType::UInt64, false),
            Field::new("latency_ms", DataType::UInt64, false),
            Field::new("rejected", DataType::Boolean, false),
            Field::new("audit_record_hash", DataType::Binary, false),
        ])
    }
}

fn split_u128(value: u128) -> (u64, u64) {
    ((value >> 64) as u64, value as u64)
}

pub fn episodic_audit_raw_text_ref_hash(
    request_id: u128,
    session_id: u128,
    content_len: usize,
    content_blake3: [u8; 32],
    rejected: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-episodic-audit-raw-text-ref-v1");
    update_u128(&mut hasher, request_id);
    update_u128(&mut hasher, session_id);
    update_u64(&mut hasher, content_len as u64);
    hasher.update(&content_blake3);
    hasher.update(&[u8::from(rejected)]);
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
pub fn episodic_audit_record_hash(
    request_id: u128,
    session_id: u128,
    content_len: usize,
    content_blake3: [u8; 32],
    raw_text_ref_hash: [u8; 32],
    token_usage: usize,
    latency_ms: u64,
    rejected: bool,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-episodic-audit-record-v2");
    update_u64(&mut hasher, EPISODIC_AUDIT_SCHEMA_VERSION);
    update_u128(&mut hasher, request_id);
    update_u128(&mut hasher, session_id);
    update_u64(&mut hasher, content_len as u64);
    hasher.update(&content_blake3);
    hasher.update(&raw_text_ref_hash);
    update_u64(&mut hasher, token_usage as u64);
    update_u64(&mut hasher, latency_ms);
    hasher.update(&[u8::from(rejected)]);
    *hasher.finalize().as_bytes()
}

fn update_u128(hasher: &mut Hasher, value: u128) {
    hasher.update(&value.to_le_bytes());
}

fn update_u64(hasher: &mut Hasher, value: u64) {
    hasher.update(&value.to_le_bytes());
}

pub struct NerveRuntime {
    pub anchor: SacAnchor,
    pub memory: CogniFoldStore,
    pub audit_log: EpisodicAuditLog,
    pub processed_messages: usize,
    last_payload: [u8; PAYLOAD_VOTE_CACHE_LIMIT],
    last_payload_len: usize,
    last_payload_vote: Option<PhysicalWitnessVote>,
    llm_loop_breaker: LlmLoopCircuitBreaker,
    last_circuit_breaker_decision: Option<CircuitBreakerDecision>,
}

impl NerveRuntime {
    pub fn new() -> Self {
        Self {
            anchor: SacAnchor {
                anchor_id: 0,
                last_witness_hash: 0,
            },
            memory: CogniFoldStore::new(),
            audit_log: EpisodicAuditLog::new(),
            processed_messages: 0,
            last_payload: [0; PAYLOAD_VOTE_CACHE_LIMIT],
            last_payload_len: 0,
            last_payload_vote: None,
            llm_loop_breaker: LlmLoopCircuitBreaker::default(),
            last_circuit_breaker_decision: None,
        }
    }

    pub fn with_memory_capacity(memory_capacity: usize) -> Self {
        Self {
            anchor: SacAnchor {
                anchor_id: 0,
                last_witness_hash: 0,
            },
            memory: CogniFoldStore::with_capacity(memory_capacity),
            audit_log: EpisodicAuditLog::with_capacity(memory_capacity),
            processed_messages: 0,
            last_payload: [0; PAYLOAD_VOTE_CACHE_LIMIT],
            last_payload_len: 0,
            last_payload_vote: None,
            llm_loop_breaker: LlmLoopCircuitBreaker::default(),
            last_circuit_breaker_decision: None,
        }
    }

    pub fn with_llm_loop_circuit_breaker_config(
        config: LlmLoopCircuitBreakerConfig,
    ) -> Result<Self, &'static str> {
        let llm_loop_breaker = LlmLoopCircuitBreaker::new(config)?;
        Ok(Self {
            anchor: SacAnchor {
                anchor_id: 0,
                last_witness_hash: 0,
            },
            memory: CogniFoldStore::new(),
            audit_log: EpisodicAuditLog::new(),
            processed_messages: 0,
            last_payload: [0; PAYLOAD_VOTE_CACHE_LIMIT],
            last_payload_len: 0,
            last_payload_vote: None,
            llm_loop_breaker,
            last_circuit_breaker_decision: None,
        })
    }

    pub fn ingest_message(&mut self, message: MessageFrame) -> Result<(), &'static str> {
        self.ingest_message_ref(&message)
    }

    pub fn ingest_message_ref(&mut self, message: &MessageFrame) -> Result<(), &'static str> {
        let zero_copy = message_to_zero_copy(message)?;
        validate_zero_copy(&zero_copy, &NERVE_SCHEMA)?;
        let vote = self.physical_vote_for_payload(&message.payload)?;
        let witness = verify_physical_witness(&[vote], 1, self.anchor.last_witness_hash)?;
        self.anchor = witness.anchor;
        self.processed_messages += 1;
        Ok(())
    }

    pub fn process_llm_request(
        &mut self,
        request: &LLMRequest,
        providers: &[ProviderConfig],
    ) -> Result<(u128, u128), &'static str> {
        if !request.is_valid() {
            return Err("invalid LLM request");
        }
        let decision = route_request(request, providers).ok_or("no valid LLM provider")?;
        let _bridge_key = session_bridge_key(request);
        self.anchor.last_witness_hash = self.anchor.last_witness_hash.wrapping_add(1);
        let _ = decision;
        Ok((request.request_id, request.session_id))
    }

    pub fn process_llm_request_with_registry<'a>(
        &mut self,
        request: &LLMRequest,
        registry: &AdapterRegistry<'a>,
    ) -> Result<LLMResponse, &'static str> {
        let adapter = registry
            .select(request)
            .ok_or("no adapter matched request")?;
        let response = adapter.send(request)?;
        self.ingest_llm_response(&response)?;
        Ok(response)
    }

    pub fn process_llm_request_with_checkpoint<'a>(
        &mut self,
        request: &LLMRequest,
        registry: &AdapterRegistry<'a>,
    ) -> Result<(LLMResponse, Option<LLMCheckpoint>), &'static str> {
        let adapter = registry
            .select(request)
            .ok_or("no adapter matched request")?;
        let response = adapter.send(request)?;
        self.ingest_llm_response(&response)?;
        let checkpoint =
            checkpoint_from_response(adapter.provider_id(), adapter.model_name(), &response);
        Ok((response, checkpoint))
    }

    pub fn process_llm_request_with_fallback<'a>(
        &mut self,
        request: &LLMRequest,
        providers: &[ProviderConfig],
        registry: &AdapterRegistry<'a>,
    ) -> Result<(LLMResponse, Option<LLMCheckpoint>), &'static str> {
        let (primary, fallback) =
            route_with_fallback(request, providers).ok_or("no fallback chain available")?;
        if !primary.is_valid() {
            return Err("invalid primary routing decision");
        }
        if let Some(fallback_decision) = fallback.as_ref() {
            if !fallback_decision.is_valid() {
                return Err("invalid fallback routing decision");
            }
        }

        let mut selected_adapter =
            registry.select_provider(primary.provider_id, primary.model_name, request);
        if selected_adapter.is_none() {
            if let Some(fallback_decision) = fallback.as_ref() {
                selected_adapter = registry.select_provider(
                    fallback_decision.provider_id,
                    fallback_decision.model_name,
                    request,
                );
            }
        }

        let adapter = selected_adapter.ok_or("no adapter matched routed provider")?;
        let response = adapter.send(request)?;
        self.ingest_llm_response(&response)?;
        let checkpoint =
            checkpoint_from_response(adapter.provider_id(), adapter.model_name(), &response);
        Ok((response, checkpoint))
    }

    pub fn process_llm_request_with_budget_admission<'a>(
        &mut self,
        request: &LLMRequest,
        providers: &[ProviderConfig],
        registry: &AdapterRegistry<'a>,
        budget_ledger: &ProviderBudgetLedger,
        required_tokens: u32,
    ) -> Result<
        (
            LLMResponse,
            Option<LLMCheckpoint>,
            ProviderRouteAdmissionProof,
        ),
        &'static str,
    > {
        self.execute_budget_admitted_llm_request(
            request,
            providers,
            registry,
            budget_ledger,
            required_tokens,
        )
    }

    pub fn process_llm_request_with_budget_admission_replay<'a>(
        &mut self,
        request: &LLMRequest,
        providers: &[ProviderConfig],
        registry: &AdapterRegistry<'a>,
        budget_ledger: &ProviderBudgetLedger,
        required_tokens: u32,
        replay_ledger: &mut RunEventLedger,
    ) -> Result<
        (
            LLMResponse,
            Option<LLMCheckpoint>,
            ProviderRouteAdmissionProof,
            RunEvent,
        ),
        &'static str,
    > {
        let (response, checkpoint, admission_proof) = self.execute_budget_admitted_llm_request(
            request,
            providers,
            registry,
            budget_ledger,
            required_tokens,
        )?;
        let trace = self
            .audit_log
            .latest()
            .ok_or("missing episodic audit trace for replay binding")?;
        let replay_event = replay_ledger
            .append_budget_admitted_llm_response_received(
                trace.audit_record_hash,
                trace.raw_text_ref_hash,
                &admission_proof,
            )
            .map_err(|_| "failed to append budget-admitted llm replay event")?
            .clone();
        Ok((response, checkpoint, admission_proof, replay_event))
    }

    fn execute_budget_admitted_llm_request<'a>(
        &mut self,
        request: &LLMRequest,
        providers: &[ProviderConfig],
        registry: &AdapterRegistry<'a>,
        budget_ledger: &ProviderBudgetLedger,
        required_tokens: u32,
    ) -> Result<
        (
            LLMResponse,
            Option<LLMCheckpoint>,
            ProviderRouteAdmissionProof,
        ),
        &'static str,
    > {
        let (decision, admission_proof) =
            route_request_with_budget(request, providers, budget_ledger, required_tokens)
                .map_err(|_| "provider budget admission failed")?;
        if !admission_proof.is_valid_for(request, providers, budget_ledger, &decision) {
            return Err("invalid provider budget admission proof");
        }
        let adapter = registry
            .select_provider(decision.provider_id, decision.model_name, request)
            .ok_or("no adapter matched budget-admitted provider")?;
        let response = adapter.send(request)?;
        self.ingest_llm_response(&response)?;
        let checkpoint =
            checkpoint_from_response(adapter.provider_id(), adapter.model_name(), &response);
        Ok((response, checkpoint, admission_proof))
    }

    fn ingest_llm_response(&mut self, response: &LLMResponse) -> Result<(), &'static str> {
        let decision = self.llm_loop_breaker.observe_response(response)?;
        self.last_circuit_breaker_decision = Some(decision);
        if decision.is_tripped() {
            return Err("llm loop circuit breaker tripped");
        }
        self.audit_log.append_llm_response(response)?;
        self.anchor.last_witness_hash = self
            .anchor
            .last_witness_hash
            .wrapping_add(response.token_usage as u64);
        Ok(())
    }

    pub fn commit_physical_artifact(
        &mut self,
        session_id: u128,
        artifact: &PhysicalArtifact,
        watchdog: &PhysicalWatchdog,
    ) -> Result<crate::memory::frame::SemanticNode, BacktrackSignal> {
        use crate::memory::fold::MemoryCrystallization;

        self.memory
            .commit_to_cognifold(session_id, artifact, watchdog)
    }

    fn physical_vote_for_payload(
        &mut self,
        payload: &[u8],
    ) -> Result<PhysicalWitnessVote, &'static str> {
        if payload.len() <= PAYLOAD_VOTE_CACHE_LIMIT {
            if let Some(vote) = self.last_payload_vote {
                if self.last_payload_len == payload.len()
                    && &self.last_payload[..self.last_payload_len] == payload
                {
                    return Ok(vote);
                }
            }
            let vote = PhysicalWitnessVote::from_payload(payload, 1)?;
            self.last_payload[..payload.len()].copy_from_slice(payload);
            self.last_payload_len = payload.len();
            self.last_payload_vote = Some(vote);
            return Ok(vote);
        }
        PhysicalWitnessVote::from_payload(payload, 1)
    }

    pub fn processed_count(&self) -> usize {
        self.processed_messages
    }

    pub fn latest_session_summary(&self) -> Option<(u128, usize, f32)> {
        self.memory.session_summary()
    }

    pub fn last_circuit_breaker_decision(&self) -> Option<CircuitBreakerDecision> {
        self.last_circuit_breaker_decision
    }

    pub fn llm_loop_window_len(&self) -> usize {
        self.llm_loop_breaker.window_len()
    }

    pub fn is_ready(&self) -> bool {
        self.anchor.anchor_id == 0 || self.anchor.anchor_id == 1
    }
}

impl Default for NerveRuntime {
    fn default() -> Self {
        Self::new()
    }
}
