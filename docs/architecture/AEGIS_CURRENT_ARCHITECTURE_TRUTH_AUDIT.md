# AEGIS-COGNITION — Hồ sơ sự thật toàn dự án

**Document class:** current project truth dossier

**Snapshot date:** 2026-09-11 (Asia/Saigon)

**Repository:** `C:\Users\ADMIN\AEGIS-COGNITION`

**Branch:** `codex/aegis-ci-evidence-gates`

**Parent HEAD at dossier capture:** `f51cedc6089ddd66e454d4a00b3eb4ac1be99b80`

**Upstream at dossier capture:** `origin/codex/aegis-ci-evidence-gates` cùng SHA; ahead `0`, behind `0`

**Committed snapshot baseline:** worktree clean at the recorded parent HEAD

**Dossier capture (2026-09-11):** the six-path native FFI/profile-map change set was
reviewed in a dirty worktree; no commit or push was performed at capture. Local
artifacts record the dirty-worktree fingerprint; the exact delta is in Section 33.

**Declared project version:** `0.1.0`

**Production readiness:** `NOT VERIFIED`; policy vẫn còn năm production blocker.

**Artifact-weighted progress gate (2026-09-11):** `72.93%` readiness across
15 domains, with `overall_ok=false`; this is a measured local artifact index,
not a production or release claim. The authoritative report is
`artifacts/progress_gate_report.json`; the current capture is recorded at
`artifacts/verification/progress-gate-current-20260911-r11.meta.json`.

## 0. Phạm vi, authority và nhãn bằng chứng

Đây là hồ sơ hợp nhất của committed snapshot tại SHA trên và current working-tree delta được ghi rõ ở Section 33. “Toàn bộ” ở đây nghĩa là mọi bề mặt có ý nghĩa để hiểu, build, test, vận hành, đánh giá độ tin cậy và ra quyết định. Tài liệu không sao chép từng byte của lockfile, 633 tracked files hay từng record của registry, vì làm vậy sẽ tạo một nguồn phụ dễ lỗi thời. Các nguồn máy gốc được dẫn ở Section 31.

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

- AESE registry/drift checks: the current registry-dependent group passes
  `64 passed` after the profile-map registry regeneration;
- post-admission AESE repair: exact five registry-drift regressions pass in
  `120.58s`; inventory and claim-graph `--check` both pass after regeneration;
  checkout-bound evidence is
  `artifacts/suites/aese-drift-after-profile-map-20260911-r6.json`; the older
  repair ledgers remain historical:
  `artifacts/verification/aese-drift-regression-20260910-r8.log`,
  `artifacts/verification/aese-inventory-check-r9-20260910.log`, and
  `artifacts/verification/aese-claim-graph-check-r9-20260910.log`;
- pre-admission full Python baseline: `641 passed, 1 skipped` in `646.68s`, exit
  code 0 under CPython 3.14.7 on Windows with a fixed external pytest base
  directory; ledger `artifacts/verification/full-python-suite-20260910-r9.txt`
  and metadata `artifacts/verification/full-python-suite-20260910-r9.meta.json`;
- current Rust library: `544 passed, 0 failed, 0 ignored` in the serial direct
  run; the recorded local artifact is
  `artifacts/verification/rust-library-resolved-state-path-final-20260911-r1.meta.json`. This is local
  Windows evidence and does not close hosted, cross-platform or external
  environment gates. A separate `uv` wrapper attempt failed before test start
  with `STATUS_DLL_NOT_FOUND` and is not counted as a test result;
- post-admission full Python attempt: `656 passed, 5 failed, 1 skipped` in
  `1500.79s`, exit code 1; all five failures were AESE generated-registry drift
  and the exact five drift tests pass after regeneration. Ledger
  `artifacts/verification/full-python-suite-20260910-r10.txt`; this is not a
  full post-regeneration PASS claim.
- pre-profile-map full Python suite: `708 passed, 1 skipped` in `510.02s`, exit
  code 0; ledger `artifacts/verification/full-python-suite-20260911-r4.txt` and
  metadata `artifacts/verification/full-python-suite-20260911-r4.meta.json`.
  This is historical local Windows/CPython 3.14.7 evidence, not the current
  unpartitioned verdict.
- current Python regression partition: `649/650` non-AESE tests pass with `1`
  skip, and all `64/64` AESE registry/drift tests pass. Evidence is split between
  `artifacts/suites/python-non-aese-current-20260911-r1.json` and
  `artifacts/suites/aese-drift-after-profile-map-20260911-r6.json`; this is
  explicit partition coverage, not a single-process full-suite claim.
- current unpartitioned Python suite: `713 passed, 1 skipped` in `512.22s`,
  exit code `0`, from `uv run --locked pytest -q` on the current Windows /
  CPython `3.14.7` checkout. The direct capture is retained at
  `artifacts/verification/full-python-current-direct-20260912-r1.log` with
  metadata in `artifacts/verification/full-python-current-direct-20260912-r1.meta.json`;
  the worktree was dirty, so this is local checkout evidence rather than a
  clean-commit or hosted-CI claim.
- current project-scope Python quality gate: Ruff and strict Pyright both pass
  for `aegis_cognition`, `core/python`, `scripts` and `tests`; the focused
  regression after the export typing compatibility fix and package formatting
  pass `455/455`. Evidence:
  `artifacts/verification/python-quality-project-scope-20260911-r3.meta.json`
  and `artifacts/verification/python-quality-regression-20260911-r2.meta.json`.
  A raw repository-wide Ruff scan still reports 321 findings in auxiliary
  directories/POCs; that scan is retained as `NOT VERIFIED` and is not used to
  claim project-scope quality failure.
 - current Rust `python-extension` feature library: `545 passed, 0 failed,
  0 ignored` in the final-source serial feature run. The recorded local evidence is
  `artifacts/verification/rust-python-extension-resolved-state-path-final-20260911-r1.meta.json`. This is
  local Windows evidence only; hosted and cross-platform parity remain open.
- current Rust workspace compile across core, plugins and POCs with all targets:
  `cargo check --workspace --all-targets` passes in `129s`. Evidence:
  `artifacts/verification/rust-workspace-check-20260911-r1.meta.json`.
- current local replay schema-migration fixture matrix: `541 passed, 0 failed`
  in the full Rust library after adding explicit current/old/future schema,
  record-size, unknown-hash, truncation, corruption and valid-prefix cases;
  this proves fail-closed local reader/recovery behavior but does not prove an
  external expand/backfill/contract migration rehearsal or restore test.
  Evidence: `artifacts/verification/rust-replay-schema-migration-20260911-r2.meta.json`.
- current consolidated check refresh: replay endurance and replay chaos
  artifacts validate with `returncode=0`; the run still exits non-zero because
  external deployment, real multi-machine soak, E2E and benchmark evidence are
  not available. The authoritative aggregate is `artifacts/run_checks_report.json`,
  with run provenance at
  `artifacts/verification/run-checks-current-20260911-r2.meta.json`.
- benchmark compilation remains `NOT VERIFIED`: both parallel and single-job
  `cargo bench --bench nerve_bench --no-run` attempts terminated while compiling
  `cranelift-codegen` before producing a Criterion artifact. The captured
  attempts are `artifacts/verification/cargo-bench-no-run-20260911-r2.meta.json`,
  `artifacts/verification/cargo-bench-no-run-20260911-r3.log`, and the
  single-job/codegen-units-256 attempt at
  `artifacts/verification/cargo-bench-no-run-20260911-r4.meta.json`; the local
  machine had roughly `0.82 GB` free of `4 GB` physical memory at inspection.
- Rust lint/placement continuation: strict Clippy passes in no-default library
  mode and in the `python-extension` library mode; `cargo fmt --check` also
  passes. The final profile-repository-cache feature run is bound to
  `artifacts/verification/rust-clippy-resolved-state-path-20260911-r1.meta.json`;
  the no-default current-source lane remains recorded at
  `artifacts/verification/rust-clippy-no-default-20260911-r9.meta.json`.
