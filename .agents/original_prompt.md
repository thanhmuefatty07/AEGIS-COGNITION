# Original User Request

## 2026-06-12T01:04:11Z

# Teamwork Project Prompt — Draft

> Status: Launched.
> Goal: Craft prompt → get user approval → delegate to teamwork_preview

Research and design a developer experience (DX) and CLI setup wizard for AEGIS-COGNITION that surpasses Hermes Agent, focusing on API key management and provider auto-detection.

Working directory: ~/teamwork_projects/aegis_dx_research
Integrity mode: development

## Requirements

### R1. Deep RAG Ingestion & Competitor Analysis
Analyze the existing AEGIS codebase (Python Bridge & Rust Core) without modifying it. Research competitor CLI DX patterns (Hermes, Browser-Use, LangChain) using web searches. 

### R2. Research UX Dimensions
Develop detailed specifications for the following dimensions:
1. API Key Input UX (hidden input, cross-platform support including Windows)
2. Provider Auto-Detection (from key formats, plus Offline Mode/Local LLM detection like Ollama)
3. Setup Wizard Flow (3-5 steps)
4. Error Handling & Validation
5. Config Management
6. CLI Command Design

### R3. Deliver Unified DX Spec
Create `AEGIS_DX_RESEARCH_REPORT.md` containing the executive summary, dimension specs, and a comparison matrix against Hermes Agent.

### R4. Deliver Implementation Spec
Create `IMPLEMENTATION_SPEC.md` detailing the files to create/modify, pseudocode, and integration points with `aegis_adapter.py` and `llm.rs`.

## Acceptance Criteria

### Document Completeness
- [ ] `AEGIS_DX_RESEARCH_REPORT.md` contains evidence-based recommendations for all dimensions.
- [ ] `IMPLEMENTATION_SPEC.md` provides clear pseudocode and integration steps without altering core architecture.

### DX Standards
- [ ] Recommends a hidden input mechanism for API keys that works reliably on both Unix and Windows.
- [ ] Provides regex patterns or validation endpoints for at least 4 providers + Local LLM.
- [ ] The proposed setup flow requires 5 steps or fewer.

### Constraints Check
- [ ] No core architecture files (e.g., `hot_engine.rs`) are modified.
- [ ] Proposed solution maintains the Orthogonal 3-Pillar design.
