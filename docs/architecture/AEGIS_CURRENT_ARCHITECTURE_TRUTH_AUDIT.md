# AEGIS-COGNITION — Hồ sơ sự thật toàn dự án

**Document class:** current project truth dossier

**Snapshot date:** 2026-09-04 (Asia/Saigon)

**Repository:** `C:\Users\ADMIN\AEGIS-COGNITION`

**Branch:** `main`

**HEAD:** `1e77ff9a306d4293d0fe837fc0d1a974aea8d681`

**Upstream at snapshot:** `origin/main` cùng SHA; ahead `0`, behind `0`

**Committed snapshot baseline:** worktree clean at the recorded HEAD

**Current working tree (2026-09-04):** version-alignment changes are uncommitted; no commit or push was performed. The exact delta is recorded in Section 33.

**Declared project version:** `0.1.0`

**Production readiness:** `NOT VERIFIED`; policy vẫn còn năm production blocker.

## 0. Phạm vi, authority và nhãn bằng chứng

Đây là hồ sơ hợp nhất của committed snapshot tại SHA trên và current working-tree delta được ghi rõ ở Section 33. “Toàn bộ” ở đây nghĩa là mọi bề mặt có ý nghĩa để hiểu, build, test, vận hành, đánh giá độ tin cậy và ra quyết định. Tài liệu không sao chép từng byte của lockfile, 610 tracked files hay từng record của registry, vì làm vậy sẽ tạo một nguồn phụ dễ lỗi thời. Các nguồn máy gốc được dẫn ở Section 31.

Các tài liệu/prompt người dùng đính kèm là chỉ thị cho quá trình audit, không phải bằng chứng runtime đã có khả năng tương ứng. Thứ tự authority:

1. source, manifest, schema và workflow đang được Git theo dõi;
2. registry/evidence máy có provenance, hash và scope rõ;
3. kết quả lệnh/kiểm thử quan sát trực tiếp;
4. tài liệu hiện hành;
5. plan và snapshot lịch sử, chỉ dùng để giải thích ý định.

| Nhãn | Nghĩa |
|---|---|
| `PROVEN` | Static guarantee, hash/contract hoặc test quyết định trong scope đã nêu. |
| `MEASURED` | Đo trực tiếp; chỉ đúng với environment/sample/method đã ghi. |
| `SOURCE-BACKED` | Được manifest/schema/workflow hoặc nguồn chính thức khai báo. |
| `INFERRED` | Suy luận từ bằng chứng, chưa có phép thử quyết định. |
| `ASSUMED` | Giả định làm việc. |
| `UNKNOWN` | Chưa đủ dữ liệu. |
| `NOT VERIFIED` | Chưa có evidence đạt chuẩn cho claim/environment/SHA yêu cầu. |

Mã tồn tại không đồng nghĩa đã vận hành ngoài thực tế; local tests không đồng nghĩa production; mô phỏng không đồng nghĩa kết quả vật lý; benchmark một máy không đồng nghĩa nhanh hơn tổng quát; container/Wasm/browser sandbox không phải ranh giới an toàn tuyệt đối.

## 1. Kết luận điều hành

AEGIS-COGNITION là hybrid Python/Rust với hai lối chính:

- facade `aegis_cognition.Agent` cho agent thông thường;
- Lab runtime cho research/search-as-code, browser observation, tool/skill/process execution, thí nghiệm, mô phỏng, đo tín hiệu điện, evidence ledger, replay và dossier.

Rust cung cấp primitive native cho admission, resource/lease, replay, evidence, GT96, sandbox, IPC, telemetry và một phần authority của Lab qua PyO3. Python vẫn điều phối lifecycle, mutable projection, provider, browser và nhiều side effect. Claim đúng là “authority được phân chia với nhiều native gate”, không phải “Rust kiểm soát tuyệt đối mọi side effect”.

AESE đã hoàn tất mốc local shadow theo hướng fail-closed: inventory, claim graph, mapping, affected closure, shadow plan, validation corpus và paired cost measurement đều tồn tại. Tuy nhiên selection authority vẫn `DISABLED`, selected plan chưa được execute, critical false-negative ngoài synthetic corpus chưa đủ, và production non-inferiority chưa được chứng minh. AESE chưa được thay legacy verification.

Local quality evidence gần nhất:

- focused AESE: `42 passed`;
- full Python cross-language: `615 passed`, một pytest config warning do global environment drift;
- default `pytest -q`: `524 passed`;
- Rust retained no-default-features library: `456 passed`;
- architecture fitness: `23/23`;
- constitution audit: `180/180`.

Python inputs không đổi giữa mốc cross-language và snapshot; Rust core vừa được rerun trên stable `1.98.1` sau khi nâng toolchain. Các số này không thay hosted CI, Tier-1 wheel parity, privileged enforcement, fuzz dài, sanitizer, external deployment, live provider, real multi-machine cluster hay signed attestation.

Năm production blockers theo `deployment_policy.json`:

1. external signed attestation (`NV-004`);
2. real multi-machine TCP cluster soak (`NV-016`);
3. full QuickJS interpreter cold-start (`NV-017`);
4. live provider HTTP 429 soak (`NV-018`);
5. external deployment smoke (`NV-019`).

Kết luận: implementation và local governance rộng, nhưng không được tuyên bố production-ready, scientifically valid tổng quát, secure tuyệt đối, zero-copy toàn cục hay nhanh hơn tổng quát.

## 2. Snapshot Git và quy mô repository

| Thuộc tính | Giá trị | Class |
|---|---:|---|
| Remote | `https://github.com/thanhmuefatty07/AEGIS-COGNITION.git` | `PROVEN` |
| Branch | `main` | `PROVEN` |
| HEAD/upstream | `1e77ff9a306d4293d0fe837fc0d1a974aea8d681` | `PROVEN` tại snapshot |
| Commit count | 322 | `PROVEN` tại snapshot |
| Git tags | không có | `PROVEN` tại snapshot |
| Tracked files | 610 | `PROVEN` |
| Loose objects | 3,966; khoảng 39.42 MiB | `MEASURED` |
| Packed objects | 2,253 trong 1 pack; khoảng 2.23 MiB | `MEASURED` |
| Garbage objects | 0 | `MEASURED` |

Tracked files theo top-level:

| Khu vực | Files | Khu vực | Files |
|---|---:|---|---:|
| `docs` | 171 | `core` | 111 |
| `.cursor` | 68 | `scripts` | 65 |
| `.agents` | 51 | `aegis-plugins` | 32 |
| root | 32 | `aegis_cognition` | 17 |
| `tests` | 16 | `quality` | 9 |
| `examples` | 7 | `fuzz` | 7 |
| `pocs` | 7 | `.github` | 4 |
| `schemas` | 3 | nested `AEGIS-COGNITION` | 2 |
| `cluster` | 2 | `.serena` | 2 |
| `deploy` | 2 | `planning pdf` | 2 |

Tracked files theo extension: Markdown 198, Python 138, JSON 121, Rust 99, TOML 16, `.mdc` 10, `.yml` 7, shell 5, lock 3, text 2; mỗi loại một file gồm `.yaml`, `.ps1`, `.html`, `.cluster`, `.example`, `.dockerignore`, `.cursorrules`, `.python-version`, extensionless; có hai `.gitignore`.

