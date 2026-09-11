use super::fidelity::reinforce;
use super::frame::{
    ContextPrefix, MemoryEdge, MemoryFrame, MemoryGraph, SemanticNode, SemanticPointer,
};
use crate::physical::{
    BacktrackSignal, ObjectiveValidationReceipt, PhysicalArtifact, PhysicalWatchdog, TrapReason,
};

use std::num::NonZeroU32;

pub const RUNTIME_LAYOUT_PAYLOAD_ALIGNMENT: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RuntimeLayoutBudget {
    max_live_slots: usize,
    max_payload_bytes: usize,
    payload_alignment: usize,
}

impl RuntimeLayoutBudget {
    pub fn new(
        max_live_slots: usize,
        max_payload_bytes: usize,
        payload_alignment: usize,
    ) -> Result<Self, &'static str> {
        let budget = Self {
            max_live_slots,
            max_payload_bytes,
            payload_alignment,
        };
        if budget.is_valid() {
            Ok(budget)
        } else {
            Err("invalid runtime layout budget")
        }
    }

    pub fn bounded(max_live_slots: usize, max_payload_bytes: usize) -> Result<Self, &'static str> {
        Self::new(
            max_live_slots,
            max_payload_bytes,
            RUNTIME_LAYOUT_PAYLOAD_ALIGNMENT,
        )
    }

    pub fn is_valid(&self) -> bool {
        self.max_live_slots > 0
            && self.max_payload_bytes > 0
            && self.payload_alignment == RUNTIME_LAYOUT_PAYLOAD_ALIGNMENT
    }

    pub fn max_live_slots(&self) -> usize {
        self.max_live_slots
    }

    pub fn max_payload_bytes(&self) -> usize {
        self.max_payload_bytes
    }

    pub fn payload_alignment(&self) -> usize {
        self.payload_alignment
    }

    fn check_insert(
        &self,
        current_live_slots: usize,
        current_payload_bytes: usize,
        incoming_payload_bytes: usize,
    ) -> Result<(), &'static str> {
        if !self.is_valid() {
            return Err("invalid runtime layout budget");
        }
        let next_live_slots = current_live_slots
            .checked_add(1)
            .ok_or("runtime layout slot budget overflow")?;
        if next_live_slots > self.max_live_slots {
            return Err("runtime layout slot budget exceeded");
        }
        let next_payload_bytes = current_payload_bytes
            .checked_add(incoming_payload_bytes)
            .ok_or("runtime layout payload byte budget overflow")?;
        if next_payload_bytes > self.max_payload_bytes {
            return Err("runtime layout payload byte budget exceeded");
        }
        Ok(())
    }
}

/// # INVARIANT: Cache-Line Alignment to prevent False Sharing across P-Cores
#[repr(align(64))]
#[derive(Clone, Debug, PartialEq)]
pub struct SlabHeader {
    pub free_list_head: u32,
    pub capacity: u32,
    // Padding to ensure exactly 64 bytes
    pub _pad: [u8; 56],
}

/// # INVARIANT: Null Pointer Optimization for Generation tracking
/// Option<SlotId> takes exactly 8 bytes, not 12.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct SlotId {
    pub index: u32,
    pub generation: NonZeroU32,
}

impl SlotId {
    pub fn new(index: u32, generation: NonZeroU32, pool_class: u16) -> Self {
        let class_bits = match pool_class {
            32 => 0,
            64 => 1,
            128 => 2,
            256 => 3,
            _ => 4, // overflow
        };
        // Pack class_bits into top 3 bits of index
        let packed_index = (index & 0x1F_FF_FF_FF) | ((class_bits as u32) << 29);
        Self {
            index: packed_index,
            generation,
        }
    }

