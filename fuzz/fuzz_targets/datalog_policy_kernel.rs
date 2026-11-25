#![no_main]

use aegis_nerve::policy::{
    CapabilityClass, DeterministicPolicyKernel, EvidenceContract, PolicyDecision, PolicyFacts,
    RiskClass, SideEffectClass, StagingEvidenceKind, TypedToolIR,
};
use libfuzzer_sys::fuzz_target;

fn hash32(bytes: &[u8], offset: usize, force_nonzero: bool) -> [u8; 32] {
    let mut out = [0u8; 32];
    for (idx, slot) in out.iter_mut().enumerate() {
        *slot = bytes.get(offset + idx).copied().unwrap_or(0);
    }
    if force_nonzero && out.iter().all(|&b| b == 0) {
        out[0] = 1;
    }
    out
}

fuzz_target!(|payload: &[u8]| {
    let b0 = payload.first().copied().unwrap_or(0);
    let b1 = payload.get(1).copied().unwrap_or(0);
    let b2 = payload.get(2).copied().unwrap_or(0);
    let b3 = payload.get(3).copied().unwrap_or(0);
    let force_nonzero = (b0 & 0x80) != 0;

    let risk = match b0 % 5 {
        0 => RiskClass::R0,
        1 => RiskClass::R1,
        2 => RiskClass::R2,
        3 => RiskClass::R3,
        _ => RiskClass::R4,
    };
    let capability = match b1 % 9 {
        0 => CapabilityClass::LocalRead,
        1 => CapabilityClass::LocalWrite,
        2 => CapabilityClass::ExternalRead,
        3 => CapabilityClass::ExternalWrite,
        4 => CapabilityClass::Browser,
        5 => CapabilityClass::Computer,
        6 => CapabilityClass::Inference,
        7 => CapabilityClass::Retrieval,
        _ => CapabilityClass::FinancialLegal,
    };
    let side_effect = match b2 % 6 {
        0 => SideEffectClass::None,
        1 => SideEffectClass::LocalReversible,
        2 => SideEffectClass::ExternalRead,
        3 => SideEffectClass::ExternalWrite,
        4 => SideEffectClass::Destructive,
        _ => SideEffectClass::FinancialLegal,
    };
    let staging_kind = match b3 % 6 {
        0 => StagingEvidenceKind::SandboxExecution,
        1 => StagingEvidenceKind::TestnetExecution,
        2 => StagingEvidenceKind::IsolatedSnapshotExecution,
        3 => StagingEvidenceKind::IsolatedBrowserSession,
        4 => StagingEvidenceKind::LlmGeneratedText,
        _ => StagingEvidenceKind::MockLog,
    };

    let requires_approval = (b0 & 0x08) != 0;
    let requires_staging = (b0 & 0x10) != 0;
    let requires_physical = (b0 & 0x20) != 0;
    let has_valid_approval = (b1 & 0x08) != 0;
    let has_staging_environment = (b1 & 0x10) != 0;
    let has_hitl_signature = (b1 & 0x20) != 0;

    let contract = if requires_staging || requires_approval {
        EvidenceContract::r4_staged()
    } else if requires_physical {
        EvidenceContract::physical()
    } else {
        EvidenceContract {
            requires_physical_witness: false,
            requires_approval: false,
            requires_staging: false,
            expected_artifact_hash: if (b2 & 0x40) != 0 {
                Some(hash32(payload, 4, force_nonzero))
            } else {
                None
            },
        }
    };
    let ir = TypedToolIR::new(
        101,
        202,
        capability,
        side_effect,
        hash32(payload, 8, force_nonzero),
        risk,
        hash32(payload, 16, force_nonzero),
        hash32(payload, 24, force_nonzero),
        contract,
        if (b3 & 0x40) != 0 {
            Some(hash32(payload, 32, force_nonzero))
        } else {
            None
        },
    );
    let facts = match b3 % 3 {
        0 => PolicyFacts::with_staging_evidence(
            hash32(payload, 40, force_nonzero),
            has_valid_approval,
            hash32(payload, 48, force_nonzero),
            staging_kind,
        ),
        1 => PolicyFacts::with_hitl_signature(
            hash32(payload, 40, force_nonzero),
            has_valid_approval,
            hash32(payload, 56, force_nonzero),
        ),
        _ => PolicyFacts::new(
            hash32(payload, 40, force_nonzero),
            has_valid_approval,
            has_staging_environment,
            has_hitl_signature,
        ),
    };

    let kernel = DeterministicPolicyKernel;
    let (trace, closure) = kernel.evaluate_with_datalog(&ir, &facts);
    assert!(closure.is_valid_for(&ir, &facts));
    if ir.is_valid() && facts.is_valid() {
        assert!(trace.is_replay_consistent_with(&ir));
    } else {
        assert_eq!(trace.decision, PolicyDecision::HardBlock);
    }
    if risk == RiskClass::R4
        && !facts.has_hitl_signature
        && !(facts.has_staging_environment
            && facts
                .staging_evidence_kind
                .is_some_and(StagingEvidenceKind::is_physical))
    {
        assert_ne!(trace.decision, PolicyDecision::Allow);
    }
});
