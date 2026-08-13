use crate::physical::{PhysicalArtifact, TrapReason};
use aho_corasick::AhoCorasick;
use std::sync::OnceLock;
use syn::visit_mut::VisitMut;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct UntrustedText {
    pub content: String,
}

impl UntrustedText {
    pub fn new(content: &str) -> Self {
        Self {
            content: content.to_string(),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InvariantViolation {
    EmptyInput,
    DestructiveFilesystem,
    NetworkBypass,
    LlmJudgePattern,
    ChainOfThoughtLeak,
}

pub trait ConstitutionalGuardrail {
    fn verify_invariants(&self, untrusted_code: &str) -> Result<(), InvariantViolation>;
}

pub trait ZeroTrustGateway {
    fn sanitize_and_forward(
        &self,
        request: &HostCall,
    ) -> Result<ExternalResponse, InvariantViolation>;
}

pub trait ExecutiveAuthority {
    fn evaluate_action(&self, llm_proposal: &UntrustedText)
        -> Result<PhysicalArtifact, TrapReason>;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HostCall {
    pub target: String,
    pub payload: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalResponse {
    pub target: String,
    pub payload: String,
}

pub struct FirstOrderGuardrail;

struct GuardrailVisitor {
    violation: Option<InvariantViolation>,
}

impl VisitMut for GuardrailVisitor {
    fn visit_use_path_mut(&mut self, i: &mut syn::UsePath) {
        let ident_str = i.ident.to_string().to_lowercase();
        if ident_str == "fs" {
            self.violation = Some(InvariantViolation::DestructiveFilesystem);
        } else if ident_str == "net" {
            self.violation = Some(InvariantViolation::NetworkBypass);
        }
        syn::visit_mut::visit_use_path_mut(self, i);
    }

    fn visit_use_name_mut(&mut self, i: &mut syn::UseName) {
        let ident_str = i.ident.to_string().to_lowercase();
        if ident_str == "fs" {
            self.violation = Some(InvariantViolation::DestructiveFilesystem);
        } else if ident_str == "net" {
            self.violation = Some(InvariantViolation::NetworkBypass);
        }
        syn::visit_mut::visit_use_name_mut(self, i);
    }

    fn visit_use_rename_mut(&mut self, i: &mut syn::UseRename) {
        let ident_str = i.ident.to_string().to_lowercase();
        if ident_str == "fs" {
            self.violation = Some(InvariantViolation::DestructiveFilesystem);
        } else if ident_str == "net" {
            self.violation = Some(InvariantViolation::NetworkBypass);
        }
        syn::visit_mut::visit_use_rename_mut(self, i);
    }

    fn visit_path_mut(&mut self, i: &mut syn::Path) {
        for segment in &i.segments {
            let seg_str = segment.ident.to_string().to_lowercase();
            if seg_str == "remove_dir_all" || seg_str == "remove_file" {
                self.violation = Some(InvariantViolation::DestructiveFilesystem);
            }
            if seg_str == "tcpstream" || seg_str == "connect" {
                self.violation = Some(InvariantViolation::NetworkBypass);
            }
        }
        syn::visit_mut::visit_path_mut(self, i);
    }
}

static PATTERNS: &[&str] = &[
    // DestructiveFilesystem (indices 0..5)
    "rm -rf",
    "remove-item -recurse",
    "del /s",
    "format c:",
    "std::fs::remove_dir_all",
    // NetworkBypass (indices 5..11)
    "tcpstream::connect",
    "reqwest::",
    "hyper::client",
    "std::net::",
    "curl ",
    "wget ",
    // LlmJudgePattern (indices 11..16)
    "criticagent",
    "revieweragent",
    "llmjudge",
    "llm-as-a-judge",
    "adversarial court",
    // ChainOfThoughtLeak (indices 16..20)
    "chain-of-thought",
    "chain of thought",
    "<thinking>",
    "reason step by step",
];

static PATTERNS_NO_SPACE: &[&str] = &[
    // DestructiveFilesystem (indices 0..5)
    "rm-rf",
    "remove-item-recurse",
    "del/s",
    "formatc:",
    "std::fs::remove_dir_all",
    // NetworkBypass (indices 5..11)
    "tcpstream::connect",
    "reqwest::",
    "hyper::client",
    "std::net::",
    "curl",
    "wget",
    // LlmJudgePattern (indices 11..16)
    "criticagent",
    "revieweragent",
    "llmjudge",
    "llm-as-a-judge",
    "adversarialcourt",
    // ChainOfThoughtLeak (indices 16..20)
    "chain-of-thought",
    "chainofthought",
    "<thinking>",
    "reasonstepbystep",
];

static AC_MATCHER: OnceLock<AhoCorasick> = OnceLock::new();
static AC_NO_SPACE_MATCHER: OnceLock<AhoCorasick> = OnceLock::new();

fn get_ac_matcher() -> &'static AhoCorasick {
    AC_MATCHER
        .get_or_init(|| AhoCorasick::new(PATTERNS).expect("Failed to build AhoCorasick matcher"))
}

fn get_ac_no_space_matcher() -> &'static AhoCorasick {
    AC_NO_SPACE_MATCHER.get_or_init(|| {
        AhoCorasick::new(PATTERNS_NO_SPACE).expect("Failed to build AhoCorasick no-space matcher")
    })
}

pub fn remove_whitespace(text: &str) -> String {
    text.chars()
        .filter(|c| !c.is_whitespace())
        .collect::<String>()
        .to_ascii_lowercase()
}

impl ConstitutionalGuardrail for FirstOrderGuardrail {
    fn verify_invariants(&self, untrusted_code: &str) -> Result<(), InvariantViolation> {
        let normalized = normalize_for_scan(untrusted_code);
        if normalized.is_empty() {
            return Err(InvariantViolation::EmptyInput);
        }

        // 1. Substring Fallback/Dual-Shield Check using SIMD Aho-Corasick (Tier 1 Fast-Path)
        let ac = get_ac_matcher();
        if let Some(mat) = ac.find(&normalized) {
            let violation = match mat.pattern().as_usize() {
                0..=4 => InvariantViolation::DestructiveFilesystem,
                5..=10 => InvariantViolation::NetworkBypass,
                11..=15 => InvariantViolation::LlmJudgePattern,
                16..=19 => InvariantViolation::ChainOfThoughtLeak,
                _ => unreachable!(),
            };
            return Err(violation);
        }

        // 1b. Substring Fallback with Whitespace-Stripped (insensitivity to spacing/tabs)
        let no_space = remove_whitespace(untrusted_code);
        let ac_no_space = get_ac_no_space_matcher();
        if let Some(mat) = ac_no_space.find(&no_space) {
            let violation = match mat.pattern().as_usize() {
                0..=4 => InvariantViolation::DestructiveFilesystem,
                5..=10 => InvariantViolation::NetworkBypass,
                11..=15 => InvariantViolation::LlmJudgePattern,
                16..=19 => InvariantViolation::ChainOfThoughtLeak,
                _ => unreachable!(),
            };
            return Err(violation);
        }

        // 2. AST Check (if parseable as Rust) (Tier 2 Deep Semantic Guardrail)
        if let Ok(mut file) = syn::parse_str::<syn::File>(untrusted_code) {
            let mut visitor = GuardrailVisitor { violation: None };
            visitor.visit_file_mut(&mut file);
            if let Some(v) = visitor.violation {
                return Err(v);
            }
        }
        Ok(())
    }
}

impl ZeroTrustGateway for FirstOrderGuardrail {
    fn sanitize_and_forward(
        &self,
        request: &HostCall,
    ) -> Result<ExternalResponse, InvariantViolation> {
        self.verify_invariants(&request.payload)?;
        self.verify_invariants(&request.target)?;
        Ok(ExternalResponse {
            target: request.target.trim().to_string(),
            payload: strip_chain_of_thought(&request.payload),
        })
    }
}

pub fn strip_chain_of_thought(text: &str) -> String {
    let lower = text.to_ascii_lowercase();
    if let Some(index) = lower.rfind("final_tool_call:") {
        return text[index..].trim().to_string();
    }
    if let Some(index) = lower.rfind("final:") {
        return text[index..].trim().to_string();
    }
    text.trim().to_string()
}

pub fn normalize_for_scan(text: &str) -> String {
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_lowercase()
}