    pub fn unpack(&self) -> (u32, u16) {
        let class_bits = (self.index >> 29) & 0x7;
        let index = self.index & 0x1F_FF_FF_FF;
        let pool_class = match class_bits {
            0 => 32,
            1 => 64,
            2 => 128,
            3 => 256,
            _ => 0, // overflow
        };
        (index, pool_class)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct SlabEntry<T> {
    pub value: Option<T>,
    pub generation: NonZeroU32,
}

#[derive(Clone, Debug, PartialEq)]
pub struct SlabPool<T> {
    pub header: SlabHeader,
    pub entries: Vec<SlabEntry<T>>,
    pub free_list: Vec<u32>,
    pub live_len: usize,
}

impl<T> Default for SlabPool<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T> SlabPool<T> {
    pub fn new() -> Self {
        Self {
            header: SlabHeader {
                free_list_head: u32::MAX,
                capacity: 0,
                _pad: [0; 56],
            },
            entries: Vec::new(),
            free_list: Vec::new(),
            live_len: 0,
        }
    }

    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            header: SlabHeader {
                free_list_head: u32::MAX,
                capacity: capacity as u32,
                _pad: [0; 56],
            },
            entries: Vec::with_capacity(capacity),
            free_list: Vec::new(),
            live_len: 0,
        }
    }

    pub fn insert(&mut self, val: T) -> SlotId {
        if let Some(index) = self.free_list.pop() {
            let entry = &mut self.entries[index as usize];
            entry.value = Some(val);
            let next_gen = entry.generation.get().wrapping_add(1);
            entry.generation = NonZeroU32::new(if next_gen == 0 { 1 } else { next_gen }).unwrap();
            self.live_len += 1;
            self.header.free_list_head = self.free_list.last().copied().unwrap_or(u32::MAX);
            SlotId {
                index,
                generation: entry.generation,
            }
        } else {
            let index = self.entries.len() as u32;
            let generation = NonZeroU32::new(1).unwrap();
            let entry = SlabEntry {
                value: Some(val),
                generation,
            };
            self.entries.push(entry);
            self.header.capacity = self.entries.len() as u32;
            self.live_len += 1;
            SlotId { index, generation }
        }
    }

    pub fn get(&self, index: u32, generation: NonZeroU32) -> Option<&T> {
        if let Some(entry) = self.entries.get(index as usize) {
            if entry.generation == generation {
                return entry.value.as_ref();
            }
        }
        None
    }

    pub fn get_mut(&mut self, index: u32, generation: NonZeroU32) -> Option<&mut T> {
        if let Some(entry) = self.entries.get_mut(index as usize) {
            if entry.generation == generation {
                return entry.value.as_mut();
            }
        }
        None
    }

    pub fn remove(&mut self, index: u32, generation: NonZeroU32) -> Option<T> {
        if let Some(entry) = self.entries.get_mut(index as usize) {
            if entry.generation == generation && entry.value.is_some() {
                let val = entry.value.take();
                self.free_list.push(index);
                self.live_len = self.live_len.saturating_sub(1);
                self.header.free_list_head = index;
                return val;
            }
        }
        None
    }

    pub fn len(&self) -> usize {
        self.live_len
    }

    pub fn is_empty(&self) -> bool {
        self.live_len == 0
    }
}

pub trait HasPayloadLen {
    fn payload_len(&self) -> usize;
}

impl HasPayloadLen for MemoryFrame {
    fn payload_len(&self) -> usize {
        self.payload.len()
    }
}

impl HasPayloadLen for &str {
    fn payload_len(&self) -> usize {
        self.len()
    }
}

