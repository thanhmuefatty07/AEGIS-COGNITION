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