- Native GT96 target-evolution continuation: `2/2` focused tests pass. Bound
  targets preserve kind/stable identity/owner and may only narrow roots,
  network or external effects; revision changes remain allowed, and legacy
  unbound targets retain their binding compatibility. Evidence:
  `artifacts/verification/gt96-target-evolution-20260911-r1.meta.json`.
- Native Lab execution-cell manifest continuation: `20/20` native-focused
  tests pass, including matching, mismatched and missing prior manifest
  regressions. Evidence:
  `artifacts/verification/native-lab-binding-focused-20260911-r6.meta.json`.
- Current Python/native contract slice: Lab runtime `363/363`, GoalContract
  `56/56`, runtime coordination `33/33`, and desktop protocol `10/10` pass.
  Evidence:
  `artifacts/verification/python-lab-runtime-isolated-20260911-r1.meta.json`,
  `artifacts/verification/uv-contracts-rest-20260911-r1.meta.json`, and the
  canonical full-slice run below.
- Canonical desktop-first integration across all four current contract groups:
  `462 passed, 0 failed` in `25.48s` under `uv run --locked`; this order keeps
  the desktop profile setup deterministic; the reversed Lab-first order is also
  covered separately below.
  Evidence: `artifacts/verification/uv-native-contracts-canonical-order-20260911-r1.meta.json`.
 - The reversed explicit order (Lab → Goal → runtime → desktop) now passes
  `462/462` against the current profile-keyed native repositories after state-path
  normalization. Evidence is bound to
  `artifacts/verification/uv-native-contracts-lab-first-resolved-state-path-final-20260911-r1.meta.json`.
  The current native binding policy uses per-(resolved state path, profile)
  registries, permits explicitly isolated paths in one process, and fails closed
  when one state path is reused by another profile.
- architecture fitness: `23/23`;
- constitution audit: `180/180`.
- focused authority boundary slice: `10/10` (process interruption, replay
  writer lease, sealed execution-cell registry, global attempt budget and
  native adapter-retry guard), retained at
  `artifacts/verification/local-authority-focused-20260910.txt`;
- focused Goal/Target and progress slice: `25/25`, retained at
  `artifacts/verification/goal-target-focused-20260910.txt`.
- current R6 focused Goal/Target + Lab runtime slice: `34/34`, retained at
  `artifacts/verification/native-managed-internal-regression-20260910.log`.

Các số này không thay hosted CI, Tier-1 wheel parity, privileged enforcement, fuzz dài, sanitizer, external deployment, live provider, real multi-machine cluster hay signed attestation.

Năm production blockers theo `deployment_policy.json`:

1. external signed attestation (`NV-004`);
2. real multi-machine TCP cluster soak (`NV-016`);
3. full QuickJS interpreter cold-start (`NV-017`);
4. live provider HTTP 429 soak (`NV-018`);
5. external deployment smoke (`NV-019`).

Trong local deployment/readiness report hiện tại, ba blocker đang active là
`NV-004`, `NV-016` và `NV-019`; `NV-017` và `NV-018` vẫn là policy blocker
được deployment manifest giữ lại, nhưng artifact hiện hành không đánh dấu chúng
active. Vì policy yêu cầu mọi blocker phải clear, trạng thái deployable vẫn là
`false`.

Kết luận: implementation và local governance rộng, nhưng không được tuyên bố production-ready, scientifically valid tổng quát, secure tuyệt đối, zero-copy toàn cục hay nhanh hơn tổng quát.

## 2. Snapshot Git và quy mô repository

| Thuộc tính | Giá trị | Class |
|---|---:|---|
| Remote | `https://github.com/thanhmuefatty07/AEGIS-COGNITION.git` | `PROVEN` |
| Branch | `main` | `PROVEN` |
| HEAD/upstream | `8d15a34c3e28928aa6ca97a258d17a93bf952169` | `PROVEN` tại snapshot |
| Commit count | 323 | `PROVEN` tại snapshot |
| Git tags | không có | `PROVEN` tại snapshot |
| Tracked files | 633 | `PROVEN` |
| Loose objects | 4,409; khoảng 41.52 MiB | `MEASURED` |
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
| `deploy` | 2 | `docs/archive/planning` | 2 |

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
| Declared license | Proprietary / All Rights Reserved; written permission required |
| Rust publishing | `publish=false` |

Không có Git tag. `CHANGELOG.md` có section `0.1.0` ngày 2026-06-15 nhưng tag tương ứng không tồn tại. README và metadata hiện trỏ tới `LICENSE.txt`, trong đó giữ toàn bộ quyền và yêu cầu written permission cho mọi hình thức sử dụng. Quyền xem/fork do GitHub cấp qua Terms of Service là quyền của nền tảng, không phải license sử dụng mã nguồn.

`core/rust/src/licensing.rs` có Ed25519-related logic nhưng vẫn có production-oriented comments/soft-check paths. Không được suy ra commercial enforcement production hoàn chỉnh. Repository/issue metadata hiện trỏ remote GitHub thực tế; website/docs domains và ownership/availability vẫn chưa verified.

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
| CI uv | exact `0.12.9` | workflows |
| nextest/deny/audit | `0.9.143` / `0.20.2` / `0.22.0` | workflows |

Bash, PowerShell, YAML và HTML có mặt nhưng không pin runtime riêng. Không có `package.json`; QuickJS/Wasm là runtime-artifact concern, không phải Node application package.

### 4.2 Quan sát local

| Công cụ | Version | Đánh giá |
|---|---:|---|
| `python` trên PATH | 3.11.9 | ngoài support range |
| Python launcher default | 3.13 | ngoài support range |
| `.venv` Python | 3.14.7 | môi trường dev/local chuẩn; native extension đã được kiểm chứng |
| uv-managed Python | 3.14.7 | runtime preferred đã cài; `.python-version` khớp |
| rustc | `1.98.1`, commit `48a229cea`, LLVM `22.1.8`, `x86_64-pc-windows-msvc` | khớp stable pin; workspace check passed |
| cargo | `1.98.1` | khớp stable pin; workspace check passed |
| Git | 2.49.0.windows.1 | local |
| PATH uv | 0.12.9 | khớp project/CI/release/deep pin |
| PATH Maturin | 1.13.3 | đây là binary global; project `.venv` đã cài và xác minh Maturin exact `1.14.1` |
| PATH Ruff | 0.1.15 | thấp hơn `>=0.3` |
| PATH Pytest | 9.0.2 | vượt `<9` |
| PATH Pyright | không tìm thấy ở initial probe | không dùng làm authority |

Repository `.venv`: `aegis-cognition 0.1.0`, `blake3 1.0.9`, `fastapi 0.141.1`, `openai 1.109.1`, `python-dotenv 1.2.2`, `PyYAML 6.0.3`, `uvicorn 0.52.3`, `playwright 1.62.0`, `pytest 8.4.2`, `pytest-asyncio 1.4.0`, `ruff 0.16.3`, `pyright 1.1.411`, `maturin 1.14.1`.

Global environment từng có OpenAI 2.33.0, pytest 9.0.2, pytest-asyncio 0.21.2, Ruff/Maturin cũ; vì vậy cảnh báo `asyncio_default_fixture_loop_scope` là environment drift. Dùng `.venv` hoặc `uv sync --locked`.

## 5. Packaging, dependency và locks

Root dùng Maturin/PyO3: module `aegis_cognition.aegis_nerve`, manifest `core/rust/Cargo.toml`, feature `python-extension`, Python source root, include `core/python/*.py` và `core/python/aegis/*.py`, CLI `aegis_cognition.cli:main`.

Python runtime deps: `blake3>=0.4,<2`, `fastapi>=0.110,<1`, `openai>=1.40,<2`, `python-dotenv>=1,<2`, `pyyaml>=6,<7`, `uvicorn[standard]>=0.27,<1`. Browser extra: Playwright `>=1.40,<2`. Dev: Pyright `>=1.1,<2`, pytest `>=8,<9`, pytest-asyncio `>=1,<2`, Ruff `>=0.3,<1`.

