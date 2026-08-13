use pulldown_cmark::{Event, Parser};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum SkillError {
    #[error("invalid skill")]
    InvalidSkill,
    #[error("skill exceeds token budget")]
    TokenBudgetExceeded,
    #[error("skill not found")]
    NotFound,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Skill {
    pub name: String,
    pub description: String,
    pub version: String,
    pub content: String,
    pub content_hash: [u8; 32],
}

#[derive(Clone, Debug, Default)]
pub struct SkillsRegistry {
    skills: BTreeMap<String, Skill>,
}

impl Skill {
    pub fn new(
        name: impl Into<String>,
        description: impl Into<String>,
        version: impl Into<String>,
        content: impl Into<String>,
    ) -> Result<Self, SkillError> {
        let content = content.into();
        if content.trim().is_empty() {
            return Err(SkillError::InvalidSkill);
        }
        if estimate_tokens(&content) > 2_000 {
            return Err(SkillError::TokenBudgetExceeded);
        }
        let mut skill = Self {
            name: name.into(),
            description: description.into(),
            version: version.into(),
            content,
            content_hash: [0; 32],
        };
        if skill.name.trim().is_empty() || skill.version.trim().is_empty() {
            return Err(SkillError::InvalidSkill);
        }
        skill.content_hash = skill.compute_hash();
        Ok(skill)
    }

    pub fn headings(&self) -> Vec<String> {
        Parser::new(&self.content)
            .filter_map(|event| match event {
                Event::Text(text) => Some(text.to_string()),
                _ => None,
            })
            .collect()
    }

    pub fn compute_hash(&self) -> [u8; 32] {
        let mut hasher = blake3::Hasher::new();
        hasher.update(b"aegis-skill-v1");
        hasher.update(self.name.as_bytes());
        hasher.update(self.version.as_bytes());
        hasher.update(self.content.as_bytes());
        *hasher.finalize().as_bytes()
    }
}

impl SkillsRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_builtin_skills() -> Result<Self, SkillError> {
        let mut registry = Self::new();
        for skill in builtin_skills()? {
            registry.register(skill)?;
        }
        Ok(registry)
    }

    pub fn register(&mut self, skill: Skill) -> Result<(), SkillError> {
        if skill.content_hash != skill.compute_hash() {
            return Err(SkillError::InvalidSkill);
        }
        self.skills.insert(skill.name.clone(), skill);
        Ok(())
    }

    pub fn get(&self, name: &str) -> Result<&Skill, SkillError> {
        self.skills.get(name).ok_or(SkillError::NotFound)
    }

    pub fn select_for_task(&self, task: &str, max_skills: usize) -> Vec<&Skill> {
        let task = task.to_ascii_lowercase();
        let mut scored = self
            .skills
            .values()
            .map(|skill| {
                let haystack = format!("{} {} {}", skill.name, skill.description, skill.content)
                    .to_ascii_lowercase();
                let score = task
                    .split_whitespace()
                    .filter(|term| haystack.contains(term))
                    .count();
                (score, skill)
            })
            .filter(|(score, _)| *score > 0)
            .collect::<Vec<_>>();
        scored.sort_by(|left, right| {
            right
                .0
                .cmp(&left.0)
                .then_with(|| left.1.name.cmp(&right.1.name))
        });
        scored
            .into_iter()
            .take(max_skills)
            .map(|(_, skill)| skill)
            .collect()
    }
}

pub fn estimate_tokens(content: &str) -> usize {
    content.split_whitespace().count()
}

pub fn builtin_skills() -> Result<Vec<Skill>, SkillError> {
    Ok(vec![
        Skill::new(
            "search-sdk-skill",
            "Compose search primitives",
            "0.1.0",
            include_str!("../skills/search-sdk-skill/SKILL.md"),
        )?,
        Skill::new(
            "browser-skill",
            "Automate browser tasks",
            "0.1.0",
            include_str!("../skills/browser-skill/SKILL.md"),
        )?,
        Skill::new(
            "sandbox-skill",
            "Write restricted sandbox code",
            "0.1.0",
            include_str!("../skills/sandbox-skill/SKILL.md"),
        )?,
        Skill::new(
            "research-skill",
            "Run deep research loops",
            "0.1.0",
            include_str!("../skills/research-skill/SKILL.md"),
        )?,
        Skill::new(
            "evidence-retrieval-skill",
            "Use HotEvidenceIndex candidates",
            "0.1.0",
            include_str!("../skills/evidence-retrieval-skill/SKILL.md"),
        )?,
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builtins_are_under_budget_and_selectable() {
        let registry = SkillsRegistry::with_builtin_skills().unwrap();
        assert_eq!(registry.skills.len(), 5);
        let selected = registry.select_for_task("browser research evidence", 3);
        assert!(!selected.is_empty());
        assert!(selected
            .iter()
            .all(|skill| estimate_tokens(&skill.content) < 2_000));
    }
}
