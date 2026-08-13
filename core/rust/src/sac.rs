use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SacAnchor {
    pub anchor_id: u128,
    pub last_witness_hash: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SacVote {
    pub score: i32,
    pub suspicious: bool,
    pub quorum_threshold: i32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PhysicalWitnessVote {
    pub artifact_hash: [u8; 32],
    pub ast_fingerprint: u64,
    pub fuel_consumed: u64,
    pub suspicious: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SacPhysicalWitness {
    pub artifact_hash: [u8; 32],
    pub ast_fingerprint: u64,
    pub fuel_consumed: u64,
    pub witness_id: u128,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WitnessProof {
    pub anchor: SacAnchor,
    pub blake3_hash: [u8; 32],
    pub ast_fingerprint: u64,
    pub witness_count: usize,
    pub fuel_consumed: u64,
}

type WitnessGroupKey = ([u8; 32], u64);
type WitnessGroup<'a> = (WitnessGroupKey, Vec<&'a PhysicalWitnessVote>);

impl SacVote {
    pub fn is_valid(&self) -> bool {
        self.quorum_threshold >= 0 && self.score >= 0
    }
}

impl PhysicalWitnessVote {
    pub fn from_artifact(artifact: &crate::physical::PhysicalArtifact) -> Self {
        Self {
            artifact_hash: artifact.artifact_hash,
            ast_fingerprint: artifact.ast_fingerprint,
            fuel_consumed: artifact.fuel_consumed,
            suspicious: false,
        }
    }

    pub fn from_payload(payload: &[u8], fuel_consumed: u64) -> Result<Self, &'static str> {
        if payload.is_empty() || fuel_consumed == 0 {
            return Err("invalid physical vote payload");
        }
        let artifact_hash = crate::physical::blake3_digest(payload);
        let mut fingerprint_bytes = [0u8; 8];
        fingerprint_bytes.copy_from_slice(&artifact_hash[..8]);
        let ast_fingerprint = u64::from_le_bytes(fingerprint_bytes);
        Ok(Self {
            artifact_hash,
            ast_fingerprint,
            fuel_consumed,
            suspicious: false,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.fuel_consumed > 0 && self.artifact_hash.iter().any(|byte| *byte != 0)
    }
}

impl SacPhysicalWitness {
    pub fn from_vote(witness_id: u128, vote: PhysicalWitnessVote) -> Self {
        Self {
            artifact_hash: vote.artifact_hash,
            ast_fingerprint: vote.ast_fingerprint,
            fuel_consumed: vote.fuel_consumed,
            witness_id,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.witness_id > 0
            && self.fuel_consumed > 0
            && self.artifact_hash.iter().any(|byte| *byte != 0)
    }
}

pub fn accept_vote(score: i32, quorum_threshold: i32) -> bool {
    score >= 0 && quorum_threshold >= 0 && score >= quorum_threshold
}

pub fn anchored_witness(
    score: i32,
    quorum_threshold: i32,
    last_witness_hash: u64,
) -> Result<SacAnchor, &'static str> {
    if !accept_vote(score, quorum_threshold) {
        return Err("witness rejected");
    }

    Ok(SacAnchor {
        anchor_id: 1,
        last_witness_hash,
    })
}

pub fn filter_vote(
    score: i32,
    suspicious: bool,
    quorum_threshold: i32,
) -> Result<SacAnchor, &'static str> {
    if suspicious {
        return Err("vote quarantined");
    }
    if score < 0 || quorum_threshold < 0 {
        return Err("invalid vote inputs");
    }
    anchored_witness(score, quorum_threshold, 0)
}

pub fn update_anchor(anchor: &mut SacAnchor, new_hash: u64) {
    anchor.last_witness_hash = new_hash;
}

pub fn witness_from_vote(
    vote: &SacVote,
    last_witness_hash: u64,
) -> Result<SacAnchor, &'static str> {
    if !vote.is_valid() {
        return Err("invalid vote");
    }
    if vote.suspicious {
        return Err("vote quarantined");
    }
    anchored_witness(vote.score, vote.quorum_threshold, last_witness_hash)
}

pub fn anchor_fingerprint(anchor: &SacAnchor) -> u64 {
    anchor.last_witness_hash ^ (anchor.anchor_id as u64)
}

pub fn witness_verification_round(
    votes: &[SacVote],
    last_witness_hash: u64,
) -> Result<SacAnchor, &'static str> {
    if votes.is_empty() {
        return Err("no votes");
    }

    let mut accepted = 0i32;
    let mut threshold_sum = 0i32;
    let mut eligible = 0i32;
    for vote in votes {
        if vote.is_valid() && !vote.suspicious {
            accepted += vote.score;
            threshold_sum += vote.quorum_threshold;
            eligible += 1;
        }
    }

    if eligible == 0 {
        return Err("no eligible votes");
    }

    let quorum_threshold = threshold_sum / eligible;
    anchored_witness(accepted, quorum_threshold, last_witness_hash)
}

pub fn verify_physical_witness(
    votes: &[PhysicalWitnessVote],
    total_witnesses: usize,
    last_witness_hash: u64,
) -> Result<WitnessProof, &'static str> {
    if votes.is_empty() || total_witnesses == 0 {
        return Err("no physical votes");
    }

    let required_matches = physical_witness_threshold(total_witnesses);
    if total_witnesses == 1 && votes.len() == 1 {
        let vote = votes[0];
        if vote.is_valid() && !vote.suspicious {
            let anchor = physical_anchor(
                vote.artifact_hash,
                vote.ast_fingerprint,
                vote.fuel_consumed,
                1,
                last_witness_hash,
            );
            return Ok(WitnessProof {
                anchor,
                blake3_hash: vote.artifact_hash,
                ast_fingerprint: vote.ast_fingerprint,
                witness_count: 1,
                fuel_consumed: vote.fuel_consumed,
            });
        }
        return Err("physical witness rejected");
    }

    let mut groups: BTreeMap<WitnessGroupKey, Vec<&PhysicalWitnessVote>> = BTreeMap::new();
    for vote in votes {
        if vote.is_valid() && !vote.suspicious {
            groups
                .entry((vote.artifact_hash, vote.ast_fingerprint))
                .or_default()
                .push(vote);
        }
    }

    let mut winner: Option<WitnessGroup<'_>> = None;
    for (key, group) in groups {
        if group.len() < required_matches {
            continue;
        }
        if let Some((_, best_group)) = &winner {
            if group.len() <= best_group.len() {
                continue;
            }
        }
        winner = Some((key, group));
    }

    let ((artifact_hash, ast_fingerprint), group) = winner.ok_or("physical witness rejected")?;
    let fuel_consumed = group
        .iter()
        .map(|vote| vote.fuel_consumed)
        .max()
        .unwrap_or(0);
    let anchor = physical_anchor(
        artifact_hash,
        ast_fingerprint,
        fuel_consumed,
        group.len(),
        last_witness_hash,
    );

    Ok(WitnessProof {
        anchor,
        blake3_hash: artifact_hash,
        ast_fingerprint,
        witness_count: group.len(),
        fuel_consumed,
    })
}

fn physical_witness_threshold(total_witnesses: usize) -> usize {
    let f = total_witnesses.saturating_sub(1) / 3;
    f + 1
}

fn physical_anchor(
    artifact_hash: [u8; 32],
    ast_fingerprint: u64,
    fuel_consumed: u64,
    quorum_size: usize,
    last_witness_hash: u64,
) -> SacAnchor {
    let mut head = [0u8; 8];
    let mut tail = [0u8; 8];
    head.copy_from_slice(&artifact_hash[..8]);
    tail.copy_from_slice(&artifact_hash[8..16]);
    let witness_hash = u64::from_le_bytes(head).rotate_left(17)
        ^ u64::from_le_bytes(tail).rotate_left(7)
        ^ ast_fingerprint.rotate_left(31)
        ^ fuel_consumed.rotate_left(13)
        ^ (quorum_size as u64).rotate_left(3)
        ^ last_witness_hash.rotate_left(5);
    SacAnchor {
        anchor_id: quorum_size as u128,
        last_witness_hash: witness_hash,
    }
}