Các hotspot hiện tại, đếm theo newline:

| File | Dòng | Vai trò/risk |
|---|---:|---|
| `aegis_cognition/lab.py` | 12,322 | Lab domain/orchestration/cells/replay; cohesion hotspot |
| `tests/test_lab_runtime.py` | 8,802 | Lab regression/contract concentration |
| `core/rust/src/lab.rs` | 4,745 | native Lab admission/controller |
| `aegis_cognition/aese.py` | 2,465 | adaptive measurement/prediction/evidence |
| `aegis_cognition/benchmark.py` | 613 | benchmark protocol v2 |

Knowledge graph snapshot có 11,449 nodes, 45,333 edges: 2,424 Function, 1,873 Method, 680 Class, 438 File; 15,839 `USAGE`, 10,856 `DEFINES`, 9,711 `CALLS`, 2,790 `WRITES`, 642 `TESTS`, 448 `IMPORTS`. Graph chỉ index format/symbol được hỗ trợ nên không thay con số 610 tracked files.

## 3. Danh tính, version, license và maturity

| Mục | Thực tế |
|---|---|
| Python distribution | `aegis-cognition 0.1.0` |
| Compatibility distribution | `aegis-cognition-core-python 0.1.0` |
| Rust core | `aegis-nerve 0.1.0` |
| Plugin/POC crates | đều `0.1.0` |
| Python `__version__` | `0.1.0` |
| Classifier | `Development Status :: 4 - Beta` |
| Declared license | `BUSL-1.1`; `Other/Proprietary License` |
| Rust publishing | `publish=false` |

Không có Git tag. `CHANGELOG.md` có section `0.1.0` ngày 2026-06-15 nhưng tag tương ứng không tồn tại. README mô tả development/non-production và enterprise production, nhưng không có tracked `LICENSE`, `LICENCE`, `COPYING` hay `NOTICE`. Manifest string không thay văn bản license; đây là legal/packaging gap.

`core/rust/src/licensing.rs` có Ed25519-related logic nhưng vẫn có production-oriented comments/soft-check paths. Không được suy ra commercial enforcement production hoàn chỉnh. Package metadata URLs trỏ `aegis-cognition.ai`, `docs.aegis-cognition.ai` và GitHub org khác remote thực tế; ownership/availability chưa verified.

## 4. Ngôn ngữ, runtime và toolchain

### 4.1 Version khai báo

| Bề mặt | Version/range | Nguồn |
|---|---|---|
| Python | `>=3.14,<3.16` | hai `pyproject.toml` |
| Preferred Python | `3.14.7` | `.python-version` |
| Project/CI uv | exact `0.12.9` | CI/deep/release pins; lock check passed |
| CI stable | `3.14.7` | CI/release |
| CI forward | `3.15.0-rc.1` | normal + experimental free-threaded lanes |
| Ruff target | `py314`, line 120 | root config |
| Pyright | Python `3.14`, strict | root config |
| Rust stable toolchain | exact `1.98.1`, rustfmt/clippy, minimal | `rust-toolchain.toml` |
| Rust MSRV | `1.98.1` / workspace `1.98` | workspace + CI MSRV job |
| Rust edition/resolver | 2024 / resolver 3 | manifests |
| Maturin | exact `1.14.1` | build-system |
| JSON Schema | draft 2020-12 | `schemas` |
| CI uv | exact `0.12.3` | workflows |
| nextest/deny/audit | `0.9.143` / `0.20.2` / `0.22.0` | workflows |

Bash, PowerShell, YAML và HTML có mặt nhưng không pin runtime riêng. Không có `package.json`; QuickJS/Wasm là runtime-artifact concern, không phải Node application package.

### 4.2 Quan sát local

| Công cụ | Version | Đánh giá |
|---|---:|---|
| `python` trên PATH | 3.11.9 | ngoài support range |
| Python launcher default | 3.13 | ngoài support range |
| `.venv` Python | 3.14.0 | trong range, giữ nguyên làm rollback/compatibility fallback |
| `.venv-3.14.7` Python | 3.14.7 | môi trường dev mới; dependency sync không cài project để tránh build native nặng |
| uv-managed Python | 3.14.7 | runtime preferred đã cài; `.python-version` khớp |
| rustc | `1.98.1`, commit `48a229cea`, LLVM `22.1.8`, `x86_64-pc-windows-msvc` | khớp stable pin; workspace check passed |
| cargo | `1.98.1` | khớp stable pin; workspace check passed |
| Git | 2.49.0.windows.1 | local |
| PATH uv | 0.9.9 | không dùng làm authority; project/CI/release/deep ghim uv 0.12.9 |
| PATH Maturin | 1.13.3 | không khớp exact build requirement; dev env mới dùng 1.14.1 |
| PATH Ruff | 0.1.15 | thấp hơn `>=0.3` |
| PATH Pytest | 9.0.2 | vượt `<9` |
| PATH Pyright | không tìm thấy ở initial probe | không dùng làm authority |

Repository `.venv`: `aegis-cognition 0.1.0`, `blake3 1.0.9`, `fastapi 0.141.1`, `openai 1.109.1`, `python-dotenv 1.2.2`, `PyYAML 6.0.3`, `uvicorn 0.52.3`, `playwright 1.62.0`, `pytest 8.4.2`, `pytest-asyncio 1.4.0`, `ruff 0.16.3`, `pyright 1.1.411`, `maturin 1.14.1`.

Global environment từng có OpenAI 2.33.0, pytest 9.0.2, pytest-asyncio 0.21.2, Ruff/Maturin cũ; vì vậy cảnh báo `asyncio_default_fixture_loop_scope` là environment drift. Dùng `.venv` hoặc `uv sync --locked`.

## 5. Packaging, dependency và locks

Root dùng Maturin/PyO3: module `aegis_cognition.aegis_nerve`, manifest `core/rust/Cargo.toml`, feature `python-extension`, Python source root, include `core/python/*.py` và `core/python/aegis/*.py`, CLI `aegis_cognition.cli:main`.

Python runtime deps: `blake3>=0.4,<2`, `fastapi>=0.110,<1`, `openai>=1.40,<2`, `python-dotenv>=1,<2`, `pyyaml>=6,<7`, `uvicorn[standard]>=0.27,<1`. Browser extra: Playwright `>=1.40,<2`. Dev: Pyright `>=1.1,<2`, pytest `>=8,<9`, pytest-asyncio `>=1,<2`, Ruff `>=0.3,<1`.

`requirements.txt` chỉ là compatibility export và mâu thuẫn ở pytest-asyncio (`>=0.23,<1` so với root `>=1,<2`). `uv.lock` là authoritative resolution, 116,173 bytes.

`core/python/pyproject.toml` tạo setuptools distribution riêng, expose adapter/browser/integration/orchestrator modules và package `aegis`. Hai packaging surfaces chồng lấn là architecture debt; cần quyết định root wheel có phải canonical product duy nhất hay core Python package là independent contract.