`requirements.txt` là compatibility export và đã khớp root ở `pytest-asyncio>=1,<2`; `uv.lock` là authoritative resolution, 116,173 bytes.

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

Core direct deps: Thiserror 2, Memmap2 0.9, Parking_lot 0.12, Ed25519-dalek 2, PyO3 0.29.2, Arrow 54, FlatBuffers 24.12.23, Syn 2, Proc-macro2 1, Quote 1, Serde/JSON 1, Regex 1.10, Tracing 0.1, Wasmtime exact 47.0.4, WAT 1.251.0, BLAKE3 1.5.0, xxhash-rust 0.8.10, Aho-Corasick 1.1.2, Tokio 1, Rayon 1.12; Windows thêm windows-sys 0.61.2. Dev deps: Tempfile 3, Criterion 0.5, Proptest 1.4.0.

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

Non-Lab memory indexing có degraded telemetry path; Lab post-completion strict hơn. CLI hỗ trợ `init`, `run`, `examples`, `version`, `config show`, `config set`. Bề mặt CLI canonical hiện ghi cấu hình atomically, validate key/value, không echo secret, redacts `config show` và trả exit status khác 0 cho usage/config/run failure; các hành vi này có local regression evidence trong Section 33. Compatibility-only `core/python/aegis_cli.py` vẫn là một bề mặt độc lập cần migration hoặc secret-store policy trước khi có thể gọi package/CLI ownership hoàn tất.

CLI quảng bá OpenAI, Anthropic, OpenRouter, Nvidia NIM, Ollama nhưng provider parity chưa được chứng minh. Claim setup “under 2 minutes” là self-report.

## 8. Lab runtime

Lab tự bật khi request dùng `lab`, lab mode, `search_as_code`, researcher/search program, experiment/simulation runner, tool runner hoặc tool calls. Các type chính: `AuthorityMode`, `LabPolicy`, `LabBudget`, `LabMissionSpec`, `Lab`, `LabApplication`, `LabSession`, `LabRun`, `LabDossier`, `ExecutionCellRegistry/Binding`, `ProcessExecutionCell`, `ReplayWriterLease`, search program/executor, source/claim/hypothesis/experiment/observation records, browser cell/view, skill manifest/admission/receipt, simulation/calibration/ODE/electrical signal.

Lifecycle do Python orchestration, trong khi transition/record admission quan trọng có native validation khi runtime khả dụng. Replay writer lease, per-key external-effect lease, event binding, record IDs/hash và final dossier cho forensic trace tốt hơn sandbox thuần. Giới hạn:

- không phải mọi repository side effect bắt buộc qua cell registry;
- native fallback/compat paths tồn tại;
- `lab.py` 12k+ dòng chứa quá nhiều trách nhiệm;
- live browser/provider/experiment phụ thuộc credential/environment;
- per-key external-effect lease chỉ là local advisory coordination, không khóa provider hoặc kernel/network;
- Lab có gate không đồng nghĩa hostile-code isolation tuyệt đối.

## 9. Research, search-as-code và browser

Luồng mong đợi: preregister mission/budget → lập typed search program → thu source qua adapter/browser → chuẩn hóa provenance → tạo claim/hypothesis → tìm rival/counterexample → experiment/verification → dossier với uncertainty/gaps.

Browser có optional Playwright, URL/navigation validation, DNS/IP checks chống literal/resolved private targets, capture URL/DOM/screenshot/accessibility/network và prompt-injection marker checks. Explicit Goal targets now narrow host-based search/browser/generic network reads, and exact declared external effects use local advisory leases. Chưa chứng minh phòng thủ hoàn chỉnh trước DNS rebinding, proxy, browser exploit, download/file handlers hay mọi redirect chain. Chưa có evidence rằng live web research đầy đủ chạy trên mọi browser/platform/provider.

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
| `current_inventory.json` | `INVENTORY_COMPLETE_DISPOSITIONS_RETAINED_MAPPING_PENDING` | 152 surfaces, 141 paths, 656 live files, 11 jobs, missing scope 0 |
| `current_claim_graph.json` | `SHADOW_GRAPH_PARTIAL_MAPPING_SELECTION_DISABLED` | 46 claims, 396 edges, 92 verifications, 143 unmapped surfaces |
| `current_s2_mapping.json` | `S2_FAIL_CLOSED_MAPPING_COMPLETE` | declared-critical mapping complete; selection disabled |
| `current_affected_closure.json` | `AFFECTED_CLOSURE_PLAN_ONLY_SELECTION_DISABLED` | plan only; unknown widens to retained suite |
| `current_shadow_plan.json` | `SHADOW_PLAN_ONLY_SELECTION_DISABLED` | không execute |
| `current_validation_corpus.json` | `LOCAL_SHADOW_VALIDATION_ONLY` | synthetic planning corpus |
| `current_cost_measurement.json` | `MEASURED_EXPLORATORY_PAIRED` | ba samples, một Rust GT96 change class |

Inventory: 58 script/gate, 27 Rust unit tests, 16 Python tests, 11 hosted jobs, 5 Python benchmarks, 5 Rust benchmarks, 4 fuzz targets, 4 workflows, 1 Rust integration test. Raw items đều `NOT_VERIFIED`, `NOT_MAPPED`, risk/security `UNKNOWN`, `RETAIN_UNCHANGED`; S2 chỉ enrich subset.

Claim graph: 46 claims/code contracts/invariants, 27 code nodes, 396 edges, 20 future obligations, 152 surfaces (9 mapped/143 unmapped), 92 verifications (70 unmapped), 0 unresolved code refs. Component evidence: 10 PROVEN, 1 MEASURED, 3 SOURCE-BACKED, 32 NOT VERIFIED; cả 46 high-level claims vẫn `IMPLEMENTED / NOT VERIFIED`.

S2 có 9 declared-critical và 15 high-selection mapped records; 137 surfaces unknown. Scope chỉ `DECLARED_MAPPED_RECORDS_ONLY`, unknown có thể chứa criticality. S3/S4 chỉ lập plan; mẫu `core/rust/src/gt96.rs` would-run 1, would-skip 151, reuse 0. Đây là prediction, không phải executed safety evidence.

S5: 4 dev + 6 final cases; 5 synthetic critical targets reached, 0 miss, 5 widen events. Chỉ chứng minh planning reachability, không chứng minh mutation kill/real false-negative/non-inferiority.

S6, ba paired warm-incremental samples:

| Metric | Legacy | Selected | Planner |
|---|---:|---:|---:|
| Samples (s) | 98.718078; 57.168086; 71.778817 | 3.203918; 1.444539; 1.521086 | 8.139848; 6.937248; 6.625486 |
| Median | 71.778817 | 1.521086 | 6.937248 |
| p95 | 98.718078 | 3.203918 | 8.139848 |

Net savings min/median/max: 48.786299/63.632245/87.374312 s. Chỉ `LOCAL_EXPLORATORY_ONLY`; không claim 10x/global speedup. Đây là phép đo lịch sử giữ nguyên từ source head `14ad392b9ba58285e3875b05bf661c04e79331fc`; current source head là `8d15a34c3e28928aa6ca97a258d17a93bf952169`, nên số đo không được trình bày như benchmark của toàn bộ working tree.

## 12. Rust core và native authority

`aegis-nerve` tạo `cdylib`, `rlib`, binary `aegis-nerve-cli`; default features rỗng, `python-extension` bật PyO3. 43 modules:

`bridge_mmap`, `browser_witness`, `circuit_breaker`, `cli`, `context`, `descriptor`, `distributed`, `eac`, `evidence_index`, `execution`, `ffi`, `goal_intake`, `governance`, `gt96`, `guardrail`, `harness`, `hot_engine`, `integrations`, `ipc`, `lab`, `layout`, `learning`, `licensing`, `llm`, `memory`, `message`, `mvcc`, `orchestrator`, `physical`, `policy`, `replay`, `resource`, `resource_platform`, `runtime`, `sac`, `sandbox`, `schema`, `shm`, `skill_registry`, `speculative`, `task_ledger`, `telemetry`, `tool_gateway`.

