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

/// The central MVCC Store providing thread-safe, wait-free reads for Copy-on-Write snapshots.
/// We use RwLock to allow multiple Wasm workers to acquire read locks simultaneously without blocking,
/// while an exclusive write lock is required only momentarily to append a new state version.
pub struct MvccStore {
    states: RwLock<BTreeMap<StateVersion, MvccState>>,
    latest_version: parking_lot::Mutex<StateVersion>,
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

    /// Retrieves the most recent snapshot available in the store.
    pub fn latest_version(&self) -> Option<MvccState> {
        let states_read = self.states.read();
        states_read.last_key_value().map(|(_, state)| state.clone())
    }

    /// Prunes states older than the given version to prevent unbounded memory growth.
    pub fn prune_before(&self, version: StateVersion) {
        let mut states_write = self.states.write();
        states_write.retain(|&k, _| k >= version);
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