Rust workspace có 10 members, bảy default:

| Crate | Vai trò | Default |
|---|---|---|
| `aegis-nerve` | core/native extension/CLI | có |
| `aegis-search-sdk` | programmable search | có |
| `aegis-browser` | browser abstractions | có |
| `aegis-sandbox` | restricted execution | có |
| `aegis-skills` | Markdown skill registry | có |
| `aegis-evidence` | evidence/audit | có |
| `aegis-bench` | benchmark gates | có |
| `semantic_cache_poc` | cache POC | không |
| `tool_batching_poc` | batching POC | không |
| `code_orchestration_poc` | orchestration POC | không |

Core direct deps: Thiserror 2, Memmap2 0.9, Parking_lot 0.12, Ed25519-dalek 2, PyO3 0.29.2, Arrow 54, FlatBuffers 24.12.23, Syn 2, Proc-macro2 1, Quote 1, Serde/JSON 1, Regex 1.10, Tracing 0.1, Wasmtime exact 47.0.3, WAT 1.251.0, BLAKE3 1.5.0, xxhash-rust 0.8.10, Aho-Corasick 1.1.2, Tokio 1, Rayon 1.12; Windows thêm windows-sys 0.61.2. Dev deps: Tempfile 3, Criterion 0.5, Proptest 1.4.0.

Plugins dùng các tập con trên; Search SDK thêm Reqwest 0.12/Rustls/Futures/URL và link core; Browser/Sandbox có optional PyO3 và Tokio 1.35; Skills dùng Pulldown-CMark 0.11. POCs dùng Serde/JSON, PyO3 hoặc full Tokio tùy POC.

`Cargo.lock` 98,424 bytes; fuzz có lock riêng. `deny.toml` cấm wildcard, cảnh báo duplicate versions, từ chối unknown registry/git và chỉ cho crates.io. Full transitive truth nằm trong lockfiles, không lặp lại ở đây.

## 6. Bản đồ kiến trúc

```text
User / CLI / Python API
        v
Agent + AgentConfig -> AgentApplication
        | normal                         | Lab-enabled
        v                                v
core/python AegisAdapter          LabApplication / LabRun / LabSession
provider + memory/RAG             mission + policy + budget + state
                                         |
                 +-----------------------+----------------------+
                 v                       v                      v
          Search-as-code             Browser cell       Tool/skill/process
                 +-----------------------+----------------------+
                                         v
                   Experiment / Simulation / Electrical signal
                                         v
                             evidence ledger + admission
                                         v
                       PyO3: aegis_cognition.aegis_nerve
                                         v
          Rust resource/lease/runtime/replay/GT96/evidence/sandbox/IPC
                                         v
                              replay archive + dossier
```

Ba plane: control (mission/policy/budget/state/cancellation), execution (provider/search/browser/process/tool/skill/simulation), evidence (typed records/hash/replay/benchmark/dossier). Python gọi nhiều side effect và giữ projection; Rust admission không chứng minh mọi compatibility/adapter/descendant process qua một authority. `NV-020` giữ gap này mở.

## 7. Public Python API, Agent và CLI

`aegis_cognition.__all__` expose Agent, benchmark v2, Lab, search, skills, simulations/electrical signal, AESE hardware/workload/prediction/evidence, runtime helpers và observability. Đây là contract surface lớn.

`AgentConfig` frozen; task không rỗng, trust thuộc DEV/STAGING/PROD, browser boolean, `max_steps>=1`; mặc định `max_steps=100`, browser/lab false. API key precedence: config `[llm].api_key` → `OPENAI_API_KEY` → `AEGIS_API_KEY`. Trust: explicit → config → `AEGIS_TRUST_LEVEL` → DEV. Lab replay: `AEGIS_LAB_REPLAY_DIR` hoặc `.aegis/lab-replay`. `Agent.run()` từ chối active async loop; dùng `arun()`.

Non-Lab memory indexing có degraded telemetry path; Lab post-completion strict hơn. CLI hỗ trợ `init`, `run`, `examples`, `version`, `config show`, `config set`. Hai lỗi/gap:

- `config set` hiện chỉ in “Set”, không ghi file;
- `init` có thể lưu API key plaintext vào `~/.aegis/config.toml`, còn `config show` in toàn file, có nguy cơ lộ secret.

CLI quảng bá OpenAI, Anthropic, OpenRouter, Nvidia NIM, Ollama nhưng provider parity chưa được chứng minh. Claim setup “under 2 minutes” là self-report.

## 8. Lab runtime

Lab tự bật khi request dùng `lab`, lab mode, `search_as_code`, researcher/search program, experiment/simulation runner, tool runner hoặc tool calls. Các type chính: `AuthorityMode`, `LabPolicy`, `LabBudget`, `LabMissionSpec`, `Lab`, `LabApplication`, `LabSession`, `LabRun`, `LabDossier`, `ExecutionCellRegistry/Binding`, `ProcessExecutionCell`, `ReplayWriterLease`, search program/executor, source/claim/hypothesis/experiment/observation records, browser cell/view, skill manifest/admission/receipt, simulation/calibration/ODE/electrical signal.

Lifecycle do Python orchestration, trong khi transition/record admission quan trọng có native validation khi runtime khả dụng. Replay writer lease, event binding, record IDs/hash và final dossier cho forensic trace tốt hơn sandbox thuần. Giới hạn:

- không phải mọi repository side effect bắt buộc qua cell registry;
- native fallback/compat paths tồn tại;
- `lab.py` 12k+ dòng chứa quá nhiều trách nhiệm;
- live browser/provider/experiment phụ thuộc credential/environment;
- Lab có gate không đồng nghĩa hostile-code isolation tuyệt đối.

## 9. Research, search-as-code và browser

Luồng mong đợi: preregister mission/budget → lập typed search program → thu source qua adapter/browser → chuẩn hóa provenance → tạo claim/hypothesis → tìm rival/counterexample → experiment/verification → dossier với uncertainty/gaps.

Browser có optional Playwright, URL/navigation validation, DNS/IP checks chống literal/resolved private targets, capture URL/DOM/screenshot/accessibility/network và prompt-injection marker checks. Chưa chứng minh phòng thủ hoàn chỉnh trước DNS rebinding, proxy, browser exploit, download/file handlers hay mọi redirect chain. Chưa có evidence rằng live web research đầy đủ chạy trên mọi browser/platform/provider.

## 10. Toán học, vật lý và tín hiệu điện

Implementation hiện có:

- unit/dimension registry cho current, voltage, power, resistance và đại lượng khác; alias `μ`→`u`, `Ω`→`Ohm`;
- `PhysicalConstraint` bắt tolerance hữu hạn, không âm;
- simulation spec kiểm tra convergence/calibration metadata; held-out RMSE phải dưới tolerance khi claim calibration;
- ODE cell hỗ trợ Euler và classical RK4, coarse step so với hai half-steps, convergence tolerance, invariant callback/drift gate; thiếu tolerance là `NOT_REQUESTED`, không giả pass;
- output phải có residual cho mọi declared constraint, đúng unit, hữu hạn, trong tolerance;
- signal spec preregister sample rate/duration/max frequency/anti-alias cutoff/resistance/voltage tolerance/sensor gain-offset/reference;
- cell kiểm tra Nyquist, sample count, finite values, gain-offset, residual `V-I·R`, `P=V·I`, trapezoidal energy và RMS voltage/current;
- reference sensor error chỉ có khi reference samples tồn tại.