Native responsibilities: goal intake/typed graph/progress-budget-finalization; resource contracts/admission/lease/deadline/cancel; platform adapters; replay/hash/schema recovery; evidence/hot memory/MVCC; Wasmtime sandbox; IPC/mmap/message; policy/governance/guardrail/tool gateway; LLM capabilities; telemetry; Lab admission; PyO3. `pub mod` không chứng minh production completion; platform/distributed/replay/fuzz/global-trust gaps vẫn mở.

## 13. Python ↔ Rust FFI

PyO3 build thành `aegis_cognition.aegis_nerve`; `ffi.rs` registration chia compat, EAC, hot, lab, learning, mmap, runtime, status. Contract risks: exception mapping, buffer/mmap lifetime, GIL/free-threaded behavior, serialization/hash parity, cancellation qua FFI, editable vs wheel import, feature parity và ABI/schema compatibility.

Cooperative placement now has a native local admission path in addition to the
planner-only preview: sorted inventory digest, aggregate reservation ledger,
idempotent attempt binding, and exact-generation release fencing. Python falls
back to an explicit non-executable envelope when the extension is unavailable.
The ledger is process-local; hosted single-writer, durable multi-process state,
adapter-wide effect coverage, and non-cooperative OS/process containment remain
open under `LAB-AUTH-001`.

CI định nghĩa wheel/import/smoke và experimental free-threaded lanes, nhưng Tier-1 parity vẫn `NV-008`; macOS wheel được continue-on-error do hosted rustup/cargo-fmt conflict. Matrix definition không phải matrix pass.

## 14. Data, schema và persistence

Không có một database duy nhất. State nằm trong BLAKE3/hash-linked replay, Arrow IPC, mmap/shared memory/hot evidence, JSON artifacts, `.aegis/lab-replay`, session/RAG memory, `~/.aegis/config.toml`, và deploy path `/var/lib/aegis`.

| Schema | Nội dung |
|---|---|
| `resource-contract-v1.json` | task/attempt/work kind; CPU/memory/accelerator/IO/process/thread/fd; API/token budget; deadline/priority/side-effect class |
| `lease-token-v1.json` | lease ID/generation/attempt; handle không tự là authority |
| `runtime-telemetry-v1.json` | timestamp/correlation/kind/outcome/queue/active/limit |

Current-format/prefix recovery có logic/tests. Local Rust library evidence now retains an explicit migration matrix for current, old, future, additive/removed record layouts, unknown schema identity, truncation, corruption, and partial-tail recovery (`artifacts/verification/rust-replay-schema-migration-20260911-r2.meta.json`). This proves fail-closed local behavior and committed-prefix recovery; it does not replace production-shaped expand/backfill/contract or external persisted-fixture restore (`NV-009`, `NV-012`).

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
- compatibility-only CLI vẫn ghi credential vào `.env`; canonical root CLI đã redacts output và atomic-write nhưng chưa có platform secret-store integration;
- branch protection, full fuzz, Miri/ASan, signed attestation chưa verified;
- browser adversarial coverage chưa đủ claim SSRF-proof;
- enforcement ngoài nền tảng phụ thuộc vào quyền sở hữu bản quyền và hồ sơ written permission;
- retention/deletion/export cho research/browser artifacts chưa chứng minh đầy đủ.

## 17. Resource, concurrency, cancellation và failure semantics

Resource contract biểu diễn CPU, memory, accelerator, IO, process/thread/fd, API/token budgets, deadline, priority và side-effect class. Runtime có admission, active/queue limits, leases, generation fencing, cancellation/deadline và telemetry. GT96 giữ reserve cho finalization/recovery, no-progress/cycle/finalization boundaries và retry disposition theo effect semantics.

Platform truth:

- Windows Job Object: artifact `artifacts/local-runtime/windows-job-object-live-20260910.json` chứng minh live assignment, containment, active-process, termination và deadline; allocation-pressure kill vẫn tách riêng (`NV-002`).
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

Hosted CI current HEAD vẫn `NOT VERIFIED`. Run `34266949093` at the recorded
HEAD failed before runner allocation with zero steps/logs; its GitHub annotation
explicitly reports an account billing/spending-limit condition. Không retry/spam
workflow đến khi owner sửa Actions billing/permission/service condition.

## 21. Verification hiện có

| Verification | Kết quả | Scope/giới hạn |
|---|---:|---|
| AESE registry/drift checks | `64 passed; PROVEN local` | Current registry-dependent group at `artifacts/suites/aese-drift-after-profile-map-20260911-r6.json`; local Windows, shadow-only, selection authority remains disabled |
| Focused Goal/Target–Lab contract slice | `25 passed (historical slice)` | superseded by the current `29` Goal/Target tests and the full Python r4 run; CPython 3.14.7 |
| Current Lab runtime regression | `355 passed (historical pre-R6)` | CPython 3.14.7; `tests/test_lab_runtime.py`, including exact-key external-effect lease regression and cross-process contention |
| Current Python Lab regression | `363 passed; PASS_LOCAL` | CPython 3.14.7; current Lab lifecycle, execution-cell manifest/resource-policy binding, recovery and process-cell tests; `artifacts/verification/python-lab-runtime-isolated-20260911-r1.meta.json` |
| Replay terminal-prefix lifecycle | `359 passed; PASS_LOCAL` | Current full `tests/test_lab_runtime.py` run at `artifacts/verification/lab-runtime-replay-lifecycle-20260911-r2.log`; focused failure-prefix/post-completion replay reproduction `8 passed` at `artifacts/verification/lab-runtime-replay-lifecycle-20260911-r1.log`; strict Pyright passes at `artifacts/verification/pyright-lab-lifecycle-20260911-r2.log`; failure archive remains local/native-dependent |
| Current Goal/Target contract regression | `29 passed (historical pre-R6)` | CPython 3.14.7; `tests/test_goal_contract.py`; target evolution, semantic descendant read/write-root narrowing, network host, and external-effect lease boundaries, including URI dot-segment and encoded-separator rejection |
| Current Python GoalContract focused gate | `56 passed; PASS_LOCAL` | CPython 3.14.7; current target binding/evolution, scope narrowing, execution-cell and external-effect contract tests; current uv-locked source collection and canonical integration evidence |
| Current Python native contract integration | `462 passed; PASS_LOCAL` | Desktop-first order across desktop protocol, GoalContract, Lab runtime and runtime coordination; `uv run --locked`, `25.48s`; `artifacts/verification/uv-native-contracts-canonical-order-20260911-r1.meta.json` |
| Current Goal/Target–Lab suite evidence | `419 passed; PROVEN local` | Checkout-bound result at `artifacts/suites/goal-target-lab-contract-current-20260911-r8.json`; CPython 3.14.7, exit code 0, with the dirty-worktree fingerprint recorded at test time. |
 | Reversed Lab-first native contract integration | `462 passed; PROVEN local` | Lab → Goal → runtime → desktop against the current profile-keyed native repositories after state-path normalization; `artifacts/verification/uv-native-contracts-lab-first-resolved-state-path-final-20260911-r1.meta.json`; same-path/different-profile reuse remains fail-closed. |
