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

fn gbnf_escape_literal(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 4);
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\"\\\"\\\""),
            '\\' => out.push_str("\\\\\\\\"),
            '\n' => out.push_str("\\\\n"),
            '\r' => out.push_str("\\\\r"),
            '\t' => out.push_str("\\\\t"),
            c => out.push(c),
        }
    }
    out
}

fn sanitize_rule_name(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    for ch in raw.chars() {
        if ch.is_ascii_alphanumeric() || ch == '-' {
            out.push(ch.to_ascii_lowercase());
        } else {
            out.push('-');
        }
    }
    let trimmed = out.trim_matches('-');
    if trimmed.is_empty() {
        "field".to_string()
    } else {
        trimmed.to_string()
    }
}

fn compile_schema_node(
    rule_name: &str,
    schema: &serde_json::Value,
    rules: &mut Vec<(String, String)>,
    depth: usize,
) -> Result<String, String> {
    if depth > 24 {
        return Err("JSON schema nesting depth exceeds GBNF bound (24)".to_string());
    }
    let Some(obj) = schema.as_object() else {
        return Err("JSON schema node must be an object".to_string());
    };

    if let Some(const_val) = obj.get("const") {
        let serialized =
            serde_json::to_string(const_val).map_err(|e| format!("invalid const value: {e}"))?;
        let expr = format_json_token_literal(&serialized);
        rules.push((rule_name.to_string(), expr));
        return Ok(rule_name.to_string());
    }

    if let Some(enum_vals) = obj.get("enum").and_then(|v| v.as_array()) {
        if enum_vals.is_empty() {
            return Err("JSON schema enum must not be empty".to_string());
        }
        let mut alts = Vec::with_capacity(enum_vals.len());
        for item in enum_vals {
            let serialized =
                serde_json::to_string(item).map_err(|e| format!("invalid enum item: {e}"))?;
            alts.push(format_json_token_literal(&serialized));
        }
        rules.push((rule_name.to_string(), alts.join(" | ")));
        return Ok(rule_name.to_string());
    }

    for union_key in ["anyOf", "oneOf"] {
        if let Some(variants) = obj.get(union_key).and_then(|v| v.as_array()) {
            if variants.is_empty() {
                return Err(format!("JSON schema {union_key} must not be empty"));
            }
            let mut alts = Vec::with_capacity(variants.len());
            for (idx, variant) in variants.iter().enumerate() {
                let sub_name = format!("{rule_name}-alt-{idx}");
                alts.push(compile_schema_node(&sub_name, variant, rules, depth + 1)?);
            }
            rules.push((rule_name.to_string(), alts.join(" | ")));
            return Ok(rule_name.to_string());
        }
    }

    let ty = obj.get("type").and_then(|v| v.as_str()).unwrap_or_else(|| {
        if obj.contains_key("properties") {
            "object"
        } else if obj.contains_key("items") {
            "array"
        } else {
            "any"
        }
    });

    match ty {
        "string" => Ok("json-string".to_string()),
        "integer" => Ok("json-integer".to_string()),
        "number" => Ok("json-number".to_string()),
        "boolean" => Ok("json-boolean".to_string()),
        "null" => Ok("json-null".to_string()),
        "array" => {
            let item_rule = if let Some(items_schema) = obj.get("items") {
                let sub = format!("{rule_name}-item");
                compile_schema_node(&sub, items_schema, rules, depth + 1)?
            } else {
                "json-value".to_string()
            };
            let expr = format!("\"[\" ws ({item_rule} (ws \",\" ws {item_rule})*)? ws \"]\"");
            rules.push((rule_name.to_string(), expr));
            Ok(rule_name.to_string())
        }
        "object" => {
            if let Some(props) = obj.get("properties").and_then(|v| v.as_object()) {
                if props.is_empty() {
                    rules.push((rule_name.to_string(), "\"{\" ws \"}\"".to_string()));
                    return Ok(rule_name.to_string());
                }
                let required: std::collections::BTreeSet<&str> = obj
                    .get("required")
                    .and_then(|v| v.as_array())
                    .map(|arr| arr.iter().filter_map(|x| x.as_str()).collect())
                    .unwrap_or_default();

                let mut prop_entries: Vec<(&String, &serde_json::Value)> = props.iter().collect();
                prop_entries.sort_by(|a, b| a.0.cmp(b.0));

                let mut kv_rules = Vec::with_capacity(prop_entries.len());
                for (prop_key, prop_schema) in &prop_entries {
                    let safe_prop = sanitize_rule_name(prop_key);
                    let val_rule_hint = format!("{rule_name}-{safe_prop}-val");
                    let val_rule =
                        compile_schema_node(&val_rule_hint, prop_schema, rules, depth + 1)?;
                    let kv_rule = format!("{rule_name}-{safe_prop}-kv");
                    let escaped_key = gbnf_escape_literal(prop_key);
                    rules.push((
                        kv_rule.clone(),
                        format!("\"\\\"{escaped_key}\\\"\" ws \":\" ws {val_rule}"),
                    ));
                    kv_rules.push((
                        prop_key.as_str(),
                        kv_rule,
                        required.contains(prop_key.as_str()),
                    ));
                }

                // Emit deterministic ordered member sequence honoring required vs optional members.
                let all_required = kv_rules.iter().all(|(_, _, req)| *req);
                let expr = if all_required {
                    let seq: Vec<String> = kv_rules.into_iter().map(|(_, kv, _)| kv).collect();
                    format!("\"{{\" ws {} ws \"}}\"", seq.join(" ws \",\" ws "))
                } else {
                    // Build tail-optional chain so optional properties may be omitted cleanly.
                    let mut parts = Vec::new();
                    let mut seen_required = false;
                    for (_, kv, is_req) in kv_rules {
                        if is_req {
                            if !parts.is_empty() {
                                parts.push(format!("ws \",\" ws {kv}"));
                            } else {
                                parts.push(kv);
                            }
                            seen_required = true;
                        } else if seen_required || !parts.is_empty() {
                            parts.push(format!("(ws \",\" ws {kv})?"));
                        } else {
                            parts.push(format!("({kv})?"));
                        }
                    }
                    format!("\"{{\" ws {} ws \"}}\"", parts.join(" "))
                };
                rules.push((rule_name.to_string(), expr));
                Ok(rule_name.to_string())
            } else {
                Ok("json-object".to_string())
            }
        }
        "any" => Ok("json-value".to_string()),
        other => Err(format!("unsupported JSON schema type: {other}")),
    }
}