impl HasPayloadLen for String {
    fn payload_len(&self) -> usize {
        self.len()
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct GenerationalSlab<T> {
    pub pool_32: SlabPool<T>,
    pub pool_64: SlabPool<T>,
    pub pool_128: SlabPool<T>,
    pub pool_256: SlabPool<T>,
    pub pool_overflow: SlabPool<T>,
    pub layout_budget: Option<RuntimeLayoutBudget>,
    pub live_len: usize,
    pub payload_bytes: usize,
}

impl<T: HasPayloadLen> Default for GenerationalSlab<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T: HasPayloadLen> GenerationalSlab<T> {
    pub fn new() -> Self {
        Self {
            pool_32: SlabPool::new(),
            pool_64: SlabPool::new(),
            pool_128: SlabPool::new(),
            pool_256: SlabPool::new(),
            pool_overflow: SlabPool::new(),
            layout_budget: None,
            live_len: 0,
            payload_bytes: 0,
        }
    }

    pub fn with_capacity(capacity: usize) -> Self {
        let secondary_capacity = capacity.saturating_div(4).max(1).min(capacity);
        Self {
            pool_32: SlabPool::with_capacity(capacity),
            pool_64: SlabPool::with_capacity(secondary_capacity),
            pool_128: SlabPool::with_capacity(secondary_capacity),
            pool_256: SlabPool::with_capacity(secondary_capacity),
            pool_overflow: SlabPool::with_capacity(secondary_capacity),
            layout_budget: None,
            live_len: 0,
            payload_bytes: 0,
        }
    }

    pub fn with_layout_budget(budget: RuntimeLayoutBudget) -> Result<Self, &'static str> {
        if !budget.is_valid() {
            return Err("invalid runtime layout budget");
        }
        let mut slab = Self::with_capacity(budget.max_live_slots());
        slab.layout_budget = Some(budget);
        Ok(slab)
    }

    pub fn insert(&mut self, val: T) -> SlotId {
        self.try_insert(val)
            .expect("runtime layout budget exceeded during slab insert")
    }

    pub fn try_insert(&mut self, val: T) -> Result<SlotId, &'static str> {
        let len = val.payload_len();
        if let Some(budget) = self.layout_budget {
            budget.check_insert(self.live_len, self.payload_bytes, len)?;
        }

        let slot_id = self.insert_unchecked(val, len);
        self.live_len += 1;
        self.payload_bytes += len;
        Ok(slot_id)
    }

    fn insert_unchecked(&mut self, val: T, len: usize) -> SlotId {
        match pool_class_for_payload_len(len) {
            32 => {
                let inner_id = self.pool_32.insert(val);
                SlotId::new(inner_id.index, inner_id.generation, 32)
            }
            64 => {
                let inner_id = self.pool_64.insert(val);
                SlotId::new(inner_id.index, inner_id.generation, 64)
            }
            128 => {
                let inner_id = self.pool_128.insert(val);
                SlotId::new(inner_id.index, inner_id.generation, 128)
            }
            256 => {
                let inner_id = self.pool_256.insert(val);
                SlotId::new(inner_id.index, inner_id.generation, 256)
            }
            _ => {
                let inner_id = self.pool_overflow.insert(val);
                SlotId::new(inner_id.index, inner_id.generation, 0)
            }
        }
    }

    pub fn get(&self, slot_id: SlotId) -> Option<&T> {
        let (index, pool_class) = slot_id.unpack();
        match pool_class {
            32 => self.pool_32.get(index, slot_id.generation),
            64 => self.pool_64.get(index, slot_id.generation),
            128 => self.pool_128.get(index, slot_id.generation),
            256 => self.pool_256.get(index, slot_id.generation),
            _ => self.pool_overflow.get(index, slot_id.generation),
        }
    }

    pub fn get_mut(&mut self, slot_id: SlotId) -> Option<&mut T> {
        if self.layout_budget.is_some() {
            return None;
        }
        self.get_mut_untracked(slot_id)
    }

    fn get_mut_untracked(&mut self, slot_id: SlotId) -> Option<&mut T> {
        let (index, pool_class) = slot_id.unpack();
        match pool_class {
            32 => self.pool_32.get_mut(index, slot_id.generation),
            64 => self.pool_64.get_mut(index, slot_id.generation),
            128 => self.pool_128.get_mut(index, slot_id.generation),
            256 => self.pool_256.get_mut(index, slot_id.generation),
            _ => self.pool_overflow.get_mut(index, slot_id.generation),
        }
    }

