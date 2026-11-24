pub const HALF_LIFE_DEFAULT_DAYS: f64 = 30.0;
pub const MIN_REFLECT_CORROBORATION: u32 = 2;

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ReflectDisposition {
    NeutralUnverified,
    Corroborated,
    Contested,
    Deprecated,
    StaleCodeFingerprint,
}

#[derive(Clone, Copy, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExperientialReflectVerdict {
    pub disposition: ReflectDisposition,
    pub effective_score: f32,
    pub decayed_boost: f32,
    pub corroboration_count: u32,
    pub contradiction_count: u32,
}

pub fn compute_fidelity(current: f32, reinforcement: f32, decay: f32) -> f32 {
    if !current.is_finite() || !reinforcement.is_finite() || !decay.is_finite() {
        return 0.0;
    }
    (current - decay + reinforcement).clamp(0.0, 1.0)
}

pub fn reinforce(current: f32, signal_strength: f32) -> f32 {
    compute_fidelity(current, signal_strength, 0.0)
}

pub fn decay(current: f32, rate: f32) -> f32 {
    compute_fidelity(current, 0.0, rate)
}

/// True exponential half-life decay: `w(t) = current * 2^(-elapsed_days / half_life_days)`.
pub fn decay_half_life(current: f32, elapsed_days: f64, half_life_days: f64) -> f32 {
    if !current.is_finite()
        || !elapsed_days.is_finite()
        || !half_life_days.is_finite()
        || half_life_days <= 0.0
    {
        return 0.0;
    }
    let clamped_current = current.clamp(0.0, 1.0);
    if elapsed_days <= 0.0 {
        return clamped_current;
    }
    let factor = (-std::f64::consts::LN_2 * (elapsed_days / half_life_days)).exp();
    ((clamped_current as f64) * factor).clamp(0.0, 1.0) as f32
}

/// Experiential `reflect` scoring with 30-day half-life decay, `MIN_CORROBORATION = 2`
/// promotion gate, explicit `Contested` state when supporting & contradicting outcomes
/// collide, and immediate `_code_fingerprint` staleness demotion.
pub fn compute_experiential_reflect_score(
    base_fidelity: f32,
    supporting_outcomes: u32,
    contradicting_outcomes: u32,
    elapsed_days: f64,
    half_life_days: f64,
    code_fingerprint_current: bool,
) -> ExperientialReflectVerdict {
    let base = if base_fidelity.is_finite() {
        base_fidelity.clamp(0.0, 1.0)
    } else {
        0.0
    };
    let effective_half_life = if half_life_days.is_finite() && half_life_days > 0.0 {
        half_life_days
    } else {
        HALF_LIFE_DEFAULT_DAYS
    };

    if !code_fingerprint_current {
        return ExperientialReflectVerdict {
            disposition: ReflectDisposition::StaleCodeFingerprint,
            effective_score: (base * 0.25).clamp(0.0, 0.25),
            decayed_boost: 0.0,
            corroboration_count: supporting_outcomes,
            contradiction_count: contradicting_outcomes,
        };
    }

    if supporting_outcomes > 0 && contradicting_outcomes > 0 {
        let ratio = (supporting_outcomes as f64)
            / ((supporting_outcomes as f64) + (contradicting_outcomes as f64));
        let contested_cap = ((base as f64) * (0.40 + 0.35 * ratio)).clamp(0.0, 0.70) as f32;
        return ExperientialReflectVerdict {
            disposition: ReflectDisposition::Contested,
            effective_score: contested_cap,
            decayed_boost: 0.0,
            corroboration_count: supporting_outcomes,
            contradiction_count: contradicting_outcomes,
        };
    }

    if contradicting_outcomes > 0 {
        let penalty_raw = (1.0 - (-0.45 * (contradicting_outcomes as f64)).exp()) as f32;
        let penalty = decay_half_life(penalty_raw, elapsed_days.max(0.0), effective_half_life);
        let deprecated_score = (base * (1.0 - 0.85 * penalty)).clamp(0.0, 1.0);
        return ExperientialReflectVerdict {
            disposition: ReflectDisposition::Deprecated,
            effective_score: deprecated_score,
            decayed_boost: -penalty,
            corroboration_count: 0,
            contradiction_count: contradicting_outcomes,
        };
    }

    if supporting_outcomes < MIN_REFLECT_CORROBORATION {
        return ExperientialReflectVerdict {
            disposition: ReflectDisposition::NeutralUnverified,
            effective_score: base,
            decayed_boost: 0.0,
            corroboration_count: supporting_outcomes,
            contradiction_count: 0,
        };
    }

    let raw_boost =
        (1.0 - (-0.30 * ((supporting_outcomes - 1) as f64)).exp()).clamp(0.0, 0.50) as f32;
    let decayed_boost = decay_half_life(raw_boost, elapsed_days.max(0.0), effective_half_life);
    let effective_score = (base + (1.0 - base) * decayed_boost).clamp(0.0, 1.0);
    ExperientialReflectVerdict {
        disposition: ReflectDisposition::Corroborated,
        effective_score,
        decayed_boost,
        corroboration_count: supporting_outcomes,
        contradiction_count: 0,
    }
}

pub fn stabilize_series(samples: &[f32]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    let mut total = 0.0f32;
    let mut count = 0usize;
    for sample in samples {
        if sample.is_finite() {
            total += sample.clamp(0.0, 1.0);
            count += 1;
        }
    }
    if count == 0 {
        0.0
    } else {
        (total / count as f32).clamp(0.0, 1.0)
    }
}
