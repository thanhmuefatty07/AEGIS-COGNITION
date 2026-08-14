use blake3::Hasher;
use parking_lot::Mutex;
use std::env;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread::JoinHandle;
use tokio::io::AsyncWriteExt;
use tokio::sync::{mpsc, oneshot};

pub type ArtifactHash = [u8; 32];

const DEFAULT_MAX_ARENA_BYTES: usize = 64 * 1024 * 1024;
const DEFAULT_MAX_ARTIFACT_BYTES: usize = 16 * 1024 * 1024;
const DEFAULT_SEAL_BATCH_BYTES: usize = 4 * 1024 * 1024;
/// Depth of the shadow-sealer bounded mpsc channel.
///
/// Tuned for production backpressure ceilings:
/// - 1024 produced a hard throughput ceiling around 8K-10K ops/sec under
///   sustained load where senders blocked before receiving `Ok`.
/// - 4096 (BUG-002 fix) covers ~40K ops/sec at p99, with backpressure only
///   emerging at spikes above that sustained rate.
/// - Upper bound kept at a power of two so callers can use it as the
///   `mpsc::channel` channel-capacity argument with predictable wait-queue
///   memory usage (~ `queue_depth * sizeof(ShadowSealCommand)`).
///
/// Override at runtime via `start_with_queue_depth` from config; this
/// constant is only the default.
const SHADOW_SEALER_QUEUE_DEPTH: usize = 4096;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TrustLevel {
    Dev,
    Staging,
    Prod,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ArenaAdmission {
    Accepted,
    DegradedWarning,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum HotEngineError {
    InvalidInput,
    OversizedArtifact,
    ArenaFull,
    InvalidHandle,
    GenerationMismatch,
    SealerClosed,
    SealerBackpressure,
    SealFailed,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct EvidenceHandle {
    pub slot: u32,
    pub generation: u32,
    pub byte_len: u64,
    pub artifact_hash: ArtifactHash,
    pub storage_ref_hash: ArtifactHash,
    pub trust_level: TrustLevel,
    pub admission: ArenaAdmission,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArenaStats {
    pub live_bytes: usize,
    pub live_slots: usize,
    pub free_slots: usize,
    pub total_commits: u64,
    pub total_degraded_commits: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ShadowSealReceipt {
    pub slot: u32,
    pub generation: u32,
    pub byte_len: u64,
    pub artifact_hash: ArtifactHash,
    pub file_path: PathBuf,
    pub seal_hash: ArtifactHash,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ShadowSealBatchReceipt {
    pub receipt_count: usize,
    pub total_bytes: u64,
    pub batch_hash: ArtifactHash,
    pub receipts: Vec<ShadowSealReceipt>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ShadowSealBackpressureRecord {
    pub slot: u32,
    pub generation: u32,
    pub byte_len: u64,
    pub artifact_hash: ArtifactHash,
    pub storage_ref_hash: ArtifactHash,
    pub queue_depth: usize,
    pub backpressure_sequence: u64,
    pub backpressure_hash: ArtifactHash,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ShadowSealerStats {
    pub queue_depth: usize,
    pub queued_admissions: u64,
    pub backpressure_rejections: u64,
}

pub enum ShadowSealAdmission {
    Queued(oneshot::Receiver<Result<ShadowSealReceipt, HotEngineError>>),
    Backpressured(ShadowSealBackpressureRecord),
}

#[derive(Clone, Debug)]
pub struct InMemoryEvidenceArena {
    inner: Arc<Mutex<ArenaInner>>,
    max_live_bytes: usize,
    max_artifact_bytes: usize,
    trust_level: TrustLevel,
    total_commits: Arc<AtomicU64>,
    total_degraded_commits: Arc<AtomicU64>,
}

#[derive(Clone, Debug)]
struct ArenaInner {
    slots: Vec<ArenaSlot>,
    free: Vec<u32>,
    live_bytes: usize,
}

#[derive(Clone, Debug)]
struct ArenaSlot {
    generation: u32,
    payload: Option<Arc<[u8]>>,
    artifact_hash: ArtifactHash,
    storage_ref_hash: ArtifactHash,
}

#[derive(Clone, Debug)]
struct ShadowSealRequest {
    handle: EvidenceHandle,
    payload: Arc<[u8]>,
}

pub struct AsyncShadowSealer {
    sender: mpsc::Sender<ShadowSealCommand>,
    queue_depth: usize,
    queued_admissions: Arc<AtomicU64>,
    backpressure_rejections: Arc<AtomicU64>,
    join: Option<JoinHandle<()>>,
    #[cfg(test)]
    paused_receiver: Option<mpsc::Receiver<ShadowSealCommand>>,
}

enum ShadowSealCommand {
    Seal(
        ShadowSealRequest,
        oneshot::Sender<Result<ShadowSealReceipt, HotEngineError>>,
    ),
    Flush(oneshot::Sender<Result<ShadowSealBatchReceipt, HotEngineError>>),
    Shutdown,
}

impl TrustLevel {
    pub fn from_env() -> Self {
        match env::var("AEGIS_TRUST_LEVEL")
            .unwrap_or_else(|_| "PROD".to_string())
            .trim()
            .to_ascii_uppercase()
            .as_str()
        {
            "DEV" => Self::Dev,
            "STAGING" => Self::Staging,
            _ => Self::Prod,
        }
    }

    pub fn physical_witness_required(self) -> bool {
        matches!(self, Self::Prod)
    }

    pub fn fail_closed(self) -> bool {
        matches!(self, Self::Prod)
    }
}

impl InMemoryEvidenceArena {
    pub fn new(trust_level: TrustLevel, max_live_bytes: usize, max_artifact_bytes: usize) -> Self {
        Self {
            inner: Arc::new(Mutex::new(ArenaInner {
                slots: Vec::new(),
                free: Vec::new(),
                live_bytes: 0,
            })),
            max_live_bytes: max_live_bytes.max(1),
            max_artifact_bytes: max_artifact_bytes.max(1),
            trust_level,
            total_commits: Arc::new(AtomicU64::new(0)),
            total_degraded_commits: Arc::new(AtomicU64::new(0)),
        }
    }

    pub fn from_env() -> Self {
        Self::new(
            TrustLevel::from_env(),
            DEFAULT_MAX_ARENA_BYTES,
            DEFAULT_MAX_ARTIFACT_BYTES,
        )
    }

    pub fn commit(&self, payload: impl AsRef<[u8]>) -> Result<EvidenceHandle, HotEngineError> {
        let bytes = payload.as_ref();
        if bytes.is_empty() {
            return Err(HotEngineError::InvalidInput);
        }
        if bytes.len() > self.max_artifact_bytes {
            return Err(HotEngineError::OversizedArtifact);
        }

        let artifact_hash = simd_blake3_hash(bytes);
        let admission = if self.trust_level == TrustLevel::Dev {
            self.total_degraded_commits.fetch_add(1, Ordering::Relaxed);
            ArenaAdmission::DegradedWarning
        } else {
            ArenaAdmission::Accepted
        };
        let payload: Arc<[u8]> = Arc::from(bytes);
        let mut inner = self.inner.lock();
        let next_live_bytes = inner
            .live_bytes
            .checked_add(payload.len())
            .ok_or(HotEngineError::ArenaFull)?;
        if next_live_bytes > self.max_live_bytes {
            return Err(HotEngineError::ArenaFull);
        }

        let slot_index = if let Some(slot) = inner.free.pop() {
            slot
        } else {
            let slot = inner.slots.len();
            if slot > u32::MAX as usize {
                return Err(HotEngineError::ArenaFull);
            }
            inner.slots.push(ArenaSlot {
                generation: 0,
                payload: None,
                artifact_hash: [0; 32],
                storage_ref_hash: [0; 32],
            });
            slot as u32
        };
        let generation = inner.slots[slot_index as usize]
            .generation
            .wrapping_add(1)
            .max(1);
        let storage_ref_hash = arena_storage_ref_hash(slot_index, generation, artifact_hash);
        inner.slots[slot_index as usize] = ArenaSlot {
            generation,
            payload: Some(payload),
            artifact_hash,
            storage_ref_hash,
        };
        inner.live_bytes = next_live_bytes;
        self.total_commits.fetch_add(1, Ordering::Relaxed);

        Ok(EvidenceHandle {
            slot: slot_index,
            generation,
            byte_len: bytes.len() as u64,
            artifact_hash,
            storage_ref_hash,
            trust_level: self.trust_level,
            admission,
        })
    }

    pub fn payload_arc(&self, handle: &EvidenceHandle) -> Result<Arc<[u8]>, HotEngineError> {
        let inner = self.inner.lock();
        let slot = inner
            .slots
            .get(handle.slot as usize)
            .ok_or(HotEngineError::InvalidHandle)?;
        if slot.generation != handle.generation
            || slot.artifact_hash != handle.artifact_hash
            || slot.storage_ref_hash != handle.storage_ref_hash
        {
            return Err(HotEngineError::GenerationMismatch);
        }
        slot.payload
            .as_ref()
            .cloned()
            .ok_or(HotEngineError::InvalidHandle)
    }

    pub fn release(&self, handle: &EvidenceHandle) -> Result<(), HotEngineError> {
        let mut inner = self.inner.lock();
        let slot = inner
            .slots
            .get_mut(handle.slot as usize)
            .ok_or(HotEngineError::InvalidHandle)?;
        if slot.generation != handle.generation
            || slot.artifact_hash != handle.artifact_hash
            || slot.storage_ref_hash != handle.storage_ref_hash
        {
            return Err(HotEngineError::GenerationMismatch);
        }
        let byte_len = slot
            .payload
            .as_ref()
            .map(|payload| payload.len())
            .ok_or(HotEngineError::InvalidHandle)?;
        slot.payload = None;
        inner.live_bytes = inner.live_bytes.saturating_sub(byte_len);
        inner.free.push(handle.slot);
        Ok(())
    }

    pub fn stats(&self) -> ArenaStats {
        let inner = self.inner.lock();
        ArenaStats {
            live_bytes: inner.live_bytes,
            live_slots: inner
                .slots
                .iter()
                .filter(|slot| slot.payload.is_some())
                .count(),
            free_slots: inner.free.len(),
            total_commits: self.total_commits.load(Ordering::Relaxed),
            total_degraded_commits: self.total_degraded_commits.load(Ordering::Relaxed),
        }
    }

    pub fn trust_level(&self) -> TrustLevel {
        self.trust_level
    }
}

impl EvidenceHandle {
    pub fn is_valid(&self) -> bool {
        self.slot != u32::MAX
            && self.generation > 0
            && self.byte_len > 0
            && nonzero_hash(&self.artifact_hash)
            && nonzero_hash(&self.storage_ref_hash)
            && self.storage_ref_hash
                == arena_storage_ref_hash(self.slot, self.generation, self.artifact_hash)
    }
}

impl ShadowSealReceipt {
    pub fn is_valid(&self) -> bool {
        self.generation > 0
            && self.byte_len > 0
            && nonzero_hash(&self.artifact_hash)
            && nonzero_hash(&self.seal_hash)
            && !self.file_path.as_os_str().is_empty()
            && self.seal_hash
                == shadow_seal_receipt_hash(
                    self.slot,
                    self.generation,
                    self.byte_len,
                    self.artifact_hash,
                    &self.file_path,
                )
    }
}

impl ShadowSealBatchReceipt {
    pub fn is_valid(&self) -> bool {
        self.receipt_count > 0
            && self.receipt_count == self.receipts.len()
            && self.total_bytes
                == self
                    .receipts
                    .iter()
                    .map(|receipt| receipt.byte_len)
                    .sum::<u64>()
            && nonzero_hash(&self.batch_hash)
            && self.batch_hash == shadow_seal_batch_hash(&self.receipts)
            && self.receipts.iter().all(ShadowSealReceipt::is_valid)
    }
}

impl AsyncShadowSealer {
    pub fn start(directory: impl AsRef<Path>) -> Result<Self, HotEngineError> {
        Self::start_with_queue_depth(directory, SHADOW_SEALER_QUEUE_DEPTH)
    }

    pub fn start_with_queue_depth(
        directory: impl AsRef<Path>,
        queue_depth: usize,
    ) -> Result<Self, HotEngineError> {
        let directory = directory.as_ref().to_path_buf();
        std::fs::create_dir_all(&directory).map_err(|_| HotEngineError::SealFailed)?;
        let queue_depth = queue_depth.max(1);
        let (sender, receiver) = mpsc::channel(queue_depth);
        let join = std::thread::Builder::new()
            .name("aegis-async-shadow-sealer".to_string())
            .spawn(move || {
                let runtime = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .expect("shadow sealer tokio runtime");
                runtime.block_on(shadow_sealer_loop(directory, receiver));
            })
            .map_err(|_| HotEngineError::SealFailed)?;
        Ok(Self {
            sender,
            queue_depth,
            queued_admissions: Arc::new(AtomicU64::new(0)),
            backpressure_rejections: Arc::new(AtomicU64::new(0)),
            join: Some(join),
            #[cfg(test)]
            paused_receiver: None,
        })
    }

    #[cfg(test)]
    pub fn start_paused_for_backpressure_test(queue_depth: usize) -> Self {
        let queue_depth = queue_depth.max(1);
        let (sender, receiver) = mpsc::channel(queue_depth);
        Self {
            sender,
            queue_depth,
            queued_admissions: Arc::new(AtomicU64::new(0)),
            backpressure_rejections: Arc::new(AtomicU64::new(0)),
            join: None,
            paused_receiver: Some(receiver),
        }
    }

    pub fn try_submit(
        &self,
        arena: &InMemoryEvidenceArena,
        handle: EvidenceHandle,
    ) -> Result<ShadowSealAdmission, HotEngineError> {
        if !handle.is_valid() {
            return Err(HotEngineError::InvalidHandle);
        }
        let payload = arena.payload_arc(&handle)?;
        let (tx, rx) = oneshot::channel();
        match self.sender.try_send(ShadowSealCommand::Seal(
            ShadowSealRequest { handle, payload },
            tx,
        )) {
            Ok(()) => {
                self.queued_admissions.fetch_add(1, Ordering::Relaxed);
                Ok(ShadowSealAdmission::Queued(rx))
            }
            Err(mpsc::error::TrySendError::Full(_)) => {
                let sequence = self
                    .backpressure_rejections
                    .fetch_add(1, Ordering::Relaxed)
                    .saturating_add(1);
                Ok(ShadowSealAdmission::Backpressured(
                    shadow_seal_backpressure_record(handle, self.queue_depth, sequence),
                ))
            }
            Err(mpsc::error::TrySendError::Closed(_)) => Err(HotEngineError::SealerClosed),
        }
    }

    pub fn submit(
        &self,
        arena: &InMemoryEvidenceArena,
        handle: EvidenceHandle,
    ) -> Result<oneshot::Receiver<Result<ShadowSealReceipt, HotEngineError>>, HotEngineError> {
        match self.try_submit(arena, handle)? {
            ShadowSealAdmission::Queued(receiver) => Ok(receiver),
            ShadowSealAdmission::Backpressured(_) => Err(HotEngineError::SealerBackpressure),
        }
    }

    pub fn flush_blocking(&self) -> Result<ShadowSealBatchReceipt, HotEngineError> {
        let (tx, rx) = oneshot::channel();
        self.sender
            .blocking_send(ShadowSealCommand::Flush(tx))
            .map_err(|_| HotEngineError::SealerClosed)?;
        rx.blocking_recv()
            .map_err(|_| HotEngineError::SealerClosed)?
    }

    pub fn stats(&self) -> ShadowSealerStats {
        ShadowSealerStats {
            queue_depth: self.queue_depth,
            queued_admissions: self.queued_admissions.load(Ordering::Relaxed),
            backpressure_rejections: self.backpressure_rejections.load(Ordering::Relaxed),
        }
    }
}

impl Drop for AsyncShadowSealer {
    fn drop(&mut self) {
        if self.join.is_some() {
            let _ = self.sender.blocking_send(ShadowSealCommand::Shutdown);
        } else {
            let _ = self.sender.try_send(ShadowSealCommand::Shutdown);
        }
        #[cfg(test)]
        let _ = self.paused_receiver.take();
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

async fn shadow_sealer_loop(directory: PathBuf, mut receiver: mpsc::Receiver<ShadowSealCommand>) {
    let mut pending = Vec::new();
    while let Some(command) = receiver.recv().await {
        match command {
            ShadowSealCommand::Seal(request, receipt_tx) => {
                let result = write_shadow_payload(&directory, request).await;
                if let Ok(receipt) = &result {
                    pending.push(receipt.clone());
                    let pending_bytes: u64 = pending.iter().map(|receipt| receipt.byte_len).sum();
                    if pending_bytes as usize >= DEFAULT_SEAL_BATCH_BYTES {
                        pending.clear();
                    }
                }
                let _ = receipt_tx.send(result);
            }
            ShadowSealCommand::Flush(flush_tx) => {
                let receipt = batch_receipt(&pending);
                pending.clear();
                let _ = flush_tx.send(Ok(receipt));
            }
            ShadowSealCommand::Shutdown => break,
        }
    }
}

async fn write_shadow_payload(
    directory: &Path,
    request: ShadowSealRequest,
) -> Result<ShadowSealReceipt, HotEngineError> {
    let file_name = format!(
        "{:08x}-{:08x}-{}.bin",
        request.handle.slot,
        request.handle.generation,
        hex_hash_prefix(&request.handle.artifact_hash)
    );
    let path = directory.join(file_name);
    let mut file = tokio::fs::File::create(&path)
        .await
        .map_err(|_| HotEngineError::SealFailed)?;
    file.write_all(&request.payload)
        .await
        .map_err(|_| HotEngineError::SealFailed)?;
    file.flush().await.map_err(|_| HotEngineError::SealFailed)?;
    file.sync_all()
        .await
        .map_err(|_| HotEngineError::SealFailed)?;
    let seal_hash = shadow_seal_receipt_hash(
        request.handle.slot,
        request.handle.generation,
        request.handle.byte_len,
        request.handle.artifact_hash,
        &path,
    );
    Ok(ShadowSealReceipt {
        slot: request.handle.slot,
        generation: request.handle.generation,
        byte_len: request.handle.byte_len,
        artifact_hash: request.handle.artifact_hash,
        file_path: path,
        seal_hash,
    })
}

fn batch_receipt(receipts: &[ShadowSealReceipt]) -> ShadowSealBatchReceipt {
    let total_bytes = receipts.iter().map(|receipt| receipt.byte_len).sum();
    let batch_hash = shadow_seal_batch_hash(receipts);
    ShadowSealBatchReceipt {
        receipt_count: receipts.len(),
        total_bytes,
        batch_hash,
        receipts: receipts.to_vec(),
    }
}

pub fn simd_blake3_hash(bytes: &[u8]) -> ArtifactHash {
    *blake3::hash(bytes).as_bytes()
}

pub fn arena_storage_ref_hash(
    slot: u32,
    generation: u32,
    artifact_hash: ArtifactHash,
) -> ArtifactHash {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-hot-arena-storage-ref-v1");
    hasher.update(&slot.to_le_bytes());
    hasher.update(&generation.to_le_bytes());
    hasher.update(&artifact_hash);
    *hasher.finalize().as_bytes()
}

pub fn shadow_seal_receipt_hash(
    slot: u32,
    generation: u32,
    byte_len: u64,
    artifact_hash: ArtifactHash,
    path: &Path,
) -> ArtifactHash {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-shadow-seal-receipt-v1");
    hasher.update(&slot.to_le_bytes());
    hasher.update(&generation.to_le_bytes());
    hasher.update(&byte_len.to_le_bytes());
    hasher.update(&artifact_hash);
    hasher.update(path.to_string_lossy().as_bytes());
    *hasher.finalize().as_bytes()
}

pub fn shadow_seal_batch_hash(receipts: &[ShadowSealReceipt]) -> ArtifactHash {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-shadow-seal-batch-v1");
    hasher.update(&(receipts.len() as u64).to_le_bytes());
    for receipt in receipts {
        hasher.update(&receipt.seal_hash);
        hasher.update(&receipt.artifact_hash);
        hasher.update(&receipt.byte_len.to_le_bytes());
    }
    *hasher.finalize().as_bytes()
}

pub fn shadow_seal_backpressure_hash(
    slot: u32,
    generation: u32,
    byte_len: u64,
    artifact_hash: ArtifactHash,
    storage_ref_hash: ArtifactHash,
    queue_depth: usize,
    backpressure_sequence: u64,
) -> ArtifactHash {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-shadow-seal-backpressure-v1");
    hasher.update(&slot.to_le_bytes());
    hasher.update(&generation.to_le_bytes());
    hasher.update(&byte_len.to_le_bytes());
    hasher.update(&artifact_hash);
    hasher.update(&storage_ref_hash);
    hasher.update(&(queue_depth as u64).to_le_bytes());
    hasher.update(&backpressure_sequence.to_le_bytes());
    *hasher.finalize().as_bytes()
}

pub fn shadow_seal_backpressure_record(
    handle: EvidenceHandle,
    queue_depth: usize,
    backpressure_sequence: u64,
) -> ShadowSealBackpressureRecord {
    let backpressure_hash = shadow_seal_backpressure_hash(
        handle.slot,
        handle.generation,
        handle.byte_len,
        handle.artifact_hash,
        handle.storage_ref_hash,
        queue_depth,
        backpressure_sequence,
    );
    ShadowSealBackpressureRecord {
        slot: handle.slot,
        generation: handle.generation,
        byte_len: handle.byte_len,
        artifact_hash: handle.artifact_hash,
        storage_ref_hash: handle.storage_ref_hash,
        queue_depth,
        backpressure_sequence,
        backpressure_hash,
    }
}

impl ShadowSealBackpressureRecord {
    pub fn is_valid(&self) -> bool {
        self.generation > 0
            && self.byte_len > 0
            && self.queue_depth > 0
            && self.backpressure_sequence > 0
            && nonzero_hash(&self.artifact_hash)
            && nonzero_hash(&self.storage_ref_hash)
            && self.storage_ref_hash
                == arena_storage_ref_hash(self.slot, self.generation, self.artifact_hash)
            && self.backpressure_hash
                == shadow_seal_backpressure_hash(
                    self.slot,
                    self.generation,
                    self.byte_len,
                    self.artifact_hash,
                    self.storage_ref_hash,
                    self.queue_depth,
                    self.backpressure_sequence,
                )
    }
}

fn hex_hash_prefix(hash: &ArtifactHash) -> String {
    hash[..8]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>()
}

fn nonzero_hash(hash: &ArtifactHash) -> bool {
    hash.iter().any(|byte| *byte != 0)
}