| Current Goal/Target + Lab runtime regression | `384 scoped tests (historical pre-R6)` | CPython 3.14.7; `tests/test_goal_contract.py` (`29 passed`) + `tests/test_lab_runtime.py` (`355 passed`) |
| Combined contract/release/runtime/Lab regression | `390 passed (historical pre-R6)` | superseded as current-source evidence by the R6 focused regression; CPython 3.14.7 |
| Pre-admission full Python suite (including AESE) | `641 passed, 1 skipped; PASS_LOCAL baseline` | CPython 3.14.7 Windows, fixed external pytest base directory, `646.68s`, exit code 0; ledger `artifacts/verification/full-python-suite-20260910-r9.txt` and metadata `artifacts/verification/full-python-suite-20260910-r9.meta.json`; this predates cooperative admission |
| Post-admission full Python attempt | `656 passed, 5 failed, 1 skipped; NOT PASS` | `1500.79s`, exit code 1; all five failures were generated AESE registry drift, then the exact five drift tests passed after registry regeneration; ledger `artifacts/verification/full-python-suite-20260910-r10.txt` and focused repair `artifacts/verification/aese-drift-regression-20260910-r8.log` |
| Pre-profile-map full Python suite | `708 passed, 1 skipped; historical PASS_LOCAL` | `510.02s`, exit code 0 before the current profile-map source checkpoint; `artifacts/verification/full-python-suite-20260911-r4.txt` + `.meta.json`; local Windows/CPython 3.14.7 |
| Current Python regression partition | `649/650 non-AESE + 64/64 AESE passed; 1 skipped; PROVEN by partitions` | Non-AESE run is `artifacts/suites/python-non-aese-current-20260911-r1.json`; AESE registry/drift run is `artifacts/suites/aese-drift-after-profile-map-20260911-r6.json`. This is explicit partition coverage, not a single-process full-suite claim. |
| Current unpartitioned Python suite | `713 passed, 1 skipped; PROVEN local` | `uv run --locked pytest -q`, `512.22s`, exit code `0`; direct log `artifacts/verification/full-python-current-direct-20260912-r1.log` and metadata `artifacts/verification/full-python-current-direct-20260912-r1.meta.json`; dirty Windows/CPython 3.14.7 checkout, hosted and cross-platform scope open |
| Current project-scope Python quality | `Ruff lint PASS; Ruff format PASS; Pyright PASS; 455 regression tests PASS_LOCAL` | Ruff scope `aegis_cognition core/python scripts tests`; strict Pyright package scope; artifacts `artifacts/verification/python-quality-project-scope-20260911-r3.meta.json` and `artifacts/verification/python-quality-regression-20260911-r2.meta.json`; repository-wide auxiliary Ruff scan remains `NOT VERIFIED` with 321 findings |
| R6 focused Python regression | `34/34 PASS_LOCAL` | `tests/test_goal_contract.py` + `tests/test_lab_runtime.py` filtered for tool execution, external effects, controller action plans, provider attempts, execution cells and managed-internal memory effects; ledger `artifacts/verification/native-managed-internal-regression-20260910.log` |
| Cooperative admission Python contract | `18 passed; PASS_LOCAL` | Native-unavailable fail-closed envelope, lease-required admission, exact release forwarding and malformed native response rejection; ledger `artifacts/verification/cooperative-admission-python-contract-20260910-r1.log` |
| Cooperative admission Rust fencing | `1 passed; PASS_LOCAL` | Same-attempt idempotency, plan-digest mismatch protection, forged-generation rejection and duplicate-release rejection; ledger `artifacts/verification/cooperative-admission-rust-regression-20260910-r1.log` |
| R6 full Goal/Lab Python gate | `387 passed` | complete `tests/test_goal_contract.py` + `tests/test_lab_runtime.py` on CPython 3.14.7; ledger `artifacts/verification/goal-lab-full-r6-20260910.log` |
| Architecture fitness | `23/23` | local static/contract |
| Constitution audit | `180/180` | local policy/label |
| Current focused Rust GT96 gate | `18 passed; PASS_LOCAL` | Rust 1.98.1; target validation, Goal contract, evidence primitives and bound/unbound target-evolution parity; `artifacts/verification/gt96-focused-20260911-r2.meta.json` |
| Current focused Rust Lab binding/manifest gate | `20 passed; PASS_LOCAL` | Rust 1.98.1; current execution-cell manifest binding, lineage, target-binding, evidence and external-effect-key tests; `artifacts/verification/native-lab-binding-focused-20260911-r6.meta.json` |
| Rust no-default-features lib | `526 passed; PASS_LOCAL` | Rust 1.98.1; current-source run in one build worker, `126.67s`; ledger `artifacts/verification/full-rust-lib-20260910-r7.log`; local Windows evidence only |
| Rust `python-extension` feature lib | `528 passed; PASS_LOCAL` | Rust 1.98.1, one build worker, `98.04s`; ledger `artifacts/verification/rust-python-extension-lib-20260910-r8.log`; local feature coverage only |
| Current Rust no-default library | `544 passed, 0 failed; PROVEN local` | Serial direct run at `artifacts/verification/rust-library-resolved-state-path-final-20260911-r1.meta.json`; a later `uv` wrapper attempt ended before test start with Windows `STATUS_DLL_NOT_FOUND` and is recorded separately as a failed harness attempt; local Windows evidence only. |
| Current Rust `python-extension` library | `545 passed, 0 failed; PROVEN local` | Serial run at `artifacts/suites/rust-python-extension-current-20260911-r2.json`; local Windows evidence only. |
| Rust all-feature library/integration gate | `541 + 2 passed; PASS_LOCAL` | `--all-features --lib --tests`; isolated target `target/rust-all-current-20260911`; `artifacts/verification/rust-lib-integration-all-features-20260911-r1.meta.json` |
| Rust all-feature benchmark compilation | `PASS_LOCAL` | All benchmark targets compile with `cargo bench --all-features --no-run`; `artifacts/verification/rust-bench-all-features-no-run-20260911-r1.meta.json` |
| Current extension affected Python slice | `450 passed; PASS_LOCAL` | GoalContract + Lab runtime + runtime coordination against the editable extension rebuilt with project `.venv`; `artifacts/verification/python-current-extension-goal-lab-20260911-r3.meta.json` |
 | Current editable extension rebuild | `PASS_LOCAL / EXACT DEV PIN` | Built from the current Rust source with `.venv` Maturin `1.14.1`, followed by native import smoke; release provenance and hosted parity remain unverified; `artifacts/verification/maturin-develop-resolved-state-path-20260911-r1.meta.json` |
| Rust strict Clippy (no-default) | `PASS_LOCAL` | `-D warnings`; `artifacts/verification/rust-clippy-no-default-20260911-r9.meta.json` (the same current-source lane also passed `cargo fmt --check`) |
  | Rust strict Clippy (`python-extension`) | `PASS_LOCAL` | Final-source `-D warnings` run after state-path normalization at `artifacts/verification/rust-clippy-resolved-state-path-20260911-r1.meta.json`; `cargo fmt --check` and feature build/check also pass locally. |