fn format_json_token_literal(serialized_json: &str) -> String {
    let mut escaped = String::with_capacity(serialized_json.len() + 8);
    escaped.push('"');
    for ch in serialized_json.chars() {
        match ch {
            '"' => escaped.push_str("\\\""),
            '\\' => escaped.push_str("\\\\"),
            c => escaped.push(c),
        }
    }
    escaped.push('"');
    escaped
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RustLexTokenKind {
    Ident,
    Punct,
}

fn lex_rust_code_stripping_literals_and_comments(source: &str) -> Vec<(RustLexTokenKind, String)> {
    let bytes = source.as_bytes();
    let mut i = 0usize;
    let mut tokens = Vec::new();
    while i < bytes.len() {
        let b = bytes[i];
        if b.is_ascii_whitespace() {
            i += 1;
            continue;
        }
        // Line comment `//...`
        if b == b'/' && i + 1 < bytes.len() && bytes[i + 1] == b'/' {
            i += 2;
            while i < bytes.len() && bytes[i] != b'\n' {
                i += 1;
            }
            continue;
        }
        // Block comment `/* ... */` (supports nested Rust block comments)
        if b == b'/' && i + 1 < bytes.len() && bytes[i + 1] == b'*' {
            i += 2;
            let mut depth = 1usize;
            while i < bytes.len() && depth > 0 {
                if i + 1 < bytes.len() && bytes[i] == b'/' && bytes[i + 1] == b'*' {
                    depth += 1;
                    i += 2;
                } else if i + 1 < bytes.len() && bytes[i] == b'*' && bytes[i + 1] == b'/' {
                    depth -= 1;
                    i += 2;
                } else {
                    i += 1;
                }
            }
            continue;
        }
        // Raw string literal `r#"..."#` or `r"..."`
        if b == b'r' && i + 1 < bytes.len() && (bytes[i + 1] == b'"' || bytes[i + 1] == b'#') {
            let mut hashes = 0usize;
            let mut j = i + 1;
            while j < bytes.len() && bytes[j] == b'#' {
                hashes += 1;
                j += 1;
            }
            if j < bytes.len() && bytes[j] == b'"' {
                i = j + 1;
                while i < bytes.len() {
                    if bytes[i] == b'"' {
                        let mut close_hashes = 0usize;
                        let mut k = i + 1;
                        while k < bytes.len() && bytes[k] == b'#' && close_hashes < hashes {
                            close_hashes += 1;
                            k += 1;
                        }
                        if close_hashes == hashes {
                            i = k;
                            break;
                        }
                    }
                    i += 1;
                }
                continue;
            }
        }
        // Normal string literal `"..."`
        if b == b'"' {
            i += 1;
            while i < bytes.len() {
                if bytes[i] == b'\\' {
                    i = (i + 2).min(bytes.len());
                    continue;
                }
                if bytes[i] == b'"' {
                    i += 1;
                    break;
                }
                i += 1;
            }
            continue;
        }
        // Char literal `'a'` vs lifetime `'a`
        if b == b'\'' {
            if i + 2 < bytes.len() && bytes[i + 1] != b'\\' && bytes[i + 2] == b'\'' {
                i += 3;
                continue;
            }
            if i + 3 < bytes.len() && bytes[i + 1] == b'\\' {
                let mut k = i + 2;
                while k < bytes.len() && bytes[k] != b'\'' && !bytes[k].is_ascii_whitespace() {
                    k += 1;
                }
                if k < bytes.len() && bytes[k] == b'\'' {
                    i = k + 1;
                    continue;
                }
            }
        }
        // Identifier / keyword
        if b.is_ascii_alphabetic() || b == b'_' {
            let start = i;
            i += 1;
            while i < bytes.len() && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_') {
                i += 1;
            }
            tokens.push((RustLexTokenKind::Ident, source[start..i].to_string()));
            continue;
        }
        // Single ASCII punctuation
        if b.is_ascii_punctuation() {
            tokens.push((RustLexTokenKind::Punct, (b as char).to_string()));
            i += 1;
            continue;
        }
        // Advance one UTF-8 scalar
        let ch_len = source[i..].chars().next().map_or(1, char::len_utf8);
        i += ch_len;
    }
    tokens
}

