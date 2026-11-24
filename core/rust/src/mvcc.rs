use arrow::record_batch::RecordBatch;
use parking_lot::RwLock;
use std::collections::BTreeMap;
use std::sync::Arc;

pub type StateVersion = u64;

/// Represents a versioned snapshot of the Arrow RecordBatch state.
/// Using Arc ensures that cloning the state for read snapshots is extremely cheap (O(1)).
#[derive(Clone, Debug)]
pub struct MvccState {
    pub version: StateVersion,
    pub payload: Arc<RecordBatch>,
}

impl MvccState {
    pub fn new(version: StateVersion, payload: RecordBatch) -> Self {
        Self {
            version,
            payload: Arc::new(payload),
        }
    }
}

/// The central MVCC Store providing thread-safe, low-contention `RwLock` reads for
/// Copy-on-Write snapshots plus epoch/version reader pinning so garbage collection
/// never reclaims snapshots held by active readers.
pub struct MvccStore {
    states: RwLock<BTreeMap<StateVersion, MvccState>>,
    pin_counts: parking_lot::Mutex<BTreeMap<StateVersion, usize>>,
    latest_version: parking_lot::Mutex<StateVersion>,
}

/// RAII reader guard that pins a specific `StateVersion` in an `MvccStore` until dropped.
pub struct MvccPinnedSnapshot<'a> {
    store: &'a MvccStore,
    state: MvccState,
}

impl<'a> MvccPinnedSnapshot<'a> {
    pub fn state(&self) -> &MvccState {
        &self.state
    }

    pub fn version(&self) -> StateVersion {
        self.state.version
    }

    pub fn payload(&self) -> &Arc<RecordBatch> {
        &self.state.payload
    }
}

impl Drop for MvccPinnedSnapshot<'_> {
    fn drop(&mut self) {
        self.store.unpin_version(self.state.version);
    }
}

impl Default for MvccStore {
    fn default() -> Self {
        Self::new()
    }
}

impl MvccStore {
    pub fn new() -> Self {
        Self {
            states: RwLock::new(BTreeMap::new()),
            pin_counts: parking_lot::Mutex::new(BTreeMap::new()),
            latest_version: parking_lot::Mutex::new(0),
        }
    }

    /// Appends a new version of the RecordBatch to the store.
    /// This increments the version counter and stores the new Copy-on-Write state.
    pub fn append_version(&self, batch: RecordBatch) -> StateVersion {
        let mut latest = self.latest_version.lock();
        *latest += 1;
        let new_version = *latest;

        let state = MvccState::new(new_version, batch);

        let mut states_write = self.states.write();
        states_write.insert(new_version, state);

        new_version
    }

    /// Retrieves a specific snapshot by its version number.
    /// The returned `MvccState` contains an Arc to the RecordBatch, making it zero-copy
    /// and completely isolated from future writes.
    pub fn read_snapshot(&self, version: StateVersion) -> Option<MvccState> {
        let states_read = self.states.read();
        states_read.get(&version).cloned()
    }

    /// Pin and retrieve a specific snapshot version so `prune_unpinned_before`
    /// cannot evict it or any newer version while the guard is alive.
    pub fn read_pinned_snapshot(&self, version: StateVersion) -> Option<MvccPinnedSnapshot<'_>> {
        let states_read = self.states.read();
        let state = states_read.get(&version)?.clone();
        let mut pins = self.pin_counts.lock();
        *pins.entry(version).or_insert(0) += 1;
        drop(pins);
        drop(states_read);
        Some(MvccPinnedSnapshot { store: self, state })
    }

    /// Pin and retrieve the latest available snapshot atomically.
    pub fn read_pinned_latest(&self) -> Option<MvccPinnedSnapshot<'_>> {
        let states_read = self.states.read();
        let (&version, state_ref) = states_read.last_key_value()?;
        let state = state_ref.clone();
        let mut pins = self.pin_counts.lock();
        *pins.entry(version).or_insert(0) += 1;
        drop(pins);
        drop(states_read);
        Some(MvccPinnedSnapshot { store: self, state })
    }

    fn unpin_version(&self, version: StateVersion) {
        let mut pins = self.pin_counts.lock();
        if let Some(count) = pins.get_mut(&version) {
            *count = count.saturating_sub(1);
            if *count == 0 {
                pins.remove(&version);
            }
        }
    }

    /// Minimum currently pinned version across all active readers, if any.
    pub fn min_pinned_version(&self) -> Option<StateVersion> {
        self.pin_counts.lock().keys().next().copied()
    }

    /// Retrieves the most recent snapshot available in the store.
    pub fn latest_version(&self) -> Option<MvccState> {
        let states_read = self.states.read();
        states_read.last_key_value().map(|(_, state)| state.clone())
    }

    /// Prunes states older than the given version (`< version`).
    /// Also respects active `MvccPinnedSnapshot` readers by never pruning a
    /// version at or above `min_pinned_version`.
    pub fn prune_before(&self, version: StateVersion) {
        let _ = self.prune_unpinned_before(version);
    }

    /// Reclaim snapshots strictly older than `min(version, min_pinned_version)`,
    /// returning the number of evicted versions.
    pub fn prune_unpinned_before(&self, version: StateVersion) -> usize {
        let mut states_write = self.states.write();
        let pins = self.pin_counts.lock();
        let effective_floor = match pins.keys().next().copied() {
            Some(min_pinned) => version.min(min_pinned),
            None => version,
        };
        let before = states_write.len();
        states_write.retain(|&k, _| k >= effective_floor || pins.contains_key(&k));
        before.saturating_sub(states_write.len())
    }
}