| Cooperative placement/runtime focused Rust tests | `22 passed; PASS_LOCAL` | `--lib cooperative_`; `artifacts/verification/rust-cooperative-placement-runtime-20260911-r1.meta.json` |
| Native GT96 target-evolution focused tests | `2 passed; PASS_LOCAL` | Bound identity/authority narrowing and legacy-unbound compatibility; `artifacts/verification/gt96-target-evolution-20260911-r1.meta.json` |
| Native Lab binding/manifest focused tests | `20 passed; PASS_LOCAL` | Execution-cell manifest must exist before binding and match its digest; `artifacts/verification/native-lab-binding-focused-20260911-r6.meta.json` |
| Rust Clippy all targets/features | `PASS_LOCAL` | `--all-targets --all-features -- -D warnings` in an isolated target directory; `artifacts/verification/rust-clippy-all-targets-features-20260911-r2.meta.json` |
| Rust all-target aggregate test invocation | `NOT VERIFIED` | Library `541` and integration `2` passed; aggregate command was stopped by Criterion benchmark argument parsing, so benchmark execution is not claimed. The corrected split gates are recorded above. |
| Runtime FFI response-integrity slice | `21 passed; PASS_LOCAL` | Strict boolean/token/outcome boundary; `artifacts/verification/runtime-coordination-strict-bool-20260911-r1.meta.json` |
| Communication payload matrix | `PASS_LOCAL` | `36` measurements across `64 B`–`16 MiB`; p50/p95, throughput, allocation, declared copy semantics, RSS field and CPU status retained; no universal zero-copy claim; `artifacts/verification/communication-payload-benchmark-20260911-r1.meta.json` |
| Resource policy local H0/H1/H2 matrix | `PASS_LOCAL / MEASURED_LOCAL_ONLY` | Three labeled local profiles, 200 iterations each, with p50/p95/p99, CPU, RSS, queue, rejection, cancellation and fairness fields; profile labels are explicitly not hardware proof and memory pressure remains unprobed; `artifacts/verification/resource-policy-benchmark-20260911-r1.meta.json` |
| Cooperative CPU/RAM/SSD path benchmark | `PASS_LOCAL / MEASURED_LOCAL_ONLY` | One Windows host, deterministic 1 MiB workload, RAM and spill paths both matched the BLAKE2b oracle and temporary spill files were removed; RAM p50 `267.634 ms`, spill p50 `171.666 ms` for this five-sample run, so no universal performance claim is made; `artifacts/verification/cooperative-resource-benchmark-20260911-r1.meta.json` |
| PyO3 runtime FFI regression module | `14 passed; PASS_LOCAL` | Boxed cooperative lease token ownership/cancellation, deterministic global-state tests and runtime placement boundaries; `artifacts/verification/ffi-runtime-deterministic-20260911-r1.meta.json` |
| Python 3.14.7 dev environment | `PASS` | `.venv`, uv 0.12.9, locked dependencies + maturin 1.14.1; native import, public API import, targeted desktop tests, fresh PyInstaller sidecar build and runtime handshake verified |
| Current-source R7 wheel build | `PROVEN (local)` | Pinned Maturin `1.14.1`; `target/wheels-current-source-20260910-r7/aegis_cognition-0.1.0-cp314-cp314-win_amd64.whl`; SHA-256 `df23bddf2eab339e52782857cdab32a26492ff97f70ebc1e9adf1bf80df48081` |
| Current-source R7 clean wheel install/import | `PROVEN (local)` | CPython 3.14.7 isolated venv; `artifacts/local-release-20260910-current-r7-bundle/install-smoke.json`; wheel hash matches R7 |
| Current-source R7 packaged cooperative admission | `PROVEN (local)` | admit → `already_admitted` for the same attempt/digest → exact release; `artifacts/verification/release-cooperative-admission-smoke-20260910-r3.json`; wheel hash matches R7 |
| Local release wheel build | `PROVEN (R6 current-source local)` | pinned Maturin `1.14.1` via `uv`; wheel `target/wheels-current-source-20260910-r6/aegis_cognition-0.1.0-cp314-cp314-win_amd64.whl`, SHA-256 `610d55b66c427a50b90d41142a00b02ce14fb6d64bf6d8269832c828822e4830`; built from the current dirty working tree on CPython 3.14.7 Windows |
| Clean wheel install/import smoke | `PROVEN (R6 current-source local)` | `artifacts/local-release-20260910-current-r6-bundle/install-smoke.json`; isolated CPython 3.14.7 venv; wheel hash matches R6 |
| Packaged native recovery smoke | `PROVEN (R6 current-source local)` | `artifacts/local-release-20260910-current-r6-bundle/release-recovery-smoke.json`; five admissions reconcile with `open_after_recovery=0`; wheel hash matches R6 |
| Packaged controller-action smoke | `PROVEN (R6 current-source local)` | `artifacts/local-release-20260910-current-r6-bundle/controller-action-smoke.json`; Rust-native authority, benchmark `PASS`, zero blockers, replay archive and managed memory index `SUCCESS`; wheel hash matches R6 |
| Local rollback drill | `PROVEN (same-artifact scope)` | sequence N → N+1 → N hoàn tất; chưa có retained wheel N-1 nên current/next cùng SHA, chưa chứng minh cross-version persisted-fixture restore |
| Local release manifest/SBOM | `PROVEN (R6 hash generation)` | `artifacts/local-release-20260910-current-r6-evidence/release-manifest.json` and `release.sbom.spdx.json` (SPDX 2.3); four artifacts, all three R6 smoke reports and wheel hash match; independent byte/hash recheck PASS in `artifacts/verification/release-manifest-r6-independent-20260910.json`; revision `WORKTREE_DIRTY` |
| Native external-effect declaration fence | `PASS_LOCAL` | Native-required `LabRun` rejects generic effects outside the local-safe set without an explicit Goal target declaration; only replay-bound `memory.index_session::memory_write` is exempt; managed-internal slice `2/2`, prior external-effect slice `4/4`, Ruff/Pyright/Cargo checks pass; hidden adapter/provider effects remain outside scope |
| Production packaging smoke gate | `19/19; PASS_LOCAL` | latest current report `artifacts/production_packaging_smoke_gate_report.json`: Python smoke, service manifest, artifact write, Rust CLI build and runtime pass; `truth_claim=false` |
| Hosted CI final SHA | `NOT VERIFIED` | Current GitHub Actions run `34266949093` at the recorded HEAD failed before any runner step; GitHub annotation states the account billing/spending limit prevented job start. This is external infrastructure evidence, not a source/test failure. |
| Deep fuzz/Miri/ASan | `NOT VERIFIED` | definition không phải evidence |
| Tier-1 wheels | `NOT VERIFIED` | local Windows CPython 3.14 wheel đã pass; Linux/Windows/macOS matrix và Python lanes chưa đóng current SHA |
| External deployment/provider/cluster | `NOT VERIFIED` | cần live/external environment |

Không chạy deep/fuzz/Miri/ASan hoặc external probes trên workstation; các suite đó cần workflow và môi trường riêng. Local Python/Rust evidence above is current for the stated Windows scope; it does not close hosted CI, Tier-1 parity or external-environment gates.

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
| NV-005 | LOCAL ONLY | H0/H1/H2 policy | Local H0/H1/H2 matrix is retained at `artifacts/verification/resource-policy-H0-20260911-r1.json`, `...H1...`, `...H2...` with raw p50/p95/p99/RSS/CPU/queue/reject/cancel/fairness; closure still requires named representative hardware and pressure evidence |
| NV-006 | NOT VERIFIED | OTLP | real endpoint correlation/retry/cancel/failure/finalization/drop |
| NV-007 | NOT VERIFIED | full fuzz | seed/corpus/crash/duration/executions; >=10m nightly, >=60m weekly/release khi runner cho phép |
| NV-008 | NOT VERIFIED | Tier-1 wheels | clean build/install/import/native/runtime/metadata on Linux/Windows/macOS/Python lanes |
| NV-009 | LOCAL DRILL | external restore | N→N+1→N + persisted fixture restore/replay prefix |
| NV-010 | NOT VERIFIED | branch protection | owner ruleset + proof direct/stale push bị chặn |
| NV-011 | IN PROGRESS | GT96 closure | mọi row bind evidence ID/final SHA |
| NV-012 | LOCAL FIXTURE MATRIX | production migration rehearsal remains open | local old/current/future/add/remove/unknown/truncated/corrupt/partial-tail fixtures retained; external expand/backfill/contract, rollback and persisted-fixture rehearsal still required |
| NV-013 | NOT VERIFIED | Miri/ASan | exact scope/toolchain/features/exclusions/logs |
| NV-014 | LOCAL ONLY | IPC matrix | `artifacts/verification/communication-payload-benchmark-20260911-r1.json` retains 36 measurements across 64 B–16 MiB with p50/p95, throughput, allocation, copy semantics, RSS field and CPU-status fields; it does not prove cross-platform or universal zero-copy behavior |
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
| pin Python 3.14.7 | `.venv` CPython 3.14.7 là môi trường local duy nhất | old Python 3.14.0 environment và uv runtime đã được gỡ |
| CI uv 0.12.9 | PATH uv 0.12.9 | local standalone binary khớp CI/release/deep |
| Maturin exact 1.14.1 | global PATH remains 1.13.3; project `.venv` is 1.14.1 and the rebuild was run through it | build phải dùng `.venv`/locked dev env, không dùng binary global |
| README/CONTRIBUTING test counts cũ | retained 456 Rust/615 Python | docs không là evidence |
| changelog 0.1.0 | không Git tag | release history lệch |
| Proprietary license | `LICENSE.txt` đã tracked; root/core Python/Rust metadata đã đồng bộ | mọi quyền sử dụng ngoài quyền nền tảng GitHub cần written permission; không có anti-fork control ở public repository |
| repository/issue URLs | đã đồng bộ remote `thanhmuefatty07/AEGIS-COGNITION` | website/docs ownership và release identity vẫn cần external verification |
| CLI quảng bá 5 providers | parity chưa proven | overclaim risk |
| `config set` nói đã set | hiện ghi TOML atomically, validate key/type và không echo value | secret persistence vẫn nên ưu tiên env/platform store |
| `config show` in config | hiện redact credential-like values | redaction là display control, không thay secret rotation |
| compose nói multi-machine | cùng host/bridge | không đóng NV-016 |
| README timing/performance | không comparator current | self-reported |
| current evidence `CHECKOUT_HEAD` | HEAD thật `8d15a34...` | final-SHA provenance manifest chưa materialize |
| hai Python distributions | authority chưa thống nhất | import/release ambiguity |