Đây là numerical/signal validation framework, không tự là thiết bị đo chuẩn. Accuracy phụ thuộc sensor, ADC, clock, noise/aliasing, units, numerical stiffness và model validity. Euler/RK4 step comparison không chứng minh mọi ODE; residual nhỏ chỉ chứng minh declared model trong tolerance. Chưa có metrology traceability, calibrated hardware, uncertainty budget hay independent replication. “Tối ưu từng tín hiệu điện” chỉ hợp lệ với measurement chain và protocol cụ thể.

## 11. AESE và thống kê

`aese.py` có adaptive spec/session/result, block means, standard error/critical margin, baseline-clearing, minimum sample/block, lag-one/drift/stability/precision gates, evidence ledger, hardware capability vector, workload regime, anchors, analytic prediction và out-of-domain refusal. Prediction success yêu cầu input/schema/domain hợp lệ và ít nhất 30 residual samples. Đây là fail-closed logic, không phải calibration proof ngoài domain.

| Artifact | Status | Sự thật chính |
|---|---|---|
| `current_inventory.json` | `INVENTORY_COMPLETE_DISPOSITIONS_RETAINED_MAPPING_PENDING` | 131 surfaces, 120 paths, 610 files, 11 jobs, missing scope 0 |
| `current_claim_graph.json` | `SHADOW_GRAPH_PARTIAL_MAPPING_SELECTION_DISABLED` | 46 claims, 396 edges, 92 verifications, 122 unmapped surfaces |
| `current_s2_mapping.json` | `S2_FAIL_CLOSED_MAPPING_COMPLETE` | declared-critical mapping complete; selection disabled |
| `current_affected_closure.json` | `AFFECTED_CLOSURE_PLAN_ONLY_SELECTION_DISABLED` | plan only; unknown widens to retained suite |
| `current_shadow_plan.json` | `SHADOW_PLAN_ONLY_SELECTION_DISABLED` | không execute |
| `current_validation_corpus.json` | `LOCAL_SHADOW_VALIDATION_ONLY` | synthetic planning corpus |
| `current_cost_measurement.json` | `MEASURED_EXPLORATORY_PAIRED` | ba samples, một Rust GT96 change class |

Inventory: 58 script/gate, 27 Rust unit tests, 16 Python tests, 11 hosted jobs, 5 Python benchmarks, 5 Rust benchmarks, 4 fuzz targets, 4 workflows, 1 Rust integration test. Raw items đều `NOT_VERIFIED`, `NOT_MAPPED`, risk/security `UNKNOWN`, `RETAIN_UNCHANGED`; S2 chỉ enrich subset.

Claim graph: 46 claims/code contracts/invariants, 27 code nodes, 396 edges, 20 future obligations, 131 surfaces (9 mapped/122 unmapped), 92 verifications (70 unmapped), 0 unresolved code refs. Component evidence: 10 PROVEN, 1 MEASURED, 3 SOURCE-BACKED, 32 NOT VERIFIED; cả 46 high-level claims vẫn `IMPLEMENTED / NOT VERIFIED`.

S2 có 9 declared-critical và 15 high-selection mapped records; 116 surfaces unknown. Scope chỉ `DECLARED_MAPPED_RECORDS_ONLY`, unknown có thể chứa criticality. S3/S4 chỉ lập plan; mẫu `core/rust/src/gt96.rs` would-run 1, would-skip 130, reuse 0. Đây là prediction, không phải executed safety evidence.

S5: 4 dev + 6 final cases; 5 synthetic critical targets reached, 0 miss, 5 widen events. Chỉ chứng minh planning reachability, không chứng minh mutation kill/real false-negative/non-inferiority.

S6, ba paired warm-incremental samples:

| Metric | Legacy | Selected | Planner |
|---|---:|---:|---:|
| Samples (s) | 98.718078; 57.168086; 71.778817 | 3.203918; 1.444539; 1.521086 | 8.139848; 6.937248; 6.625486 |
| Median | 71.778817 | 1.521086 | 6.937248 |
| p95 | 98.718078 | 3.203918 | 8.139848 |

Net savings min/median/max: 48.786299/63.632245/87.374312 s. Chỉ `LOCAL_EXPLORATORY_ONLY`; không claim 10x/global speedup. AESE source head `14ad392b9ba58285e3875b05bf661c04e79331fc`; từ đó đến snapshot chỉ docs/quality registry đổi, Python inputs không đổi.

## 12. Rust core và native authority

`aegis-nerve` tạo `cdylib`, `rlib`, binary `aegis-nerve-cli`; default features rỗng, `python-extension` bật PyO3. 43 modules:

`bridge_mmap`, `browser_witness`, `circuit_breaker`, `cli`, `context`, `descriptor`, `distributed`, `eac`, `evidence_index`, `execution`, `ffi`, `goal_intake`, `governance`, `gt96`, `guardrail`, `harness`, `hot_engine`, `integrations`, `ipc`, `lab`, `layout`, `learning`, `licensing`, `llm`, `memory`, `message`, `mvcc`, `orchestrator`, `physical`, `policy`, `replay`, `resource`, `resource_platform`, `runtime`, `sac`, `sandbox`, `schema`, `shm`, `skill_registry`, `speculative`, `task_ledger`, `telemetry`, `tool_gateway`.

Native responsibilities: goal intake/typed graph/progress-budget-finalization; resource contracts/admission/lease/deadline/cancel; platform adapters; replay/hash/schema recovery; evidence/hot memory/MVCC; Wasmtime sandbox; IPC/mmap/message; policy/governance/guardrail/tool gateway; LLM capabilities; telemetry; Lab admission; PyO3. `pub mod` không chứng minh production completion; platform/distributed/replay/fuzz/global-trust gaps vẫn mở.

## 13. Python ↔ Rust FFI

PyO3 build thành `aegis_cognition.aegis_nerve`; `ffi.rs` registration chia compat, EAC, hot, lab, learning, mmap, runtime, status. Contract risks: exception mapping, buffer/mmap lifetime, GIL/free-threaded behavior, serialization/hash parity, cancellation qua FFI, editable vs wheel import, feature parity và ABI/schema compatibility.

CI định nghĩa wheel/import/smoke và experimental free-threaded lanes, nhưng Tier-1 parity vẫn `NV-008`; macOS wheel được continue-on-error do hosted rustup/cargo-fmt conflict. Matrix definition không phải matrix pass.

## 14. Data, schema và persistence

Không có một database duy nhất. State nằm trong BLAKE3/hash-linked replay, Arrow IPC, mmap/shared memory/hot evidence, JSON artifacts, `.aegis/lab-replay`, session/RAG memory, `~/.aegis/config.toml`, và deploy path `/var/lib/aegis`.