/// Isolated per-agent Copy-on-Write worktree delta overlay atop an `MvccStore`,
/// detecting optimistic write-write path conflicts before commit.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AgentFileDelta {
    pub relative_path: String,
    pub content_blake3: [u8; 32],
}

#[derive(Clone, Debug)]
struct AgentBranchState {
    base_version: StateVersion,
    staged_paths: BTreeMap<String, [u8; 32]>,
    staged_batch: Option<RecordBatch>,
}

pub struct WorktreeMvccOverlay {
    store: Arc<MvccStore>,
    branches: RwLock<BTreeMap<String, AgentBranchState>>,
    committed_path_versions: RwLock<BTreeMap<String, (StateVersion, [u8; 32])>>,
}

impl WorktreeMvccOverlay {
    pub fn new(store: Arc<MvccStore>) -> Self {
        Self {
            store,
            branches: RwLock::new(BTreeMap::new()),
            committed_path_versions: RwLock::new(BTreeMap::new()),
        }
    }

    pub fn fork_branch(&self, agent_id: &str) -> Result<StateVersion, &'static str> {
        if agent_id.trim().is_empty() {
            return Err("agent_id cannot be empty");
        }
        let base_version = self.store.latest_version().map(|s| s.version).unwrap_or(0);
        self.branches.write().insert(
            agent_id.to_string(),
            AgentBranchState {
                base_version,
                staged_paths: BTreeMap::new(),
                staged_batch: None,
            },
        );
        Ok(base_version)
    }

    pub fn stage_delta(
        &self,
        agent_id: &str,
        deltas: &[AgentFileDelta],
        batch: RecordBatch,
    ) -> Result<(), &'static str> {
        let mut branches = self.branches.write();
        let branch = branches
            .get_mut(agent_id)
            .ok_or("agent branch is not forked")?;
        for delta in deltas {
            if delta.relative_path.trim().is_empty() {
                return Err("delta relative_path cannot be empty");
            }
            branch
                .staged_paths
                .insert(delta.relative_path.clone(), delta.content_blake3);
        }
        branch.staged_batch = Some(batch);
        Ok(())
    }

    /// Commit an agent's staged branch into the central `MvccStore` only if no
    /// overlapping path was committed after `branch.base_version`.
    pub fn commit_branch(&self, agent_id: &str) -> Result<StateVersion, &'static str> {
        let mut branches = self.branches.write();
        let branch = branches
            .remove(agent_id)
            .ok_or("agent branch is not forked")?;
        let batch = branch
            .staged_batch
            .ok_or("agent branch has no staged batch")?;

        let mut path_versions = self.committed_path_versions.write();
        for path in branch.staged_paths.keys() {
            if let Some((committed_ver, _)) = path_versions.get(path) {
                if *committed_ver > branch.base_version {
                    return Err("mvcc write-write path conflict detected");
                }
            }
        }
        let new_version = self.store.append_version(batch);
        for (path, digest) in branch.staged_paths {
            path_versions.insert(path, (new_version, digest));
        }
        Ok(new_version)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::{Int32Array, StringArray};
    use arrow::datatypes::{DataType, Field, Schema};
    use std::sync::Arc;

    fn create_dummy_batch() -> RecordBatch {
        let schema = Schema::new(vec![
            Field::new("id", DataType::Int32, false),
            Field::new("name", DataType::Utf8, false),
        ]);

        let id_array = Int32Array::from(vec![1, 2, 3]);
        let name_array = StringArray::from(vec!["Aegis", "Nerve", "Cell"]);

        RecordBatch::try_new(
            Arc::new(schema),
            vec![Arc::new(id_array), Arc::new(name_array)],
        )
        .unwrap()
    }

    #[test]
    fn test_mvcc_append_and_read() {
        let store = MvccStore::new();
        let batch1 = create_dummy_batch();

        let v1 = store.append_version(batch1);
        assert_eq!(v1, 1);

        let snapshot = store.read_snapshot(v1).expect("Snapshot should exist");
        assert_eq!(snapshot.version, v1);
        assert_eq!(snapshot.payload.num_rows(), 3);

        let batch2 = create_dummy_batch();
        let v2 = store.append_version(batch2);
        assert_eq!(v2, 2);

        let latest = store
            .latest_version()
            .expect("Latest snapshot should exist");
        assert_eq!(latest.version, 2);
    }

    #[test]
    fn test_mvcc_prune() {
        let store = MvccStore::new();
        let batch1 = create_dummy_batch();
        let batch2 = create_dummy_batch();

        store.append_version(batch1);
        let v2 = store.append_version(batch2);

        store.prune_before(v2);

        assert!(store.read_snapshot(1).is_none());
        assert!(store.read_snapshot(2).is_some());
    }
}