## 26. Technical debt và ưu tiên

1. Production provenance/blockers: NV-004/016/017/018/019.
2. Packaging authority: canonical root wheel và role của core Python distribution.
3. Proprietary license/release identity: license text, URLs, tags, changelog.
4. Lab cohesion: tách `lab.py` chỉ sau characterization tests; không mass-refactor vì thẩm mỹ.
5. Global authority: same subject/cell policy qua provider/browser/process/benchmark/compat.
6. AESE calibration: mapping, mutation/incident corpus, representative workloads trước cutover.
7. Replay evolution: version fixtures, crash prefix, external restore.
8. Platform enforcement: privileged Linux, Windows pressure, explicit macOS semantics.
9. Docs drift: README/CONTRIBUTING/CHANGELOG và wording local-vs-proven.

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

Tại snapshot này, AEGIS-COGNITION là beta hybrid runtime giàu contract và local verification, có Lab framework thực sự cho research/browser/experiment/simulation/evidence thay vì sandbox thuần. Nó có nhiều primitive nghiêm ngặt cho authority, replay, resource, toán-vật lý và adaptive evidence. R6 hiện đã bind wheel và ba packaged smoke bằng manifest/SBOM trên local Windows, đồng thời native fence đã phân biệt managed memory indexing với generic external effects; nhưng authority vẫn chia Python/Rust, license/config còn drift, AESE chỉ shadow, repo-wide final-SHA/hosted/external/platform proof còn thiếu, và năm production blockers còn mở.

Claim mạnh nhất được phép: **implementation rộng và local evidence đáng kể đã tồn tại, với fail-closed registries giữ phần chưa biết ở trạng thái chưa xác minh.**

Không được claim: **production-ready, secure tuyệt đối, real multi-machine proven, scientifically accurate tổng quát, cross-hardware optimized, AESE non-inferior, full QuickJS validated, live-provider resilient, hoặc release provenance signed.**

## 33. Current working-tree implementation and version-alignment delta (2026-09-10)

Phần này supersede các giá trị local drift của committed snapshot khi mô tả
working tree hiện tại. Tất cả thay đổi dưới đây đang **uncommitted**; không có
commit, push hoặc workflow retry nào được thực hiện.

| Hạng mục | Trạng thái hiện tại | Evidence/giới hạn |
|---|---|---|
| Python project baseline | `>=3.14,<3.16`; preferred `3.14.7` | `.python-version`, pyproject và CI/release đã cùng policy |
| Python dev environment | `.venv` dùng CPython `3.14.7`, 42 locked packages và `maturin 1.14.1` | `uv pip check --python .venv\Scripts\python.exe` PASS; native extension import, full current-source Python suite, and R6 focused Goal/Lab regression pass; hosted/Tier-1/OS/multi-machine scope remains open |
| Old local environment | CPython `3.14.0` environment và uv-managed duplicate `cpython-3.14` junction đã xoá; `.venv` được tái tạo ở `3.14.7` | canonical `.venv` vẫn trỏ đúng CPython 3.14.7, không còn local fallback gây drift |
| Rust stable/MSRV | exact `1.98.1`; workspace MSRV `1.98` | rustfmt/file-scoped checks PASS; current R6 no-default-features library `524 passed, 0 failed` in `81.62s`; local Windows evidence only |
| uv | `0.12.9` ghim trong `ci.yml`, `deep.yml`, `release.yml` | `uv lock --check` resolved 42 packages |
| Python export | `pytest-asyncio>=1,<2` | khớp root `pyproject.toml`; generator cũng đã sửa để không tái sinh range cũ |
| Installer identity/toolchain | Windows, POSIX và Termux installers dùng GitHub remote thực tế và yêu cầu Rust `1.98.1` trước build | local shell/PowerShell syntax parse pass; end-to-end install trên Linux/macOS/Termux chưa chạy |
| Editor rules | Python 3.14 baseline; `3.14t/3.15t` chỉ experimental/verified lane | loại bỏ chỉ thị active dựa trên Python 3.13 No-GIL |
| Historical references | Rust `1.97.1`, Python 3.13 và captured old dependency strings còn trong snapshot/evidence history | cố ý giữ bất biến để không làm sai provenance; không phải active build input; Rust 1.97.1 đã gỡ khỏi máy |
| Desktop authority path | stdio protocol, local provider path, transcript persistence, bounded source snapshot và native watcher đã tích hợp | targeted desktop/code-intelligence tests pass; watcher chỉ là invalidation hint, hash-bound snapshot vẫn là authority |
| Desktop release artifacts | sidecar PyInstaller, Vite renderer, Tauri release build | fresh r4 sidecar build + `service.shutdown` handshake pass on CPython 3.14.7; binary SHA-256 `9c0d9be12afcece87788b1614ec1df14a33c7bf914ca07a55ae0bc2fa0c12c7e`; Vite renderer and Tauri Cargo check pass; installer/MSI/NSIS and packaged local-provider E2E remain separate evidence gates |
| Local wheel/release evidence | current-source R6 wheel + clean install + controller/recovery smoke + R6 manifest/SBOM | artifact/report set nằm trong `target/wheels-current-source-20260910-r6`, `artifacts/local-release-20260910-current-r6-bundle` và `artifacts/local-release-20260910-current-r6-evidence`; wheel và cả ba smoke cùng SHA `610d55b6…2e4830`; managed memory index is `SUCCESS`; manifest fail-closed label `WORKTREE_DIRTY`; signing/hosted parity và retained N-1 rollback subject vẫn chưa có |
| CLI config boundary | `config set` validates documented keys, writes TOML atomically and never echoes values; `config show` redacts credential-like fields | local regression tests pass; environment/platform secret-store integration remains a user choice |
| Package identity | root, core Python and Rust metadata declare proprietary/all-rights-reserved policy; repository/issue URLs match the actual Git remote | tag/release identity and external website ownership remain open |

Global Python 3.11/3.13 vẫn được giữ vì có thể phục vụ project khác. Rust
1.97.1, uv 0.9.9 và uv-managed Python 3.14.0 đã được gỡ sau khi xác nhận
workspace và native extension chạy bằng Rust 1.98.1/CPython 3.14.7. uv trên
PATH hiện là bản standalone 0.12.9, khớp CI.

Cleanup đã giải phóng môi trường CPython 3.14.0 cũ và package/tooling drift;
CPython 3.14.7 cùng Rust 1.98.1 vẫn còn nguyên và đã được kiểm chứng.

Các gate sau thay đổi: `uv lock --check` và `uv pip check` PASS, architecture
fitness `23/23`, document consistency PASS, focused Goal/Target–Lab gate
`25/25` (historical focused slice), targeted Ruff/Pyright PASS, Rust
fmt PASS, strict Pyright `0 errors`, focused native Lab `24/24`, current
no-default-features Rust core `508 passed, 0 failed, 0 ignored, 0 filtered`
in `86.48s` (output retained at
`artifacts/verification/full-rust-lib-20260910-r3.txt`),
AESE registry/closure/shadow direct validators PASS; the latest production
packaging smoke is `19/19` with `overall_ok=true`, covering the Python smoke,
service manifest, artifact write, Rust CLI build and Rust CLI runtime in
`artifacts/production_packaging_smoke_gate_report.json`. The R3 wheel
clean-install/import, packaged recovery/controller smoke and manifest/SBOM
hash generation remain historical for their earlier source snapshot (wheel
SHA `33c3b218…01461`); the historical R4 wheel and packaged evidence are recorded
in the table and the current-source section below. The pre-R6 full Python suite is `632 passed, 1
skipped in 586.74s`, exit code 0, retained at
`artifacts/verification/full-python-suite-20260910-r4.txt`; the earlier `631`
result is historical pre-hardening evidence. The earlier
617-pass AESE-drift attempt and partial retry remain historical evidence only;
hosted CI, Tier-1, OS isolation, multi-machine and production promotion remain
`NOT VERIFIED`.
Full wheel
matrix trên CPython 3.14.7, hosted CI current SHA, Tier-1 wheel parity, deep
fuzz/Miri/ASan và external deployment vẫn `NOT VERIFIED`.