struct AstSafetyVisitor {
    violation_found: bool,
}

impl<'ast> syn::visit::Visit<'ast> for AstSafetyVisitor {
    fn visit_expr_unsafe(&mut self, _node: &'ast syn::ExprUnsafe) {
        self.violation_found = true;
    }

    fn visit_item_fn(&mut self, node: &'ast syn::ItemFn) {
        if node.sig.unsafety.is_some() {
            self.violation_found = true;
            return;
        }
        syn::visit::visit_item_fn(self, node);
    }

    fn visit_impl_item_fn(&mut self, node: &'ast syn::ImplItemFn) {
        if node.sig.unsafety.is_some() {
            self.violation_found = true;
            return;
        }
        syn::visit::visit_impl_item_fn(self, node);
    }

    fn visit_item_impl(&mut self, node: &'ast syn::ItemImpl) {
        if node.unsafety.is_some() {
            self.violation_found = true;
            return;
        }
        syn::visit::visit_item_impl(self, node);
    }

    fn visit_item_trait(&mut self, node: &'ast syn::ItemTrait) {
        if node.unsafety.is_some() {
            self.violation_found = true;
            return;
        }
        syn::visit::visit_item_trait(self, node);
    }

    fn visit_item_foreign_mod(&mut self, _node: &'ast syn::ItemForeignMod) {
        self.violation_found = true;
    }

