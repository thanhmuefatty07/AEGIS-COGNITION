#![no_main]

use aegis_nerve::agent_coordination::{AgentGraphRequest, TeamTaskDeltaBoard, validate};
use aegis_nerve::ipc::{message_to_zero_copy, validate_default_zero_copy};
use aegis_nerve::message::MessageFrame;
use libfuzzer_sys::fuzz_target;

fuzz_target!(|payload: &[u8]| {
    if let Ok(request) = serde_json::from_slice::<AgentGraphRequest>(payload) {
        let _ = validate(request);
    }

    let mut board = TeamTaskDeltaBoard::new();
    for (idx, chunk) in payload.chunks(4).take(64).enumerate() {
        let task_id = (idx as u128) + 1;
        let dep = chunk.first().copied().unwrap_or(0) as u128;
        let deps = if dep > 0 && dep != task_id {
            vec![dep]
        } else {
            Vec::new()
        };
        let lease = format!("res/slot-{}", chunk.get(1).copied().unwrap_or(0) % 16);
        let _ = board.upsert_task(task_id, deps, vec![lease]);
        let agent = format!("agent-{}", chunk.get(2).copied().unwrap_or(0) % 4);
        if board.claim_task(task_id, &agent).is_ok() {
            let _ = board.complete_task(task_id, &agent, vec![format!("@sym:{task_id}")]);
        }
    }
    let _ = board.delta_since(0);

    let msg_id = u128::from(payload.first().copied().unwrap_or(1));
    let sess_id = u128::from(payload.get(1).copied().unwrap_or(1));
    let frame = MessageFrame::with_identity(msg_id, sess_id, payload.to_vec());
    if let Ok(zc) = message_to_zero_copy(&frame) {
        let _ = validate_default_zero_copy(&zc);
    }
});