    pub fn try_update<F>(&mut self, slot_id: SlotId, update: F) -> Result<(), &'static str>
    where
        T: Clone,
        F: FnOnce(&mut T),
    {
        let (old_len, old_value) = {
            let value = self.get(slot_id).ok_or("slot not found")?;
            (value.payload_len(), value.clone())
        };
        let (_, old_pool_class) = slot_id.unpack();

        {
            let value = self.get_mut_untracked(slot_id).ok_or("slot not found")?;
            update(value);
        }

        let new_len = self
            .get(slot_id)
            .map(HasPayloadLen::payload_len)
            .ok_or("slot not found")?;
        let new_pool_class = pool_class_for_payload_len(new_len);
        let current_without_old = self.payload_bytes.saturating_sub(old_len);
        let next_payload_bytes = current_without_old
            .checked_add(new_len)
            .ok_or("runtime layout payload byte budget overflow")?;
        let budget_result = if new_pool_class != old_pool_class {
            Err("runtime layout pool class change requires remove and insert")
        } else if let Some(budget) = self.layout_budget {
            if next_payload_bytes > budget.max_payload_bytes() {
                Err("runtime layout payload byte budget exceeded")
            } else {
                Ok(())
            }
        } else {
            Ok(())
        };

        if let Err(err) = budget_result {
            if let Some(value) = self.get_mut_untracked(slot_id) {
                *value = old_value;
            }
            return Err(err);
        }

        self.payload_bytes = next_payload_bytes;
        Ok(())
    }

    pub fn remove(&mut self, slot_id: SlotId) -> Option<T> {
        let (index, pool_class) = slot_id.unpack();
        let removed = match pool_class {
            32 => self.pool_32.remove(index, slot_id.generation),
            64 => self.pool_64.remove(index, slot_id.generation),
            128 => self.pool_128.remove(index, slot_id.generation),
            256 => self.pool_256.remove(index, slot_id.generation),
            _ => self.pool_overflow.remove(index, slot_id.generation),
        };
        if let Some(value) = removed.as_ref() {
            self.live_len = self.live_len.saturating_sub(1);
            self.payload_bytes = self.payload_bytes.saturating_sub(value.payload_len());
        }
        removed
    }

    pub fn len(&self) -> usize {
        self.live_len
    }

    pub fn is_empty(&self) -> bool {
        self.live_len == 0
    }

    pub fn total_payload_bytes(&self) -> usize {
        if self.layout_budget.is_some() {
            self.payload_bytes
        } else {
            self.iter().map(HasPayloadLen::payload_len).sum()
        }
    }

    pub fn iter(&self) -> impl Iterator<Item = &T> {
        self.pool_32
            .entries
            .iter()
            .filter_map(|e| e.value.as_ref())
            .chain(self.pool_64.entries.iter().filter_map(|e| e.value.as_ref()))
            .chain(
                self.pool_128
                    .entries
                    .iter()
                    .filter_map(|e| e.value.as_ref()),
            )
            .chain(
                self.pool_256
                    .entries
                    .iter()
                    .filter_map(|e| e.value.as_ref()),
            )
            .chain(
                self.pool_overflow
                    .entries
                    .iter()
                    .filter_map(|e| e.value.as_ref()),
            )
    }

    pub fn iter_mut(&mut self) -> impl Iterator<Item = &mut T> {
        assert!(
            self.layout_budget.is_none(),
            "budgeted slab raw mutable iteration is disabled; use try_update"
        );
        self.pool_32
            .entries
            .iter_mut()
            .filter_map(|e| e.value.as_mut())
            .chain(
                self.pool_64
                    .entries
                    .iter_mut()
                    .filter_map(|e| e.value.as_mut()),
            )
            .chain(
                self.pool_128
                    .entries
                    .iter_mut()
                    .filter_map(|e| e.value.as_mut()),
            )
            .chain(
                self.pool_256
                    .entries
                    .iter_mut()
                    .filter_map(|e| e.value.as_mut()),
            )
            .chain(
                self.pool_overflow
                    .entries
                    .iter_mut()
                    .filter_map(|e| e.value.as_mut()),
            )
    }
}

fn pool_class_for_payload_len(len: usize) -> u16 {
    if len <= 32 {
        32
    } else if len <= 64 {
        64
    } else if len <= 128 {
        128
    } else if len <= 256 {
        256
    } else {
        0
    }
}

pub struct CogniFoldStore {
    pub frames: GenerationalSlab<MemoryFrame>,
    pub order: Vec<SlotId>,
}