| Schema | Nội dung |
|---|---|
| `resource-contract-v1.json` | task/attempt/work kind; CPU/memory/accelerator/IO/process/thread/fd; API/token budget; deadline/priority/side-effect class |
| `lease-token-v1.json` | lease ID/generation/attempt; handle không tự là authority |
| `runtime-telemetry-v1.json` | timestamp/correlation/kind/outcome/queue/active/limit |

Current-format/prefix recovery có logic/tests, nhưng old/future cross-version fixture matrix chưa đầy đủ (`NV-012`). Local package rollback không thay external persisted-fixture restore (`NV-009`).

## 15. Provider, network và integrations

Provider route có capability/fallback/budget/rate-limit classification. OpenAI là dependency/config rõ nhất; Nvidia NIM có env/client riêng. CLI liệt kê provider khác nhưng parity chưa proven. External surfaces: provider HTTP, Reqwest search, Playwright, distributed TCP, OTLP dự kiến, external deployment/health và GitHub Actions.

Policy cần giữ: timeout/cancel, retry transient có budget, idempotency/effect awareness, không layered blind retries, rate-limit/backoff evidence, SSRF guard, secret redaction, correlation. Live 429 soak (`NV-018`) và OTLP exporter (`NV-006`) chưa verified.

## 16. Security, trust và privacy

Trust levels: DEV/STAGING/PROD. Validated AgentConfig tạo canonical subject; Lab bind subject vào mission/gateway. Direct compatibility defaults còn khác nhau và provider/browser/process/benchmark receipts chưa chứng minh cùng subject xuyên cells; `NV-020` mở.

Controls có trong code/workflow: typed validation, fail-closed schema parsing, BLAKE3/Ed25519 primitives, lease fencing, Wasmtime/sandbox/skill admission, SSRF-oriented checks, injection markers, secret scan, pinned Actions, cargo-deny/audit, non-root/read-only/no-new-privileges/cap-drop containers, evidence không tự nâng status.

Residual risks:

- sandbox/container/browser không tuyệt đối;
- `.env` có local nhưng ignored; nội dung không được đọc/ghi vào hồ sơ;
- `.env.example` chỉ placeholder NIM;
- CLI có thể lưu và in plaintext API key;
- branch protection, full fuzz, Miri/ASan, signed attestation chưa verified;
- browser adversarial coverage chưa đủ claim SSRF-proof;
- thiếu license text;
- retention/deletion/export cho research/browser artifacts chưa chứng minh đầy đủ.

## 17. Resource, concurrency, cancellation và failure semantics

Resource contract biểu diễn CPU, memory, accelerator, IO, process/thread/fd, API/token budgets, deadline, priority và side-effect class. Runtime có admission, active/queue limits, leases, generation fencing, cancellation/deadline và telemetry. GT96 giữ reserve cho finalization/recovery, no-progress/cycle/finalization boundaries và retry disposition theo effect semantics.

Platform truth:

- Windows Job Object: assignment, containment, active-process, termination và deadline đã local live verified một phần; allocation-pressure kill vẫn tách riêng (`NV-002`).
- Linux cgroup v2: implementation/probe có, privileged live evidence chưa có (`NV-001`).
- macOS: cooperative/measurement-only; không claim kernel-equivalent enforcement (`NV-003`).
- distributed: loopback/container tests không chứng minh multi-machine lease, partition, backpressure hay recovery (`NV-016`).

Các tình huống phải fail closed: duplicate/reordered work, stale lease, timeout ambiguity, cancel sau side effect, retry storm, partial finalization, crash giữa append/commit, queue starvation, resource pressure và descendant process thoát containment.

## 18. Observability và operator API

Rust/Python có structured correlation và telemetry; schema v1 mang mission/task/run/attempt/lease, outcome, queue/active/limit. Communication benchmark thu payload class, latency, throughput, allocation/copy/RSS/CPU ở local scope.

Read-only operator API mặc định `127.0.0.1:8765`:

- `/`, `/health`: health schema có `truth_claim=false`;
- `/production-closure`: đọc retained production closure artifact và chỉ trả deployable khi artifact hợp lệ.

External OTLP chưa exercise correlation/retry/cancel/failure/finalization/drop (`NV-006`). Dashboard, alert và SLO production chưa proven. Health endpoint không chứng minh provider, replay recovery hoặc external dependencies sẵn sàng.

## 19. Deployment, vận hành và configuration

Root Docker build multi-stage: `rust:bookworm` builder cài pinned toolchain; runtime `python:3.14-slim`. Compose chính dùng non-root user, read-only FS, tmpfs, `no-new-privileges`, `cap_drop: ALL` và bind operator API localhost.

`deploy/docker-compose.yml` có `aegis-core`, `operator-api`, `remote-worker` với ba replica. `deploy/kubernetes.yaml` là Kubernetes deployment/config intent, không phải external evidence.

Cluster dùng `python:3.14-alpine`, TCP 9000 và `NET_ADMIN` cho chaos. Nhiều container trên cùng Docker bridge vẫn là single-host, không phải real multi-machine. Không dùng compose này để đóng `NV-016`.

Environment/config keys được code/script nhận biết; chỉ tên, không ghi value:

- core/provider: `OPENAI_API_KEY`, `AEGIS_API_KEY`, `AEGIS_TRUST_LEVEL`, `AEGIS_LAB_REPLAY_DIR`;
- NIM: `NVIDIA_NIM_API_KEY`, `NVIDIA_NIM_BASE_URL`, `NVIDIA_NIM_MODEL`;
- operator/artifact: `AEGIS_OPERATOR_ROOT`, `AEGIS_ARTIFACTS_DIR`, `AEGIS_WORKER_MODE`;
- cluster: `AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON`, `AEGIS_REAL_MULTI_MACHINE_CLUSTER_TIMEOUT_SECONDS`, `AEGIS_WORKER_ID`, `AEGIS_WORKER_PORT`, `AEGIS_WORKER_ROLE`;
- live 429: `AEGIS_LIVE_PROVIDER_429_SOAK_URL`, `AEGIS_LIVE_PROVIDER_429_SOAK_METHOD`, `AEGIS_LIVE_PROVIDER_429_SOAK_HEADERS_JSON`, `AEGIS_LIVE_PROVIDER_429_SOAK_BODY`, `AEGIS_LIVE_PROVIDER_429_SOAK_TIMEOUT_SECONDS`;
- external: `AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL`;
- QuickJS: `AEGIS_QUICKJS_FULL_INTERPRETER_RUNNER_JSON`;
- privilege: `AEGIS_RUN_PRIVILEGED_PROBES`;
- benchmark: `AEGIS_CONTAINER_IMAGE`, `AEGIS_GPU_DRIVER`;
- provenance: `GITHUB_SHA`, `GITHUB_REF`, `GITHUB_RUN_ID`, `SOURCE_DATE_EPOCH`, `ATTESTATION_OUTCOME`.

Critical config phải fail fast và không log secret. `.gitignore` không thay secret scanning/rotation nếu credential từng bị commit.

## 20. CI/CD và release gates

Có bốn workflows, tổng 11 jobs:

| Workflow | Trigger | Jobs | Giới hạn |
|---|---|---|---|
| `CI` | push/PR main, manual | rust fast, Python matrix, MSRV, Tier-1 smoke, wheel parity, Rust beta | free-threaded experimental, macOS wheel và beta có continue-on-error theo scope |
| `Deep evidence` | manual; thứ Bảy 03:17 UTC | security/replay, fuzz, Miri/sanitizer | không chạy mỗi push; cần retained runner evidence |
| `Release evidence` | tag `v*.*.*`, manual | build/test/wheel/SBOM/provenance/attestation | attestation step continue-on-error nhưng outcome phải ghi |
| `aegis-plugins` | path-scoped push/PR, manual | Windows workspace/plugin | không thay Tier-1 parity |

CI dùng concurrency cancellation và action SHA pins. Rust fast gate chạy architecture/document/AESE/secret/evidence gates, fmt, check/test/Clippy, deny và dependency/Wasmtime checks. Python sync lock/extras, Ruff, Pyright strict, Python/cross-language tests, wheel inspection và SBOM. MSRV/stable 1.98.1; Tier-1 gồm Ubuntu/Windows/macOS.

Deep workflow có workspace/nextest, deny/audit, coverage/resource/replay probes, bốn fuzz targets (`resource_contract`, `protocol_frame`, `runtime_ffi_contract`, `archive_prefix`), selected Miri và ASan. Target/workflow tồn tại không đồng nghĩa campaign current-SHA đã pass.

Hosted CI current HEAD vẫn `NOT VERIFIED`. Registry ghi attempt gần đây fail trước runner allocation, zero steps/logs/0 ms; một report từng liên hệ billing/spending nhưng run metadata không chứng minh root cause. Không retry/spam workflow đến khi owner sửa Actions billing/permission/service condition.

## 21. Verification hiện có

| Verification | Kết quả | Scope/giới hạn |
|---|---:|---|
| Focused AESE | `42 passed` | local Windows |
| Full Python cross-language | `615 passed`, 1 config warning | source head `14ad392`; Python inputs không đổi |
| Default pytest | `524 passed` | `testpaths=["tests"]` |
| Architecture fitness | `23/23` | local static/contract |
| Constitution audit | `180/180` | local policy/label |
| Rust no-default-features lib | `456 passed` | current Rust 1.98.1, `cargo test ... --locked -j 2`, 96.48 s |
| Python 3.14.7 dev environment | `PASS` | `.venv-3.14.7`, uv 0.12.9, locked dependencies + maturin 1.14.1; project build intentionally omitted for RAM safety |
| Hosted CI final SHA | `NOT VERIFIED` | chưa có retained runner run |
| Deep fuzz/Miri/ASan | `NOT VERIFIED` | definition không phải evidence |
| Tier-1 wheels | `NOT VERIFIED` | chưa đóng matrix current SHA |
| External deployment/provider/cluster | `NOT VERIFIED` | cần live/external environment |

Không chạy deep/fuzz/Miri/ASan hoặc external probes trên workstation; các suite đó cần workflow và môi trường riêng. Rust core test được chạy giới hạn `-j 2` vì thay đổi compiler/MSRV là contract change; Python runtime source không đổi nên không lặp lại full cross-language suite.

## 22. GT96 traceability

Evidence manifest có 35 rows; tất cả đang `IMPLEMENTED / NOT VERIFIED` trong current manifest vì chưa bind run ID/final SHA:

| IDs | Contract |
|---|---|
| 001–002 | goal intake, typed IR |
| 003 | runtime graph/orphan/transition |
| 004 | progress ledger/evidence gate |
| 005 | budget/finalization reserve |
| 006 | no-progress/cycle/finalization |
| 007 | orchestrator elimination/replay |
| 008 | bounded retry theo effects |
| 009 | result vs artifact/cache identity |
| 010–011 | evidence policy/index/artifact ref |
| 012 | forged/stale resource lease |
| 013 | provider capability degradation |
| 014 | execution-lane capability |
| 015–018 | cancel/deadline/admission/queue drain |
| 019 | sandbox verification gauntlet |
| 020 | finalization boundary/reserve |
| 021 | schema round-trip |
| 022–023 | context/projection/invalidation/memory |
| 024 | message/IPC/FFI |
| 025 | cache namespace |
| 026 | replay/memory rebuild |
| 027 | telemetry correlation |
| 028 | retry-storm stress/property |
| 029 | lease adversarial matrix |
| 030 | task ledger/runtime graph |
| 031–032 | migration/crash-prefix |
| 033 | 64 B–16 MiB IPC benchmark |
| 034 | provider fallback |
| 035 | semantic cache cannot become authoritative result |

`NV-011` vẫn `IN PROGRESS`: nhiều direct Rust tests đã có, nhưng full runtime integration và final-SHA manifest binding chưa đóng.

## 23. Evidence và provenance truth

`docs/architecture/evidence/current.json` là fail-closed machine source nhưng hiện là template/staging manifest:

- `commit`/`head_sha` là `CHECKOUT_HEAD`;
- timestamp `2026-08-25T00:00:00Z`;
- `LOCAL-RUST-001`, `LOCAL-PYTHON-001`, `CI-001`, `PLUGIN-001`, `DEEP-001`, `REL-001` không có current run/count và là `NOT VERIFIED`;
- current suites chưa materialize discovered/passed/failed;
- historical CI/deep/release IDs bind commit `07101ce3...`, chỉ `HISTORICAL`.

Điều này không phủ định local session results; nó nói production manifest chưa bind/attest final SHA. Không sửa placeholder bằng tay rồi gọi là provenance; gate phải materialize từ retained artifacts.

Document authority:

- `document_inventory.json` xác định current/historical docs;
- Lab master plan là narrative, không override code/registry;
- `NOT_VERIFIED_REGISTRY.md` là human companion;
- JSON registry + deployment policy là machine policy;
- root reports/nested subtree có historical snapshots;
- file này là hợp nhất để đọc, không tự đóng evidence row.

## 24. Registry `NOT VERIFIED` và cách đóng

