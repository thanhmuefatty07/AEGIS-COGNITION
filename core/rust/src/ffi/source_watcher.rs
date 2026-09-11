//! Bounded cross-platform filesystem notifications for source projections.
//!
//! The watcher is only an invalidation hint. A caller must rescan and hash the
//! approved root before using source data. Overflow and backend errors are
//! represented explicitly so callers can fail closed to a full rescan.

use notify::{Event, RecursiveMode, Watcher};
use parking_lot::Mutex;
use pyo3::prelude::*;
use serde::Serialize;
use std::collections::{BTreeMap, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

const MAX_WATCHERS: usize = 64;
const MAX_EVENTS: usize = 2_048;
const MAX_PATH_BYTES: usize = 4_096;

struct WatchState {
    events: VecDeque<WatchEvent>,
    overflowed: bool,
}

struct WatchHandle {
    _watcher: notify::RecommendedWatcher,
    state: Arc<Mutex<WatchState>>,
}

#[derive(Clone, Serialize)]
struct WatchEvent {
    path: String,
    kind: String,
}

#[derive(Serialize)]
struct PollResult {
    schema: &'static str,
    events: Vec<WatchEvent>,
    overflowed: bool,
}

static WATCHERS: OnceLock<Mutex<BTreeMap<String, WatchHandle>>> = OnceLock::new();

fn watchers() -> &'static Mutex<BTreeMap<String, WatchHandle>> {
    WATCHERS.get_or_init(|| Mutex::new(BTreeMap::new()))
}

fn bounded_root(root: &str) -> PyResult<PathBuf> {
    if root.trim().is_empty() || root.len() > MAX_PATH_BYTES || root.contains('\0') {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "watch root must be a bounded non-empty path",
        ));
    }
    let path = PathBuf::from(root)
        .canonicalize()
        .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
    if !path.is_dir() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "watch root must be an existing directory",
        ));
    }
    Ok(path)
}

fn relative_event_path(root: &Path, path: &Path) -> Option<String> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        root.join(path)
    };
    let relative = absolute.strip_prefix(root).ok()?;
    let value = relative.to_string_lossy().replace('\\', "/");
    if value.is_empty() || value.len() > MAX_PATH_BYTES {
        return None;
    }
    Some(value)
}

fn enqueue(state: &Arc<Mutex<WatchState>>, root: &Path, event: notify::Result<Event>) {
    let mut state = state.lock();
    let Ok(event) = event else {
        state.overflowed = true;
        return;
    };
    let kind = format!("{:?}", event.kind);
    for path in event.paths {
        let Some(relative) = relative_event_path(root, &path) else {
            state.overflowed = true;
            continue;
        };
        if state.events.len() >= MAX_EVENTS {
            state.overflowed = true;
            break;
        }
        state.events.push_back(WatchEvent {
            path: relative,
            kind: kind.clone(),
        });
    }
}

/// Start a recursive watcher and return its opaque stable root token.
#[pyfunction]
pub fn aegis_start_source_watcher(root: String) -> PyResult<String> {
    let path = bounded_root(&root)?;
    let token = blake3::hash(path.to_string_lossy().as_bytes())
        .to_hex()
        .to_string();
    let mut registry = watchers().lock();
    if registry.contains_key(&token) {
        return Ok(token);
    }
    if registry.len() >= MAX_WATCHERS {
        return Err(pyo3::exceptions::PyRuntimeError::new_err(
            "source watcher limit reached",
        ));
    }
    let state = Arc::new(Mutex::new(WatchState {
        events: VecDeque::new(),
        overflowed: false,
    }));
    let callback_state = Arc::clone(&state);
    let callback_root = path.clone();
    let mut watcher = notify::recommended_watcher(move |event| {
        enqueue(&callback_state, &callback_root, event);
    })
    .map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))?;
    watcher
        .watch(&path, RecursiveMode::Recursive)
        .map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))?;
    registry.insert(
        token.clone(),
        WatchHandle {
            _watcher: watcher,
            state,
        },
    );
    Ok(token)
}

/// Drain at most `max_events` invalidation hints from a watcher.
#[pyfunction]
pub fn aegis_poll_source_watcher(token: String, max_events: usize) -> PyResult<String> {
    if token.trim().is_empty() || token.len() > 128 || max_events == 0 || max_events > MAX_EVENTS {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "invalid source watcher poll arguments",
        ));
    }
    let registry = watchers().lock();
    let Some(handle) = registry.get(&token) else {
        return Err(pyo3::exceptions::PyKeyError::new_err(
            "source watcher does not exist",
        ));
    };
    let mut state = handle.state.lock();
    let take = max_events.min(state.events.len());
    let events = state.events.drain(..take).collect();
    let result = PollResult {
        schema: "aegis-source-watch-events-v1",
        events,
        overflowed: state.overflowed,
    };
    state.overflowed = false;
    serde_json::to_string(&result)
        .map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))
}

/// Stop a watcher. Repeated stops are idempotent.
#[pyfunction]
pub fn aegis_stop_source_watcher(token: String) -> PyResult<bool> {
    if token.len() > 128 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "source watcher token is too long",
        ));
    }
    Ok(watchers().lock().remove(&token).is_some())
}