pub trait MemoryCrystallization {
    fn commit_to_cognifold(
        &mut self,
        session_id: u128,
        artifact: &PhysicalArtifact,
        watchdog: &PhysicalWatchdog,
    ) -> Result<SemanticNode, BacktrackSignal>;
}

pub trait SemanticPointerResolver {
    fn resolve_pointer(&self, pointer: &SemanticPointer) -> Result<SemanticNode, &'static str>;
    fn inject_context_prefix(&self, node: &SemanticNode) -> Result<ContextPrefix, &'static str>;
}

pub trait CognitiveFolding {
    fn fold_memory(&self, graph: &mut MemoryGraph) -> Result<(), &'static str>;
    fn decay_links(&self, graph: &mut MemoryGraph) -> Result<(), &'static str>;
}

pub struct FoldingConfig {
    pub decay_rate: f32,
    pub reinforce_factor: f32,
    pub pruning_threshold: f32,
}

impl FoldingConfig {
    pub fn is_valid(&self) -> bool {
        self.decay_rate.is_finite()
            && self.decay_rate >= 0.0
            && self.reinforce_factor.is_finite()
            && self.reinforce_factor >= 0.0
            && self.pruning_threshold.is_finite()
            && self.pruning_threshold >= 0.0
    }
}

pub struct CogniFoldEngine {
    pub config: FoldingConfig,
}

impl Default for CogniFoldStore {
    fn default() -> Self {
        Self::new()
    }
}

impl CogniFoldStore {
    pub fn new() -> Self {
        Self {
            frames: GenerationalSlab::new(),
            order: Vec::new(),
        }
    }

    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            frames: GenerationalSlab::with_capacity(capacity),
            order: Vec::with_capacity(capacity),
        }
    }

    pub fn ingest(&mut self, frame: MemoryFrame) -> Result<(), &'static str> {
        if !frame.is_valid() {
            return Err("invalid memory frame");
        }
        let slot = self.frames.try_insert(frame)?;
        self.order.push(slot);
        Ok(())
    }

    pub fn ingest_many<I>(&mut self, frames: I) -> Result<usize, &'static str>
    where
        I: IntoIterator<Item = MemoryFrame>,
    {
        let mut inserted = 0usize;
        for frame in frames {
            self.ingest(frame)?;
            inserted += 1;
        }
        Ok(inserted)
    }

    pub fn validate_session(&self, session_id: u128) -> bool {
        session_id > 0
            && self
                .frames
                .iter()
                .any(|frame| frame.session_id == session_id)
    }

    pub fn latest(&self) -> Option<&MemoryFrame> {
        if let Some(&slot) = self.order.last() {
            if let Some(frame) = self.frames.get(slot) {
                return Some(frame);
            }
        }
        self.order
            .iter()
            .rev()
            .find_map(|&slot| self.frames.get(slot))
    }

    pub fn len(&self) -> usize {
        self.order.len()
    }

    pub fn is_empty(&self) -> bool {
        self.order.is_empty()
    }

    pub fn total_payload_bytes(&self) -> usize {
        self.frames.total_payload_bytes()
    }

    pub fn average_fidelity(&self) -> f32 {
        let count = self.len();
        if count == 0 {
            return 0.0;
        }
        if count == 1 {
            return self
                .latest()
                .map(|frame| frame.fidelity.clamp(0.0, 1.0))
                .unwrap_or(0.0);
        }
        let sum: f32 = self.frames.iter().map(|frame| frame.fidelity).sum();
        let average = sum / count as f32;
        average.clamp(0.0, 1.0)
    }

    pub fn reinforce_latest(&mut self, signal_strength: f32) -> Result<(), &'static str> {
        let slot = self.order.last().ok_or("no memory frames")?;
        let latest = self.frames.get_mut(*slot).ok_or("no memory frames")?;
        latest.fidelity = reinforce(latest.fidelity, signal_strength);
        Ok(())
    }

    pub fn session_summary(&self) -> Option<(u128, usize, f32)> {
        let latest = self.latest()?;
        Some((latest.session_id, self.len(), self.average_fidelity()))
    }

    /// Commit a semantic memory representation without routing it through the
    /// physical artifact/PAV authority path. The durable text remains owned by
    /// `MemoryRepository`; CogniFold stores only its bounded semantic digest.
    pub fn commit_semantic_memory(
        &mut self,
        session_id: u128,
        content: &[u8],
        relevance_score: f32,
    ) -> Result<SemanticNode, &'static str> {
        if session_id == 0
            || content.is_empty()
            || !relevance_score.is_finite()
            || !(0.0..=1.0).contains(&relevance_score)
        {
            return Err("invalid semantic memory");
        }
        let mut hasher = blake3::Hasher::new();
        hasher.update(b"aegis-semantic-memory-v1");
        hasher.update(&[0]);
        hasher.update(&session_id.to_le_bytes());
        hasher.update(content);
        let semantic_hash = *hasher.finalize().as_bytes();
        let node_id = u128::from_be_bytes(semantic_hash[..16].try_into().unwrap());
        if node_id == 0 {
            return Err("semantic memory hash produced an invalid node id");
        }
        let node = SemanticNode {
            node_id,
            session_id,
            // Historical field name retained for pointer compatibility; this
            // digest is semantic content identity, never a physical artifact.
            artifact_hash: semantic_hash,
            ast_fingerprint: 0,
            fidelity: relevance_score,
        };
        if !node.is_valid() {
            return Err("invalid semantic memory node");
        }
        self.ingest(MemoryFrame {
            frame_id: node.node_id,
            session_id,
            payload: semantic_hash.to_vec(),
            fidelity: relevance_score,
        })?;
        Ok(node)
    }
}