**Historical supersession (2026-09-10, external-effect-key change):** the
earlier `25/25` Goal/Target–Lab and `501/501` Rust snapshot, the `617 passed,
1 skipped, 5 failed` AESE-drift attempt, and its `40/40` focused follow-up are
retained for provenance. They were superseded by the later clean local run
recorded below and must not be used as the current full-Python verdict.

**Historical supersession (2026-09-10, target-root/native-key hardening):**
the earlier Python Goal/Target contract gate (`29/29`), Lab runtime regression
(`355/355`), focused native GT96/Lab gates and Rust `508/508` library run are
retained for provenance. The current R6 focused regression and Rust library
gate are recorded in the table above. This is still local Windows evidence; it
does not close provider, kernel, hosted, multi-machine or cross-platform gates.

**Historical full-Python run (2026-09-10):** the complete pre-R6 suite finished
with `632 passed, 1 skipped in 586.74s`, exit code 0 on CPython 3.14.7 Windows
using an external basetemp; it predates the managed-internal R6 source edit and
does not serve as current evidence for that edit.

**Local FFI/CLI continuation (2026-09-10):** the memory-transition PyO3 helper
now rejects an invalid internal operation before repository access and returns
a typed error instead of relying on a panic path. The feature-gated regression
passes `1/1` in `artifacts/verification/ffi-learning-invalid-operation-20260910.log`.
The canonical CLI now exposes meaningful process status (`0` success, `2`
usage/unknown command, `1` configuration or run failure); its focused contract
slice passes `5/5`, with Ruff and strict Pyright artifacts retained at
`artifacts/verification/cli-contract-20260910.log`,
`artifacts/verification/cli-ruff-20260910.log` and
`artifacts/verification/cli-pyright-20260910.log`. These are local source
checks only; the historical R4 wheel and packaged smoke evidence contain this
current canonical CLI/FFI source.

The compatibility-only setup path in `core/python/aegis_cli.py` was also
hardened to replace its legacy YAML and `.env` files atomically, set a
user-only mode where supported and quote `.env` values. Its focused regression
is `1/1` with Ruff and Pyright passing in the retained legacy CLI artifacts.
This keeps the old format intact; it does not close platform secret-store
integration, credential migration or package-owner convergence.

After the Rust FFI edit, a bounded no-default-features library run passes
`506/506` with two long wrapper tests explicitly filtered; the ledger is
`artifacts/verification/rust-lib-without-long-tests-20260910-r2.log`. The
earlier `508/508` full ledger remains historical for the pre-edit source
snapshot, while the feature-gated invalid-operation regression is the decisive
current check for the changed branch.

The attempted current-source release rebuild initially failed: the parallel
build hit a compiler pipe/Windows resource failure and the single-worker retry
was terminated during dependency compilation. The raw first failure is
`artifacts/verification/maturin-current-source-20260910-r4.log`. A later
uncontended cached single-worker rebuild succeeded; the historical R4 wheel, packaged smokes,
manifest and SBOM evidence are retained below and supersede this historical
failure note.

**Historical current-source R4 packaged evidence (2026-09-10):** the rebuilt wheel is
`target/wheels-current-source-20260910-r4/aegis_cognition-0.1.0-cp314-cp314-win_amd64.whl`
with SHA-256
`a61ef9e41e80d16538c217add3aa7db4d15183e34a2a31611d5ee075ef8404de`.
Install/import, controller-action and recovery smoke are all `PROVEN` under
local Windows/CPython 3.14.7, each report binds that hash, and the controller
reports Rust-native authority, benchmark `PASS`, zero blockers and replay
archive. Recovery validates the event chain and leaves zero open admissions.
The R4 release manifest and SPDX 2.3 SBOM are `PROVEN` for generated hashes,
with revision `WORKTREE_DIRTY`. This closes only the local current-source
packaging gap; signed provenance, hosted current-SHA parity, Tier-1/cross-
platform wheels, OS/kernel/provider containment, multi-machine execution and
production promotion remain `NOT VERIFIED`.

Independent manifest byte/hash verification passes in
`artifacts/verification/release-manifest-r4-independent-20260910.json`.
The wheel also contains a byte-identical canonical `aegis_cognition/cli.py`,
the native extension and the bridge payload; this content check passes in
`artifacts/verification/release-r4-wheel-content-20260910.json`.

The current authority/replay focused slice also passes `37/37` in `18.19s`:
execution-cell registry sealing, replay/effect leases, cross-process writer
contention, non-cooperative process and descendant termination, provider retry
and cancellation fences, and typed controller actions. Evidence is retained at
`artifacts/verification/authority-replay-focused-20260910.log`.

**Replay terminal-prefix lifecycle (2026-09-11):** successful Lab runs now
archive after the post-completion `memory.index_session` admission/settlement,
so the durable snapshot covers the complete terminal event prefix. Failure and
cancellation paths reconcile open admissions before attempting a failure-prefix
archive under the same writer lease; archive failure is retained as a blocker
without masking the original exception. The local regression is `359 passed`
for `tests/test_lab_runtime.py`, with an additional `8 passed` focused
reproduction. This does not prove provider-side rollback, kernel containment or
hosted multi-process authority, so `LAB-AUTH-001` remains open.

**Native external-effect declaration fence (2026-09-10):** native-required
`LabRun` now rejects a generic tool effect outside the local-safe set when the
exact `tool_name::effect_class` is absent from an explicit Goal target. The
only managed-internal exception is the replay-bound
`memory.index_session::memory_write` pair behind a `post_completion_effect`
cell; this is validated in Python and native Rust. The GoalContract
external-effect slice passes `4/4`, the managed-internal slice passes `2/2`,
and the combined Lab/Goal regression passes `34/34`; Ruff, strict Pyright and
Cargo checks pass. This removes the local policy-only bypass while preserving
projection/legacy compatibility. It does not establish provider idempotency,
external rollback, hidden adapter-effect containment or hosted single-writer
authority, so `LAB-AUTH-001` remains `OPEN_LOCAL`.

**Current-source R6 packaged evidence (2026-09-10):** R6 supersedes R4 as the
current local package after the managed-internal native fence change. The wheel
is `target/wheels-current-source-20260910-r6/aegis_cognition-0.1.0-cp314-cp314-win_amd64.whl`
with SHA-256
`610d55b66c427a50b90d41142a00b02ce14fb6d64bf6d8269832c828822e4830`.
Install/import, controller-action and recovery smoke are all `PROVEN` under
local Windows/CPython 3.14.7. The controller reports Rust-native authority,
benchmark `PASS`, zero blockers, replay archive and memory index `SUCCESS`;
recovery validates the event chain and leaves zero open admissions. Reports are
in `artifacts/local-release-20260910-current-r6-bundle`; the release manifest
and SPDX 2.3 SBOM are in `artifacts/local-release-20260910-current-r6-evidence`
with revision `WORKTREE_DIRTY`.

Independent manifest/hash and wheel-content checks pass in
`artifacts/verification/release-manifest-r6-independent-20260910.json` and
`artifacts/verification/release-r6-wheel-content-20260910.json`. This is local
package evidence only; signed provenance, hosted current-SHA parity, Tier-1
wheels, OS/provider containment, multi-machine execution and production
promotion remain `NOT VERIFIED`.
