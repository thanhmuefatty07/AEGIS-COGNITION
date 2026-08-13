use quote::ToTokens;
use regex::Regex;
use syn::visit_mut::VisitMut;

pub trait SkeletonGenerator {
    fn generate_skeleton(trait_code: &str) -> Result<String, String>;
}

pub trait CompilerFeedbackLoop {
    fn analyze_compile_errors(cargo_check_output: &str) -> Vec<String>;
}

pub struct NeuroSymbolicHarness;

struct SkeletonVisitor;

impl VisitMut for SkeletonVisitor {
    fn visit_impl_item_fn_mut(&mut self, i: &mut syn::ImplItemFn) {
        i.block = syn::parse_quote! { { todo!() } };
        syn::visit_mut::visit_impl_item_fn_mut(self, i);
    }

    fn visit_trait_item_fn_mut(&mut self, i: &mut syn::TraitItemFn) {
        if i.default.is_some() {
            i.default = Some(syn::parse_quote! { { todo!() } });
        }
        syn::visit_mut::visit_trait_item_fn_mut(self, i);
    }
}

impl SkeletonGenerator for NeuroSymbolicHarness {
    fn generate_skeleton(trait_code: &str) -> Result<String, String> {
        let mut file: syn::File =
            syn::parse_str(trait_code).map_err(|e| format!("Failed to parse Rust code: {}", e))?;
        SkeletonVisitor.visit_file_mut(&mut file);
        Ok(file.to_token_stream().to_string())
    }
}

impl CompilerFeedbackLoop for NeuroSymbolicHarness {
    fn analyze_compile_errors(cargo_check_output: &str) -> Vec<String> {
        let ansi_regex = Regex::new(r"\x1B\[[0-9;]*[a-zA-Z]").unwrap();
        let stripped = ansi_regex.replace_all(cargo_check_output, "");

        let path_regex = Regex::new(r"[A-Za-z]:\\[\w\\]*?\\core\\").unwrap();
        let norm1 = path_regex.replace_all(&stripped, "/core/");
        let path_regex2 = Regex::new(r"(/[\w/]*?)/core/").unwrap();
        let normalized_output = path_regex2.replace_all(&norm1, "/core/");

        let mut errors = Vec::new();
        let mut current_error = String::new();
        let mut capturing = false;

        for line in normalized_output.lines() {
            let line_trimmed = line.trim();
            if line_trimmed.starts_with("error[") || line_trimmed.starts_with("error:") {
                if !current_error.is_empty() {
                    errors.push(current_error);
                    current_error = String::new();
                }
                capturing = true;
                current_error.push_str(line);
                current_error.push('\n');
            } else if capturing {
                if line_trimmed.starts_with("warning:") || line_trimmed.is_empty() {
                    if !current_error.is_empty() {
                        errors.push(current_error);
                        current_error = String::new();
                    }
                    capturing = false;
                } else {
                    current_error.push_str(line);
                    current_error.push('\n');
                }
            }
        }

        if !current_error.is_empty() {
            errors.push(current_error);
        }

        errors.into_iter().map(|e| e.trim().to_string()).collect()
    }
}

pub trait GrammarConstraintEngine {
    fn build_gbnf_grammar(&self, schema_json: &str) -> Result<String, String>;
    fn validate_next_token(&self, current_prefix: &str, next_token: &str) -> bool;
}

pub struct GbnfConstraintEngine;

impl GrammarConstraintEngine for GbnfConstraintEngine {
    fn build_gbnf_grammar(&self, schema_json: &str) -> Result<String, String> {
        if schema_json.is_empty() {
            return Err("empty schema".to_string());
        }
        Ok(format!("root ::= json_object\nschema: {}", schema_json))
    }

    fn validate_next_token(&self, current_prefix: &str, next_token: &str) -> bool {
        let proposed = format!("{}{}", current_prefix, next_token);
        if proposed.contains("unsafe")
            || proposed.contains(".unwrap")
            || proposed.contains("TcpStream")
        {
            return false;
        }
        true
    }
}