impl MemoryCrystallization for CogniFoldStore {
    /// Candidate-only compatibility path. Authoritative callers must use
    /// `commit_to_cognifold_with_objective` with a bound receipt.
    fn commit_to_cognifold(
        &mut self,
        session_id: u128,
        artifact: &PhysicalArtifact,
        watchdog: &PhysicalWatchdog,
    ) -> Result<SemanticNode, BacktrackSignal> {
        if session_id == 0 {
            return Err(BacktrackSignal::HardBacktrack(
                TrapReason::PavBelowThreshold,
            ));
        }
        watchdog.accepts(artifact)?;

        let node_id = u128::from_be_bytes(artifact.artifact_hash[..16].try_into().unwrap());
        let fidelity =
            (artifact.bytes_changed as f32 / artifact.fuel_consumed.max(1) as f32).clamp(0.0, 1.0);
        let node = SemanticNode {
            node_id,
            session_id,
            artifact_hash: artifact.artifact_hash,
            ast_fingerprint: artifact.ast_fingerprint,
            fidelity,
        };
        if !node.is_valid() {
            return Err(BacktrackSignal::HardBacktrack(
                TrapReason::PavBelowThreshold,
            ));
        }

        self.ingest(MemoryFrame {
            frame_id: node.node_id,
            session_id,
            payload: artifact.artifact_hash.to_vec(),
            fidelity: node.fidelity,
        })
        .map_err(|_| BacktrackSignal::HardBacktrack(TrapReason::PavBelowThreshold))?;

        Ok(node)
    }
}

impl CogniFoldStore {
    /// Authoritative memory commit requiring both the advisory PAV gate and an
    /// objective-level validation receipt. The legacy trait method remains a
    /// candidate-only compatibility path for existing callers.
    pub fn commit_to_cognifold_with_objective(
        &mut self,
        session_id: u128,
        artifact: &PhysicalArtifact,
        watchdog: &PhysicalWatchdog,
        receipt: &ObjectiveValidationReceipt,
    ) -> Result<SemanticNode, BacktrackSignal> {
        if !receipt.is_valid_for(artifact) {
            return Err(BacktrackSignal::HardBacktrack(
                TrapReason::InvariantViolation,
            ));
        }
        watchdog.accepts_with_objective(0, artifact, receipt)?;
        self.commit_to_cognifold(session_id, artifact, watchdog)
    }
}

