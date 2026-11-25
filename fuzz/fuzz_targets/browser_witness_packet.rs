#![no_main]

use aegis_nerve::browser_witness::{
    BrowserActionKind, BrowserActionPlanKind, BrowserActionPlanRecord, BrowserActionTrace,
    BrowserCollectorKind, BrowserObservationPacket, BrowserPromptDecision, BrowserWitnessProof,
};
use aegis_nerve::policy::SideEffectClass;
use libfuzzer_sys::fuzz_target;

fn hash32(bytes: &[u8], offset: usize) -> [u8; 32] {
    let mut out = [0u8; 32];
    for (idx, slot) in out.iter_mut().enumerate() {
        *slot = bytes.get(offset + idx).copied().unwrap_or(0);
    }
    out
}

fuzz_target!(|payload: &[u8]| {
    let b0 = payload.first().copied().unwrap_or(0);
    let b1 = payload.get(1).copied().unwrap_or(0);
    let b2 = payload.get(2).copied().unwrap_or(0);
    let b3 = payload.get(3).copied().unwrap_or(0);

    let action_kind = match b0 % 9 {
        0 => BrowserActionKind::Navigate,
        1 => BrowserActionKind::Click,
        2 => BrowserActionKind::TypeText,
        3 => BrowserActionKind::SelectOption,
        4 => BrowserActionKind::Download,
        5 => BrowserActionKind::PermissionPrompt,
        6 => BrowserActionKind::StateSnapshot,
        7 => BrowserActionKind::SearchPage,
        _ => BrowserActionKind::FindElements,
    };
    let plan_kind = match b1 % 9 {
        0 => BrowserActionPlanKind::Navigate,
        1 => BrowserActionPlanKind::StateSnapshot,
        2 => BrowserActionPlanKind::SearchPage,
        3 => BrowserActionPlanKind::FindElements,
        4 => BrowserActionPlanKind::Click,
        5 => BrowserActionPlanKind::InputText,
        6 => BrowserActionPlanKind::SelectOption,
        7 => BrowserActionPlanKind::Download,
        _ => BrowserActionPlanKind::PermissionPrompt,
    };
    let collector_kind = match b2 % 4 {
        0 => BrowserCollectorKind::InAppBrowser,
        1 => BrowserCollectorKind::ChromeExtension,
        2 => BrowserCollectorKind::ComputerUse,
        _ => BrowserCollectorKind::PlaywrightCdp,
    };
    let prompt_decision = match b3 % 4 {
        0 => Some(BrowserPromptDecision::Deny),
        1 => Some(BrowserPromptDecision::Allow),
        2 => Some(BrowserPromptDecision::Dismiss),
        _ => None,
    };
    let input_hash = if (b0 & 0x80) != 0 {
        Some(hash32(payload, 4))
    } else {
        None
    };
    let coords = if (b1 & 0x80) != 0 {
        Some((i32::from(b2), i32::from(b3)))
    } else {
        None
    };
    let run_id = u128::from(b0);
    let action_id = u128::from(b1);
    let seq = u64::from(b2);
    let policy_window = hash32(payload, 8);
    let session_hash = hash32(payload, 12);
    let redaction_hash = hash32(payload, 16);

    let trace = BrowserActionTrace::new(
        action_id,
        action_kind,
        hash32(payload, 20),
        input_hash,
        coords,
        prompt_decision,
        policy_window,
    );
    let _ = trace.is_valid();

    let plan = BrowserActionPlanRecord::new(
        run_id,
        action_id,
        plan_kind,
        seq,
        hash32(payload, 24),
        hash32(payload, 28),
        hash32(payload, 32),
        input_hash,
        coords,
        prompt_decision,
        policy_window,
        session_hash,
        redaction_hash,
        (b3 & 0x80) != 0,
    );
    let _ = plan.is_valid();

    let proof = BrowserWitnessProof::new(
        run_id,
        action_id,
        SideEffectClass::ExternalRead,
        hash32(payload, 4),
        hash32(payload, 8),
        hash32(payload, 12),
        hash32(payload, 16),
        hash32(payload, 20),
        hash32(payload, 24),
        hash32(payload, 28),
        hash32(payload, 32),
        trace.trace_hash,
        policy_window,
        session_hash,
        redaction_hash,
    );
    let _ = proof.is_valid();

    let packet = BrowserObservationPacket::new(
        collector_kind,
        seq,
        u64::from(b3) * 1_000,
        hash32(payload, 36),
        hash32(payload, 40),
        hash32(payload, 44),
        trace,
        proof,
    );
    let _ = packet.is_valid();
    let _ = plan.is_valid_for_packet(hash32(payload, 24), &packet);
});