    fn visit_expr_method_call(&mut self, node: &'ast syn::ExprMethodCall) {
        if node.method == "unwrap" {
            self.violation_found = true;
            return;
        }
        syn::visit::visit_expr_method_call(self, node);
    }

    fn visit_path(&mut self, node: &'ast syn::Path) {
        if node.segments.iter().any(|seg| seg.ident == "TcpStream") {
            self.violation_found = true;
            return;
        }
        syn::visit::visit_path(self, node);
    }
}

impl GrammarConstraintEngine for GbnfConstraintEngine {
    fn build_gbnf_grammar(&self, schema_json: &str) -> Result<String, String> {
        if schema_json.trim().is_empty() {
            return Err("empty schema".to_string());
        }
        let parsed: serde_json::Value =
            serde_json::from_str(schema_json).map_err(|e| format!("invalid JSON schema: {e}"))?;
        let mut rules: Vec<(String, String)> = Vec::new();
        let entry_rule = compile_schema_node("root-body", &parsed, &mut rules, 0)?;

        let mut grammar = String::new();
        grammar.push_str(&format!("root ::= ws {entry_rule} ws\n"));
        for (name, expr) in rules {
            grammar.push_str(&format!("{name} ::= {expr}\n"));
        }
        grammar.push_str(concat!(
            "json-value ::= json-object | json-array | json-string | json-number | json-boolean | json-null\n",
            "json-object ::= \"{\" ws (json-member (ws \",\" ws json-member)*)? ws \"}\"\n",
            "json-member ::= json-string ws \":\" ws json-value\n",
            "json-array ::= \"[\" ws (json-value (ws \",\" ws json-value)*)? ws \"]\"\n",
            "json-string ::= \"\\\"\" json-char* \"\\\"\"\n",
            "json-char ::= [^\"\\\\\\x00-\\x1F] | \"\\\\\" ([\"\\\\/bfnrt] | \"u\" [0-9a-fA-F]{4})\n",
            "json-integer ::= \"-\"? (\"0\" | [1-9] [0-9]*)\n",
            "json-number ::= json-integer (\".\" [0-9]+)? ([eE] [+-]? [0-9]+)?\n",
            "json-boolean ::= \"true\" | \"false\"\n",
            "json-null ::= \"null\"\n",
            "ws ::= [ \\t\\n\\r]*\n",
        ));
        Ok(grammar)
    }

    fn validate_next_token(&self, current_prefix: &str, next_token: &str) -> bool {
        let proposed = format!("{}{}", current_prefix, next_token);
        if let Ok(ast) = syn::parse_file(&proposed) {
            let mut visitor = AstSafetyVisitor {
                violation_found: false,
            };
            syn::visit::Visit::visit_file(&mut visitor, &ast);
            if visitor.violation_found {
                return false;
            }
        }

        let tokens = lex_rust_code_stripping_literals_and_comments(&proposed);
        for (idx, (kind, text)) in tokens.iter().enumerate() {
            if *kind == RustLexTokenKind::Ident {
                if text == "unsafe" || text == "TcpStream" {
                    return false;
                }
                if text == "unwrap"
                    && idx > 0
                    && tokens[idx - 1] == (RustLexTokenKind::Punct, ".".to_string())
                {
                    let followed_by_ident_or_other = tokens
                        .get(idx + 1)
                        .is_some_and(|(nk, nt)| *nk != RustLexTokenKind::Punct || nt != "(");
                    if !followed_by_ident_or_other {
                        return false;
                    }
                }
            }
        }
        true
    }
}