impl SemanticPointerResolver for CogniFoldStore {
    fn resolve_pointer(&self, pointer: &SemanticPointer) -> Result<SemanticNode, &'static str> {
        if !pointer.is_valid() {
            return Err("invalid semantic pointer");
        }

        let frame = self
            .frames
            .iter()
            .find(|frame| {
                frame.frame_id == pointer.node_id
                    && frame.session_id == pointer.session_id
                    && frame.payload.as_slice() == pointer.artifact_hash
            })
            .ok_or("semantic pointer not found")?;

        let mut artifact_hash = [0u8; 32];
        artifact_hash.copy_from_slice(&frame.payload[..32]);
        let node = SemanticNode {
            node_id: frame.frame_id,
            session_id: frame.session_id,
            artifact_hash,
            ast_fingerprint: 0,
            fidelity: frame.fidelity,
        };
        if node.is_valid() {
            Ok(node)
        } else {
            Err("invalid semantic node")
        }
    }

    fn inject_context_prefix(&self, node: &SemanticNode) -> Result<ContextPrefix, &'static str> {
        if !node.is_valid() {
            return Err("invalid semantic node");
        }
        let hash_hex = hex32(&node.artifact_hash);
        let prefix = format!(
            "AEGIS_PREFIX:session={};node={};artifact_blake3={};fidelity={:.6}",
            node.session_id, node.node_id, hash_hex, node.fidelity
        );
        let context = ContextPrefix {
            session_id: node.session_id,
            node_id: node.node_id,
            prefix,
        };
        if context.is_valid() {
            Ok(context)
        } else {
            Err("invalid context prefix")
        }
    }
}

impl CognitiveFolding for CogniFoldEngine {
    fn fold_memory(&self, graph: &mut MemoryGraph) -> Result<(), &'static str> {
        if !self.config.is_valid() {
            return Err("invalid folding config");
        }
        reinforce_active_paths(graph, self.config.reinforce_factor)?;
        self.decay_links(graph)?;
        deduplicate_edges(graph);
        Ok(())
    }

    fn decay_links(&self, graph: &mut MemoryGraph) -> Result<(), &'static str> {
        if !self.config.is_valid() {
            return Err("invalid folding config");
        }
        for edge in graph.edges.iter_mut() {
            if !edge.is_valid() {
                return Err("invalid memory edge");
            }
            let decay = (-self.config.decay_rate * edge.time_since_last_access).exp();
            edge.weight = (edge.weight * decay).clamp(0.0, 1.0);
        }
        graph
            .edges
            .retain(|edge| edge.weight >= self.config.pruning_threshold);
        Ok(())
    }
}

fn reinforce_active_paths(
    graph: &mut MemoryGraph,
    reinforce_factor: f32,
) -> Result<(), &'static str> {
    if !reinforce_factor.is_finite() || reinforce_factor < 0.0 {
        return Err("invalid reinforce factor");
    }
    for edge in graph.edges.iter_mut() {
        if edge.time_since_last_access == 0.0 {
            edge.weight = (edge.weight + reinforce_factor).clamp(0.0, 1.0);
        }
    }
    Ok(())
}

fn deduplicate_edges(graph: &mut MemoryGraph) {
    graph
        .edges
        .sort_by_key(|edge| (edge.from_node, edge.to_node, edge.edge_id));
    let mut compacted: Vec<MemoryEdge> = Vec::with_capacity(graph.edges.len());
    for edge in graph.edges.drain(..) {
        // Sorting by the same endpoint pair makes all duplicates adjacent, so
        // only the last compacted edge can match. This keeps folding linear
        // after the required sort instead of rescanning all prior edges.
        if let Some(existing) = compacted.last_mut().filter(|existing| {
            existing.from_node == edge.from_node && existing.to_node == edge.to_node
        }) {
            if edge.weight > existing.weight {
                existing.weight = edge.weight;
                existing.time_since_last_access = edge.time_since_last_access;
            }
        } else {
            compacted.push(edge);
        }
    }
    graph.edges = compacted;
}

fn hex32(bytes: &[u8; 32]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(64);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}