| ID | Status | Gap | Closure evidence |
|---|---|---|---|
| NV-001 | NOT VERIFIED | Linux cgroup v2 live | privileged child/pressure/deadline/`cgroup.kill` logs |
| NV-002 | PARTIAL | Windows memory pressure | allocation-pressure observation ngoài containment đã có |
| NV-003 | NOT VERIFIED | macOS controls | capability report + cooperative cancellation, không claim kernel parity |
| NV-004 | BLOCKER | signed attestation | signed predicate + final-SHA subject digest |
| NV-005 | LOCAL ONLY | H0/H1/H2 policy | 3 representative hosts + raw p50/p95/p99/RSS/CPU/queue/reject/cancel/fairness/pressure |
| NV-006 | NOT VERIFIED | OTLP | real endpoint correlation/retry/cancel/failure/finalization/drop |
| NV-007 | NOT VERIFIED | full fuzz | seed/corpus/crash/duration/executions; >=10m nightly, >=60m weekly/release khi runner cho phép |
| NV-008 | NOT VERIFIED | Tier-1 wheels | clean build/install/import/native/runtime/metadata on Linux/Windows/macOS/Python lanes |
| NV-009 | LOCAL DRILL | external restore | N→N+1→N + persisted fixture restore/replay prefix |
| NV-010 | NOT VERIFIED | branch protection | owner ruleset + proof direct/stale push bị chặn |
| NV-011 | IN PROGRESS | GT96 closure | mọi row bind evidence ID/final SHA |
| NV-012 | NOT VERIFIED | schema migration | old/current/future/add/remove/unknown/truncated/corrupt/partial-tail fixtures |
| NV-013 | NOT VERIFIED | Miri/ASan | exact scope/toolchain/features/exclusions/logs |
| NV-014 | LOCAL ONLY | IPC matrix | retained 64 B–16 MiB latency/throughput/allocation/copy/RSS/CPU |
| NV-015 | NOT VERIFIED | hosted runner | owner-side fix + rerun CI/plugins/deep/release + retained IDs |
| NV-016 | BLOCKER | real multi-machine | named hosts + RTT/loss/partition/lease/recovery/backpressure/cancel |
| NV-017 | BLOCKER | full QuickJS | pinned full artifact + semantic corpus/distribution/failures/hash |
| NV-018 | BLOCKER | live 429 | real/independent endpoint + retry/backoff/idempotency/route/budget |
| NV-019 | BLOCKER | external deploy | clean target + digest/startup/health/replay/rollback |
| NV-020 | LOCAL LAB ONLY | global trust binding | approved precedence + same subject hash across every cell/fresh process |

Policy đánh dấu 15 row còn lại non-blocking cho quyết định deployment hẹp; không có nghĩa chúng không quan trọng hay production-ready được proven.

## 25. Mâu thuẫn và drift

| Mâu thuẫn | Truth hiện tại | Hệ quả |
|---|---|---|
| audit cũ: SHA `f9645...`, 58 modified + 17 untracked | snapshot mới parity/clean trước doc | audit cũ phải thay |
| requirements pytest-asyncio `<1` | root dev và export `>=1,<2` | đã đồng bộ; không còn active mismatch |
| pin Python 3.14.7 | `.venv` 3.14.0 giữ lại; `.venv-3.14.7` đã tạo | fallback cũ cố ý giữ, preferred env đã khớp |
| CI uv 0.12.9 | PATH uv 0.9.9 | PATH cũ không là authority; CI/release/deep dùng pin mới |
| Maturin exact 1.14.1 | PATH 1.13.3; dev env mới đúng | build phải dùng locked/dev env |
| README/CONTRIBUTING test counts cũ | retained 456 Rust/615 Python | docs không là evidence |
| changelog 0.1.0 | không Git tag | release history lệch |
| BUSL declared | thiếu license text | legal/distribution gap |
| package URLs khác remote | chưa verified ownership | release identity gap |
| CLI quảng bá 5 providers | parity chưa proven | overclaim risk |
| `config set` nói đã set | không ghi file | behavior bug |
| `config show` in config | có thể lộ key | security bug |
| compose nói multi-machine | cùng host/bridge | không đóng NV-016 |
| README timing/performance | không comparator current | self-reported |
| current evidence `CHECKOUT_HEAD` | HEAD thật `1e77ff9...` | provenance chưa materialize |
| hai Python distributions | authority chưa thống nhất | import/release ambiguity |

## 26. Technical debt và ưu tiên

1. Production provenance/blockers: NV-004/016/017/018/019.
2. Secret/config UX: redact, không lưu/in key dễ lộ, hoàn thiện `config set`.
3. Packaging authority: canonical root wheel và role của core Python distribution.
4. License/release identity: license text, URLs, tags, changelog.
5. Lab cohesion: tách `lab.py` chỉ sau characterization tests; không mass-refactor vì thẩm mỹ.
6. Global authority: same subject/cell policy qua provider/browser/process/benchmark/compat.
7. AESE calibration: mapping, mutation/incident corpus, representative workloads trước cutover.
8. Replay evolution: version fixtures, crash prefix, external restore.
9. Platform enforcement: privileged Linux, Windows pressure, explicit macOS semantics.
10. Docs drift: README/CONTRIBUTING/CHANGELOG và wording local-vs-proven.

Graph fan-in/test concentration là structural indicator, không phải performance metric. Không tối ưu chỉ vì fan-in lớn.

## 27. Threat/failure model cần giữ

- forged/stale lease hoặc duplicate completion;
- retry non-idempotent side effect sau ambiguous timeout;
- child process thoát containment hoặc sống sau cancel;
- DNS/redirect biến public URL thành private target;
- prompt injection vượt mission policy;
- 429 gây retry amplification/spend runaway;
- đúng hash nhưng sai mission/subject/environment binding;
- partial replay tail mất committed prefix;
- old archive vỡ sau schema change;
- cache candidate thành authoritative result;
- AESE bỏ security/contract test do unmapped edge;
- benchmark sai do warm cache/workload khác/bỏ planner cost;
- model residual pass trong mô hình vật lý sai;
- sensor/clock sai nhưng output có vẻ chính xác;
- CLI/log lộ API key;
- docs được dùng thay signed evidence;
- single-host containers bị gọi multi-machine proof.

T2/T3 changes phải có direct test/retained evidence cho failure path áp dụng.

## 28. Ma trận hoàn thiện

| Năng lực | Code | Local | External/production | Authority |
|---|---|---|---|---|
| Agent facade | có | tests có | chưa full external | Python |
| Native resource/runtime | có | mạnh local | platform live thiếu | Rust/adapters |
| Lab lifecycle/evidence | có | coverage lớn | multi-cell live thiếu | split Python/Rust |
| Search-as-code | có | contracts | research quality chưa calibrated | Python/adapters |
| Browser | optional Playwright | local/mock | cross-platform/security thiếu | Python cell |
| Skill/tool/process | có | admission/receipt | descendant/global bind thiếu | registry/native gates |
| Simulation/ODE | có | numerical tests | domain scientific validity riêng | Python cells |
| Electrical signal | có | formula/tolerance | calibrated hardware thiếu | Python cell |
| Benchmark v2 | có | protocol tests | representative cross-hardware thiếu | Python protocol |
| AESE | có | shadow + 3 pairs | non-inferiority/cutover thiếu | legacy authoritative |
| Replay/hash | có | tests/drills | migration/restore thiếu | Rust/archive |
| Distributed | primitives/compose | loopback/single host | real multi-machine thiếu | runtime/worker |
| Observability | internal/API | local | OTLP/SLO/alerts thiếu | split |
| CI/CD | workflows | definitions inspected | hosted current SHA thiếu | intended Actions |
| Release provenance | logic/SBOM | pieces local | signed attestation thiếu | not authoritative |
| Production deploy | manifests | local artifacts | external smoke thiếu | blocked |

## 29. Trình tự đóng an toàn

