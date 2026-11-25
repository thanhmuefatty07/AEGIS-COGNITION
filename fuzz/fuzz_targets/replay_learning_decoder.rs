#![no_main]

use aegis_nerve::learning::{LearningEventType, LearningLedger};
use aegis_nerve::replay::{BinaryRunEventSegment, RunEvent, RunEventLedger};
use libfuzzer_sys::fuzz_target;
use std::time::{SystemTime, UNIX_EPOCH};

fuzz_target!(|payload: &[u8]| {
    let mut learning = LearningLedger::new();
    for (idx, chunk) in payload.chunks(32).take(32).enumerate() {
        let mut hash = [0u8; 32];
        for (i, slot) in hash.iter_mut().enumerate() {
            *slot = chunk.get(i).copied().unwrap_or(0);
        }
        let kind = match chunk.first().copied().unwrap_or(0) % 8 {
            0 => LearningEventType::SkillCreated { task_hash: hash },
            1 => LearningEventType::SkillImproved {
                usage_count: u32::from(chunk.get(1).copied().unwrap_or(10)),
                improvement_hash: hash,
            },
            2 => LearningEventType::MemoryCandidateCreated {
                candidate_hash: hash,
            },
            3 => LearningEventType::MemoryCommitted { memory_hash: hash },
            4 => LearningEventType::MemoryProjectionCommitted { memory_hash: hash },
            5 => LearningEventType::MemoryPersisted { memory_hash: hash },
            6 => LearningEventType::UserModelUpdated { model_hash: hash },
            _ => LearningEventType::SessionRecallIndexed { session_hash: hash },
        };
        let ts = 1_700_000_000_000 + (idx as u64) + 1;
        let _ = learning.append(kind, Some((idx as u128) + 1), ts, 1);
    }
    let _ = learning.verify_integrity();

    // Verify RunEvent hash chaining
    let run_id = 42u128;
    let mut events = Vec::new();
    let mut prev = [0u8; 32];
    for (idx, chunk) in payload.chunks(16).take(16).enumerate() {
        let mut h = [1u8; 32];
        for (i, slot) in h.iter_mut().enumerate() {
            *slot = chunk.get(i).copied().unwrap_or((idx as u8).wrapping_add(1));
        }
        if h.iter().all(|&b| b == 0) {
            h[0] = 1;
        }
        let ev = RunEvent::checkpoint_sealed(
            (idx as u64) + 1,
            run_id,
            (idx as u128) + 10,
            h,
            prev,
        );
        let _ = ev.is_valid();
        prev = ev.event_hash;
        events.push(ev);
    }
    if let Ok(ledger) = RunEventLedger::from_events(run_id, events) {
        let _ = ledger.verify_hash_chain();
        let _ = ledger.last_hash();
    }

    // Exercise BinaryRunEventSegment mmap decoder safely ONLY on valid magic prefix inputs.
    if payload.len() >= 88 && payload.starts_with(b"AEGRUN06") {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let temp_path = std::env::temp_dir().join(format!(
            "aegis-fuzz-seg-{}-{}.bin",
            std::process::id(),
            nanos
        ));
        if std::fs::write(&temp_path, payload).is_ok() {
            let expected_hash = [1u8; 32];
            let _ = BinaryRunEventSegment::verify_mmap(&temp_path, expected_hash);
            if let Err(_err) = std::fs::remove_file(&temp_path) {}
        }
    }
});