1. Sửa CLI secret/config và packaging/license identity bằng diff nhỏ có test.
2. Giải quyết owner-side GitHub Actions một lần; sau đó chạy một current-final-SHA set, không spam retry.
3. Materialize evidence manifest từ retained runs, không sửa placeholder thủ công.
4. Đóng NV-004/017/018/019 trong môi trường đúng.
5. Chạy NV-016 trên nhiều máy thật.
6. Hoàn tất Tier-1 wheels, fuzz/sanitizer, migration/restore, platform enforcement.
7. Đóng NV-020 cross-cell authority.
8. Mở rộng AESE mapping + mutation/incident corpus + representative workloads.
9. Chỉ cân nhắc selective cutover khi critical false-negative bound và production non-inferiority đạt policy được phê duyệt.
10. Sau khi behavior được khóa, tách Lab theo ownership với diff nhỏ.

Không có blocker ngăn maintenance/local shadow measurement. Có blocker thật đối với production declaration, release provenance và AESE authority cutover.

## 30. Lệnh tái lập an toàn

```powershell
# Nhẹ
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
uv sync --locked --all-extras --dev
.\.venv\Scripts\python.exe scripts\document_consistency_gate.py
.\.venv\Scripts\python.exe scripts\constitution_audit.py
.\.venv\Scripts\ruff.exe check aegis_cognition tests scripts
.\.venv\Scripts\pyright.exe

# Tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests core/python/tests.py -q

# Nặng hơn
cargo fmt --all -- --check
cargo check --workspace --no-default-features
cargo test --manifest-path core/rust/Cargo.toml --lib --no-default-features

# Evidence; dùng exact args trong workflow/runbook
.\.venv\Scripts\python.exe scripts\aese_preflight.py
.\.venv\Scripts\python.exe scripts\evidence_consistency_gate.py
```

Deep fuzz, Miri, ASan, privileged probes, multi-machine, live provider và external deployment phải chạy qua đúng workflow/runbook với budget/artifact retention; không chạy ngẫu nhiên trên workstation.

## 31. Nguồn sự thật chi tiết

**Manifest/toolchain:** `pyproject.toml`, `core/python/pyproject.toml`, `requirements.txt`, `uv.lock`, `.python-version`, `Cargo.toml`, `core/rust/Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`, `deny.toml`, `fuzz/Cargo.toml`, các plugin/POC `Cargo.toml`.

**Runtime:** `aegis_cognition/__init__.py`, `agent.py`, `application.py`, `lab.py`, `aese.py`, `benchmark.py`, `runtime.py`, `observability.py`; `core/python/aegis_adapter.py`, `aegis/provider.py`, `browser_playwright_runtime.py`, `operator_api.py`; `core/rust/src/lib.rs`, `ffi.rs`, `lab.rs`, `gt96.rs`, `runtime.rs`, `resource.rs`, `resource_platform.rs`, `replay.rs`, `sandbox.rs`.

**Contract/evidence:** `schemas/*.json`; bảy `quality/registry/current_*.json`; `docs/architecture/evidence/current.json`; `not_verified_registry.json`; `NOT_VERIFIED_REGISTRY.md`; `deployment_policy.json`; GT96 traceability; `document_inventory.json`; Lab master plan.

**CI/deploy:** bốn `.github/workflows/*.yml`; `deploy/docker-compose.yml`, `deploy/kubernetes.yaml`, `cluster/docker-compose.yml`, `cluster/Dockerfile`.

## 32. Final truth statement

Tại snapshot này, AEGIS-COGNITION là beta hybrid runtime giàu contract và local verification, có Lab framework thực sự cho research/browser/experiment/simulation/evidence thay vì sandbox thuần. Nó có nhiều primitive nghiêm ngặt cho authority, replay, resource, toán-vật lý và adaptive evidence. Tuy nhiên authority vẫn chia Python/Rust, packaging/license/config còn drift, AESE chỉ shadow, evidence manifest chưa bind final SHA, hosted/external/platform proof còn thiếu, và năm production blockers còn mở.

Claim mạnh nhất được phép: **implementation rộng và local evidence đáng kể đã tồn tại, với fail-closed registries giữ phần chưa biết ở trạng thái chưa xác minh.**

Không được claim: **production-ready, secure tuyệt đối, real multi-machine proven, scientifically accurate tổng quát, cross-hardware optimized, AESE non-inferior, full QuickJS validated, live-provider resilient, hoặc release provenance signed.**

## 33. Current working-tree version-alignment delta (2026-09-04)

Phần này supersede các giá trị local drift của committed snapshot khi mô tả
working tree hiện tại. Tất cả thay đổi dưới đây đang **uncommitted**; không có
commit, push hoặc workflow retry nào được thực hiện.

| Hạng mục | Trạng thái hiện tại | Evidence/giới hạn |
|---|---|---|
| Python project baseline | `>=3.14,<3.16`; preferred `3.14.7` | `.python-version`, pyproject và CI/release đã cùng policy |
| Python dev environment | `.venv-3.14.7` dùng CPython `3.14.7`, 40 locked packages và `maturin 1.14.1` | `uv sync --locked --no-install-project --all-extras --dev`; project native build cố ý chưa chạy vì RAM |
| Existing `.venv` | CPython `3.14.0` giữ nguyên | fallback/rollback an toàn; không xoá hoặc mutate |
| Rust stable/MSRV | exact `1.98.1`; workspace MSRV `1.98` | rustfmt, check và Rust lib `456 passed` trên local; upstream patch fix được ghi ở ADR-004 |
| uv | `0.12.9` ghim trong `ci.yml`, `deep.yml`, `release.yml` | `uv lock --check` resolved 42 packages |
| Python export | `pytest-asyncio>=1,<2` | khớp root `pyproject.toml`; generator cũng đã sửa để không tái sinh range cũ |
| Editor rules | Python 3.14 baseline; `3.14t/3.15t` chỉ experimental/verified lane | loại bỏ chỉ thị active dựa trên Python 3.13 No-GIL |
| Historical references | Rust `1.97.1`, Python 3.13 và captured old dependency strings còn trong snapshot/evidence history | cố ý giữ bất biến để không làm sai provenance; không phải active build input; Rust 1.97.1 đã gỡ khỏi máy |

Global Python 3.11/3.13 và các binary PATH cũ **không bị uninstall**. Rust
1.97.1 đã được gỡ sau khi xác nhận workspace chạy bằng 1.98.1; uv-managed
Python 3.13.9 và 3.15.0a1 cũng đã gỡ vì không thuộc support range của AEGIS.
Xoá Python hệ thống hoặc binary PATH mà chưa kiểm tra toàn bộ project khác là
thao tác destructive ngoài phạm vi repo và có thể phá môi trường máy. `.venv`
3.14.0 vẫn giữ làm fallback; `.venv-3.14.7` là preferred dev environment.

Cleanup đã giải phóng xấp xỉ `1.18 GB` theo phép đo trước/sau; stable Rust,
nightly Rust (deep/fuzz lane), Python 3.14.0/3.14.7 và các package environment
đang dùng vẫn còn nguyên.

Các gate sau version alignment: `uv lock --check` PASS, architecture fitness
`23/23`, document consistency PASS, evidence consistency `8 passed`, Rust fmt
PASS, Rust workspace check PASS và Rust core `456 passed`. Full Python project
install/wheel trên CPython 3.14.7, hosted CI current SHA, Tier-1 wheel parity,
deep fuzz/Miri/ASan và external deployment vẫn `NOT VERIFIED`.
