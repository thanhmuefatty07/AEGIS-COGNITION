---
document_id: AEGIS-LAB-RUNTIME-MASTER-PLAN
document_type: canonical_current_implementation_plan
status: IN_EXECUTION
authority: derived_from_checkout_and_evidence_manifest
applies_to_commit: CHECKOUT_HEAD + WORKTREE_DIRTY
created_at: 2026-08-26
last_verified_at: 2026-09-10
supersedes: browser-native-proposal-and-cumulative-harness-roadmap-as-execution-authority
evidence_source: docs/architecture/not_verified_registry.json + deployment_policy.json + quality/registry
execution_scope: current checkout; hosted parity remains separate
verification_scope: design plan only; current claims and gate status belong to the evidence registries
---

# AEGIS Lab Runtime — Master Implementation Plan

## 0. Quy chế của tài liệu

Đây là kế hoạch triển khai canonical cho AEGIS Lab Runtime. Tài liệu này không
phải bằng chứng rằng các hạng mục đã hoàn thành, không tự nâng trạng thái
`NOT VERIFIED`, và không biến một tuyên bố trong tài liệu cũ thành sự thật.

Tài liệu tham khảo được ghi trước WP00 là tệp Ultimate Software Engineering Constitution — Maximum-Rigor Prompt.md. Tại ngày 2026-10-08,
bản repository trước chỉnh có SHA-256 UTF-8 sau chuẩn hóa LF:
`4921db890f7b375341b74a89b6aad2b193b3b268ea5b676803227f52335f80fa`. Mệnh đề trước đây nói attachment trùng bản repository chưa được xác nhận lại
và không xác nhận trạng thái hiện tại của attachment.

WP00 chỉ cập nhật mục 51. Bản repository hiện hành có SHA-256 UTF-8 sau
chuẩn hóa LF:
`f25aed99706a686f92fc8df1531491d99518bc2a98e4ec163b73ccdea8951c61` (đã kiểm tra ngày 2026-10-08).
Bản repository là authority. WP00 không mở hoặc cập nhật attachment ngoài repository;
attachment đó không thay thế policy này.

Mọi claim trong plan phải được phân biệt bằng ba lớp:

- `FACT`: quan sát được từ code, manifest hoặc artifact có hash.
- `TARGET`: thiết kế cần xây.
- `GATE`: điều kiện phải vượt qua trước khi được đổi trạng thái.

Không dùng các từ `proven`, `production-ready`, `zero-copy`, `bias-free`,
`absolute`, `fully autonomous` nếu không có evidence class, phạm vi, commit,
protocol và validator tương ứng.

### 0.1 Historical evidence and retained design subjects

Detailed R3-R6 packaging results and dated local verification records are kept
in the private local archive, with this complete pre-cleanup snapshot and its
SHA-256 manifest. They describe earlier revisions and do not establish the
status of the current checkout.

The architecture below retains the decision-bearing subjects recorded in those
amendments: external-effect declaration boundaries, cooperative placement,
replay completion, runtime FFI outcomes, execution-cell manifest binding,
Goal/Target admission and restoration, and FFI state-store binding. Consult
the implementation and current evidence registries for present behavior.

## 1. Quyết định kiến trúc cấp cao

### 1.1 Quyết định chính

AEGIS sẽ có một lớp **Lab Runtime** ở phía trên các **Execution Cell**. Sandbox
không bị xóa; nó trở thành một loại cell cô lập cho thực thi code/Wasm. Người
dùng nhìn thấy một phòng Lab thống nhất, còn các cell bên dưới đảm nhiệm cô
lập, browser, mô phỏng, container và công việc tin cậy.

```text
User
 │
 ▼
Mission Contract ──► LabController (Rust authority + replay reducer)
                           │
              ┌────────────┼─────────────┐
              ▼            ▼             ▼
       Research Plane  Experiment Plane  Claim/Evidence Graph
       search/browser  cells/simulation   support/contradiction
              └────────────┼─────────────┘
                           ▼
               Falsifier + Blind Replicator
                           ▼
                 Benchmark Protocol V2
                           ▼
                     Lab Dossier / Export
```

Nguyên tắc vận hành:

1. LLM đề xuất; Rust xác thực schema, policy, budget, transition và evidence.
2. Browser/page/PDF/network content là dữ liệu không tin cậy, không phải
   instruction.
3. Mọi kết quả được phân loại theo epistemic status; không có đường tắt từ
   text của model đến `VERIFIED`.
4. Tự trị tối đa chỉ có nghĩa là tự trị trong policy, capability, budget và
   side-effect scope đã cấp. Nó không mở rộng quyền hạn.
5. Multi-agent là một chiến lược được chọn theo cấu trúc nhiệm vụ, không phải
   mặc định.
6. Mọi run kết thúc bằng `VERIFIED`, `INCONCLUSIVE`, `BLOCKED`, `FAILED` hoặc
   `INVALID`; không dùng trạng thái thành công mơ hồ.

### 1.2 Coding và communication profile: Ponytail + Caveman

`Ponytail` và `Caveman` được dùng như quy ước nội bộ, không được trình bày như
tiêu chuẩn bên ngoài đã được chứng minh. Ý tưởng gốc được mô tả trong
[PonytailCaveman](https://github.com/iharshgandhi/PonytailCaveman). Hai profile
không có cùng phạm vi:

- **Ponytail áp dụng cho code:** viết ít code nhất nhưng vẫn đúng, dễ kiểm
  chứng và dễ rollback. Trước khi thêm abstraction, dependency, service, flag
  hoặc schema mới, phải chứng minh code/schema/stdlib hiện có không đủ.
- **Caveman áp dụng cho giao tiếp:** progress và kết luận ngắn, trực tiếp,
  không filler. Không được nén mất safety warning, uncertainty, assumption,
  evidence class, đơn vị, điều kiện lỗi hoặc thứ tự thao tác.

Các luật bắt buộc cho mọi diff:

1. Reuse ladder: existing function/type → existing module → standard library →
   platform primitive → new abstraction chỉ khi có gap được ghi bằng evidence.
2. Một source of truth cho mỗi schema, trạng thái và hash; không tạo bản sao
   “tiện tay” ở Python/UI/benchmark.
3. Diff nhỏ nhất **đủ đúng**, không phải diff ngắn nhất. Không được bỏ validation,
   rollback, observability, error handling hoặc test để giảm LOC.
4. Không thêm dependency/runtime/service nếu chưa có ADR, threat model, cost
   model, owner và benchmark chứng minh lợi ích ròng.
5. Boundary code dùng typed error; không dùng `unwrap/expect`, silent fallback,
   magic default hoặc mutation trước validation trên đường runtime.
6. Mọi thay đổi public/API/schema phải có migration, compatibility test,
   replay impact và rollback path.
7. Mọi tối ưu phải có baseline, workload, metric, uncertainty và proof rằng
   correctness không giảm. Token/LOC reduction không tự chứng minh quality.
8. Source code, identifier, schema field và test name vẫn phải rõ nghĩa; Caveman
   không phải lý do để viết code hoặc comment tối nghĩa.

Quality gate cho diff mới:

```text
reuse_checked → scope_minimized → invariants_written → typed_errors
→ deterministic_hash/replay → unit+property+negative tests
→ lint/typecheck → benchmark only where measurable → review/rollback note
```

Nếu một diff làm tăng complexity, allocation, dependency hoặc authority surface,
phải ghi rõ delta, lý do, phương án đơn giản hơn đã loại và gate bù trừ. Không
được gọi “Ponytail compliant” chỉ vì file ngắn hơn.

Các lệnh chất lượng tối thiểu cho implementation gate phải chạy trên đúng
commit và environment đã ghi trong manifest:

```text
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --no-default-features -- -D warnings
cargo nextest run --workspace --no-default-features
python -m ruff check .
python -m pyright
python -m pytest tests core/python/tests.py -q
python scripts/constitution_audit.py
python scripts/architecture_fitness.py
```

Lệnh pass chỉ chứng minh phạm vi mà nó thật sự chạy. Không được đổi
`no-default-features` thành full feature, hoặc đổi test subset thành claim
toàn workspace, nếu plan không ghi rõ scope mới.

### 1.3 Không làm

- Không thay thế Wasmtime/Execution Cell bằng một lớp browser không cô lập.
- Không tạo microservice hoặc database mới chỉ để chứa Lab state; dùng replay,
  content-addressed artifacts và các boundary hiện có.
- Không dùng consensus, self-Elo, số lượng agent hoặc độ dài output làm proof.
- Không cho simulation tự nâng thành quan sát đời thực.
- Không cho một agent đồng thời sinh giả thuyết, chạy thí nghiệm, chấm điểm và
  chứng nhận kết quả.
- Không cố hứa “không bias”, “đúng tuyệt đối” hay “cover mọi tình huống”. Thay
  vào đó là coverage matrix, adversarial testing, calibration và residual risk.

## 2. Baseline sự thật tại checkout

### 2.1 Evidence authority

This plan records architecture intent; it is not a verification report.
Current evidence and blocker status are maintained in
docs/architecture/not_verified_registry.json,
docs/architecture/deployment_policy.json, and quality/registry/. The generated
human-readable view is docs/architecture/AEGIS_LAB_STATUS_GENERATED.md.
Host-specific measurements, test logs, and historical reports belong in the
private local archive after review; do not copy their raw values into this plan.

### 2.2 Nền móng có thể tái sử dụng

- Rust runtime authority, Wasmtime policy, resource admission và platform
  capability adapters.
- `TaskLedger`, replay ledger, checkpoint/next-action binding và BLAKE3
  artifact identity.
- Typed Tool IR, policy facts/proof trace, approval scope và browser witness
  packet.
- Browser collector/Playwright capture producer và file-backed witness path.
- Search SDK có các primitive lexical/fetch/filter/dedupe/rerank/aggregate.
- Python `Agent`/`AegisAdapter` compatibility surface.

### 2.3 Khoảng trống tại snapshot ban đầu

Các mục dưới đây là khoảng trống được phát hiện tại snapshot trước khi thực
thiện Lab. Working-tree đã lấp một phần bằng các adapter bounded; trạng thái
hiện tại và giới hạn của chúng nằm trong blocker disposition và Section 16.

- Public `Lab`/`LabSession` facade và live evidence stream đã có bounded
  implementation; Rust `LabController` authority đã có native reducer,
  budget/finalization binding, replay epoch admission và PyO3 surface, nhưng
  Python `LabRun` chưa delegate toàn bộ lifecycle sang reducer này và hosted
  single-writer proof vẫn chưa hoàn tất.
- Search-as-code executor đã có HTTPS fetch, render/extract đơn giản,
  citation-span/hash và contradiction/cross-check adapter; live provider
  evidence và semantic extraction production vẫn chưa có.
- Các record canonical (`SourceRecord`, `ClaimRecord`, `HypothesisRecord`,
  `ExperimentSpec`, `ObservationRecord`, `BenchmarkProtocolV2`, `LabDossier`)
  đã có bounded implementation; provenance clustering, falsifier và blind
  replication độc lập vẫn còn thiếu.
- Browser launcher Playwright managed-context/route-guard, read-only observer
  role và bounded simulation cell (scalar constraints plus Euler/RK4 ODE
  primitive) đã có; generic tool execution cũng đã có typed admission/settlement
  với lease, retry và cooperative cancellation. Execution-cell orchestration
  chung, validated solver và runtime authority duy nhất cho mọi adapter vẫn
  chưa hoàn tất.
- Benchmark protocol đã có local raw trials/baseline/CI/contamination và
  optional validator; hidden hosted validator và independent reproduction vẫn
  chưa có.

### 2.4 Kiểm kê kế hoạch, tài liệu trùng và quyền ưu tiên

Đã quét danh sách Markdown/JSON trong checkout và đối chiếu các tài liệu có
vai trò kế hoạch, kiến trúc, constitution, traceability hoặc release evidence.
Kết quả kiểm kê được cố định ở đây để không còn nhiều “kế hoạch hiện tại” cạnh
tranh nhau:

| Nhóm | Tài liệu | Quyền trong runtime | Xử lý |
|---|---|---|---|
| Canonical plan | `docs/architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md` | kế hoạch triển khai Lab duy nhất | current, được cập nhật bằng evidence |
| Normative constitution | `docs/ENGINEERING_CONSTITUTION.md` | quy tắc kỹ thuật/gate | authority; không tự thay yêu cầu người dùng |
| Machine evidence | `docs/architecture/evidence/current.json`, `docs/architecture/not_verified_registry.json`, `docs/architecture/deployment_policy.json` | trạng thái kiểm chứng/release | machine source of truth; không sửa tay để “đóng” blocker |
| Architecture/ADR | `docs/adr/*.md`, `docs/architecture/*.md` | hợp đồng và quyết định thành phần | tham chiếu theo scope, không phải Lab status |
| Historical plans | `docs/archive/planning/agent-harness-continuation-plan.md`, `docs/archive/planning/architecture-optimization.md` | không có quyền current status | giữ lịch sử; bị plan này supersede khi nói về Lab |
| Historical research and reports | local-only archive outside the repository | không có quyền promotion | retained as provenance only; current status comes from the tracked registry |
| User-provided engineering constitution | `docs/ENGINEERING_CONSTITUTION.md` | canonical repository authority | external copies are references and do not override repository policy |

Không phát hiện một plan-file độc lập nào khác ngoài các mục historical đã nêu
trong lần quét này. “Trùng” về nội dung không được gộp bằng cách xóa file; file
lịch sử vẫn tồn tại nhưng không được dùng làm current status. Việc ADR-012
trùng số (`foundation-convergence` và `portability`) đã được xử lý bằng
`ADR-015-portability.md` và compatibility stub giữ liên kết cũ; phần còn mở của
B3.3 chỉ là generated view và metadata/link lint có owner rõ ràng.

## 3. Blocker ledger — phải đóng trước khi nối quyền tự trị

Trạng thái bên dưới là trạng thái thật tại ngày lập plan. `OPEN` không có nghĩa
không thể sửa; nó có nghĩa chưa có proof đủ mạnh để coi là đã đóng.

Sau khi bắt đầu thực thi, snapshot này vẫn được giữ để bảo toàn lý do và phản
ví dụ ban đầu. Trạng thái working-tree hiện tại phải đọc cùng Section 16
(`Execution ledger hiện tại`); Section 16 không tự đóng các external blocker.

### B0 — Authority và invariant nền tảng

#### B0.1 BudgetLedger có thể vượt tổng ngân sách

**Bằng chứng:** `core/rust/src/gt96.rs:435-520` khởi tạo
`remaining=total`, nhưng exploration chỉ trừ reserve khi tính availability và
`consume_finalizer` lại tính `remaining + finalization_reserve`.

**Phản ví dụ componentwise:** với `T=100`, `F=20`, `R=10`, exploration 70 để
`remaining=30`. Finalizer 50 được nhận vì `30+20=50`; tổng spend thành 120/100.

**Tác động:** nếu nối ledger này vào LabController, retry/finalization có thể
  chi quá hạn mức trong khi replay vẫn có vẻ hợp lệ.

**Cách sửa bắt buộc:** thay bằng các pool độc lập và lease có identity:

```text
T_i = E_i + F_i + R_i + O_i + S_i
```

Trong đó `E/F/R` là phần còn sẵn sàng, `O` là outstanding child leases và `S`
là settled spend. Mọi action là `reserve → execute → settle/return`, có
`lease_id`, idempotency key và exact-once settlement. Risk không được coi là
đại lượng fungible; nó là hard exposure ceiling. Money phải có currency; token
phải có model/tokenizer identity.

**Working-tree disposition:** `BudgetLedger` hiện đã tách pool exploration,
finalization và recovery; các phép consume/refund kiểm tra conservation trước
khi commit, và bộ counterexample/property test Rust đã phủ trường hợp reserve
bị dùng lại. Khi native extension được yêu cầu, `LabRun.admit_exploration()`
còn chuyển allocation vào `LabController` để consume exploration pool, tăng
step và ghi event đồng nhất với projection; native smoke đã kiểm tra 90 token
còn lại sau allocation 10 trên tổng 100. Blocker chưa đóng hoàn toàn vì
LabController chưa sở hữu toàn bộ budget lease của mọi Agent/tool side effect,
còn currency/model-tokenizer identity chưa được nối thành một authority chung.

#### B0.2 Thao tác refund lỗi không atomic

`refund()` cộng vào `remaining` rồi mới kiểm tra vượt `total`. Một refund không
hợp lệ có thể trả lỗi nhưng vẫn làm state đổi và epoch không đổi.

**Gate đóng:** mọi `Err` phải để byte state, hash và epoch bất biến; property
test phải tạo được sequence refund/spend tùy ý và chứng minh conservation.

**Working-tree disposition:** đường `refund` hiện tính state kế tiếp trước khi
ghi, nên lỗi underflow/overflow/conservation không làm đổi state hoặc epoch;
Rust regression đã chạy. Cần thêm property/fuzz và integration proof trên
mọi lease lane của LabController trước khi coi gate này đã đóng ở release.

#### B0.3 Evidence có thể tự khai là `Validated`

`ArtifactRef` có field public; constructor `validated()` tự đặt
`validation_state=Validated`; `ProgressLedger.mark_verified()` chỉ kiểm tra
field không rỗng/nonzero. Cùng một artifact hiện có thể dùng cho nhiều
criterion.

**Cách sửa:** field authoritative phải private. Chỉ objective validator được
  admission mới mint `ValidatorProof` chứa:

```text
mission_hash, contract_version, criterion_id, artifact_hash,
validator_hash/version, environment_hash, observed_at,
validity_interval, policy_window_hash, result_hash
```

`mark_verified` chỉ nhận proof đúng criterion, mission, environment, freshness
và policy. Support, contradiction và negative result phải là record riêng,
không phải một mutable artifact ref.

**Working-tree disposition:** `ValidatorProof`, `validated_for` và kiểm tra
criterion/contract/artifact hash đã được nối; negative test chứng minh proof bị
tamper hoặc sai binding bị từ chối. Constructor metadata-only hiện bắt đầu ở
`Unvalidated`; kể cả caller tự đổi public `validation_state` cũng không vượt
qua được `validation_hash`/criterion/contract/validator binding. Các field
authoritative vẫn còn public và proof chưa mang đầy đủ environment/freshness
window, nên đây là hardening cục bộ, chưa đóng blocker authority.

#### B0.4 GoalContract hash thiếu domain separation

`compute_hash()` nối lần lượt constraints, invariants và non-goals mà không ghi
field tag/list length. Với các list phù hợp, phân vùng khác nhau có thể tạo cùng
chuỗi hash. `CriterionStatus` cũng không nên nằm trong immutable goal
definition; status thuộc event ledger.

**Cách sửa:** canonical encoding có schema/version, field tags, list lengths,
ordering rule và domain separation. Evolution phải bind
`parent_contract_hash`, author/reason và effective epoch.

**Working-tree disposition:** Python `GoalContract` và Rust GT96 hiện đều có
identity/hash domain riêng, target binding, immutable definition/progress split,
generation CAS, parent contract hash, author và effective epoch; unit tests đã
phủ hash partition, round-trip, stale evolution và target binding. Python
`GoalContract` JSON và Rust GT96 internal encoding không phải cùng một protocol;
cầu nối hiện tại chỉ kiểm tra canonical GoalContract JSON tại native mission
boundary, không phải một lossless cross-language reducer.
Explicit contracts flow through `LabRun` snapshots/manifests and canonical JSON
mission validation. Native per-event goal/target binding is implemented and
validated by the focused native GoalContract tests (5/5), the current Lab
runtime regression (355/355), the current full Rust core library suite
(508/508; output retained at
`artifacts/verification/full-rust-lib-20260910-r3.txt`),
Python Goal/Target–Lab regression tests (29/29 + 355/355), the post-edit
targeted Python set (50/50), and targeted Ruff and strict Pyright. The current
full Python run is `632 passed, 1 skipped` at
`artifacts/verification/full-python-suite-20260910-r4.txt` under CPython 3.14.7
Windows with an external basetemp. The older `631 passed` ledger is historical.
Đây vẫn chưa phải authority hosted duy nhất của toàn bộ Agent lifecycle (xem
B0.6 và `LAB-AUTH-001`). Packaged wheel evidence is retained for the earlier
r3 source snapshot in `target/wheels-current-source-20260910-r3` and
`artifacts/local-release-20260910-current-r3`; it is not current-source
release evidence after the latest Goal/Target and Rust edits.

#### B0.5 PAV không phải correctness proof

`PhysicalArtifact::new()` nhận `ast_fingerprint`, `fuel_consumed` và
`bytes_changed` từ caller. PAV hiện là AST-distance/fuel; nó không chứng minh
patch đúng. Fallback distance còn phụ thuộc registry trong process, và memory
nudge dùng sentinel fingerprint/fuel.

**Cách sửa:** PAV chỉ còn là novelty/efficiency diagnostic. Metadata phải được
trusted executor derive và bind vào witness hash. Correctness phải do
criterion-specific tests, oracle, formal proof hoặc observation độc lập quyết
định. Replay fresh process phải cho cùng verdict.

**Working-tree disposition:** `ObjectiveValidationReceipt` và
`accepts_with_objective` đã tách objective correctness khỏi PAV; test tamper
đã pass. API cũ vẫn tồn tại để tương thích và metadata caller-supplied chưa
được thay hoàn toàn bằng trusted executor, vì vậy PAV chưa thể là release
correctness proof.

#### B0.6 GT96 là primitive, chưa phải runtime authority

`BudgetLedger`, `ProgressLedger`, `FinalizationBoundary`, `NoProgressDetector`
và `CycleDetector` đã có các primitive/test riêng; native `LabController` hiện
đã nối runtime, exploration/finalization reserve, bounded step admission và
đường projection của `LabRun` khi policy yêu cầu. Tuy vậy Python vẫn còn là
materializer/tool-side-effect adapter cho một phần Agent lifecycle; các lane
progress/cycle, mọi budget lease và hosted single-writer authority chưa được
chứng minh như một reducer duy nhất.

**Cách sửa:** tạo một `LabController` duy nhất nhận typed replay events, tính
state mới, kiểm tra admission và phát projection. Reconstruction-only mutation
phải private. Mọi action cần `ActionAdmission` bind mission version, state epoch,
task selection, effect/risk, budget lease, expected observation schema và stop
rule.

**Working-tree disposition:** Rust `LabRuntime` và Python bounded controller đã
được nối vào cùng record/event vocabulary; mọi Python event append hiện được
native Rust verifier admission trước khi phát ra stream nếu extension có mặt;
state transition cũng được Rust kiểm tra trước mutation, và policy `PROD`
fail-closed khi extension/verifier thiếu. Native `LabController` hiện sở hữu
Rust runtime, exploration budget, finalization boundary, epoch-bound admission,
budget/finalization events, snapshot/restore và PyO3 API; khi extension thật có
mặt, `LabRun` projection event được admit tuần tự vào native single-writer
chain, state transition được truyền vào native reducer và snapshot restore
replay lại cùng event root. Admission chạy transaction trên native clone để
record bị từ chối không thể làm thay đổi snapshot, state, epoch hay projection
index. Typed projection admission hiện kiểm tra payload JSON, side-effect
receipt fields và source→claim→hypothesis→experiment→observation references,
seed và clean-replication rules trước khi ghi native event; browser action,
research-program, skill, experiment và generic tool admission/execution
receipts đều bind lease/actor, policy/input/result hashes, mission/replay parent
và uniqueness rules. Goal/Target metadata hiện được lưu ở mission/context/
manifest/snapshot; explicit Python execution events carry goal generation,
contract hash và target digest, và Rust native boundary đối chiếu canonical
contract shape, hash, generation, budget, target và event binding. Payload
đã nhận được lưu trong native projection index và được kiểm tra lại khi
snapshot restore; legacy hash-only admission bị từ chối cho các typed
side-effect kinds để không tạo đường bypass. Native smoke đã từ chối claim
forged và khôi phục projection state từ snapshot. Exploration allocation và
finalization projection cũng đi qua native boundary khi policy yêu cầu.
Python vẫn là adapter cho typed record materialization và side-effect runners;
skill, browser, research, experiment, generic tool execution, gateway/controller
model calls và cancellation hiện đã có native pre-admission/settlement trên các
đường đã triển khai.
Experiment/tool retry dùng execution identity + attempt fence riêng cho từng
lần chạy; tool lease/effect/role/schema/stop-rule được bind vào cả admission và
settlement; cooperative task cancellation settle `CANCELLED` trước khi abort.
Cancel operator ghi admission trước state transition và settlement sau
transition (kể cả settlement `REJECTED` khi transition thất bại). Điều này
đóng được local projection gap cho các explicit lanes và các gateway/controller
model calls đã khai báo, nhưng native controller vẫn chưa sở hữu trọn vẹn
planner/lease của mọi Agent/tool call (đặc biệt provider routing, compatibility
adapters và side effects phát sinh ngầm), cancellation không hợp tác của
provider/process, retry policy xuyên mọi adapter và hosted single-writer
authority. Partial-failure recovery của các explicit admitted lanes đã có local
proof; recovery cho implicit/ngoài-contract adapters vẫn mở. Các claim còn lại
vẫn mở.

### B1 — Khả năng Lab chưa được nối

#### B1.1 `Agent.run()` vẫn là one-shot (compatibility path)

Compatibility path vẫn chuẩn bị prompt và gọi gateway một lần. Working-tree đã
thêm `lab=True`/`mode=lab`: bounded controller gọi nhiều bước và chỉ trả
dossier, không tự nâng thành truth claim.

**Cách sửa:** giữ `Agent.run()` để tương thích; thêm `Lab` facade rõ ràng. Không
âm thầm biến `browser=True` thành quyền tự trị. Phát deprecation warning cho
flag inert cho đến khi có `mode=lab` hoặc API Lab tương ứng.

**Working-tree disposition:** `Lab`, `LabSession` và `Agent(..., lab=True)` đã
được thêm theo hướng additive; Lab chạy bounded controller, phát dossier và
giữ one-shot semantics của `Agent.run()`. Đường Lab browser chỉ nhận
serializable typed actions; callable actions vẫn tồn tại riêng trên
compatibility adapter. Đây là compatibility implementation, chưa phải
long-horizon controller authority vì phần reducer Rust duy nhất và hosted
delivery vẫn còn mở ở B0.6.

#### B1.2 Browser chưa có lifecycle manager

Playwright wrapper hiện yêu cầu một page đã tồn tại. Cần Browser Cell có launch,
context/session, quota, egress allowlist, snapshot policy, cleanup, lease,
crash recovery và observer/actor separation.

**Working-tree disposition:** `BrowserCell` hiện đã có typed action vocabulary,
HTTPS/host policy, quota, launcher lease và idempotent cleanup; runtime adapter
đã thu witness trước/sau; `launch_playwright_session` thêm managed context và
route guard; managed Playwright initial URLs và intercepted routes cũng từ chối
literal/legacy-obfuscated unsafe IP destinations. Chromium/Headless Shell revision `1234` đã được cài và live local
navigation tới `example.com` cùng unauthorized-route rejection đã pass. Bounded
session recovery/relaunch đã có. Mỗi browser side-effect receipt hiện bind
`action_id`, lease, actor role, action kind, input/result/policy hashes và
`SUCCESS` status; native projection admission kiểm tra các trường này.
`BrowserObserverView` và `BrowserCell.observe()` hiện chỉ cho phép read
URL/accessibility/network log/wait, có quota riêng và reject callable/actor
actions. Session do caller sở hữu được bind vào cell bằng lease riêng nhưng
không bị cell tự đóng; session do launcher cấp được giữ bound trong toàn bộ
controller loop để agent có thể tương tác nhiều bước, sau đó cleanup
idempotent. Mỗi
observer read giờ phát replay-visible `BrowserObservationRecorded` receipt
bind observer role, observation kind/count, lease và input/result/policy hashes;
native typed admission reject hash-only bypass và kiểm tra uniqueness. Browser
actor/observer intent giờ được native-admit trước side effect, sau đó settle
thành `SUCCESS` hoặc `REJECTED`; provider research program cũng dùng cùng
admit→execute→settle pattern. Process-level crash injection, OS/process
isolation đầy đủ và hosted cross-domain evidence vẫn mở.

#### B1.3 Search SDK chưa phải research plane

Lexical search hiện chủ yếu chấm các source đã được cung cấp; `FetchUrl` là
direct HTTP fetch; semantic path dùng proxy score. Cần provider adapters, query
planning, source snapshots, extraction spans, citation binding, freshness và
contradiction search. Không được gọi proxy score là semantic retrieval đã được
chứng minh.

**Working-tree disposition:** `SearchProgram` typed IR hiện bind operation list,
allowlist, provider, freshness window, independent provenance-cluster quota và
program hash; `SearchProgramExecutor` có HTTPS fetch, bounded response bytes,
deterministic render/extract, citation spans, source/snapshot hash, cross-check
và contradiction adapter; executor bridge native-admits program intent trước khi
gọi provider, settle thành success/rejected receipt, ghi event provenance và
chặn marker prompt-injection. Provider query planning live, semantic extraction quality và
independent contradiction retrieval vẫn chưa có external evidence; live
`example.com` fetch/extract đã pass, nhưng freshness/provenance quotas và
redirect/private-IP rejection mới chỉ có local contract gates, chưa phải
provider-quality evidence.

#### B1.4 Skill system chưa phải scientific controller

Skill selection hiện là word overlap; research skill chỉ là prose guideline.
Cần skill capability manifest, preconditions, validator, version, cost/risk
profile và replay-bound execution. Skill không tự được promote vào authority.

**Working-tree disposition:** `SkillManifest`/`SkillRegistry` hiện yêu cầu
implementation/policy hash, capability, precondition, validator version, cost
và risk class; `LabRun.admit_skill()` bind admission vào mission epoch và
replay parent, còn `SkillRegistry.execute()` chỉ trả receipt sau validator
acceptance. Admission/execution được ghi thành event, snapshot-preserve và
tamper-check; `LabApplication` đã chạy các `skill_requests` được caller khai
báo rõ; mỗi request có timeout policy hữu hạn và mọi lỗi/timeout sau admission
đều được settle thành receipt `REJECTED` (cancellation vẫn `CANCELLED`). Python adapter và native event-wire smoke đã pass, nhưng registry
chưa phải Rust authority duy nhất, chưa có policy-driven skill planner và
chưa có FFI/hosted proof cho validator isolation; các phần đó vẫn là blocker
P1/P4.

### B2 — Benchmark và epistemic gate chưa đủ nghiêm

`aegis-bench` hiện chủ yếu có `estimate_ns`/`threshold_ns` và fail-fast. Nó
thiếu raw trial, baseline, CI, estimand, environment, contamination, judge
calibration và invalid state. Criterion point estimate không đủ chứng minh
regression hoặc superiority.

**Cách sửa:** triển khai `BenchmarkProtocolV2` ở Phase P0/P5. Protocol phải
được hash và seal trước candidate run. Raw records, failed trials, timeout,
refusal, retry và exclusion reason đều phải giữ trong content-addressed store.

**Working-tree disposition:** Python/Rust đã giữ raw trials, artifact hash, CI,
environment binding, contamination flags và validator rejection. Ngoài callback
tương thích, `evaluate_benchmark()` hiện có protocol subprocess
`aegis-hidden-validator-input-v1`/`aegis-hidden-validator-result-v1`: argv không
qua shell, protocol/raw-trial/input hash binding, version bắt buộc, timeout và
output limit; `LabApplication` truyền `benchmark_validator_command` chỉ từ
operator-owned options. Local evidence vì vậy chứng minh validator chạy ngoài
candidate process, nhưng command vẫn phải được harness/operator cung cấp; hosted
hidden validator, scorer secrecy, answer-lookup adversary và independent
reproduction vẫn mở.

### B2.1 Simulation/physics records cần đơn vị và độ bất định

Một observation số không đủ làm bằng chứng vật lý: thiếu dimension, scale,
uncertainty, conservation residual và clean replication thì agent có thể tối ưu
nhầm đại lượng hoặc biến sai số số học thành phát hiện khoa học.

**Working-tree disposition:** `UnitRegistry` đã hỗ trợ dimensional algebra
(derived electrical/mechanical units), canonical conversion về experiment unit,
constraint residual units, `PhysicalConstraint`, `SimulationSpec`/`SimulationCell`,
held-out RMSE calibration record, explicit convergence error/step gate, 95%
descriptive interval, uncertainty gate và clean-replication gate đã được nối vào
Lab reducer. `SimulationCell.integrate_ode()` hiện cung cấp bounded Euler/RK4,
step-doubling local-error evidence và optional invariant/energy-drift gate;
trace trả về toàn bộ accepted states để hash/archive. `ElectricalSignalSpec` /
`ElectricalSignalCell` bổ sung contract bounded cho V/A/W/J: sample-count và
Nyquist/anti-alias declarations, sensor gain/offset correction, calibration
digest, Ohm residual và trapezoid energy, với epistemic status
`MEASURED_INPUTS_ONLY`; primitive này không tự suy ra năng lượng phần cứng.
Đây vẫn chưa phải solver vật lý đã được validated: PDE/stiffness, solver
convergence trên nhiều problem-class, electrical circuit/state-space residuals,
sampling/aliasing ngoài contract, sensor error model đầy đủ, calibrated energy
instrumentation và blind independent replication còn mở.

### B3 — Source of truth và release evidence chưa đồng bộ

#### B3.1 Registry và manifest phải dùng cùng một inventory

`not_verified_registry.json` có 20 entries. Trước lần reconciliation này,
`current.json` và các view sinh ra đã không có cùng inventory; các mục production
blocker đã từng bị rơi là:

- `NV-016`: real multi-machine TCP cluster soak.
- `NV-017`: full QuickJS interpreter cold-start.
- `NV-018`: live provider HTTP 429 soak.
- `NV-019`: external deployment smoke.

`NV-020` là khoảng trống owner/cross-cell trust binding mới được bổ sung. Nó
được phân loại `non_blocking_registry_ids` trong deployment policy vì chưa phải
là blocker phát hành production, nhưng vẫn là blocker đối với surgical
architecture convergence và không được phép biến mất khỏi evidence manifest.

Đây là lỗi authority, không chỉ là lỗi tài liệu. `deployment_policy.json` vẫn
tham chiếu cả bốn và đánh dấu chúng block production.

**Gate đóng:** sinh Markdown và `current.json.not_verified_ids` từ một JSON
registry duy nhất; CI bắt buộc equality ba chiều giữa registry, manifest và
deployment policy.

**Working-tree disposition:** inventory hiện ghi rõ đủ 20 registry IDs và cả 20
ID đã được materialize vào `current.json`; `NOT_VERIFIED_REGISTRY.md` và
`AEGIS_LAB_STATUS_GENERATED.md` cũng đủ 20 dòng, còn policy khai báo rõ
partition blocking/non-blocking thay vì để ID rơi im lặng.
`scripts/evidence_consistency_gate.py` hiện có parity check fail-closed cho
missing/unknown registry IDs, Markdown inventory/status và deployment-policy
partition; đường `materialize_for_head()` tự sinh `not_verified_ids` từ registry
duy nhất. `scripts/document_consistency_gate.py` hiện sinh
`AEGIS_LAB_STATUS_GENERATED.md` từ registry + policy, kiểm tra metadata inventory
cho architecture/ADR/user-doc scopes và lint link Markdown trong repository
(artifact research tree được loại khỏi scope); gate local đã pass. Final-SHA
hosted artifact vẫn chưa có, nên blocker source-of-truth chỉ còn mở ở release
scope.

#### B3.2 GT96 status drift

Trước khi sửa, 11/35 status trong `GT96_TRACEABILITY.md` không khớp
`current.json`; một số Markdown ghi `PROVEN` trong khi machine-readable
authority ghi `IMPLEMENTED / NOT VERIFIED`.

**Gate đóng:** chỉ giữ một status authority; Markdown là generated view. Tách
`implementation_state`, `verification_state` và `release_state`, không nối
chúng bằng một chuỗi mơ hồ.

**Working-tree disposition:** master plan đã tách FACT/TARGET/GATE và ghi
working-tree evidence riêng; 35 status của GT96 Markdown hiện đã đồng bộ với
machine `current.json`, và gate fail-closed sẽ phát hiện drift tiếp theo ở mỗi
lần chạy. Matrix vẫn chưa được sinh toàn bộ từ một nguồn duy nhất và final-SHA
evidence chưa có, nên gate release vẫn mở cho đến khi parity chạy trên final
SHA.

#### B3.3 Tài liệu lịch sử đang bị dùng như current status

Continuation Plan 4.100 dòng và Project Overview chứa nhiều claim lịch sử,
future tense hoặc stale path. Browser proposal ghi “nothing implemented” dù
collector/witness đã có một phần. ADR-012 bị trùng số.

**Gate đóng:** thêm metadata `document_id`, `type`, `status`, `authority`,
`applies_to_commit`, `supersedes`, `last_verified`; archive proposal/report;
không cho hand-edited mega-status làm authority.

**Working-tree disposition:** master plan đã có front matter authority,
commit-scope, status và supersedes; `document_inventory.json` phân loại các
authority/status/user-doc scopes và historical paths; generated status view và
metadata/link gate đã pass. Các tài liệu lịch sử vẫn được giữ nguyên để
provenance, nhưng không còn được dùng làm current status. Quyết định portability từng mang số ADR-012 đã được chuyển sang ADR-015; đường dẫn cũ chỉ là compatibility stub. Phần còn lại của blocker này là
phải lặp gate trên final SHA và không để generated view bị sửa tay.

### B4 — Blocker release phụ thuộc external state

Các mục sau không thể đóng chỉ bằng commit local:

| Blocker | Bằng chứng cần có | Chủ thể ngoài repo |
|---|---|---|
| NV-001 Linux cgroup v2 | privileged live enforcement trên target kernel | runner/host có quyền |
| NV-002 Windows Job Object | live process-limit/kill enforcement on the supported Windows host | Windows runner/host có quyền |
| NV-003 macOS enforcement | live adapter evidence trên macOS | macOS runner |
| NV-004 signed attestation | external verifier ký đúng subject hash | release/attestation owner |
| NV-005 H0/H1/H2 freeze | named hardware profiles, raw p50/p95/p99, pressure | hardware lab |
| NV-006 OpenTelemetry exporter | external collector/backend receives redacted traces/metrics with correlation and retention evidence | observability backend owner |
| NV-007 fuzz campaign | retained corpus, crashes, duration, zero reproducible crash/UB | CI/fuzz runner |
| NV-008 wheel parity | clean Linux/Windows/macOS install/import/runtime | hosted build fleet |
| NV-009 external restore | restore from an external release backup, with RPO/RTO and integrity verification | backup/release environment owner |
| NV-010 branch protection | repository ruleset export và stale-evidence rejection | repository owner |
| NV-011 GT96 closure | every applicable requirement has current final-SHA evidence or an explicit accepted exception | requirement/release owner |
| NV-012 migration rehearsal | production-shaped expand/backfill/contract rehearsal, rollback and recovery evidence | deployment/data owner |
| NV-013 Miri/ASan | hosted memory/undefined-behavior runs on the supported Rust targets with retained logs | CI/toolchain owner |
| NV-014 communication exporter | external delivery of operator/event communication with redaction, retries and receipt evidence | operations/observability owner |
| NV-015 hosted CI | final-SHA CI/Deep/Release run IDs | GitHub billing/permissions |
| NV-016 cluster soak | thật nhiều máy, network/RTT/lease/recovery capture | multi-machine lab |
| NV-017 QuickJS | expected full interpreter Wasm + semantic cold-start corpus | runtime artifact owner |
| NV-018 provider soak | real 429 responses, redacted hashes, fallback capture | provider accounts |
| NV-019 deployment smoke | clean external/container deployment capture | deployment environment |

Các mục này được coi là `OPEN_EXTERNAL`, không được giả vờ đóng bằng mock,
loopback, local-only hoặc historical report. Plan có thể tiếp tục, nhưng
production release phải fail-closed cho đến khi evidence được bind vào đúng
final SHA.

### 3.1 Quy ước quản trị blocker

Mỗi blocker là một record có đủ `id`, `class`, `severity`, `owner`, `dependency`,
`affected_capabilities`, `reproduction`, `closure_test`, `required_evidence`,
`fallback` và `release_impact`. `class` được giới hạn ở `AUTHORITY`, `SAFETY`,
`EPISTEMIC`, `CAPABILITY`, `SOURCE_OF_TRUTH`, `PLATFORM` hoặc `EXTERNAL`;
`severity=critical` nếu lỗi có thể làm vượt policy, thất lạc replay, biến
simulation thành fact, hoặc phát hành artifact chưa được chứng minh.

Vòng đời chuẩn:

```text
OPEN → IMPLEMENTED → LOCAL-PROVEN → HOSTED-PROVEN → CLOSED
  ↘ BLOCKED_EXTERNAL / INVALID / REGRESSED
```

- `IMPLEMENTED` chỉ chứng minh code path tồn tại; `LOCAL-PROVEN` chỉ chứng minh
  test/replay trong checkout; cả hai không đủ để phát hành.
- `HOSTED-PROVEN` cần run ID, final SHA, environment hash, retained raw output,
  independent verifier và reproduction command. Với physics/benchmark còn cần
  protocol hash, uncertainty/CI và untouched holdout.
- `CLOSED` chỉ hợp lệ khi closure test pass, artifact không bị tamper, registry
  và generated views parity, owner ký xác nhận, và không còn dependency mở.
- `BLOCKED_EXTERNAL` không phải là “đã xử lý”: hệ thống phải giữ nguyên blocker,
  nêu rõ chủ thể ngoài repo, điều kiện vào/ra, thời hạn/không có thời hạn và
  hành vi fail-closed. `INVALID` hoặc `REGRESSED` phải làm mất quyền promotion
  ngay cả khi một run trước đó từng đạt.

Không gọi “không còn blocker” khi chỉ có coverage tốt hơn. Unknown unknowns
được quản lý bằng coverage matrix, adversarial/fuzz/chaos campaigns và residual
risk dossier; không có phương pháp trung thực nào chứng minh đã bao phủ mọi
tình huống có thể xảy ra.

## 4. Mô hình dữ liệu canonical

### 4.1 `LabMissionSpec`

```text
mission_id, mission_version, objective, scope, non_goals,
acceptance_criteria[], failure_criteria[], constraints[], capabilities[],
effect_policy, risk_ceiling, budget_policy, allowed_data_classes,
research_domains, stop_rules, user_feedback_policy, created_at, mission_hash
```

`acceptance_criteria` phải observable hoặc có validator cụ thể. Objective mơ hồ
được giữ nguyên như input và biên dịch thành contract version; không tự ý mở
rộng quyền hay mục tiêu.

### 4.2 `SourceRecord` và `ClaimRecord`

`SourceRecord` bắt buộc có URI/DOI/commit, retrieval time, source class, locale,
snapshot/content hash, extraction spans, parent source, provenance cluster và
network policy.

`ClaimRecord` bắt buộc có atomic claim, scope, temporal validity, epistemic
status, support refs, contradiction refs, negative-result refs, derivation
trace và validator refs. Các epistemic status hợp lệ:

```text
OBSERVED | DERIVED | INFERRED | HYPOTHESIS | ASSUMED |
CONFLICTED | UNKNOWN | REJECTED
```

Corroboration chỉ tăng theo independent provenance cluster. Ten URL sao chép
cùng một press release là một cluster, không phải ten bằng chứng.

### 4.3 `HypothesisRecord`

Phải có prediction, measurable variables, alternative hypotheses, assumptions,
falsifiers, expected evidence, prior/likelihood assumptions nếu dùng Bayesian
analysis, owner role, preregistration hash và status.

### 4.4 `ExperimentSpec` và `ObservationRecord`

`ExperimentSpec` phải khai báo:

- independent/dependent/control variables, units và ranges;
- randomization, controls, replicates, seeds, precision và tolerance;
- execution cell, image/runtime/toolchain hash, resource lease;
- protocol, analysis plan, stopping rule và exclusion rule;
- expected artifacts, validator, negative controls và replication requirement.

`ObservationRecord` chỉ được tạo từ cell output hoặc external observation đã
được capture; chứa raw artifact refs, uncertainty, environment hash, run/seed,
timestamp và observation validator.

### 4.5 `LabRunManifest` và `LabDossier`

Manifest phải bind git SHA, schema versions, model/provider/checkpoint,
prompt/scaffold/tool hashes, browser/container/OS/hardware, budgets, policy
window, replay root và artifact Merkle/BLAKE3 root.

Dossier cuối phải có:

1. mission version và terminal status;
2. claim ledger, evidence, contradictions, negative results và unknowns;
3. hypothesis/experiment/observation index;
4. benchmark scorecard và all gate results;
5. cost/token/tool/time/resource ledger;
6. limitations, assumptions, residual gaps và forbidden inferences;
7. replay hash và clean reproduction bundle.

### 4.6 `SkillManifest`, `SkillAdmission` và `SkillExecutionReceipt`

Skill không phải prompt prose. Manifest phải cố định `skill_id`, semantic
version, capability yêu cầu, precondition có thứ tự, implementation hash,
policy hash, validator version, cost units và risk class. Registry chỉ nhận
manifest có validator callable; duplicate `(skill_id, version)` bị từ chối.

Admission bind manifest hash vào `mission_id`, `state_epoch`, capability set,
kết quả từng precondition và replay parent hash. Mọi precondition phải có
result tường minh và đều `true`; thiếu capability hoặc thay đổi thứ tự
precondition là rejection, không phải degradation im lặng.

Execution chỉ được chạy từ admission đã ghi vào replay. Receipt phải bind
admission hash, mission, replay parent hiện tại, input/result/artifact hash,
validator version và execution hash. Validator từ chối thì không có đường
thăng cấp thành evidence; snapshot/replay phải giữ admission và phát hiện
tamper. Python registry hiện là bounded adapter; Rust registry/controller và
validator isolation hosted vẫn là điều kiện release.

## 5. LabController và adaptive reasoning

### 5.1 Một reducer duy nhất

`LabController` là state reducer duy nhất. Mọi event phải kiểm tra mission
version, state epoch, dependency, capability, side effect, budget lease và
expected evidence schema trước khi append. Projection Python chỉ là view; nó
không có authority riêng. Working-tree hiện mới đạt điều này trong native
controller surface; `LabRun` projection vẫn còn adapter-owned cho lifecycle
recording, nên câu “duy nhất” chỉ là kiến trúc đích cho đến khi delegation và
hosted single-writer gate đóng.

### 5.2 Progress potential

Không dùng thay đổi câu chữ hoặc response novelty làm progress. Dùng một
potential verifier-owned:

```text
G_t = Σ_j w_j * unresolved_criterion_j
      + λ * unresolved_material_contradictions
      + μ * unreplicated_material_claims
      + ν * unmeasured_uncertainty
```

Một action chỉ được ghi là progress nếu giảm `G_t` theo evidence validator,
giảm uncertainty theo preregistered analysis hoặc tạo falsifier có giá trị.
Các trọng số phải được khai báo trước; nếu chưa calibration, dùng tuple thứ tự
định tính thay vì giả vờ precision.

### 5.3 Chọn action

Trước tiên lọc:

```text
A_t = {a | capability ∧ effect_scope ∧ risk_policy
            ∧ dependencies_ready ∧ componentwise_budget}
```

Trong `A_t`, policy lexicographic là:

1. expected verified acceptance-gap closure;
2. expected falsifier/information gain;
3. critical-path/evidence-unblock impact;
4. conservative normalized cost;
5. deterministic action hash.

Nếu dùng xác suất/EIG, phải có calibration data và interval. Nếu không có,
label rõ đó là proxy. Không cộng token, millisecond, tiền và risk trực tiếp.

### 5.4 Dừng hữu hạn

Dừng khi một trong các điều kiện sau đúng:

- mọi acceptance criterion đã có validator proof;
- không còn action admissible có expected gain dương;
- exploration pool hết nhưng finalization/recovery pool còn đủ;
- no-progress plateau hoặc cycle sau khi qua hysteresis/cooldown;
- deadline, policy hoặc risk ceiling chặn;
- chỉ còn external approval hoặc evidence không thể truy cập.

Khi `G_t > 0` tại stop, kết quả là `INCONCLUSIVE`, kèm exact residual gap.

### 5.5 Multi-agent policy

Roles là function, không phải persona: retriever, experiment designer,
executor, statistician/UQ, falsifier, blind replicator, citation auditor và
safety/resource controller.

Chỉ parallelize một ready antichain không có resource/effect conflict. Dùng
single-agent cho task tuần tự hoặc coupling cao. Chỉ bật supervisor-worker khi
expected saved critical-path time lớn hơn coordination + merge + expected
rework cost. Kết quả worker đi vào CAS; supervisor nhận refs và unresolved gaps,
không nhận transcript khổng lồ mặc định.

## 6. Research Plane

### 6.1 Search-as-code

LLM tạo một typed search program; deterministic engine thực thi:

```text
search → query_variant → fetch → render → extract → normalize
       → citation_expand → filter → dedupe → provenance_cluster
       → contradiction_search → rank → snapshot
```

Mỗi primitive có input/output schema, quota, timeout, retry class, side-effect
class và evidence obligation. Query plan được replay bằng program hash; refetch
web ở thời điểm khác là run mới.

Controller output không được thực thi như code. Nếu cần tiếp tục điều tra,
controller chỉ được trả về một object có schema
`aegis-lab-action-plan-v1` và danh sách bounded actions. Working-tree hiện hỗ
trợ năm kind có adapter rõ ràng: `search_program` (được parse thành
`SearchProgram`, native research admission/settlement và source ingestion),
`browser_action` (chỉ trên session đã bind, qua BrowserCell policy và witness
capture), `experiment_action`, `simulation_action` và `tool_call` (được
chuyển vào generic tool fence). Mọi edge runner hiện được chuẩn hóa qua
`ExecutionCellRegistry`; mỗi binding có `cell_id`, action kind, capability,
effect class và trust envelope; `execution_cell_manifest_recorded` bind canonical
inventory, count và projection digest vào event ledger trước invocation, còn
snapshot/dossier vẫn giữ projection tương thích. Tool action chỉ
được `read_only`, `network_read` hoặc `compute` nếu chưa có policy
`allow_external_writes` rõ ràng. Action khác, thiếu schema, quá quota, provider không được cấu hình hoặc payload không typed đều trở thành
blocker; model không thể cung cấp Python callable, code tùy ý, credential hay
thay đổi policy. Đây là cầu nối để controller thật sự mở rộng nghiên cứu trong
run, nhưng chưa phải bằng chứng live-provider quality hay hosted planner
authority.

### 6.2 Browser Cell

Browser Cell phải có:

- Playwright/CDP lifecycle manager và session lease;
- observer/actor permission tách biệt;
- URL/domain egress allowlist, rate limit, cookie/secret boundary;
- before/after URL, DOM, screenshot, accessibility, network và action trace;
- snapshot hash, redaction policy và prompt-injection classification;
- kill/timeout/cancel/recovery và artifact cleanup;
- live observation không được tự replay thành fact nếu không có snapshot.

External content luôn được đánh dấu `UNTRUSTED_CONTENT`. Nội dung này không thể
thay đổi system prompt, mission contract, policy hoặc secret scope.

### 6.3 Source quality

Ưu tiên primary source, implementation, paper, dataset, official documentation
và direct measurement. Claim bị mâu thuẫn phải giữ `CONFLICTED`; không trung
bình hóa bất đồng bằng điểm confidence không được calibration.

## 7. Experiment Plane và physics discipline

### 7.1 Execution cells

V1 adapters:

1. `WasmCell`: Wasmtime, fuel/epoch/memory/egress limits.
2. `TrustedLocalCell`: DEV only, explicit warning, không dùng release evidence.
3. `ContainerCell`: OCI digest, read-only input, copy-on-write output, network
   policy và resource controller.
4. `BrowserCell`: Playwright/CDP như Section 6.
5. `SimulationCell`: containerized model với model version, numerical precision,
   seed, tolerance và calibration record; local ODE primitive chỉ nhận
   derivative bounded, Euler/RK4 và explicit convergence/invariant gates.

MicroVM/Firecracker chỉ thêm sau khi threat model và benchmark chứng minh lợi
ích so với cell hiện có; không mặc định tăng complexity.

### 7.2 Toán học và vật lý đúng phạm vi

- Dimensional analysis và typed units cho mọi biến thực nghiệm.
- Conservation componentwise cho budget, lease và artifact accounting.
- Queueing chỉ dùng khi có stationarity evidence; ghi rõ `λ`, `μ`, utilization
  và phân biệt queue delay/service time.
- Simulation phải ghi discrepancy model:
  `y_real = y_sim + δ(x) + ε`. Nếu `δ` chưa được estimate trên held-out live
  observations, kết quả chỉ là `SIMULATED` hoặc `INFERRED`.
- Với mục tiêu tối ưu tín hiệu điện, model phải giữ riêng điện áp `V`, dòng
  `A`, công suất `W`, năng lượng `J`, trở kháng và thời gian lấy mẫu; mỗi
  bước phải có residual Kirchhoff/state-space, giới hạn ổn định, noise/sensor
  model, anti-aliasing và calibration artifact. Không được gọi proxy CPU,
  wall-time hoặc độ sáng tín hiệu là năng lượng điện nếu chưa có phép đo phần
  cứng đã calibration.
- Không suy ra joule/energy từ wall time hoặc CPU utilization nếu không có
  hardware energy measurement đã calibration.
- Tolerance, floating-point mode, BLAS/GPU driver và platform phải nằm trong
  environment hash.
- Với một observation duy nhất, Lab chỉ báo point measurement và trạng thái
  `INSUFFICIENT_REPLICATION`; không được phát một khoảng CI95 suy biến như thể
  đã đo được sampling uncertainty.

## 8. Benchmark Protocol V2

### 8.1 Protocol bắt buộc

Mỗi benchmark phải seal trước khi candidate chạy:

```text
identity: protocol_id, schema_version, protocol_hash, owner, created_at
claim: intended_use, construct, claim_scope, baseline, non_goals
subject: git_sha, artifact_hash, model/provider/checkpoint, prompt/scaffold/tool hashes
data: dataset/version/content_hash, split, item_ids_hash, cutoff, contamination
environment: OS/kernel, CPU/GPU/RAM, driver, container, locale, network, browser policy
execution: items, trials, seeds, warmups, attempts, timeouts, budgets, stopping rule
measurement: unit, metric, estimand, aggregation, uncertainty, MDE, power, alpha
gates: direction, frozen threshold, confidence level, hard/advisory class
scoring: objective validator, hidden-validator digest, judge calibration artifact
integrity: canaries, anti-cheat, negative controls, reproduction rule
```

### 8.2 Recording rule

Mỗi `item × trial × seed` phải có raw record, kể cả success, failure, timeout,
refusal, retry, cancellation và error. Không được âm thầm drop sample. Baseline
và candidate chạy cùng environment, interleaved/paired khi có thể. Ghi prompt,
model, provider, tool calls, token input/output, wall time, compute time, money,
stdout/stderr, transcript refs và artifact refs.

Working-tree contract `BenchmarkTrialRecord` hiện giữ `item_id`,
`trial_index`, `seed`, `status`, metric value nếu thành công, `error_class` và
artifact hash; `BenchmarkResultV2` tách `trial_count` (mọi raw record) khỏi
`valid_trial_count` (chỉ metric hợp lệ). Một trial lỗi làm benchmark
`REJECTED` nhưng record lỗi vẫn được giữ để điều tra và reproduction.

### 8.3 Thống kê mặc định

- Confidence level mặc định 95%; family-wise hard gates dùng Holm/FWER,
  khai báo trước khi chạy.
- Trial lặp trên cùng item là clustered; không coi là independent n.
- Correctness benchmark tối thiểu phải chạy toàn bộ item của frozen split; nếu
  stochastic, tối thiểu 5 seed đã preregister cho mỗi item, hoặc sample-size
  lớn hơn do power analysis yêu cầu.
- Performance benchmark tối thiểu có 10 warmup observations và 30 paired
  baseline/candidate blocks; p99 không được báo nếu không có ít nhất 1.000
  observations hợp lệ trong cùng stratum. Các floor này chỉ là sàn, không thay
  thế power analysis.
- Correctness superiority/non-inferiority dùng effect size và one-sided CI.
- Metric cần lớn hơn `τ`: PASS chỉ khi `LCB ≥ τ`.
- Metric cần nhỏ hơn `τ`: PASS chỉ khi `UCB ≤ τ`.
- Regression dùng non-inferiority margin `δ` đã freeze.
- CI cắt ngưỡng là `INCONCLUSIVE`, không gọi là pass hay scientific fail.
- Latency đo trực tiếp p50/p95/p99; không đổi mean thành p95.
- Nếu chưa đủ power/MDE, report là `INCONCLUSIVE`, không suy diễn.
- Performance mặc định có warmup tách biệt, monotonic clock, random/interleave
  baseline-candidate và ghi background load/thermal state.

### 8.4 Ba track contamination

1. `SEALED_CAPABILITY`: blind/sequestered, internet tắt, hidden validator ngoài
   quyền đọc của agent.
2. `OPEN_WEB_RESEARCH`: internet là công cụ hợp lệ; đo search, citation,
   synthesis, experiment và contradiction handling, không gọi là clean-memory.
3. `LIVE_DYNAMIC`: ghi timestamp, locale, region, snapshot và block interleave;
   claim chỉ áp dụng cho web state đã quan sát.

Contamination status:

```text
SEALED_FRESH | NO_KNOWN_EXPOSURE | POSSIBLE_PUBLIC |
SUSPECTED | CONFIRMED | UNKNOWN
```

### 8.4.1 Anti-overfitting bắt buộc

Benchmark không được biến thành bài luyện đúng một bộ câu hỏi hoặc một môi
trường:

1. Protocol, threshold, denominator, scorer và stopping rule được hash/seal
   trước khi candidate nhìn test/holdout.
2. Tách `dev`, `validation`, `test` và `holdout`; mọi tuning scaffold/prompt/
   tool budget chỉ được làm trên dev.
3. Nếu agent hoặc người xây harness quan sát holdout dù chỉ một lần để sửa
   system, holdout bị hạ xuống dev và phải rotate một holdout mới.
4. Giữ hidden canaries, adversarial items, unseen domain/task family và
   contamination audit; không công khai toàn bộ validator.
5. Randomize item order, seed, baseline/candidate interleave và giữ lại toàn
   bộ attempts/best-of-N; best-of-N phải tính đầy đủ token/time/cost.
6. Báo kết quả theo item/seed và aggregate theo task cluster; một aggregate
   đẹp không được che failure concentration ở một domain.
7. Mỗi thay đổi lớn của model, prompt, tool, dataset, browser hoặc environment
   tạo subject/protocol hash mới; không nối chuỗi kết quả như thể cùng một thử
   nghiệm.
8. Release claim phải có untouched holdout hoặc independent reproduction. Nếu
   không còn holdout sạch, trạng thái cao nhất là `INCONCLUSIVE`.

### 8.5 Judge policy

Hard pass/fail ưu tiên objective validator, hidden test, artifact, DOM/state
assertion, hash và invariant. LLM judge chỉ advisory cho tới khi được blind,
randomized và calibration trên gold labels độc lập. Phải báo confusion matrix,
agreement, near-threshold errors và prompt/order/model sensitivity.

### 8.6 Failure protocol

1. Seal toàn bộ failure evidence; không đổi threshold, denominator hoặc xóa trial.
2. Chạy reference solver và controls để xác định run hợp lệ.
3. Phân loại `CANDIDATE`, `HARNESS`, `ENVIRONMENT`, `DATA`, `SCORER`,
   `RESOURCE`, `STATISTICAL` hoặc `POLICY`.
4. Nếu harness lỗi, phát hành protocol version mới và rerun baseline/candidate.
5. Nếu candidate lỗi, giữ `FAIL`; ablation chỉ trên dev set.
6. Holdout đã dùng để tune trở thành dev set; phải rotate holdout.
7. Hết budget mà CI vẫn cắt ngưỡng thì trả `INCONCLUSIVE` với residual gap.
8. Kết quả mới chỉ supersede kết quả cũ; không xóa lịch sử.

Trạng thái chuẩn:

```text
NOT_RUN | PASS | FAIL | INCONCLUSIVE | INVALID |
CONTAMINATION_SUSPECTED | BLOCKED_BY_POLICY
```

## 9. Giao tiếp và vận hành

### 9.1 Public API mục tiêu

```python
lab = Lab(policy=LabPolicy.max_within_policy(), budget=LabBudget(...))
run = lab.start(LabMissionSpec(...))

async for event in run.events():
    observe(event)

result = await run.result()
dossier = await run.export_dossier()
```

`Agent.run()` vẫn giữ semantics one-shot. `Lab` là long-horizon API; không thay
đổi meaning của API cũ trong im lặng.

Working-tree đã triển khai `Lab`/`LabSession` với `events()`, `result()`,
`export_dossier()`, `pause()`, `resume()` và `cancel()`; Rust native verifier
admit event append/transition khi policy yêu cầu. Controller có typed
`search_program`, `browser_action`, `experiment_action`, `simulation_action`
và `tool_call`; launcher-owned browser session được giữ xuyên loop và đóng
idempotent sau loop. Event delivery hiện vẫn là projection local in-process,
còn operator UI, durable multi-process stream và hosted delivery guarantee
vẫn là target.

### 9.2 Commands

`start`, `status`, `watch`, `pause`, `resume`, `branch`, `cancel`, `approve`,
`export`, `replay`, `inspect_claims`, `inspect_gaps`.

### 9.3 Event stream

Event user-facing chỉ là evidence delta: mission sealed, source captured,
hypothesis registered, experiment queued/started/completed, claim supported or
contradicted, benchmark gate, blocked, retry, finalization và dossier sealed.

Không phát raw chain-of-thought. User feedback tạo mission version mới và được
replay-bind; không âm thầm mutate mục tiêu đang chạy.

Working-tree hiện đã có `LabRun.event_cursor()`/`events_since(cursor)` với
`LabEvent` hash-chain, native event/transition admission, security/research/
observation/blocker events và `RunResult.lab_events`; đây là projection API
local, chưa phải operator UI, durable multi-process stream hoặc hosted delivery
guarantee.

### 9.4 Autonomy matrix

| Hoạt động | MAX_WITHIN_POLICY |
|---|---|
| Read/search/fetch/browser observation | tự động trong quota |
| Local compute/Wasm/container experiment | tự động trong cell/budget |
| Retry, branch, parallel worker | tự động nếu side-effect safe |
| Login/account/cookie use | explicit scoped capability |
| Email, post, external write, deployment | approval bắt buộc |
| Payment, legal commitment, destructive action, secret exfiltration | không tự động |

## 10. Lộ trình triển khai và exit gates

### P0 — Truth Convergence và Invariant Repair

**Deliverables:**

- Sửa BudgetLedger theo pool/lease/conservation; refund atomicity; recovery path.
- ValidatorProof criterion-specific; private authoritative fields.
- Domain-separated GoalContract hash và immutable definition/progress split.
- Demote PAV khỏi correctness; derive metadata trong trusted executor.
- Lab capability ledger design; registry parity check; ADR-015.
- Generated status views; archive stale plans/reports; sửa broken links.
- Deprecation/diagnostic cho inert `browser/max_steps`.

**Exit gate:** property tests cho conservation/atomicity/hash uniqueness/evidence
binding pass; registry 20/20 parity; GT96 status drift bằng 0; không có claim
production dựa trên local-only evidence.

### P1 — Lab Contract Kernel

**Deliverables:** `core/rust/src/lab/`, schemas, event kinds, `LabController`,
state transitions, ActionAdmission, skill manifest/admission/validator receipt,
typed experiment/tool execution admission-settlement, replay reducer, Python
`Lab` facade.

**Exit gate:** illegal transition, stale lease, duplicate settlement, failed
validator, crash/replay và cancellation đều fail-closed; cùng event log tạo
cùng next legal action trong process mới.

**Local checkpoint (2026-08-27):** experiment/simulation và explicit generic
tool execution hiện có admission trước side effect, `attempt`/execution
identity fence, lease/effect/role/schema/stop-rule binding, settlement
`SUCCESS|REJECTED|TIMED_OUT|CANCELLED` và bounded retry; `LabSession.cancel()`
để runner settle `CANCELLED` trước khi native cancellation aborts the run.
Rust projection tests, Python regression và v28 wheel smoke chứng minh
duplicate/timeout/retry/cooperative-cancel paths trong checkout; v18 packaged
smoke đã chứng minh generic tool, controller step và synthesis đều đi qua
`rust_native_verified` với sáu tool events; v19 thêm gateway retry fence và
được packaged smoke lại kiểm chứng. v20, v21, v23 và v24 packaged smoke ngoài
checkout đều chạy typed controller search + browser action plan qua
`rust_native_verified`, với source capture và browser action settlement.
Controller action plan typed hiện có thể đưa `SearchProgram`, bounded
`ExperimentSpec`/`SimulationSpec` hoặc generic tool request động vào cùng các
fence đó; runner được resolve duy nhất qua `ExecutionCellRegistry` từ cấu hình
trusted, không nhận callable từ model. `ProcessExecutionCell` là cell opt-in
cho runner picklable; local timeout/cancellation test terminate child process
không hợp tác, nhưng không chứng minh OS resource enforcement hoặc descendant
cleanup. Launcher-owned browser session được giữ
bound xuyên controller loop và
cleanup sau loop. Test đã chứng minh search/experiment/simulation action không
cần static action hook, action kind không rõ bị từ chối, external effect không
được escalate nếu policy không cấp; dispatcher hiện chạy search actions ở pha
trước preregistration để source/claim/hypothesis/experiment trong cùng một
response không phụ thuộc thứ tự JSON; v28 packaged outside-checkout smoke còn
chứng minh một response gộp research, browser, experiment và simulation qua
`rust_native_verified`, không blocker và có hai observation distinct; launcher
continuity và controller-selected experiment cũng không tạo blocker. Replay
directory giờ có `ReplayWriterLease` advisory lock giữ bằng file descriptor, và
cross-process contention regression chứng minh hai writer không thể cùng chiếm
một directory; đây chỉ là local writer serialization, chưa phải hosted authority.
P1 chưa đóng vì implicit Agent/provider planner-lease coverage, non-cooperative
interruption, adapter ngoài registry contract và hosted single-writer vẫn chưa có
evidence bắt buộc. Crash-prefix recovery hiện đã có một API event-log-driven cho toàn bộ
explicit execution lanes: research, experiment, browser actor/observer, skill
và generic tool đều được settle `REJECTED` với `UNKNOWN_SIDE_EFFECT`, rồi dossier
được chuyển `blocked`; reconcile sau `aborted` bị từ chối fail-closed.

Vòng thực thi tiếp theo đã bổ sung provider-attempt hook vào gateway adapter:
primary và từng fallback candidate hiện được admit/settle riêng bằng cùng
`tool_execution` fence, gắn `phase`, `call_id`, `gateway_attempt`, candidate
index, provider-budget/policy hash và output/error hash. Cancellation được settle
`CANCELLED` trước khi Lab chuyển `aborted`; snapshot crash-prefix có thể được
operator reconcile thành `REJECTED`/`UNKNOWN_SIDE_EFFECT` và chuyển `blocked`,
không bao giờ tự nâng một side effect mơ hồ thành `SUCCESS`. Điều này đóng thêm
phần local provider-attempt coverage, nhưng không chứng minh process kill,
non-cooperative provider, adapter ngoài contract hoặc hosted single-writer.
Gói v38 (SHA-256 `822268bdc6f0b0b5be3c6d729de5b6a1fda4a8f988d0be17ac929f5ae8bcdc04`)
là bằng chứng đóng gói lịch sử cho lane này; v37/v36/v35/v34/v33/v28/v27 chỉ
còn là tiền nhiệm rollback lịch sử. Bằng chứng đóng gói hiện hành là
replacement wheel được ghi ở Section 18.7 và closure artifacts; v63 chỉ còn
là historical vì wheel/records không còn trên máy. v38 thêm native state admission cho
settlement phục hồi ở trạng thái `blocked`; mixed-lane crash-prefix regression
được chạy lại sau restore.
Native-required gateway giờ còn có post-construction handshake: factory không
được phép chỉ nhận rồi bỏ qua callback fence; regression mới chứng minh trường
hợp đó bị từ chối trước provider side effect.
M2 guard bổ sung còn đối chiếu route trả về với từng nested provider receipt:
gateway có thể không được settle `SUCCESS` nếu route thiếu, candidate order lệch,
provider selection không nhất quán hoặc callback đã được cài nhưng không hề được
gọi. Guard này chỉ chứng minh fail-closed sau khi adapter trả kết quả; nó không
hoàn tác một provider side effect mà adapter đã thực hiện ngoài contract.

Vòng thực thi kế tiếp đã hợp nhất các edge runner vào
`ExecutionCellRegistry`. Compatibility options cũ được chuyển thành binding
trusted cho `search_program`, `browser_action`, `experiment_action`,
`simulation_action`, `tool_call`, `skill_execution` và `post_completion_effect`; custom binding có thể khai báo rõ
`cell_id`, capability, effect class và trust level. Dispatcher không còn lấy
runner từ một nhánh tùy ý sau khi controller đã chọn action: cell identity và
policy được kiểm tra trước invocation, `execution_cell_manifest_recorded` bind
canonical manifest/count/digest vào native event ledger trước cell execution,
còn snapshot/dossier giữ projection tương thích; duplicate action-kind hoặc cell/effect/capability mismatch trở thành
blocker. Sau khi manifest event được chụp, registry được seal; mutation hậu cấu hình
bị từ chối để binding thực thi không thể lệch khỏi replay. Test registry chứng
minh identity, capability, effect, duplicate registration và late mutation đều
fail-closed. Crash recovery giờ quét event log thay vì dựa vào
in-memory index và settle được mọi admission đang mở mà không đổi thành công giả.
Khi operator truyền `lab_execution_cells`, registry giờ là strict allowlist;
native-required/PROD runs cũng bật strict resolution sau khi convert các legacy
options thành binding:
dispatcher không còn rơi xuống legacy runner trong `options` nếu lane bị bỏ
thiếu; test search/browser/skill chứng minh adapter ngoài registry không được gọi.
Sau khi manifest event được chụp, registry được seal để late registration không thể
làm lệch binding replay; regression chứng minh mutation hậu cấu hình bị từ chối.
Post-completion `memory.index_session` cũng được resolve qua dedicated cell;
`PROD` hoặc policy bắt buộc sẽ ghi `post_completion_effect_missing` nếu không có
trusted cell, thay vì return im lặng.
Đây là local closure cho explicit adapter contract và partial-failure recovery;
implicit side effects, hosted multi-process writer và OS-level process/resource
enforcement vẫn mở. `ProcessExecutionCell` hiện là cell opt-in cho runner
picklable: local test chứng minh timeout và cancellation sẽ terminate child
process không hợp tác, nhưng không mở rộng thành guarantee cho descendants,
Job Object/cgroup, provider-side effects hoặc mọi adapter đã tồn tại.

Replay archive recovery cũng đã được siết: native verifier mới đối chiếu
`manifest_hash` đã đóng dấu trong snapshot với manifest của prefix phục hồi.
Prefix ngắn hơn, thiếu segment hoặc tail hỏng vì vậy không thể tiếp tục như
run hoàn tất; verifier boolean cũ chỉ còn là compatibility fallback cho adapter
không có strict surface.

Explicit external-effect keys now also use a local advisory lease per exact
`tool_name::effect_class` key for the whole Lab run. Missing local lease
coordination for an explicit external-effect contract fails closed. This reduces
same-key local concurrency, but does not claim provider-side locking,
idempotency, rollback, DNS/proxy/kernel containment or hosted authority.

### P2 — Research Plane

**Deliverables:** search-as-code AST/IR, provider adapters, source snapshot,
claim graph, citation spans, browser lifecycle/cell, prompt-injection boundary,
network outage and stale-source handling.

**Exit gate:** seed live research task tạo citation exact, snapshot hash,
contradiction set, browser witness và dossier; page instruction không thể đổi
mission/policy/secret scope.

### P3 — Experiment Plane

**Deliverables:** Wasm/container/browser/simulation cell adapters, ParameterRegister,
ExperimentSpec, ObservationRecord, unit checks, seeds, controls, numerical
tolerance, bounded Euler/RK4 ODE traces with step-doubling/invariant gates,
environment capture và replication bundle.

**Exit gate:** computational experiment tái lập từ clean bundle; flaky run,
network outage, cancellation và simulator discrepancy được phân loại đúng.

### P4 — Adaptive Science

**Deliverables:** acceptance-gap potential, deterministic action selector,
hysteresis/cooldown/no-progress/cycle logic, functional worker roles, falsifier,
blind replicator, replay-bound skill planner và contradiction resolution.

**Exit gate:** controller không chọn inadmissible action, dừng hữu hạn, không
false-positive cycle với replication seed, và multi-agent chỉ được bật khi
đạt coordination/rework efficiency gate.

### P5 — Benchmark, Operations và Rollout

**Deliverables:** BenchmarkProtocolV2/RunV2, hidden evaluator cell, contamination
tracks, statistical gate engine, adversarial suite, operator event UI, chaos
resume, external benchmark adapters (BrowserGym/AgentLab, PaperBench, RE-Bench,
Inspect-style task/solver/scorer).

**Exit gate:** chỉ `PASS` qua tất cả hard safety/policy/witness/correctness gates
mới được release. `INCONCLUSIVE`, `INVALID` và `BLOCKED` đều fail-closed.

## 11. Acceptance test matrix

### 11.1 Unit/property

- Arbitrary budget reserve/spend/lease/refund bảo toàn `T_i` và non-negativity.
- Bất kỳ `Err` nào giữ nguyên state bytes/hash/epoch.
- Lease settle/refund exactly once; duplicate/idempotency bị từ chối.
- GoalContract list partition khác nhau không hash giống nhau.
- Không criterion upgrade nếu thiếu ValidatorProof đúng mission/criterion/env.
- Correlated duplicate sources không làm tăng evidence support.
- `success=true` không hợp lệ nếu thiếu evidence obligation.
- PAV metadata giả không thể thay đổi acceptance verdict.
- Task status không bypass transition.
- Native Rust event/transition admission rejects invalid append before the
  Python projection becomes visible; `PROD` without the extension fails closed.
- Skill manifest duplicate, missing-capability, false-precondition, stale
  replay parent, validator rejection và receipt tamper đều fail-closed trước
  khi skill output trở thành evidence.
- Blocker additions are deduplicated, typed and hash-chained; a failed or
  blocked path remains visible in the evidence stream rather than mutating a
  side list silently.
- Search freshness windows reject future/stale snapshots, and contradiction
  quotas reject correlated results without distinct provenance clusters.
- Observation values and named constraint residuals are converted to a
  canonical experiment/constraint unit before statistics or tolerance gates.
- Finalization reserve luôn reachable; reopen cần critical-gap evidence và
  bounded generation.
- Native `LabController` binds exploration admission to the current replay
  epoch, rejects admission while finalizing, records budget/finalization events,
  verifies the typed `BudgetVector` payload hash, mirrors projection state
  transitions and restores only snapshots whose runtime chain, budget lanes and
  step bound are valid.
- Browser observer admissions bind an externally-owned or launcher-owned lease
  before any read, enforce the current HTTPS/host policy, consume only the
  observer quota, and emit a typed receipt before synthesis; actor actions and
  observer reads cannot silently share an untracked side effect.

### 11.2 Fuzz và adversarial

- Malformed JSON/Arrow/manifest/browser artifacts/path refs.
- Prompt injection trong HTML, PDF, source code và tool output.
- Hash mismatch, missing/duplicate/reordered/tampered artifacts.
- Secret lure, credential exfiltration, external write ngoài scope.
- Redirect ngoài allowlist, private-IP fetch và DNS/egress policy drift.
- Timeout, crash boundary, network outage, provider drift và flaky experiment.
- Benchmark answer lookup, hard-coded output, scorer/reference tamper,
  monkeypatch clock, reward hacking và evaluation awareness.

### 11.3 Integration/E2E seed missions

1. Open-web technical research có citation, snapshot, contradiction và dossier.
2. Hypothesis computational có control, negative result, uncertainty và blind
   replication.
3. Benchmark regression fail; controller reproduce/root-cause/retest mà không
   nới threshold.
4. Malicious page prompt injection bị chặn và được lưu evidence.
5. Run 100+ steps crash/resume trả cùng next legal action.
6. Max autonomy cố external write nhưng bị `BLOCKED_BY_POLICY`.
7. Simulator cho kết quả lệch live observation và không được nâng thành
   `OBSERVED`.

## 12. Chỉ số và quy tắc claim

Lab phải báo tối thiểu:

- correctness theo criterion và item-level result;
- citation precision/coverage/freshness và contradiction discovery;
- replication rate và negative-result rate;
- policy violation, hard block, approval và prompt-injection resistance;
- crash/resume, cancellation, budget adherence, replay determinism;
- token, tool call, wall/compute time, money và resource utilization;
- latency p50/p95/p99 khi protocol yêu cầu;
- uncertainty, calibration và exact residual gaps.

Không dùng weighted average để hiệu năng cao che safety/policy violation. Safety,
policy, witness và evidence là conjunctive hard gates. Claim luôn kèm:

```text
claim_scope, evidence_class, applies_to_commit, protocol_hash,
environment_hash, model/provider, known_limitations, residual_risk
```

## 13. Governance, migration và rollback

### 13.1 Source-of-truth hierarchy

1. Normative contracts/ADR/schema.
2. Machine-readable capability/evidence manifest.
3. Generated Markdown views.
4. Historical plans/reports.
5. User request and external research as provenance/input, never as runtime
   authority.

### 13.2 Compatibility

- `Agent.run()` và `AegisAdapter` giữ output contract cũ.
- `Lab` là additive API, feature-flagged trong giai đoạn đầu.
- Không migrate persisted archive cho đến khi schema/version/replay fixtures có.
- Mọi new event có version và forward-unknown policy.

### 13.3 Rollback

- Rollback bằng feature flag về one-shot Agent hoặc bounded Python task.
- Không xóa replay/artifact; đánh dấu run superseded.
- Nếu schema migration lỗi, dừng promotion, giữ last valid prefix và export
  recovery dossier.
- Không rollback bằng cách hạ threshold hoặc đổi claim scope.

### 13.4 Bảo trì plan và change control

- `applies_to_commit` chỉ được cập nhật sau khi rerun baseline/evidence audit;
  không đổi metadata để hợp thức hóa code chưa test.
- Mỗi phase có owner, dependency, exit gate và evidence refs; trạng thái phase
  không được nhập tay vào nhiều tài liệu.
- Scope, authority, side-effect policy, schema hoặc threshold đổi phải có
  ADR/version mới, migration note và impact lên replay/benchmark.
- Mọi generated view phải phát sinh từ capability/evidence registry; CI phải
  fail khi normalized duplicate, broken link, orphan requirement hoặc status
  drift xuất hiện.
- Bản plan lịch sử không bị xóa; bản mới chỉ `supersede` bản cũ bằng
  `document_id`, commit và lý do thay đổi.
- Trước mỗi release candidate, plan phải tự vượt front-matter/schema/link
  lint, source-of-truth parity, code-quality gate và benchmark protocol audit.

## 14. Ràng buộc nghiên cứu bên ngoài đã hấp thụ

- Search-as-code: học mô hình typed programmable retrieval primitives từ
  [Perplexity Research](https://research.perplexity.ai/articles/rethinking-search-as-code-generation).
- Multi-agent: dùng task decomposability/coupling để chọn topology; không mặc
  định swarm, phù hợp kết quả [Google Research](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)
  và [Anthropic Engineering](https://www.anthropic.com/engineering/multi-agent-research-system).
- Scientific collaboration: học specialized generation/reflection/ranking/
  evolution/meta-review từ [Google AI co-scientist](https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/),
  nhưng self-Elo vẫn là advisory, không phải ground truth.
- Browser/evaluation: tham khảo [BrowserGym/AgentLab](https://github.com/ServiceNow/BrowserGym)
  cho environment/trace/reproducibility, không coi đây là production browser
  authority.
- Research replication: tham khảo [PaperBench](https://openai.com/index/paperbench/),
  [RE-Bench](https://metr.org/blog/2024-11-22-evaluating-r-d-capabilities-of-llms/)
  và [AISI Inspect](https://www.aisi.gov.uk/blog/open-sourcing-our-testing-framework-inspect)
  cho rubric, human-equivalent resources, task/dataset/solver/scorer và sandbox.
- Provenance export có thể tương thích W3C [PROV](https://www.w3.org/TR/prov-overview/),
  nhưng không kéo RDF/ontology vào hot path nếu không có gate.

## 15. Definition of Done của toàn Lab

AEGIS Lab chỉ được gọi là đạt mục tiêu khi tất cả điều kiện sau đồng thời đúng:

1. P0 authority/invariant không còn phản ví dụ mở.
2. LabController là authority duy nhất và replay determinism đã được chứng minh.
3. Một seed research mission có browser/search/evidence/claim/contradiction
   loop thực sự, không phải one-shot LLM call.
4. Một seed experiment có protocol, controls, observations, uncertainty,
   negative result và clean replication.
5. Adaptive controller tự chọn strategy theo task structure và dừng hữu hạn.
6. External content không thể vượt prompt/policy/secret boundary.
7. Benchmark V2 lưu raw records, baseline, CI, contamination, hidden validator,
   adversarial cases và reproduction.
8. Không có status drift giữa registry, manifest và generated views.
9. Production blockers NV-004, NV-016, NV-017, NV-018 và NV-019 đã có external
   evidence đúng final SHA; các platform/release blockers còn lại cũng đã được
   disposition rõ.
10. Dossier cuối nêu cả điều hệ thống không biết, không làm được hoặc chưa
    được kiểm chứng.

Cho đến khi 10 điều kiện này đồng thời đạt, trạng thái đúng của sản phẩm là
`LAB_RUNTIME_IMPLEMENTATION_IN_PROGRESS`, không phải production-ready.

## 16. Current execution status

The plan remains IN_EXECUTION. Current blockers and verification claims are
owned by the evidence registries listed in Section 2.1 and summarized in the
generated status view. This plan does not duplicate dated run ledgers or raw
benchmark results. Treat any claim without current registry evidence as
NOT VERIFIED.

## 17. Traceability từ yêu cầu người dùng tới kế hoạch

| Yêu cầu | Nơi đáp ứng trong plan | Evidence bắt buộc |
|---|---|---|
| Sandbox trở thành phòng Lab | Sections 1, 5, 7, P1–P3 | LabController + Execution Cell E2E |
| Nghiên cứu thật và search-as-code | Section 6, P2 | live source snapshots, citations, replay |
| Browser tương tác liên tục | Section 6.2, B1.2, P2 | Playwright/CDP witness + injection test |
| Knowledge synthesis và làm điều chưa biết | Sections 4.2–4.4, 5.2, P4 | claim graph, hypotheses, falsifiers, replication |
| Thí nghiệm và mô phỏng | Section 7, P3 | ExperimentSpec, controls, uncertainty, clean rerun |
| Tối ưu adaptive, tiết kiệm token | Sections 1.2, 5.2–5.5, 12 | paired cost/correctness benchmark |
| Toán học/vật lý đúng phạm vi | Sections 3, 5.2, 7.2 | conservation, units, calibration, discrepancy evidence |
| Không bias/không overclaim | Sections 0, 4.2, 8.4–8.6, 12 | contradiction, contamination, calibration, qualified claims |
| Benchmark cực nghiêm | Section 8, P5 | sealed protocol, raw trials, CI, hidden validator |
| Codebase thống nhất, không over-engineer | Sections 1.2, 3.3, 13 | reuse audit, single authority, ADR, rollback |
| Caveman + Ponytail | Section 1.2 | minimal-correct diff và evidence-preserving communication |
| Giao tiếp/vận hành rõ ràng | Section 9 | event stream, commands, pause/resume/branch/cancel |
| Cover tình huống và failure | Sections 3, 8.6, 11 | adversarial, fuzz, chaos, residual-risk dossier |

Một hàng chỉ được chuyển sang `CLOSED` khi evidence trong cột cuối tồn tại,
đúng commit/protocol scope và vượt gate tương ứng. Việc có code symbol, test
name hoặc tài liệu mô tả không đủ để đóng hàng.

## 18. Historical verification records

Detailed local closure reports, reconciliation errata, package outputs, and
host paths from earlier revisions are retained in the private local archive
with the pre-cleanup plan snapshot. They are historical records, not evidence
of current release readiness. Current claims must be checked against the
evidence registries.

## 19. Target architecture design và migration graph sau design-closure

### 19.0 Trạng thái, phạm vi và cơ sở quyết định

Phần này là **target design**, được viết sau design-closure pass gần nhất; epoch
được tham chiếu duy nhất từ local-only design-closure evidence
để tránh nhân bản một giá trị dễ lỗi thời. Nó không
biến bất kỳ claim `PARTIAL_LOCAL`, `NOT VERIFIED`, `UNKNOWN` hoặc blocker
external nào thành proof. Mục tiêu là khóa quyết định và thứ tự migration để
vòng surgical convergence sau đó không tiếp tục tạo thêm fork kiến trúc.

Phạm vi mục tiêu là một **AI Agent Laboratory**: một mission có thể nghiên cứu
bằng search-as-code và browser nhiều bước, tổng hợp kiến thức có provenance,
đề xuất và phản biện giả thuyết, chạy thí nghiệm/simulation trong cell được
kiểm soát, rồi xuất dossier có replay và mức độ tri thức trung thực. Sandbox
Wasm/code không bị thay thế; nó là một `ExecutionCell` trong Lab.

Không được hiểu mục tiêu “đúng tuyệt đối”, “không bias” hay “cover mọi tình
huống” theo nghĩa tuyệt đối không thể chứng minh. Runtime phải thay bằng:

1. coverage matrix và failure taxonomy có phạm vi rõ;
2. phản biện đối lập, falsifier và blind replication;
3. calibration/uncertainty và provenance từng claim;
4. fail-closed khi thiếu authority, witness, budget hoặc schema;
5. residual-risk ledger bắt buộc trong mọi dossier và release.

### 19.1 Quyết định đích đã khóa

| ID | Quyết định | Owner đích | Điều không được phép |
|---|---|---|---|
| D-001 | Root `aegis-cognition` là distribution canonical và command `aegis` canonical | root `pyproject.toml` + release manifest | Hai distribution cùng publish command `aegis` |
| D-002 | `core/python` là compatibility bridge có versioned sunset, không là authority | compatibility package owner | Bridge tự phát hành `VERIFIED` hoặc tự sở hữu reducer |
| D-003 | Một Rust `LabController` là single-writer reducer của mọi Lab event/lease/budget/settlement | `core/rust/src/lab.rs` và typed PyO3 boundary | Python mutable projection hoặc adapter ngầm ghi authority |
| D-004 | `LabRun` Python chỉ là typed façade/projection; mọi projection phải được native admission trước khi phát event | `aegis_cognition/lab.py` + FFI | Hash-only legacy admission cho typed side effect |
| D-005 | Trust policy thuộc mission/controller, được bind bằng `policy_hash`; wrapper chỉ chọn context mặc định có ghi rõ | `LabMissionSpec`/Rust controller | `DEV`/`PROD` ngầm ghi đè nhau ở evidence/provider |
| D-006 | Execution cell là owner duy nhất của effect retry, attempt fence và idempotency | `ExecutionCellRegistry` + từng cell | Application/adapter/SDK retry chồng không giới hạn |
| D-007 | Browser, network, provider, process và filesystem đều là capability-scoped cells | cell registry + platform adapter | Direct sink ngoài admission, kể cả helper “tiện ích” |
| D-008 | Search-as-code là declarative research program; model chỉ diễn giải dữ liệu không tin cậy | `ResearchCell`/`SearchProgramExecutor` | Page/provider text trở thành instruction hoặc proof |
| D-009 | Simulation chỉ phát `SIMULATED`/`MEASURED_INPUTS_ONLY` trừ khi có calibration witness độc lập | `ExperimentCell`/validator | Simulation tự nâng thành physical truth |
| D-010 | Benchmark raw trials, protocol hash và verdict tách khỏi candidate; hidden scorer là external gate | Benchmark protocol/validator owner | Self-reported score hoặc validator cùng quyền bị coi là generalization |
| D-011 | `ffi.rs` chỉ là compatibility façade sau khi internal families được tách có bằng chứng | FFI API owner | Đổi visibility hoặc split mù trước API inventory |

Các quyết định D-001–D-011 là lựa chọn thiết kế đích, không phải bằng chứng rằng
checkout hiện tại đã đạt chúng. Bất kỳ quyết định nào làm thay đổi public
contract phải đi qua migration graph và compatibility gate bên dưới.

### 19.2 Mô hình đích và ranh giới quyền hạn

```text
User / Operator
      │ mission + explicit policy + scope
      ▼
Mission Compiler (Python, pure/typed)
      │ LabMissionSpec + contract_hash + policy_hash
      ▼
Rust LabController / single writer / replay reducer
      │ admission → lease → attempt → effect → settlement
      │                 │
      │                 ├── ResearchCell
      │                 │     ├── SearchProgram (declarative)
      │                 │     └── BrowserCell (actor/observer split)
      │                 ├── ProviderCell (route + SDK policy + receipt)
      │                 ├── ExperimentCell
      │                 │     ├── code/Wasm/process cell
      │                 │     ├── simulation/physics cell
      │                 │     └── electrical/hardware adapter (external)
      │                 ├── SkillCell (manifest + admission + receipt)
      │                 └── Memory/ContextCell (bounded, hash-bound)
      ▼
+Evidence Graph + Falsifier + Blind Replicator
      ▼
Review reducer → LabDossier → replay/archive/export
```

Mỗi effect-bearing action phải mang tối thiểu:

```text
mission_id, contract_hash, state_epoch, action_id, attempt_id, lease_id,
actor_role, effect_class, input_hash, policy_hash, expected_schema,
stop_rule, deadline, idempotency_key
```

Receipt terminal phải mang `status`, `result_hash` hoặc `error_class`,
observed-at, environment/capability hash và `settlement_id`. Một receipt thiếu
schema, lease, attempt, policy hoặc terminal identity không được nâng thành
success; tùy effect, nó phải là `REJECTED`, `UNKNOWN_SIDE_EFFECT` hoặc blocker.

Rust reducer sở hữu transition, sequence, state epoch, budget conservation,
lease/attempt uniqueness, cross-record references, replay hash chain và
completion eligibility. Python được phép materialize typed records, gọi cell
runner sau admission, hiển thị stream và xây projection; Python không được
quyết định terminal truth bằng cách tự đổi field.

### 19.3 Public API và giao tiếp vận hành

Public API đích vẫn additive ở giai đoạn đầu:

```python
lab = Lab(policy=explicit_policy)
session = lab.start(LabMissionSpec(...))
async for event in session.events():
    render(event)                 # observation-only
dossier = await session.export_dossier()
```

`Agent.run()` giữ one-shot compatibility semantics. `Agent(..., lab=True)` và
`Lab.start()` là đường duy nhất để yêu cầu long-horizon research/experiment.
Không suy diễn quyền tự trị từ `browser=True`, tên model, số agent hay độ dài
output.

Event stream hướng người dùng/operator phải phân biệt rõ:

- `PLANNED`, `RESEARCHING`, `EXPERIMENTING`, `REVIEWING`, `COMPLETED`,
  `BLOCKED`, `ABORTED`;
- action đang chờ admission, đang chạy, settled, bị từ chối hoặc có side effect
  không xác định;
- budget đã reserve/consume/return và phần còn lại;
- source/claim/hypothesis/experiment/observation nào đã có witness;
- blocker ID, owner, điều kiện đóng và residual risk.

Pause/resume/cancel phải idempotent. Cancellation ghi admission và settlement
trước khi abort khi có thể; nếu process/provider không hợp tác, recovery phải
đóng prefix thành `UNKNOWN_SIDE_EFFECT` và chặn `COMPLETED`. Telemetry chỉ
quan sát, không mutate reducer và không chứa secret/raw provider content.

### 19.4 Research plane: search-as-code và browser liên tục

`SearchProgram` là chương trình dữ liệu có schema/version/hash, gồm operations
query, fetch, normalize, extract, deduplicate, source-quality, freshness và
contradiction. Executor không nhận callable tùy ý trên Lab path. Mỗi source
phải giữ URL canonical, retrieval time, exact span/selector nếu có, content
hash, provider/response metadata, license/robots decision và epistemic class.

BrowserCell tách actor và observer:

- actor chỉ nhận typed actions đã admission; observer chỉ đọc URL,
  accessibility tree, network log và bounded page state;
- session lifecycle, lease, quota, cleanup và recovery thuộc cell;
- initial URL và mọi redirect/intercepted route phải qua HTTPS/allowlist,
  hostname-resolution và private/reserved-address policy;
- page/PDF/network text luôn là untrusted data; marker prompt-injection chỉ là
  security signal, không phải classifier hoàn chỉnh;
- DNS rebinding, kernel containment và descendant kill chỉ được claim khi có
  platform witness tương ứng.

Knowledge synthesis phải xây graph `Source → Claim → Hypothesis → Experiment →
Observation → Evidence`. Claim có thể `SUPPORT`, `CONTRADICT`, `INCONCLUSIVE`
hoặc `UNVERIFIED`; không được mutate source thành proof. Khi nguồn xung đột,
controller phải giữ cả hai nhánh, nêu điều kiện khác nhau và chọn falsifier,
không bỏ nguồn bất tiện.

### 19.5 Experiment/physics plane

Simulation cell nhận `SimulationSpec` có model hash, state/input schema, đơn vị,
solver, timestep, stopping rule, seed, tolerances, expected invariants và
uncertainty model. Tối thiểu:

1. dimensional analysis và unit conversion trước tích phân;
2. Euler/RK4 hoặc solver tương ứng chỉ trong miền ổn định đã ghi;
3. residual, convergence/discrepancy và conservation checks;
4. seed/replay determinism và independent clean rerun;
5. sensitivity/uncertainty propagation, không chỉ một số point estimate;
6. status tách `SIMULATED`, `MEASURED_INPUTS_ONLY`, `CALIBRATED` và
   `INDEPENDENTLY_REPLICATED`.

Điện áp, dòng, công suất, năng lượng và thời gian phải có đơn vị, sampling
rate, instrument/model identity và sai số. Công thức kiểu
`E = ∫ V(t)I(t)dt` chỉ là phép tính trên input đã đo hoặc mô phỏng; nó không
chứng minh hardware energy nếu thiếu calibration witness. Solver PDE/stiff,
circuit model, sensor bias và hardware safety là các capability/gate riêng.

### 19.6 Adaptive reasoning, phản biện và kiểm soát bias

Mỗi bước controller chọn action từ tập feasible sau khi lọc hard constraints.
Score nội bộ chỉ là heuristic để xếp thứ tự, không là truth score. Một action
research/experiment đáng kể phải có:

- leading hypothesis và ít nhất một rival có thể bác bỏ;
- criterion, expected observation schema và stop rule;
- cost/budget/side-effect class và reversibility;
- falsifier cụ thể trước khi chạy;
- clean replication hoặc lý do vì sao không thể replication;
- reviewer/validator độc lập với actor khi claim quan trọng.

Không dùng consensus giữa agent, self-Elo, confidence tự báo, số lượng nguồn,
độ mới hoặc độ dài output làm proof. Nếu không đủ evidence, kết quả phải là
`INCONCLUSIVE`, `BLOCKED` hoặc `UNVERIFIED`; dossier phải ghi “không biết” và
đường closure tiếp theo.

### 19.7 Benchmark protocol đích

Mọi benchmark quan trọng phải đóng gói:

```text
protocol_hash, task_set_hash, environment_hash, model/provider identity,
tool/version hashes, resource limits, raw trials (including failures),
metric definitions/units, validator hash/version, seed policy,
contamination policy, run count, dispersion/outliers, limitations
```

Track tối thiểu gồm development, held-out và hidden/external. Validator không
được nằm trong cùng quyền kiểm soát với candidate khi claim release; subprocess
local chỉ là isolation cục bộ. Không báo “improved”, “SOTA”, “generalizes” hay
“faster” nếu chưa có comparator, cùng workload/environment, nhiều run và
independent reproduction. Primary metrics là task success, regression,
security/safety violation, data-loss/irreversible failure và review severity;
LOC/token/time chỉ là secondary metrics sau khi quality không giảm.

### 19.8 Migration graph — thứ tự bắt buộc

Migration phải additive/expand → dual-read hoặc dual-write có kiểm soát →
cutover → contract. Mỗi node có entry evidence, change budget, exit gate và
rollback; không nhảy node khi upstream còn `UNKNOWN` ở contract liên quan.

```text
M0 Freeze + manifest provenance
 ├── M1 Package/CLI ownership
 ├── M2 Canonical reducer + projection contract
 │    ├── M3 Trust policy unification
 │    ├── M4 Retry/effect-cell unification
 │    │    ├── M5 Research/browser/experiment migration
 │    │    └── M6 FFI internal convergence
 │    └── M7 Replay/archive + recovery cutover
 └── M8 Benchmark/ops/release closure
```

#### M0 — Freeze và provenance

**Entry:** closure epoch ổn định, dirty worktree được owner xác nhận, không có
process audit còn chạy.

**Thực hiện:** tạo final commit/manifest policy; regenerate `current.json`,
generated status, evidence pack và design-closure artifacts từ cùng SHA; ký
subject hash khi release.

**Exit:** evidence gate pass trên final SHA; mọi artifact có `HEAD`, epoch,
method, limitation; rollback record trỏ đúng artifact. Nếu không, chỉ được giữ
`PARTIAL_LOCAL`.

**Rollback:** bỏ commit tài liệu/manifest, không sửa status thủ công. Owner
phải tái tạo artifact từ checkout mới.

#### M1 — Package và CLI ownership

**Entry:** ADR-006 và D-001 được owner chấp thuận; compatibility window được
ghi.

**Thực hiện tối thiểu:** root giữ `aegis`; core bridge cài độc lập được nhưng
không publish command trùng tên. Trong compatibility window có thể cung cấp
`aegis-core` hoặc direct-file legacy command với deprecation notice; không đổi
import path âm thầm. Root wheel phải chứa native module, core bridge và RECORD
đúng source → wheel → installed import.

**Exit gate:** root clean install, core clean install, combined install chỉ có
một owner `aegis`; compatibility tests cho direct bridge; no repository on
`sys.path`; wheel contents/entry points/RECORD hash verified.

**Rollback:** giữ root wheel cũ và bridge package cũ trong compatibility window;
không phát hành artifact có hai `aegis` owner.

#### M2 — Canonical reducer và projection contract

**Entry:** M0 pass hoặc local-only branch có manifest bất biến; Rust
`LabController` API/version đã có contract test.

**Thực hiện:** chuyển `LabRun` writes sang typed native admission theo từng
lane; projection payload lưu và restore được; mọi rejected admission không đổi
snapshot/hash/epoch; expose explicit `AuthorityMode` để DEV test không bị nhầm
với PROD.

**Exit gate:** fresh-process replay cùng prefix/verdict; duplicate/stale
settlement, cancellation, crash-prefix, budget conservation, registry seal và
projection tamper đều fail-closed; không còn đường Python tự append typed
side-effect event.

**Rollback:** dual-read từ native snapshot nhưng giữ projection cũ chỉ ở DEV;
PROD không downgrade silently. Nếu mismatch, dừng ở `BLOCKED` và lưu cả hai
hash để điều tra.

#### M3 — Trust policy unification

**Entry:** M2 event/mission contract ổn định.

**Thực hiện:** tạo policy một lần ở mission boundary; truyền `policy_hash` tới
provider/gateway và bind nó vào native mission `contract_hash`; evidence
normalizer không tự default khác controller trên compatibility paths. DEV/STAGING/
PROD là explicit context với capability matrix; PROD thiếu native authority thì
fail closed. Lab facade ghi đè `lab_trust_policy_hash` từ `LabPolicy`, gateway
phải nhận/giữ hash trong native-required mode, và AegisAdapter từ chối hash sai.
Legacy direct `LabRun` snapshots không có hash tiếp tục dùng contract hash cũ
để không phá khả năng khôi phục; chúng không được coi là policy-bound proof.

**Exit gate:** constructor matrix chứng minh từng entrypoint, core bridge và
Lab hash bằng nhau cho cả ba trust level, Rust nhận đúng mission-bound hash,
native-required gateway không thể bỏ qua hash, no silent DEV fallback,
compatibility behavior được gắn nhãn và không thể tạo release verification.

**Rollback:** giữ old wrapper chỉ cho compatibility result không-verifiable;
không rollback policy trong cùng một persisted run.

#### M4 — Retry và effect-cell unification

**Entry:** M2 authority + M3 policy pass.

**Thực hiện:** retry thuộc cell; route selection không tự nhân effect; SDK
retries phải được tắt hoặc khai báo trong cell policy; mỗi attempt có identity,
budget reservation, deadline, idempotency và settlement. Application/adapter
không retry lại một admitted effect.

**Exit gate:** validator tính được finite `N_external_max` cho từng policy;
timeout ambiguity, duplicate receipt, cancellation và partial effect đều có
terminal outcome; layered retry multiplication test pass.

**Rollback:** compatibility one-shot path giữ retry cũ nhưng bị gắn
`NON_LAB_EFFECT`; không cho nó ghi evidence `VERIFIED`.

**M4 local guard continuation (2026-09-01):** native-required Lab now rejects
a gateway instance that exposes an adapter-owned multi-attempt loop
(`max_retries > 1`) before the first provider call. A one-attempt adapter
remains compatible with the Lab-owned retry fence; opaque SDK-internal retries,
user-runner retries and external idempotency remain explicitly unverified. The
regression `test_native_required_gateway_rejects_adapter_owned_retry_loop` and
the complete Python gate pass. This closes one locally observable retry
amplification path only; M4 remains `OPEN_LOCAL` until the cell contract can
prove a finite global external-attempt bound.

**M4 deadline/idempotency continuation (2026-09-01):** each Lab-owned gateway,
explicit generic tool, experiment and simulation attempt now validates one
finite positive deadline (`gateway_timeout_seconds`, `tool_timeout_seconds`,
`experiment_timeout_seconds` or `simulation_timeout_seconds`), derives a
deterministic BLAKE2b-256 idempotency key from mission/execution/input/policy
identity, and carries both fields through admission and settlement receipts.
`asyncio.wait_for` converts an uncompleted cooperative local call into one
`TIMED_OUT` settlement; duplicate/mismatched deadline or idempotency metadata
is rejected by the generic settlement seam. Retry regressions check distinct
keys and deadline propagation, while
`test_lab_gateway_deadline_fence_settles_timeout`,
`test_lab_generic_tool_deadline_fence_settles_timeout`,
`test_lab_experiment_deadline_fence_settles_timeout` and
`test_lab_simulation_deadline_fence_settles_timeout` prove bounded local
timeout settlement. The focused M4 selector passes **11 tests**; the full
Python gate is **255 passed**, Ruff and Pyright are clean,
Rust `cargo test --workspace --no-default-features --quiet -- --test-threads=1`
passes 439 unit tests plus 2 integration tests, and the replacement wheel/import probe is recorded in
`artifacts/local-runtime/provider-fence-settlement-20260901/provider_fence_settlement_packaging.json`.
This is `LOCAL-PROVEN` for the Python/native local seam only; an external
effect may already have happened before an adapter timeout, and opaque SDK or
user-runner retries, provider idempotency, hosted single-writer authority and
finite global `N_external_max` remain NOT VERIFIED, so M4 stays `OPEN_LOCAL`.

**M4 strict retry-policy continuation (2026-09-02):** gateway, generic-tool,
experiment and simulation retry counts now reject booleans, floats, numeric
strings, zero and negative values instead of silently coercing them through
`int(...)`; accepted integer counts are deterministically capped at the
mission `max_steps` quota. `AdaptiveController` also rejects non-integer
budget bounds. Regression coverage exercises malformed values and the finite
cap, while the existing per-lane retry/timeout tests remain green. This makes
the local retry contract precise but does not establish provider idempotency,
opaque SDK/user-runner retry absence, external-effect reversal, or a hosted
global `N_external_max`; M4 remains `OPEN_LOCAL`.

**M4 compatibility-adapter retry metadata continuation (2026-09-03):**
`core/python.aegis_adapter.AegisAgent` now rejects boolean, numeric-string,
floating-point, zero and negative `max_retries` values before constructing the
adapter. The compatibility default remains three retries, while native Lab
authority still rejects adapter-owned retry loops above one attempt. The
focused compatibility cases and the complete **91-test** `core/python/tests.py`
suite pass with deprecation warnings treated as errors. This prevents lossy
retry-policy metadata from entering the compatibility adapter; it does not
prove that an underlying SDK or user runner performs no hidden retries,
provide provider idempotency, reverse an already-triggered external effect, or
close the finite global-attempt gate, so M4 remains `OPEN_LOCAL`.

**P3 experiment-contract continuation (2026-09-02):** `ExperimentSpec` now
validates identity strings, non-empty controls/variables, unique integer
preregistered seeds, strict boolean uncertainty policy and integer observation
quotas at admission and snapshot restore. Controller coercion no longer turns
numeric strings or booleans into scientific parameters silently. Regression
coverage proves duplicate/lossy seed metadata is rejected. This hardens the
local preregistration boundary only; solver convergence, calibration, sensor
uncertainty and independent physical replication remain `OPEN_EXTERNAL` under
`LAB-PHYS-004`.

**P3 observation-contract continuation (2026-09-02):** `ObservationRecord`
now rejects lossy seed/measurement/boolean/uncertainty metadata at live
admission and snapshot restore, and runner ingestion preserves raw values so
invalid types cannot be normalized into apparently valid measurements.
Regression coverage exercises each rejected field. This improves local
epistemic integrity and replay safety; it is not calibration evidence and does
not close `LAB-PHYS-004`.

**P3 numerical-contract continuation (2026-09-02):** `SimulationSpec`,
`PhysicalConstraint` and `ElectricalSignalSpec` now reject lossy boolean/string
numeric metadata, duplicate/non-integer seeds, invalid digest subjects and
non-integral step/sample quotas. Mapping coercion preserves raw values until
these validators run. Regression coverage confirms malformed numerical
contracts fail closed. This is bounded input/invariant evidence only; it does
not prove solver convergence, hardware calibration or physical validity.

**P3 runtime-output continuation (2026-09-02):** `SimulationCell` now rejects
lossy runner observations, convergence evidence, constraint residuals and
epistemic labels before calculation; ODE integration validates exact numeric
state/derivative/invariant values, bounded integer steps and method metadata;
calibration pairs and electrical V/I/reference samples reject string,
boolean, non-finite or untyped inputs before arithmetic. Undeclared residual
maps are rejected instead of being silently ignored. Negative coverage now
totals **266 focused Lab tests** and **372 combined Python/cross-language
tests**. This closes local numerical ingestion and calculation boundaries only;
validated solver convergence, sensor calibration, hardware energy witnesses
and independent physical replication remain `OPEN_EXTERNAL` under
`LAB-PHYS-004`.

**P1 policy-boundary continuation (2026-09-02):** `LabRun`,
`LabMissionSpec`, `LabPolicy`, `LabBudget` and `BrowserCellPolicy` now reject
lossy task, scope, host, quota, policy-flag and budget metadata (including
booleans, floats and non-string entries) at construction/validation rather
than relying on implicit coercion. Regression coverage exercises malformed
policy and budget fields, and the strict checks preserve the
mission/controller ownership boundary. This is local input-contract evidence
only; OS/process containment, hosted single-writer authority and live browser
security remain open under `LAB-AUTH-001`/`LAB-BROWSER-002`.

**M2 execution-cell boundary continuation (2026-09-02):**
`ProcessExecutionCell`, `ExecutionCellBinding` and `ExecutionCellRegistry` now
reject lossy timeout, start-method, identity, capability, effect, trust-level
and policy-hash metadata before a runner is admitted or resolved. Invalid
container/entry types fail with typed errors instead of reaching `.strip()` or
numeric coercion paths; regression coverage includes process-cell and registry
metadata. This hardens the local registry boundary but does not prove
descendant cleanup, OS resource enforcement or hosted single-writer authority.

**M5 research-contract continuation (2026-09-02):** `SearchOperation`,
`SearchProgram` and `SearchProgramExecutor` now reject non-string operation
arguments, untyped operations, invalid allowlist entries and lossy quota,
freshness, timeout or byte-limit metadata instead of normalizing through
`str()`/numeric coercion. Regression coverage confirms malformed search
programs fail before provider or fetch execution. This closes a local
search-as-code input boundary only; live-provider freshness, semantic quality
and contradiction precision/recall remain `OPEN_EXTERNAL` under
`LAB-RESEARCH-003`.

**M2 snapshot-contract continuation (2026-09-02):** `LabRun.from_payload`
now rejects lossy top-level identity, scope, trust, budget, epoch, collection
and record metadata before native/replay validation; `SourceRecord`,
`ClaimRecord` and `HypothesisRecord` validate exact field/container types at
live admission and restore. Regression coverage mutates each boundary with
string/boolean/numeric substitutions and confirms fail-closed restoration.
This strengthens local replay integrity; it does not replace fresh-process,
cross-platform or hosted crash-prefix evidence.

**M2/P1 application-boundary continuation (2026-09-02):** controller-structured
claims, hypotheses and experiments, search-provider source records, mission
scope/options, browser policy maps and restore metadata now preserve raw values
until strict validation; lossy `str()`/`int()`/`bool()` substitutions are
rejected before they can become apparently valid evidence, policy or scientific
parameters. Typed controller action plans, skill/tool requests and timeout/
iteration/search quotas follow the same fail-closed rule. Event, blocker,
security, tool-admission and skill-admission snapshot fields are also
type-checked before replay semantics run. Focused coverage is **215 passed**
and the cross-language Python gate is **321 passed**. This closes locally
observable coercion paths only; hidden adapter effects, process descendants,
provider/SDK retry behavior and hosted single-writer authority remain
`OPEN_LOCAL`/`NOT VERIFIED` under `LAB-AUTH-001`.

**P5 benchmark-contract continuation (2026-09-02):** `BenchmarkProtocolV2`,
`EnvironmentFingerprint` and `BenchmarkTrialRecord` now require exact field
types, finite numeric values, uppercase trial statuses, integer seed/identity
metadata and explicit contamination/environment contracts. The evaluator and
isolated validator boundary reject malformed options, argv containers,
timeouts, output limits and validator verdicts without coercing strings or
booleans into accepted measurements; `LabApplication` preserves raw benchmark
trials, environment and validator metadata until this validation runs. Numeric
strings remain retained `ERROR` trials rather than becoming successful values.
Negative coverage now totals **230 focused Lab tests** and **336 combined
Python/cross-language tests**; targeted Ruff and Pyright are clean. This is
local benchmark-contract evidence only: hidden scorer secrecy, contamination
resistance, provider-independent reproduction and hosted evidence remain
`OPEN_EXTERNAL` under `LAB-BENCH-005` and `LAB-RELEASE-006`.

**M2 archive/registry-snapshot continuation (2026-09-02):** replay snapshots
now validate archive manifest hashes (native byte-array or canonical hex form),
mission/run identity, snapshot path and persisted snapshot hash before restore.
Execution-cell manifests are checked for canonical IDs, supported action kinds,
typed capability/effect/trust sequences, policy-hash subjects and duplicate
identity/action entries; malformed or lossy metadata cannot reach replay
verification. Recovery also requires an exact persisted snapshot hash instead
of coercing arbitrary values through `str()`. Negative coverage now totals
**235 focused Lab tests** and **341 combined Python/cross-language tests**.
This strengthens local snapshot integrity only; process-descendant containment,
cross-platform crash-prefix injection and hosted restore/single-writer evidence
remain open under `LAB-AUTH-001`, `LAB-OPS-007` and `LAB-RELEASE-006`.

**M5 provider-boundary continuation (2026-09-02):** `SearchProgramExecutor`
now fails closed on untyped provider entries, ambiguous `results`/`candidates`
aliases, non-canonical URI/content/source aliases, mismatched content digests,
non-digest snapshot hashes, lossy retrieval/trust/score metadata, malformed
citation spans/digests and invalid provenance clusters. Render, extract, dedupe, rank,
freshness, redirect and admission paths no longer stringify or numerically
coerce provider-controlled values; malformed records are rejected rather than
silently skipped. New negative coverage brings the focused Lab suite to
**250 passed** and the combined Python/cross-language gate to **356 passed**;
targeted Ruff and Pyright remain clean. This proves a stricter local provider
contract and replay-ready source metadata, but not live-provider semantic
quality, freshness calibration, contradiction precision/recall or DNS-race/
hosted containment; `LAB-RESEARCH-003`, `LAB-BROWSER-002` and the residual
authority rows remain open.

**M5 compatibility-ingestion continuation (2026-09-02):** the compatibility
`_run_search_program` and `LabApplication._ingest_search_candidates` paths now
reject untyped bytes/objects, ambiguous URI/content/source aliases, invalid
content or snapshot digests, lossy timestamps/trust/identity metadata and
malformed citation spans instead of silently dropping or coercing records.
Typed mapping/string inputs remain supported, while every rejected candidate
leaves a blocker and security-visible reason. Negative coverage now totals
**255 focused Lab tests** and **361 combined Python/cross-language tests**.
This closes a local compatibility-ingestion bypass only; arbitrary adapter
side effects, process containment and hosted single-writer authority remain
open under `LAB-AUTH-001`.

**P2 browser-action boundary continuation (2026-09-02):** `BrowserCellPolicy`,
`BrowserObserverView`, `BrowserCell` and the typed browser action executor now
reject non-canonical host/URL projections, untyped action kinds, selector/value
coercions, boolean/string wait durations, non-list network logs and invalid
recovery counts. Observer actions remain read-only and actor actions retain
the explicit legacy callable compatibility boundary; Lab controller paths
continue to admit only typed mappings. Negative coverage now totals **273
focused Lab tests** and **379 combined Python/cross-language tests**. This is
local browser contract evidence only; DNS rebinding, browser-process/OS
containment, crash injection and hosted cross-platform continuity remain
`OPEN_EXTERNAL` under `LAB-BROWSER-002` and `LAB-AUTH-001`.

**P2 reducer-receipt continuation (2026-09-02):** `LabRun` browser
actor/observer admission and settlement receipts now enforce exact action,
policy, identity, lease/count, status and optional digest types before event
append; supplied empty or malformed hashes no longer fall back to recomputed
values, and an observation identifier cannot be silently discarded when an
admission is absent. Browser kind/status vocabularies are centralized and the
policy is revalidated at the reducer boundary. Negative coverage now totals
**274 focused Lab tests** and **380 combined Python/cross-language tests**;
targeted Ruff and Pyright remain clean. This closes a local reducer coercion
gap only; arbitrary adapter effects, DNS rebinding, OS/process containment,
hosted single-writer authority and cross-platform evidence remain open under
`LAB-AUTH-001`/`LAB-BROWSER-002`.

**P1 generic-tool receipt continuation (2026-09-02):** `LabRun` generic tool
admission and settlement now reject untyped tool/effect/role/schema/stop-rule
metadata, boolean leases/attempts, non-string identities/statuses and
non-string optional digests before normalization. Optional execution IDs no
longer use truthiness to discard invalid values; supplied empty digests remain
invalid instead of triggering recomputation, while timeout finiteness and
admission binding stay enforced. Negative coverage now totals **275 focused
Lab tests** and **381 combined Python/cross-language tests**; targeted Ruff and
Pyright remain clean. This strengthens the local generic-tool fence only;
hidden planner/lease ownership, arbitrary adapter effects, process-level
interruption and hosted multi-process single-writer evidence remain open under
`LAB-AUTH-001`.

**P3 experiment-receipt continuation (2026-09-02):** experiment execution
admission and settlement now enforce exact experiment/identity/status/count
metadata and optional digest types before lookup or normalization. Optional
execution IDs preserve explicit invalid values for rejection, and supplied
empty input/policy digests no longer trigger recomputation; timeout and
idempotency binding remain finite and replay-checked. Negative coverage now
totals **276 focused Lab tests** and **382 combined Python/cross-language
tests**; targeted Ruff and Pyright remain clean. This strengthens the local
experiment authority fence only; scientific solver validation, hidden adapter
effects, process interruption and hosted writer evidence remain open under
`LAB-PHYS-004`/`LAB-AUTH-001`.

**M5 research-program receipt continuation (2026-09-02):** research-program
admission and settlement now require exact digest/count/provider/status and
optional admission/hash metadata before `_is_digest`, lookup or normalization;
boolean operation/candidate counts and empty supplied digests fail closed
instead of being treated as valid or recomputed. Replay admission binding and
candidate-result hashes are unchanged. Negative coverage now totals **277
focused Lab tests** and **383 combined Python/cross-language tests**; targeted
Ruff and Pyright remain clean. This closes a local research receipt coercion
gap only; live-provider semantic/freshness evidence, provider-side effects
and hosted authority remain open under `LAB-RESEARCH-003`/`LAB-AUTH-001`.

**M2 cancellation-receipt continuation (2026-09-02):** cancellation admission
and settlement now reject non-string reasons, request/admission identities and
statuses before terminal-state short-circuit or normalization. Explicit invalid
request IDs are no longer discarded by truthiness, while the existing
replay-visible cancellation and abort ordering is unchanged. Negative coverage
now totals **278 focused Lab tests** and **384 combined Python/cross-language
tests**; targeted Ruff and Pyright remain clean. This is local cancellation
contract evidence only; process-level interruption, hidden side effects and
hosted single-writer authority remain open under `LAB-AUTH-001`.

**M2 admission-binder continuation (2026-09-02):** the shared
`_require_open_admission` and `_assert_admission_identity_available` paths now
reject malformed event payload containers, non-string keys/identities and
invalid stored admission IDs instead of stringifying or skipping them. A
malformed replay prefix therefore fails closed before it can match a legitimate
identity; valid event-chain and settlement behavior is unchanged. Negative
coverage now totals **279 focused Lab tests** and **385 combined
Python/cross-language tests**; targeted Ruff and Pyright remain clean. This is
local replay-binder evidence only; native authority outside the projection,
process-level interruption and hosted multi-process writer proof remain open
under `LAB-AUTH-001`.

**M2 skill-admission continuation (2026-09-02):** `SkillManifest`,
`SkillExecutionReceipt` and `SkillRegistry.admit` now reject lossy manifest,
capability, precondition, mission, epoch and receipt metadata before
normalization; numeric capabilities are no longer stringified and truthy
preconditions are no longer converted into booleans. Registry identity and
validator receipt fields are type-checked while existing capability,
precondition, hash and replay proofs remain enforced. Negative coverage now
totals **280 focused Lab tests** and **386 combined Python/cross-language
tests**; targeted Ruff and Pyright remain clean. This strengthens the local
skill fence only; external validator/adapter effects, process interruption and
hosted authority remain open under `LAB-AUTH-001`.

**M2 recovery-admission continuation (2026-09-02):** event-ledger recovery
enumeration now fails closed on malformed execution payloads, non-string keys,
identities or statuses instead of silently skipping or stringifying them;
reconciliation operator/reason fields are type-checked before hashing or
mutation. Open-admission recovery therefore cannot accidentally treat a
malformed prefix as having no work to reconcile. Negative coverage now totals
**281 focused Lab tests** and **387 combined Python/cross-language tests**;
targeted Ruff and Pyright remain clean. This strengthens local crash-prefix
accounting only; non-cooperative process effects and hosted writer/restore
evidence remain open under `LAB-AUTH-001`/`LAB-OPS-007`.

**M2 lossless recovery-field continuation (2026-09-02):** the recovery
boundary now validates the complete typed admission payload for every explicit
execution lane (generic tool, experiment, research program, browser actor,
browser observer and skill) before reading fields. Legacy tool recovery uses
the same validator, and reconciliation no longer applies `str()`/`int()` to
durable metadata; malformed numeric strings, floats, booleans, identities,
digests, roles, kinds or skill-hash collections are rejected before any
settlement or state transition. Seven focused regressions cover the lane
matrix and legacy helper; the Python 3.14 suite passes **363 tests**, with
targeted Ruff and Pyright clean. This closes a locally reproducible lossy
recovery path only; process-level interruption, hidden adapter effects,
external-effect reversal and hosted writer/restore authority remain
`OPEN_LOCAL`/`OPEN_EXTERNAL` under `LAB-AUTH-001` and `LAB-OPS-007`.

**M2 skill settlement continuation (2026-09-02):** explicit `skill_requests`
now validate a finite positive timeout policy and execute through
`asyncio.wait_for`. If an admitted skill fails or times out, the adapter emits
one hash-bound `SkillExecutionReceipt` with status `REJECTED`; cancellation
continues to settle as `CANCELLED` before propagating. Two regressions cover
cooperative timeout and executor failure, and the Python 3.14 suite passes
**365 tests** with targeted Ruff and Pyright clean. This closes only the local
skill admission/settlement gap; synchronous non-cooperative adapters,
validator isolation, hidden planners and hosted authority remain open under
`LAB-AUTH-001`.

**M3 native trust-subject continuation (2026-09-02):** direct `LabRun`
construction now derives and binds the canonical trust-policy subject whenever
the selected authority mode is `NATIVE_ADMITTED` or `NATIVE_REQUIRED`, even
when the public `Lab` facade is not used. Projection-only runs retain their
legacy optional field. Native-mode snapshot restoration likewise derives the
subject for legacy snapshots that omitted it, so the native mission contract
cannot silently remain unbound. A regression covers direct construction,
projection compatibility and legacy native snapshot restoration; the Python
3.14 suite passes **366 tests**, with targeted Ruff and Pyright clean. This
closes one local unbound-native-policy path only; DEV/PROD compatibility
defaults, Rust ownership, cross-cell receipt propagation and hosted policy
authority remain open under `LAB-AUTH-001`/`LAB-RELEASE-006`.

**M3 native gateway propagation continuation (2026-09-02):** a direct
`LabApplication` native-required gateway now takes the active run's canonical
trust-policy subject when compatibility options omit it; before a run exists it
derives the same subject from the validated config trust level. The gateway
factory handshake and returned-instance check therefore cannot silently create
an unbound native adapter. Focused regressions cover active-run propagation
and mutation rejection; full local regression passes **368 tests**. This closes only the local gateway
construction binding gap; adapter/provider receipt authority, cross-cell policy
ownership and hosted release evidence remain open under
`LAB-AUTH-001`/`LAB-RELEASE-006`.

**Final local verification continuation (2026-09-02):** after the native
gateway integrity change, the combined local command
`python -m pytest tests core/python/tests.py -q -W error::DeprecationWarning`
passes **453 tests**. The project-pinned Ruff **0.16.3** lint check passes for
the complete `aegis_cognition` package, and Pyright **1.1.411** reports zero
errors, warnings or informations; `cargo fmt --all -- --check` also passes.
Ruff format remains a separate `NOT VERIFIED` gate because four pre-existing
files would require broad mechanical reformatting unrelated to this semantic
slice. This evidence is local and source-bound; hosted CI, signed release
attestation and external platform/physical witnesses remain open.

The native workspace was also rechecked with
`cargo test --workspace --no-default-features --quiet -- --test-threads=1`:
the 441-test primary target and every emitted integration/doctest target
completed with zero failures. This is a local Windows/native result only; it
does not replace hosted multi-platform, fuzz/sanitizer, release-attestation or
external authority evidence.

**M2 operator-evidence continuation (2026-09-02):** security-event and
blocker/resolution APIs now require exact reason/detail strings and reject
non-empty artifact hashes that are not canonical digests before event append.
This prevents malformed operator metadata from entering the auditable reducer
or being silently normalized. Negative coverage now totals **282 focused Lab
tests** and **388 combined Python/cross-language tests**; targeted Ruff and
Pyright remain clean. This strengthens local forensics metadata only; hosted
telemetry, process interruption and external authority remain open under
`LAB-AUTH-001`/`LAB-OPS-007`.

**M2/M3 provider-receipt and registry continuation (2026-09-02):** the
provider-attempt callback now rejects non-mapping payloads, non-string keys,
lossy phase/call/provider/candidate/deadline/idempotency/task metadata and
malformed settlement fences before any nested receipt is appended. Native
route reconciliation now requires a canonical route digest, typed provider
and throttled-provider sequences, a typed fallback flag, and validates any
present schema/trust/count/budget fields; native gateway results likewise
must carry exact provider/trust identities and canonical budget evidence.
The context-retrieval dispatcher also resolves an explicitly registered
execution cell even when no legacy compatibility callback is present, so a
sealed registry cannot be bypassed by an early optional-return path.
Negative coverage now totals **288 focused Lab tests** and **394 combined
Python/cross-language tests**; targeted Ruff and Pyright are clean. This is
local adapter/registry contract evidence only: provider idempotency beyond
the callback, opaque SDK/user-runner retries, external-effect reversal,
process containment and hosted single-writer authority remain open under
`LAB-AUTH-001`/`LAB-RESEARCH-003`.

**P1/M2 application compatibility continuation (2026-09-02):** the legacy
RAG callback now validates `top_k` as a positive integer before constructing
the learning manager, so a numeric string cannot bypass the context-cell
policy. Completion indexing validates the gateway hot-commit artifact as a
lowercase canonical digest before deriving the native session identity or
opening the persistence manager. Negative coverage now totals **292 focused
Lab tests** and **398 combined Python/cross-language tests**; targeted Ruff
and Pyright remain clean. This closes two locally observable compatibility
coercions only; live memory-provider semantics, external write idempotency,
process interruption and hosted authority remain open under
`LAB-AUTH-001`/`LAB-RESEARCH-003`.

**M2 replay-writer boundary continuation (2026-09-02):** `ReplayWriterLease`
now rejects non-text/non-path-like directory metadata and byte paths instead
of converting arbitrary values to a filesystem name; `LabApplication` passes
the validated path through without a `str()` fallback. Regression coverage
confirms invalid directory inputs fail before directory creation. Negative
coverage now totals **293 focused Lab tests** and **399 combined
Python/cross-language tests**; targeted Ruff and Pyright remain clean. This
hardens local replay-writer input integrity only; stale-process recovery,
descendant cleanup, cross-platform enforcement and hosted writer evidence
remain open under `LAB-AUTH-001`/`LAB-OPS-007`.

**P1 capability-flag continuation (2026-09-02):** the public `Lab.start`
boundary now rejects non-boolean `browser` options rather than treating
strings such as `"false"` as enabled capability. Regression coverage proves
the rejection occurs before `AgentConfig` construction or browser setup.
Negative coverage now totals **294 focused Lab tests** and **400 combined
Python/cross-language tests**; targeted Ruff and Pyright remain clean. This
is local option-integrity evidence only; browser process/OS containment,
DNS-race protection and hosted policy enforcement remain external blockers.

**M2/M3 native provider-option continuation (2026-09-02):** native-required
gateway construction now rejects provider names, fallback pair names,
`required_tokens`, model-derived provider identities, and provider-budget
fields when their types or canonical forms would otherwise be rewritten by the
compatibility adapter. It also rejects malformed budget containers and
inconsistent pre-normalized budget records before factory construction, so
quota admission cannot silently change between the caller's policy and the
provider-attempt fence. The compatibility `Agent` path remains unchanged.
Negative coverage now totals **301 focused Lab tests** and **407 combined
Python/cross-language tests**; targeted Ruff and Pyright remain clean. This is
local native-input integrity evidence only; opaque adapter retries,
provider-side idempotency/quota truth, process containment, external effect
reversal and hosted single-writer authority remain open under
`LAB-AUTH-001`/`LAB-RESEARCH-003`.

**M2 execution-cell identity continuation (2026-09-02):** sealed registry
lookup now rejects empty or whitespace-padded `cell_id` values instead of
treating them as an omitted identity. This removes a local fallback ambiguity
in controller-selected action dispatch while preserving the existing
operator-injected binding model. Focused coverage remains **301 Lab tests** and
the combined Python/cross-language gate remains **407 tests**; Ruff and Pyright
remain clean. This closes only the local cell-identity normalization gap;
planner-owned leases, opaque adapter effects, process descendants and hosted
single-writer authority remain open under `LAB-AUTH-001`.

**P0 provenance materialization continuation (2026-09-02):** the evidence
consistency generator was run against the current checkout and its temporary
manifest passed with commit `3a26c809e1114b7fcf58cfd291bf01b9f013418c`.
This proves only that a non-self-referential local manifest can bind the
current SHA and preserve registry parity; the tracked template intentionally
still contains `CHECKOUT_HEAD`, and retained suite artifacts, hosted run IDs
and signed attestation are absent. `LAB-RELEASE-006` therefore remains
`BLOCKED_EXTERNAL`; the temporary manifest is not release evidence and was not
added to the repository.

**M2 projection/event binding continuation (2026-09-02):** mutable typed
scientific records (`SourceRecord`, claims, hypotheses, experiments and
observations), execution admissions/settlements, blockers and security-event
projections are now compared with the immutable events that produced them
before dossier/snapshot export and after snapshot restore. The comparison
removes only the policy hash injected at the event boundary, binds a skill
admission's `admission_event_hash` to the actual event hash, and rejects
identity-set drift or same-ID metadata replacement. A tampered source record,
tool effect class, settlement identity set or operator blocker can therefore no
longer retain a valid event hash-chain while changing the exported projection;
the live export path fails closed as well. Focused coverage is **302 Lab tests**
and the combined Python/cross-language gate is **408 tests**; targeted Ruff and
Pyright are clean. This closes local projection-integrity gaps only; hidden
adapter effects, process containment, rollback of already-executed effects and
hosted single-writer authority remain open under `LAB-AUTH-001`.

**P1 authority/entrypoint type continuation (2026-09-02):** direct `LabRun`
construction and the compatibility `Agent`/`AgentConfig` entrypoints now reject
non-boolean `require_native_authority`, `lab` and `browser` values instead of
letting `bool(...)` select a different authority or execution path. The
option-derived authority mode applies the same exact-boolean rule when no
explicit mode is supplied. This prevents values such as `0`, `1` or
`"false"` from silently changing native-required versus projection-only
behavior. Focused coverage is **307 Lab tests** and the combined
Python/cross-language gate is **413 tests**; targeted Ruff and Pyright are
clean. This closes local option-boundary coercion only; native single-writer
coverage across opaque adapters, process descendants and hosted multi-process
execution remains open under `LAB-AUTH-001`.

**P1 archive/config boundary continuation (2026-09-02):** the replay archive
entrypoint now rejects non-string directories, boolean/floating/numeric-string
segment sizes and non-boolean native verifier results instead of allowing
`str(...)`, `int(...)` or `bool(...)` to rewrite the archive contract. The
post-completion persistence policy and trust-level override/configuration also
fail closed on non-boolean/non-string values; the compatibility replay-archive
flag is validated before default directory injection, and malformed policy is
recorded as a blocker before any compatibility effect is invoked. Native
event/transition, archive, and skill validators now reject non-boolean results
as well. Focused
coverage is **318 Lab tests** and the combined Python/cross-language gate is
**424 tests**;
targeted Ruff and Pyright remain clean. This is local boundary evidence only;
hosted authority, process containment, live provider semantics, and signed
release provenance remain external blockers.

**M7 snapshot/archive identity continuation (2026-09-02):** native replay now
exposes `aegis_lab_verify_archive_against_events`, which compares every
persisted snapshot `LabEvent` sequence, event hash and payload hash with the
sealed `LabEventRecorded` envelopes in the segmented archive. Archive write
and recovery call this verifier when the extension provides it, while older
extensions retain the existing manifest verifier as an explicit compatibility
fallback. A Rust regression proves that a tampered snapshot can recompute a
locally valid Lab hash chain but is rejected because its event identity no
longer matches the sealed archive. Python archive/recovery coverage confirms
the new verifier is called for both write and restore. This closes a local
snapshot-to-archive binding gap; it does not provide signatures, hosted writer
authority, cross-platform crash injection, or final-SHA release evidence.

**M2 execution-cell manifest binding continuation (2026-09-02):** registry
metadata is now emitted as exactly one `execution_cell_manifest_recorded`
projection record immediately after Lab construction and before any selected
cell executes. The record carries schema `aegis-execution-cell-manifest-v1`,
cell count, canonical inventory and a domain-separated projection digest; Rust
requires sequence 2/epoch 1 after `MissionCreated`, validates normalized cell
identity/action/capability/effect/trust fields and rechecks the digest on
snapshot restore. Python refuses to serialize or restore a manifest whose
top-level projection differs from that event. The local regression covers
round-trip, top-level tamper rejection, duplicate/invalid native records and
atomic rejection; the full native suite is **441 passed** and Python remains
**349 passed**. This closes the local registry-to-ledger binding gap, but it
does not prove universal adapter side-effect authority, hosted single-writer
ownership, process containment or external release provenance.

**M2 source-bound extension continuation (2026-09-02):** a fresh release
build from the pushed checkout (`2cd3f3b6c8590f05149ded8100dbcf6d92b609f2`)
was completed with CPython 3.14, `maturin 1.14.1` and `-j 1`. The resulting
wheel is retained at
`artifacts/local-runtime/m2-execution-cell-manifest-20260902/wheel/` with
SHA-256
`0bf16c02f7dc038211768d6973ac56807244d4515e9bebf750fa65f3de98e21c`; its
native member is
`aegis_cognition/aegis_nerve.cp314-win_amd64.pyd` (SHA-256
`28ce6eeded25ce6974f11a0c21309ba65b0d2b44b787200b269a8df46a537df4`). A
fresh process outside the checkout imported that member, exposed
`restore_snapshot_json` and the native verifier, and proved one manifest
event at sequence 2/epoch 1 plus snapshot restore and top-level tamper
rejection. The targeted native manifest/rollback selector passed **3 tests**;
the full `tests` plus `core/python/tests.py` gate through the new wheel passed
**434 tests** with no checkout path on `sys.path`. The retained witness is
`artifacts/local-runtime/m2-execution-cell-manifest-20260902/native_execution_cell_manifest.json`.
This is stronger local source-bound evidence than the prior stale-binary
probe, but the wheel intentionally reused the local dependency site-packages
without a dependency-complete clean install; cross-platform/hosted authority,
external effects and signed release provenance remain unverified.

**M2 cancellation-fence continuation (2026-09-02):** both compatibility
browser-gateway capture paths (controller action plans and explicitly supplied
browser actions) now invoke the shared `_call_fenced` boundary. A regression
adapter deliberately swallows `CancelledError`; the Lab still raises
`CancelledError` and records exactly one `browser_action_recorded` receipt with
`status=CANCELLED`, so an in-flight browser admission cannot be promoted to
success by a non-cooperative compatibility gateway. Full Python regression
passed **350 tests**; targeted Ruff and Pyright passed. This closes a local
adapter-cancellation bypass only; process/descendant interruption, DNS race,
external side effects, hosted single-writer authority and signed release
provenance remain `NOT VERIFIED`.

#### M5 — Research, browser và experiment cells

**Entry:** M2–M4 pass cho local cells; capability registry sealed.

**Thực hiện:** migrate search-as-code, BrowserCell actor/observer và simulation
cell vào registry; mọi source/observation/experiment receipt có parent hash,
lease và schema; browser session liên tục nhưng cleanup/recovery idempotent.

**Exit gate:** local deterministic corpus + hostile fixtures pass; live provider,
cross-platform OS containment, DNS race, hardware calibration và scientific
replication vẫn giữ external status cho đến khi có witness.

**Rollback:** tắt cell capability bằng policy/feature gate; không fallback sang
callable/direct sink trong PROD.

#### M6 — FFI internal convergence

**Entry:** M2 public DTO/FFI contract inventory đã được owner duyệt.

**Thực hiện:** tách nội bộ `ffi.rs` theo family (status/validation, DTO,
controller, compatibility/error) chỉ khi giữ nguyên symbol/schema hoặc có
versioned migration; thay `unwrap`/leak hazard theo error contract riêng.

**Exit gate:** public API compatibility, PyO3 round-trip, error class, lifetime
bound và FFI benchmark không regression; unused suspect exports không bị xóa
cho đến khi dynamic/consumer evidence đủ.

**Rollback:** revert một commit split; giữ façade và symbol re-export.

**Bằng chứng an toàn FFI cục bộ (2026-08-31):** hai wrapper tương thích
`aegis_llm_request` và `aegis_llm_reject` không còn dùng `Box::leak`; tham số
chuỗi chỉ dùng để kiểm tra hợp lệ/ghi nhận lỗi bounded nên được bỏ qua hoặc
thay bằng literal bounded, giữ nguyên ABI và giá trị boolean trả về. `cargo
check --features python-extension`, `cargo fmt -- --check` và test
`ffi_smoke_checks` đều PASS. Đây chỉ là xử lý leak đã quan sát ở hai wrapper;
chưa chứng minh toàn bộ lifetime của FFI, chưa thay thế inventory consumer/API,
chưa chạy allocation/soak campaign và chưa thực hiện split `ffi.rs`, vì vậy
M6 vẫn OPEN_LOCAL.

**M6 status-family extraction continuation (2026-09-03):**
implementation commit `69ea62bd52a5e3b36c35630045abd8b3a189f696` moves the
15 side-effect-free status, layout and identity PyO3 bindings into
`core/rust/src/ffi/status.rs`. `ffi.rs` re-exports the original symbols, so
the module registration and Python-visible ABI/schema names remain unchanged;
no public consumer was deleted or renamed. `cargo fmt --all -- --check`,
`cargo check -p aegis-nerve --no-default-features --lib`, and the feature-gated
`tests::tests::ffi_smoke_checks` pass. This is a bounded internal family split,
not proof of whole-FFI lifetime safety, allocation/soak behavior, or complete
controller/DTO/error-family convergence; M6 remains `OPEN_LOCAL`.

**M6 compatibility-family extraction continuation (2026-09-03):**
implementation commit `c3e3631f8b43343c2ebee1b1ad723bcd9e36f216` moves the
12 compatibility LLM, harness, physical-metrics, telemetry, trust-label and
hot-hash PyO3 bindings into `core/rust/src/ffi/compat.rs`. The parent façade
re-exports the original names and keeps hot-arena/controller bindings in their
existing owner until their own split is proven. Full Rust unit coverage passes
**441 tests** after the extraction, including the FFI and LLM contract tests;
feature-gated `tests::tests::ffi_smoke_checks` remains green. This preserves
current public symbols but does not establish complete FFI lifetime/allocation
safety or finish DTO/controller/error-family convergence, so M6 remains
`OPEN_LOCAL`.

**M6 mmap-bridge family extraction continuation (2026-09-03):**
implementation commit `4cbc5a24c5d2f3f7d1059c8970b212545af98439` moves the
five mmap/Wasmtime bridge PyO3 bindings into `core/rust/src/ffi/mmap.rs` and
re-exports their original names from the façade. The bridge functions retain
the existing typed path, identity, payload and fuel contracts; no schema or
consumer changed. `cargo fmt --all -- --check`, package compilation, the
feature-gated FFI smoke, and the complete **441-test** Rust unit suite pass.
This is an internal ownership split only; it does not prove OS containment,
full FFI lifetime/allocation behavior or finish the remaining DTO/controller/
error-family convergence, so M6 remains `OPEN_LOCAL`.

**M6 resource/runtime family extraction continuation (2026-09-03):**
implementation commit `2768e87` moves the eight hardware, admission, execution
lane, process-local runtime and resource-sample PyO3 bindings into
`core/rust/src/ffi/runtime.rs`. The façade re-exports the same names, preserving
the Python registration surface, JSON schemas, error mapping and the single
authoritative runtime lock; no consumer or ABI name changed. `cargo fmt --all
-- --check`, package compilation, feature-gated `ffi_smoke_checks`, the complete
**441-test** Rust unit suite and `cargo clippy -- -D warnings` pass. This proves
only an internal ownership reduction for the resource/runtime family; it does
not prove cross-process single-writer behavior, FFI-wide lifetime/allocation
safety, controller/DTO/error-family convergence, or external containment, so
M6 remains `OPEN_LOCAL`.

**M6 Lab-controller/replay family extraction continuation (2026-09-03):**
implementation commit `5818f61` moves the `PyLabController` class and eight
Lab event-chain, snapshot and archive-verification PyO3 bindings into
`core/rust/src/ffi/lab.rs`, including their state/error conversion helpers.
The façade re-exports the original class/function names and keeps module
registration unchanged; JSON schemas, archive hash domains, and fail-closed
manifest/legacy compatibility behavior are preserved. The complete **441-test**
Rust unit suite, feature-gated FFI smoke, formatting check and
`cargo clippy -- -D warnings` pass. This is an internal ownership split only;
it does not prove archive crash safety beyond existing tests, cross-process
authority or FFI-wide lifetime/allocation safety, so M6 remains `OPEN_LOCAL`.

**M6 remaining-family extraction continuation (2026-09-03):**
implementation commit `3a81726` moves the two hot-arena bindings, six EaC
cache/state bindings and four learning/session bindings into
`core/rust/src/ffi/hot.rs`, `core/rust/src/ffi/eac.rs` and
`core/rust/src/ffi/learning.rs`. The parent `ffi.rs` now retains shared process
state, panic boundary, helper ownership and Python module registration while
re-exporting every original public symbol; no ABI name, JSON schema or legacy
consumer changed. The complete **441-test** Rust unit suite, feature-gated FFI
smoke, formatting check and `cargo clippy -- -D warnings` pass. This completes
the bounded internal family extraction, but M6 is not release-closed: FFI-wide
lifetime/allocation/soak evidence, cross-process authority and independent
benchmarking remain unproven, so M6 remains `OPEN_LOCAL`.

**M6 replay-record error propagation continuation (2026-09-03):**
implementation commit `cdfce8f847a478b268d61db112fcebe34a103bcc` removes the
remaining production `expect` in `AgenticEvidenceProgramScratch` record
construction. `AgenticEvidenceProgram::execute` now propagates
`AgenticEvidenceProgramError` from replay-record validation instead of
panicking if an internal invariant is ever violated. Public FFI names, JSON
schemas and successful-path behavior are unchanged. `cargo fmt --all --
--check`, package `cargo check`/`clippy -D warnings` and the complete
no-default-features library suite pass **443/443**. This closes one local
panic-to-error path only; FFI-wide lifetime/allocation/soak, cross-process
single-writer authority, external containment and independent benchmarking
remain unproven, so M6 and `LAB-AUTH-001` stay open.

#### M7 — Replay/archive và recovery cutover

**Entry:** M2–M5 explicit lanes đã có terminal settlement và archive schema.

**Thực hiện:** canonical segmented replay, manifest/config binding, longest
valid prefix recovery, local writer lease và hosted writer adapter; mọi open
admission sau crash chuyển `REJECTED`/`UNKNOWN_SIDE_EFFECT` đúng policy.

**Exit gate:** fresh-process replay, mixed-lane crash, concurrent writer,
snapshot restore, corruption/truncation và rollback đều có raw witness +
independent verifier.

**Rollback:** đọc archive cũ ở compatibility mode; không append vào format mới
không có manifest/version.

**M7 fresh-process snapshot replay continuation (2026-09-03):** test commit
`751aed559ad3f710b6b5aefc845da5cb868e3378` writes a validated `LabRun`
projection to a temporary snapshot, starts an independent CPython process, and
requires `LabRun.from_payload` plus event-chain verification to reproduce the
same `event_root_hash`. The child has no shared Python object or monkeypatch;
the bounded test passes with the full Lab-runtime suite at **352/352**. This
strengthens local restart/replay evidence for the JSON projection only; it does
not prove native segmented-archive verification, crash-prefix injection,
concurrent hosted writers, cross-platform recovery, or external side-effect
reversal, so M7 and `LAB-AUTH-001`/`LAB-OPS-007` remain open.

#### M8 — Benchmark, operations và release closure

**Entry:** M0–M7 local gates pass; external owners sẵn sàng.

**Thực hiện:** hidden validator/scorer, cross-platform/hosted runs, OTel/log
and metric evidence, restore/rollback, signed release and final owner review.

**Exit gate:** benchmark generalization, platform containment, provider/browser
quality, hardware/scientific validity và final provenance đều có đúng external
witness; chỉ khi đó mới nâng status release.

### 19.9 Gate authority and starting state

The machine-readable gate and blocker authorities are
docs/architecture/not_verified_registry.json,
docs/architecture/deployment_policy.json, and quality/registry/. The generated
status view is docs/architecture/AEGIS_LAB_STATUS_GENERATED.md. This plan
defines target gates and migration order; it does not publish local benchmark
measurements or host-specific run reports.

### 19.10 Quy tắc thực thi để không phân mảnh dự án

1. Mỗi migration node chỉ có một owner, một canonical schema và một rollback.
2. Không tạo service/database/broker mới; chỉ thêm khi một requirement cụ thể
   không thể đáp ứng bằng modular monolith + replay hiện có và có ADR riêng.
3. Mọi diff phải cập nhật contract inventory, affected callers, tests, replay
   impact, docs và blocker registry trong cùng migration node.
4. Không xóa nested mirror, POC hoặc compatibility path chỉ vì không được
   runtime gọi; cần deletion evidence theo Section 19 và migration window.
5. Không chạy benchmark campaign, live provider/browser, cross-platform probe
   hoặc hardware test trên máy local khi chưa có resource budget, timeout,
   cleanup và external owner; local smoke phải bounded và không gây treo máy.
6. Sau mỗi node: recompute epoch, inspect diff, run smallest decisive gate,
   adversarial pass và cập nhật `NOT_VERIFIED` registry. Nếu epoch đổi giữa
   artifact, không gộp evidence hai epoch.
7. Chỉ chuyển sang node kế tiếp khi exit gate có artifact hash và verifier độc
   lập; “code tồn tại”, “test xanh” hoặc “README nói vậy” không đủ.

### 19.11 Definition of done cho target architecture

Target design chỉ được coi là đã chuyển thành implementation khi:

- package/CLI có một owner và compatibility window được kiểm chứng;
- Rust reducer sở hữu mọi terminal authority của Lab, Python chỉ là projection;
- trust/retry/effect policy có một owner và hash-bound contract;
- research/browser/experiment cells liên tục nhưng không vượt capability,
  budget, lease hoặc evidence rules;
- physics/benchmark claims có unit, uncertainty, comparator và independent
  evidence tương ứng;
- replay, recovery, cancellation, duplicate và partial deployment có witness;
- external boundary matrix được đóng bằng đúng môi trường, không bằng mock;
- final diff, docs, generated artifacts, registry, release signature và
  rollback cùng trỏ một commit/subject;
- residual risk vẫn hiển thị, không bị che bởi status hoặc prose.

Cho tới khi các điều kiện này đạt, trạng thái đúng là `DESIGN_READY /
CONVERGENCE_NOT_AUTHORIZED`; không được tự nâng thành `COMPLETE`,
`PRODUCTION-READY` hoặc “đã cover mọi tình huống”.

**AESE malformed-observation provenance continuation (2026-09-03):**
implementation commit `c36467c2dd533d444335d99c5ae47235eaaa7d24` removes a
determinism gap in the retained failure evidence.  AESE no longer hashes an
unsupported malformed observation through `str(object)`, whose default repr
may contain a process-local memory address; the hash serializer records a
stable fully-qualified type marker instead.  Two fresh unsupported instances
therefore produce the same `raw_observation_hash` while the result remains
`CONTAMINATED` and cannot be promoted.  The focused AESE suite passes
**52/52**, changed-file Ruff and strict Pyright pass.  The first full Python
run had **529 passed with only three expected registry-drift failures** before
regeneration; after regenerating the inventory/claim graph, the full suite
passes **532/532**, both source-tree hash/epoch `--check` gates pass, and the
document consistency gate passes.  This proves deterministic local hashing for malformed
inputs only; it does not make malformed data valid, establish independent
validator provenance, calibrate statistics, enable selective testing or
provide release authority.

**M2 FFI Wasmtime startup-boundary continuation (2026-09-03):**
implementation commit `26c30c261dd224b46924326ea6b7f083655bcbf0` adds a
fallible `WasmtimeSandbox::try_new` constructor and routes the mmap FFI entry
point through it. Engine or QuickJS-linker setup failures now return a typed
`PyRuntimeError` instead of panicking after an untrusted FFI call; the legacy
`new()` API remains available for compatibility callers and delegates to the
same constructor. A Rust regression checks the typed constructor's valid
configuration, and the complete no-default-features library suite passes
**448/448** with format/check/clippy gates passing. This is local FFI startup
failure containment only; thread/process containment, cross-platform runtime
semantics, hosted verification and release authority remain unproven.

**M2 FFI epoch-ticker startup continuation (2026-09-03):**
implementation commit `0e89b4aa7496452c546a9d3269211cce37e57817` extends the
fallible Wasmtime startup boundary through epoch-ticker creation. Both
`WasmtimeSandbox::try_new` and `with_config` now use `EpochTicker::try_new`;
thread creation is performed through a named `std::thread::Builder` and maps
failure to `TrapReason::InvariantViolation`, while worker mutex/condition
variable failures exit the ticker loop without a panic. `Drop` also avoids an
unrecoverable mutex unwrap. The no-default-features Rust library suite passes
**448/448** (45.34 s) with format, check and clippy gates passing. This proves
only local constructor/thread-startup panic containment; it does not prove
process-level interruption, cross-platform behavior, failure injection,
long-run resource safety, hosted verification or release authority.

**M2 CLI benchmark startup continuation (2026-09-03):**
implementation commit `65c8c8a87542dd3663a859e7499bea881b92f729` routes both
cold-start and warm-start QuickJS benchmark sandbox construction through
`WasmtimeSandbox::try_new`, converting runtime/thread setup failure into the
benchmark's typed error result instead of a compatibility-constructor panic.
Successful-path sampling, cache assertions and report schema are unchanged.
The complete no-default-features Rust library suite passes **448/448**
(57.14 s), with format/check/clippy gates passing. This closes only the local
CLI benchmark setup boundary; it does not establish benchmark validity,
cross-platform containment, long-run resource safety, hosted verification or
release authority.

**M6 PyO3 feature-gated smoke continuation (2026-09-03):** the exact
feature-enabled boundary command
`cargo test -p aegis-nerve --lib --no-default-features --features
python-extension ffi_smoke_checks` compiles the extension configuration and
runs `tests::tests::ffi_smoke_checks` with **1/1** pass (447 tests filtered).
The same feature-enabled configuration then runs the complete library suite
with **448/448** pass in **49.60 s**.
The initial `--exact` probe matched no test and is intentionally not counted
as evidence. This verifies the current feature-gated smoke path only; it does
not prove FFI-wide lifetime/allocation/soak safety, cross-platform behavior,
or release authority.

**NV-013 toolchain-availability classification (2026-09-03):** the current
Windows MSVC toolchain reports that the `miri` component is unavailable, and
`clang` is not installed; no local Miri or AddressSanitizer run was therefore
attempted or claimed. `NV-013` remains `NOT VERIFIED` and still requires a
supported hosted memory/undefined-behavior lane with retained logs.

**M6 EaC batch resource-boundary continuation (2026-09-03):**
implementation commit `8e8d595ca107a943b460ecdca3f36f71c923a44b` bounds the
legacy `aegis_eac_batch` FFI input to **8 MiB** before JSON parsing and rejects
more than **64** calls before the parallel executor can create one OS thread
per call. Valid inputs retain the existing transaction policy, result schema
and executor behavior. Focused no-default-features FFI regressions pass
**4/4**; the same feature-enabled FFI filter also passes **4/4** in **44.49 s**;
the complete no-default-features Rust suite passes **450/450** in **90.05 s**, with format,
check and clippy gates passing. This closes one concrete FFI resource-exhaustion
boundary only; it does not prove FFI-wide allocation/soak behavior, process
containment, cross-platform semantics or release authority.

**M6 EaC state-persistence resource-boundary continuation (2026-09-03):**
implementation commit `62dfddff2f07d57994b1afc17d89aff6d1b1a245` bounds the
legacy `aegis_eac_persist_state` `data_json` input to **8 MiB** before JSON
parsing and returns a typed `PyValueError` when the bound is exceeded. Valid
state persistence keeps the existing namespace, hash-chain and return-schema
behavior. The focused no-default-features FFI tests pass **5/5**; the same
feature-enabled filter passes **5/5** in **47.04 s**; the complete
no-default-features Rust library suite passes **451/451** in **76.90 s**, with
format, compile and clippy gates passing. This closes one additional FFI input
allocation boundary only; Python-side string allocation, FFI-wide lifetime/
soak behavior, process containment, cross-platform semantics and release
authority remain unproven.

**M7 segmented-writer capacity boundary continuation (2026-09-03):**
implementation commit `2778a4fc7fbe5013f4c1600dba39a2d9aa78a4fe` adds a
fail-closed upper bound of **65,536 events per segment** at the
`SegmentedArrowAuditStream` ownership point, before directory creation or
`Vec::with_capacity`. A `usize::MAX` adversarial regression is rejected without
creating the target directory or allocating the pending-event buffer. The
complete no-default-features Rust library suite passes **452/452** in **79.70
s**, with format, compile and clippy gates passing. This closes one local
segmented-writer allocation boundary only; crash-stale-lock recovery,
cross-process writer authority, hostile archive soak, cross-platform filesystem
semantics and release certification remain unproven.

**M6 learning-ledger input boundary continuation (2026-09-03):**
implementation commit `b6e1acb3784354f2837336a71e4df603f542c238` bounds the
legacy `aegis_get_learning_stats` `ledger_json` input to **8 MiB** before
deserialization and returns a typed `PyValueError` when the bound is exceeded.
The valid statistics schema and live index-count behavior are unchanged. The
focused no-default-features test passes **1/1**; the same feature-enabled FFI
test passes **1/1** in **46.81 s**; the complete no-default-features Rust
library suite passes **453/453** in **79.37 s**, with format, compile and
clippy gates passing. This closes one learning-ledger allocation boundary only;
Python-side string allocation, unbounded session content, FFI-wide lifetime/
soak behavior, cross-process authority and release certification remain
unproven.

**M6 runtime-admission input boundary continuation (2026-09-03):**
implementation commit `5e2b6a2ea8b8bbfb7515371e8eef020235ef6d24` adds a shared
**1 MiB** bound for runtime request, dependency-list and lease-token JSON
inputs, plus a **4,096 dependency-ID** bound before the authoritative runtime
lock/admission path. Oversized or over-counted inputs return typed
`PyValueError` values without parsing the request or mutating runtime state.
The focused no-default-features FFI tests pass **3/3**; the feature-enabled
filter also passes **3/3** in **44.17 s**; the complete no-default-features Rust
library suite passes **456/456** in **84.47 s**, with format, compile and clippy
gates passing. This closes runtime-admission input/resource bounds only;
Python-side string allocation, cross-process authority, long-run soak and
release certification remain unproven.

**AESE provenance-ledger rehydration continuation (2026-09-03):**
implementation commit `7f3d50f54097da03356d15937955a7468d221309` adds strict
`EvidenceLedgerEntry.from_dict` and `EvidenceLedger.from_dict` reconstruction.
Round-trip input must match the versioned schema exactly, retain
`DISABLED_IN_SHADOW`, use canonical types, and verify each entry artifact hash
before the aggregate ledger hash; tampered fields, promotion flags, unknown
keys, duplicate identities and forged subclasses fail closed. The focused
AESE primitive suite passes **57/57** and the full Python regression passes
**446/446** with deprecation warnings treated as errors; inventory, claim-graph
and document-consistency gates pass with **119** inventoried items and **0**
unresolved code references. This closes local provenance reconstruction only;
validator independence, calibrated statistics, external anchors, selective
test promotion and release authority remain unproven.

**AESE claim-graph source-binding continuation (2026-09-03):** implementation
commit `41fb223f8f1e5a27e30cf39789c5a772bc5e2ceb` binds the Phase-1 graph
digest to the content hash of every resolved implementation source node, not
only to inventory and prose registries. A regression mutating the effective
`aegis_cognition/aese.py` digest now proves the graph changes and therefore
cannot silently reuse prior evidence after an implementation change. The
focused inventory/graph/preflight suite passes **17/17**, the full Python
regression passes **447/447**, and isolated compatible Ruff checks pass for
the touched graph surface. This closes source-binding drift detection only;
unmapped surfaces, statistical calibration, external anchors, selective
testing and release authority remain unproven.

**AESE adaptive-result rehydration continuation (2026-09-03):** implementation
commit `425dff1ce82898cd0ef59a581ad717866fd38c5f` adds strict
`AdaptiveMeasurementResult.as_dict`/`from_dict` reconstruction and validation.
The versioned result schema requires canonical containers, finite statistics,
monotone observation/block counts, valid digest fields and a matching artifact
hash; forged result subclasses, unknown keys, non-canonical lists and tampered
fields fail closed. The focused AESE primitive suite passes **59/59** and the
full Python regression passes **449/449**; inventory, claim-graph and document
consistency gates pass after registry regeneration. This closes local adaptive
result reconstruction only; it does not establish statistical calibration,
independent validator agreement, external anchors, selective promotion or
release authority.

**AESE hardware/workload rehydration continuation (2026-09-03):** implementation
commit `1dd0c1aa409184c5cfbb929e9d40e3a471bd16c8` adds strict
`HardwareCapabilityVector.from_dict` and `WorkloadSignature.from_dict`
reconstruction. Hardware payloads must preserve the complete field set and
explicit `UNKNOWN` set; workload payloads must preserve the complete field set
and derived regime. Non-canonical containers, unknown keys, forged subclasses,
inconsistent unknown markers and tampered regimes fail closed. The focused AESE
primitive suite passes **62/62**, isolated compatible Ruff passes, and the
inventory, claim-graph and document-consistency gates pass after registry
regeneration. This closes local vector reconstruction only; vector hashes are
still evidence inputs rather than independent calibration, and external
anchors, statistical validation, selective promotion and release authority
remain unproven.

**AESE simulation/anchor/coverage rehydration continuation (2026-09-03):**
implementation commit `71081c7ccfa2e22d5c6aa7ba54856f763a04b017` adds strict
rehydration for `SimulationEvidence`, `AnchorObservation` and `CoverageVector`.
Simulation artifacts retain canonical lists and cannot set
`claimable_as_observed`; anchors bind nested hardware/workload payloads and an
evidence hash; coverage payloads reject aggregate scores and validate each
independent dimension. Unknown keys, forged subclasses, policy mutation,
derived-regime mismatch and tampered anchor values fail closed. The focused
AESE primitive suite passes **66/66**, isolated compatible Ruff passes, and the
inventory, claim-graph and document-consistency gates pass after registry
regeneration. This closes local reconstruction for these evidence primitives
only; simulation remains non-observation, anchor availability/calibration,
selective promotion and release authority remain unproven.

**AESE prediction/anchor-plan rehydration continuation (2026-09-03):**
implementation commit `809c25aa93f4c842abea02b3acfc3d21cee58d46` adds strict
serialization and reconstruction for `AnalyticPredictionModel`,
`PredictionResult` and `AnchorSelectionPlan`. Prediction success/failure/OOD
payloads retain their legacy hash layouts while validating finite intervals,
domain triples, anchor identity/hash correspondence and fail-closed status
metadata. Anchor plans validate budget arithmetic, disjoint identity groups,
non-execution policy and artifact hashes. Unknown keys, non-canonical nested
containers, forged subclasses, tampered estimates and overlapping plan groups
are rejected. The full Python regression passes **461/461**, focused AESE tests
pass **71/71**, isolated compatible Ruff passes, and inventory, claim-graph and
document-consistency gates pass after registry regeneration. This closes local
prediction/plan reconstruction only; model calibration, external anchor
execution, OOD generalization, selective promotion and release authority remain
unproven.

**AESE protocol/candidate rehydration continuation (2026-09-03):**
implementation commit `358bcf435a53592b04a1837a6e95134b7b26436b` adds strict
`AdaptiveMeasurementSpec.from_dict` and `AnchorCandidate.from_dict` support.
Measurement protocols now round-trip with a matching `protocol_hash`; anchor
candidate payloads preserve platform, priority, uncertainty and availability
metadata without executing a runner. Unknown keys, forged subclasses and
protocol-hash or schema tampering fail closed. The focused AESE primitive suite
passes **75/75**, full Python regression passes **465/465**, isolated compatible
Ruff passes, and inventory, claim-graph and document-consistency gates pass
after registry regeneration. This closes local input-contract reconstruction
only; statistical calibration, anchor availability, external execution,
selective promotion and release authority remain unproven.

**AESE result-semantic validation continuation (2026-09-03):** implementation
commit `0f6b1aaaf684057cbabe5db931bcec7d20b47a7d` hardens
`AdaptiveMeasurementResult.validate` beyond digest integrity. A result cannot
claim `PASS`, `FAIL` or `UNSTABLE` without a complete finite estimate,
confidence interval and precision statistic; mixed-null statistics, reversed
interval bounds, negative precision, invalid autocorrelation/drift ranges,
inconsistent block/count pairs, contamination attached to a successful status,
and an `UNSTABLE` status without an explicit instability reason now fail closed.
Four adversarial regressions pass, bringing the focused AESE suite to **76/76**;
isolated compatible Ruff passes. The full Python regression reached **554
passed** with **3 expected registry-drift failures** before regeneration; the
inventory and claim graph were regenerated and both `--check` gates pass with
119 retained items and 110 unmapped surfaces. This closes semantic integrity
of locally reconstructed measurement results only; it does not calibrate
statistics, provide independent validators, enable selective promotion or
close external/release obligations.

**AESE preflight schema-boundary continuation (2026-09-03):** implementation
commit `41f614f35390a3d90335c289c5d12e3bdd537de0` hardens
`validate_preflight` so an archived shadow plan must be a canonical dictionary
with the exact root schema; extra fields that could introduce an unapproved
skip or policy override, and non-dictionary payloads, are rejected before any
comparison. The focused preflight suite passes **9/9** and isolated compatible
Ruff passes with the intentional `E721` canonical-type rule. Inventory,
claim-graph and document-consistency gates remain passing at 119 retained
items and 110 unmapped surfaces. This closes local preflight schema integrity
only; it does not execute or promote a plan, prove selective-test
non-inferiority, or close hosted/release obligations.

**Adaptive controller terminal-state continuation (2026-09-03):** implementation
commit `09acf304c2093e53ef99e83ae285e302c98916b0` hardens the ownership point
for exploration admission. `AdaptiveController.next` now requires the
canonical `LabRun` type and returns no new exploration decision once a run is
`completed`, `blocked` or `aborted`; reserved/finalization/recovery budgets are
left untouched in those states. The codebase graph identifies 18 callers of
this method, and the full Lab runtime regression passes **353/353** (one
environment warning about an unsupported pytest-asyncio config option).
This closes a local terminal-state fail-open path only; it does not make the
Python controller the universal Rust authority, prove process-level
cancellation, or close hosted single-writer/release obligations.

**Adaptive controller observation fence continuation (2026-09-03):** implementation
commit `906d057ac90900b5e11e75e622a62c71dd80142b` applies the same terminal and
canonical-type guard to `AdaptiveController.observe`. A terminal run now
returns `False` instead of updating plateau state, and forged run objects are
rejected before state inspection. The four controller regressions pass and the
changed runtime/test surface passes isolated compatible Ruff. This closes the
paired observation-side fail-open path only; it does not establish calibrated
information-gain selection, multi-agent coordination efficiency, or external
authority.

**AESE-S1 statistical calibration continuation (2026-09-03):** implementation
commit `a1b4007811e6fbaf8019830bbd2897af5bffd006` adds the deterministic,
machine-readable shadow calibration harness at
`scripts/aese_statistical_calibration.py` and an adversarial rounding
regression for `_lag_one`. The campaign preregisters 18 workload families
(including IID Gaussian, skewed, mixture, variance, dependence, drift,
change, contamination and outlier cases), three scenarios, three protocol
variants with distinct floors/budgets/block sizes/alpha/precision targets, and
both every-observation and every-block checkpoints. The default campaign
retains **30 replicates per cell**, yielding **108 cells / 9,720 trials**.

The retained local artifact is
`artifacts/evidence/aese-statistical-calibration.json` with source SHA
`a1b4007811e6fbaf8019830bbd2897af5bffd006`, clean-worktree capture,
protocol hash
`c3bce0b345232e3548b1ebf2569f42f3f51b6a4cee4580e405ef813397936a8f`,
validator ID
`aese-statistical-calibration-v1:aese-statistical-calibration-generator-v1`,
and artifact hash
`681f8966f41e38a504021e9b477119f373e1942da5f49d87bfc980ea9c5bed05`.
The result is `INSUFFICIENT_EVIDENCE`: every candidate family remains
unvalidated because the preregistered stopping rule produced unexpected
`UNSTABLE` terminal outcomes; no threshold was relaxed and no candidate was
promoted. Dependence/trend/change/contamination/outlier families remain
`OUT_OF_DOMAIN`. The contamination summary is **540/1,080 detected (0.5)**
because flagged burst contamination is observable while the latent rare
outlier is intentionally not auto-detected; declared unstable-family
detection is **3,211/3,240 (0.991)**. Focused AESE/calibration tests pass
**83/83** and targeted Ruff passes. The `_lag_one` finite-domain clamp only
prevents rounding-induced invalid evidence; it is not calibration proof.

Current S1 state remains:

```text
AESE_MODE = SHADOW
LEGACY_TEST_AUTHORITY = ACTIVE
SELECTIVE_TEST_AUTHORITY = DISABLED
EVIDENCE_PROMOTION = DISABLED
STATISTICAL_PROTOCOL = IMPLEMENTED_BUT_NOT_CALIBRATED
CALIBRATION_RESULT = INSUFFICIENT_EVIDENCE
PRODUCT_RUNTIME_REFACTOR = FROZEN
EXTERNAL_CERTIFICATION = NOT_VERIFIED
RELEASE = NOT_AUTHORIZED
```

This milestone does not alter Lab/sandbox/replay/provider/FFI/runtime
semantics. The calibration artifact is synthetic local evidence; it does not
measure production observations, prove universal coverage, establish
independent validator agreement, or authorize selective execution. The next
narrow milestone is AESE-S2 critical/high-risk claim-to-test-to-source
mapping, with unknown dependencies widening conservatively.

**AESE-S1.1 meta-calibration correctness (2026-09-03):** implementation
commit `952a5e8be8e6bcfe603f0fd37bdea5e7af651435` closes the acceptance-method
seam required before any larger calibration campaign. The shadow harness now
records Bernoulli successes/trials, one-sided Wilson score bounds
(`wilson_score_v1`, nominal alpha `0.05`), and explicit
`VALIDATED`/`INVALIDATED`/`INCONCLUSIVE` decisions. Coverage, false-pass,
false-fail and decision-resolution metrics are retained as counts and rates;
the scenario semantics are explicit: `improvement` requires `PASS`, while
`null` and `regression` require `NOT_PASS`. An all-inconclusive protocol is a
recorded failure and cannot validate. Replicates are allocated in deterministic
batches until all scenario bounds are decidable or the preregistered maximum
is reached. Claim-graph status is now derived from mapping facts and unknown
criticality remains conservative.

The retained local artifact is
`artifacts/evidence/aese-statistical-calibration-s1-1.json` (ignored and not a
release artifact), with source SHA
`952a5e8be8e6bcfe603f0fd37bdea5e7af651435`, `CLEAN` worktree capture,
protocol hash
`04ed8d1debe8163d1f3ee74e640279b9e74e5cb3e100a510b1aa8e095f40fba3`,
validator ID
`aese-statistical-calibration-v1:aese-statistical-calibration-generator-v2`,
and artifact hash
`ee46af099fb98d6cf8b9b88fb901f0346584641d7e7d728dc9bbffedc36f742a`.
The preregistered campaign has 18 families × 3 variants × 2 checkpoint
policies = **108 configuration cells**; each configuration cell contains 3
scenarios, giving **324 scenario-cells** and **21,510 retained trial digests**.
Each cell allocated at least 60 and at most 120 replicates (mean
`66.38888888888889`); 97 cells stopped when bounds became decidable and 11
reached the maximum. The overall result remains
`INSUFFICIENT_EVIDENCE`: all nine declared in-domain families remain
unvalidated, while nine dependence/trend/change/contamination/outlier
families remain `OUT_OF_DOMAIN`. Declared unstable-family detection is
`6,779/6,840` (`0.9910818713450292`), and contamination detection is
`1,620/2,700` (`0.6`); the latter is intentionally limited because latent rare
outliers have no explicit contamination flag. These are synthetic finite
campaign measurements, not production or hardware evidence. No threshold was
relaxed, no unstable result was relabeled, and no evidence was promoted.

The claim graph generated from the same checkout is
`SHADOW_GRAPH_PARTIAL_MAPPING_SELECTION_DISABLED`: 131 surfaces, 9 mapped,
122 unmapped, 46 claims, 92 verifications (70 unmapped), 27 code nodes and
20 future obligations. This status is now evidence-derived; it is not a
completion claim. The unknown criticality fields prevent a narrower
critical-only completion status.

S1.1 implementation tests pass **24/24** and the full Python suite passes
**570/570** (one Python 3.11 warning for the unsupported
`asyncio_default_fixture_loop_scope` option); targeted isolated Ruff passes.
Pyright (not installed), repository formatter compatibility with the
project’s Python-3.14 target, hosted CI, independent validator agreement,
production representativeness, and release authority remain `NOT VERIFIED`.
The existing runtime authority state is unchanged:
`AESE_MODE=SHADOW`, legacy full-suite authority active, selection and
promotion disabled, runtime refactor frozen, and release unauthorized.

**S1.1 blocker ledger:** the acceptance methodology blocker is closed by the
implementation and boundary tests, but the calibration gate itself is not
closed. Unexpected `UNSTABLE` outcomes and decision-resolution failures keep
candidate families at `INSUFFICIENT_EVIDENCE`; this is a substantive result,
not a reason to tune thresholds. The remaining blockers are (1) identify and
either justify or correct the instability behavior within the declared
protocol, (2) complete the evidence-derived S2 critical/high-risk
claim-to-source-to-test mapping once S1.1 is accepted, (3) retain unknown
dependencies as conservative invalidation in S3 affected closure, and (4)
keep all hosted/release obligations deferred until runner-backed evidence is
available. No blocker authorizes changing test authority, enabling selection,
or modifying unrelated Lab runtime surfaces.

**S1.1 gate decision:** `S1_1_STATUS = INSUFFICIENT_EVIDENCE`;
`EVIDENCE_PROMOTION = DISABLED`; `SELECTIVE_TEST_AUTHORITY = DISABLED`.
The next permitted milestone is AESE-S2 after the S1.1 gate review; no larger
campaign or threshold change is authorized by this result.

**AESE-S1.1a finite-look and multiplicity closure (2026-09-03):** commit
`dc162dd5c0b13e5746f769e3ef6144a7e6deca72` closes the fixed-time Wilson peek
defect without adding a statistics framework. The canonical campaign looks
are preregistered as `30/60/90/120`; the default harness allocates one
`alpha_total=0.05` budget per configuration cell by equal Bonferroni control
over finite looks × 4 metrics × 3 scenarios. The artifact records
`error_control_scope=PER_CONFIGURATION_CELL`, `family_size=48` bound events,
`look_count`, `metric_count`, `scenario_count`, `alpha_total`, the allocation
method, per-look allocations and per-bound alpha. Custom small test campaigns
use no more than four deterministic looks and record their actual schedule.
Unknown relation/statistical decisions fail closed as `INCONCLUSIVE`.
`MIN_DECISION_RESOLUTION=0.50` remains a preregistered policy threshold whose
purpose is only to prevent always-inconclusive protocols; optimality is
`NOT_PROVEN` and belongs to S6 trade-off analysis.

S1.1a evidence is implementation/test evidence, not a new calibration PASS:
the statistical/diagnostic tests pass **12/12**, the affected AESE/registry
set passes **31/31**, and the full Python suite at the S1.1a boundary passed
**573/573** (one pytest configuration warning). Isolated Ruff passes on the changed Python
files. The current authority state remains
`AESE_MODE=SHADOW`, legacy authority active, selection/skipping/promotion
disabled, and release disabled.

**Failure-focused diagnostic (bounded, no new campaign):**
`scripts/aese_failure_diagnostic.py` replays only retained seeds from the
prior S1.1 artifact and writes the ignored artifact
`artifacts/evidence/aese-failure-diagnostic-s1-1a.json`. Its source artifact
hash is `681f8966f41e38a504021e9b477119f373e1942da5f49d87bfc980ea9c5bed05`,
diagnostic hash is
`ef997fb95e4bef4ba53dc8375bbf59abff2989035022f15a7e9589a67529a995`, and
reuse status is `RECOMPUTED_FROM_RETAINED_SEEDS`. It covers 162 failed
in-domain scenario-cells (4,860 replayed trials) and records status, lag-1,
drift, CI width, precision ratio, decision resolution, observations consumed,
failure reasons and classified causes. Aggregate statuses are
`UNSTABLE=4,335`, `FAIL=299`, `PASS=173`, and
`INSUFFICIENT_EVIDENCE=53`; decision resolution is `472/4,860`, with
`4,388` inconclusive decisions. The dominant recorded classes are
`STABILITY_DETECTOR=5,887`, `BASELINE_SEMANTICS=4,247`, and
`PRECISION_REQUIREMENT=3,420` reason occurrences. This identifies the next
failure-analysis work; it does not justify changing thresholds or relabeling
the calibration.

`S1_1A_META_VALIDITY = COMPLETE` for the implementation gate, while
`S1_1A_CALIBRATION_RESULT = INSUFFICIENT_EVIDENCE`. S2 may proceed logically
with risk-prioritized mapping, but unknown surfaces must widen conservatively;
no selector may use this diagnostic or the calibration artifact to skip an
authoritative test.

**AESE-S2 critical/high-risk mapping (2026-09-03):** commits
`9e33299e9976a085b521c9621a6c8c8670f74ee8` and
`569508a49257448dbfaa41bf8a7477f97f963815` add the explicit mapping seed and
the fail-closed validator `scripts/aese_s2_mapping.py`. Records contain
surface, claim, source-subject, test-subject and evidence-subject identities,
risk/criticality fields, relationship types, rationale and conservative
unknown-dependency policy. Source and test subjects must include a concrete
symbol and are checked against tracked source text; filename-only mapping is
rejected. The mapping is evidence-derived from the actual assertions and
invariants in the cited paths, not from filenames.

The current mapping report is
`quality/registry/current_s2_mapping.json`, artifact hash
`3c768b51f8d24ff7693b4c93cbcc39754393418423982f4752312a0a21f788e2`, with
`mapping_status=S2_FAIL_CLOSED_MAPPING_COMPLETE`,
`all_declared_critical_mapped=true`,
`all_declared_high_selection_relevant_mapped=true`,
`critical_mapping_scope=DECLARED_MAPPED_RECORDS_ONLY`,
`unknown_surfaces_may_contain_unclassified_criticality=true`,
`no_fake_mapping=true`, and
`critical_false_negative_status=NOT_EVALUATED_S3`. It verifies 9 critical
records and 15 high selection-relevant records; **116 of 131 inventory
surfaces remain UNKNOWN** and are explicitly widened rather than guessed.
The claim graph remains
`SHADOW_GRAPH_PARTIAL_MAPPING_SELECTION_DISABLED`; this mapping report cannot
enable selection, skipping or promotion. S2 mapping tests pass **3/3** and
the S2-focused closure set passes **18/18**; the post-S2 full Python suite
passes **576/576** with one known pytest configuration warning. The completed
S3 closure gate below preserves this authority state.

**AESE-S3 deterministic affected closure (2026-09-03):**
`scripts/aese_affected_closure.py` implements a typed, deterministic
contract-closure planner over explicit `IMPORT`, `CALL`, `FFI`,
`SERIALIZATION`, `CONFIG`, `SCHEMA`, `PACKAGE`, `ENTRY_POINT`,
`CARGO_FEATURE`, `WORKFLOW`, `GENERATOR`, `VALIDATOR`, `CLAIM` and `TEST`
edges. Mapping-derived edges require concrete source/test subjects; the
adversarial suite covers Python/Rust re-export paths, FFI, serialization,
Cargo features, entry points, registry generators, validators and workflow
changes. Unknown paths or dynamic edges widen to all retained inventory
items; no opaque filename score is used. The current plan artifact is
`quality/registry/current_affected_closure.json` with reproducible hash
`e9ed1e0848ae7b88a3a8e97d7c7e10eac21dfef0fac5d8735cb657a56e54060b` and
artifact hash
`a276b28ca5ab0d127491d4ccebba6dde8e4fbf65b3f41b1ee58ae0423b0d3fcc`.
The mapped-scope structural critical reachability status is
`COMPLETE_ZERO_MAPPED_SCOPE` (15 records checked); unknown repository paths,
unknown inventory surfaces, partial mappings and dynamic dependencies widen
to all retained items, and the adversarial
closure suite passes **14/14**. S3 exit-gate conditions are complete for the
declared mapped critical set; observed test false negatives are not measured,
and this is planning evidence, not permission to skip
tests or promote evidence. The S3 boundary full Python suite passes
**590/590** with the same single pytest configuration warning.

**AESE-S4 explainable shadow planner (2026-09-04):** commit
`92fb1b749b239e1c783211e2b5707589cde199c4` adds
`scripts/aese_shadow_planner.py`. For an exact `gt96` change the recorded
plan has one `WOULD_RUN` item and 130 `WOULD_SKIP` predictions, each with a
closure hash, critical-audit status and retained-authority reason. Unknown or
dynamic inputs become `WIDENED_UNKNOWN` with no skip; hosted workflow inputs
are `EXTERNAL_DEFERRED`. Reuse remains empty until all source/protocol/
validator/environment/claim-domain digests match. The artifact
`quality/registry/current_shadow_plan.json` has hash
`f0a02c4905ea89d07e9f560ac1e06b6cc836179a3aaeced45b752ba642d7f08`.
`structural_critical_reachability_status=COMPLETE_ZERO_MAPPED_SCOPE` and
`observed_critical_false_negative_status=NOT_MEASURED`; confusion-matrix
status remains `NOT_MEASURED` until explicit legacy outcomes are supplied.
Unknown or partial dependency states have no `WOULD_SKIP`; no execution or
authority change is possible.

**AESE-S5 held-out validation corpus (2026-09-04):** commits
`6c3df2ccb4715a5f2e62c5af2b9a651a776a8dba` and
`9edf0d75eddc95b06fa824820caf3d00d600b047` and
`14ad392b9ba58285e3875b05bf661c04e79331fc` add
`scripts/aese_validation_corpus.py` with disjoint development and final
validation cases. Final labels preserve `KNOWN_GOOD`, `KNOWN_BAD`, `OOD` and
`AMBIGUOUS`; the held-out set contains five synthetic critical planning
targets, all five reached (`synthetic_critical_targets_reached=5`,
`synthetic_critical_targets_missed=0`), including known-unmapped and
partial-mapping cases. Artifact
`quality/registry/current_validation_corpus.json` has hash
`5437420569a0defcad66b00b8503a94d312b5e21ea7e1afed4f5b90e9bb8dc3c`.
Status is `LOCAL_SHADOW_VALIDATION_ONLY`: this is planning reachability
evidence, not executed mutation-detection or production non-inferiority
evidence. The corpus tests pass **9/9** (including unknown/partial widening
and retained-provenance drift checks). The validator deliberately ignores
only checkout-volatile `artifact_source_sha`/`current_head`; stable decisions,
validator and environment digests, and the artifact self-hash remain mandatory.

**AESE-S6 paired cost analysis (2026-09-04):** commit
`4b3bad8f99f51f1b5ee948c6b5fdb9dcbf30a9a3` records three paired warm-cache
runs in `quality/registry/current_cost_measurement.json` (artifact hash
`2fa9559d52ba42e7c33fb74cac61f147b9af8692dcdbb14e6a3c5adeaf60ee55`). The
same Rust library workload measured 456 retained tests versus 14 `gt96`
tests: legacy median/p95 **71.778817/98.718078 s**, selected
median/p95 **1.521086/3.203918 s**, planner median/p95
**6.937248/8.139848 s**, and net-saving samples **87.374312, 48.786299,
63.632245 s**. Status is `MEASURED_EXPLORATORY_PAIRED` with
`savings_claim=LOCAL_EXPLORATORY_ONLY`; the reused artifact records
`measurement_reused=true`, `measurement_source_sha=4b3bad83edc19a3794d6e612981bdda31900343e`,
and `relevant_subjects_unchanged=true`. The saving summary is
`min_saving=48.786299`, `median_saving=63.632245000000005`, and
`max_saving=87.374312` seconds. This closes the local measurement gate for
one change class, but does not generalize to Python, cold builds, other
changes or production. AESE remains shadow-only and no 10x claim is made.

**AESE final local closure (2026-09-04):** the final correction milestone is
complete for the frozen local AESE scope. `AESE_LOCAL_TASK=COMPLETE` and
`LOCAL_SCOPE_PROGRESS=100%` are justified by passing implementation,
mapping, closure, planner, corpus, cost, registry, architecture and
constitution gates. The decisive safety invariant is enforced:
inventory-known but dependency-unmapped, partially mapped, dynamic, mixed,
and unknown repository paths all widen to `WIDENED_ALL_RETAINED` with no
`WOULD_SKIP`; exact closure is permitted only for fully mapped paths.
S2 is `S2_FAIL_CLOSED_MAPPING_COMPLETE` for declared mapped records, S3 is
`COMPLETE_ZERO_MAPPED_SCOPE`, S4 observed false-negative status is
`NOT_MEASURED` without explicit legacy outcomes, S5 is
`LOCAL_SHADOW_VALIDATION_ONLY`, and S6 is
`MEASURED_EXPLORATORY_PAIRED` with a `LOCAL_EXPLORATORY_ONLY` claim.
Authority remains `AESE_MODE=SHADOW`,
`LEGACY_TEST_AUTHORITY=ACTIVE`, `SELECTIVE_TEST_AUTHORITY=DISABLED`,
`TEST_SKIPPING_AUTHORITY=DISABLED`, and `EVIDENCE_PROMOTION=DISABLED`.
Production non-inferiority, hosted CI, multi-platform behavior, live
provider/browser behavior, physical hardware generalization, and signed
release provenance remain `NOT_VERIFIED` future-authority work; they are not
local-scope blockers. The final local reference run records
`FULL_PYTHON=615/615` with the one known pytest configuration warning;
retained Rust evidence is `FULL_RUST=456/456` no-default-features library
tests from the unchanged S6 source tree. Architecture fitness is `23/23` and
constitution audit is `180/180`. `S1_1A_CALIBRATION_RESULT=INSUFFICIENT_EVIDENCE` is
preserved and remains a future cutover constraint, not a reason to reopen
this local milestone.

**Goal/Target contract hardening continuation (2026-09-10):** the working tree
now validates the canonical Python GoalContract wire shape at the native Rust
Lab boundary, including exact fields, target identity/scope, budget parity,
genesis/evolution rules, parent digest format and per-event goal/target
binding. Python deserialization uses the same fail-closed structural rules and
strict Pyright typing. The Python GoalContract JSON and Rust GT96 binary
primitive remain explicitly separate protocols; neither is presented as a
lossless cross-language reducer.

Current post-fix Rust evidence is **497/497** with three long-duration tests
filtered; output retained at
`artifacts/verification/rust-lib-without-long-tests-20260910.txt`; focused native GoalContract
wire evidence is **5/5**; focused Python Goal/Target–Lab contract evidence is
**23/23**, targeted Ruff PASS, strict Pyright **0 errors**, Cargo fmt PASS, architecture fitness **23/23**, document
consistency PASS, AESE inventory/claim-graph drift checks PASS, and direct
inventory/claim/closure/shadow/S2 validators pass after regenerating artifacts
from the latest observed source snapshot. The retained validation corpus and
paired cost ledger were not overwritten; current corpus reproducibility remains
`NOT VERIFIED`. The current focused Rust GoalContract gate is **5/5**. The
current full Python suite is **620 passed, 1 skipped** in **557.96s**, with the
result retained at `artifacts/verification/full-python-suite-20260910.txt`.
An earlier full Python attempt emitted
artifact-drift failures after a generated cost ledger was overwritten and was
stopped before a complete report; the ledger was restored with its retained
paired provenance; that earlier interrupted attempt is historical and does not
override the later clean run.
Current packaged Goal/Target evidence is bound to the source-locked r2 wheel
`target/wheels-current-source-20260910-r2/aegis_cognition-0.1.0-cp314-cp314-win_amd64.whl`
with SHA-256 `16ad346ec0a545f4071cc4552cd9a75f5a85b570a2bfa62b36ec42b2b7f3e4cd`.
Clean install/import, mixed-admission recovery and controller-action smokes are
`PROVEN` in `artifacts/local-release-20260910-current-r2`; the r2 four-artifact
manifest and SPDX 2.3 SBOM are in
`artifacts/local-release-20260910-current-r2-evidence` and all report hashes
match the wheel. The manifest deliberately labels the revision `WORKTREE_DIRTY`.
Signing, hosted parity and promotion remain `NOT VERIFIED`.

**Current-source packaged evidence (2026-09-10):** the locked source snapshot
produced `target/wheels-current-source-20260910-r2/aegis_cognition-0.1.0-cp314-cp314-win_amd64.whl`
with SHA-256
`16ad346ec0a545f4071cc4552cd9a75f5a85b570a2bfa62b36ec42b2b7f3e4cd`.
The clean install/import, native recovery and controller-action smokes are all
`PROVEN` in `artifacts/local-release-20260910-current-r2`; all three reports
bind the same wheel hash. Recovery reconciles five admissions with zero open
after recovery; controller-action records native authority, benchmark `PASS`,
replay archive, provider retry fencing, browser/search/experiment/simulation
actions, and required memory effects with zero blockers. The r2 release
manifest and SPDX 2.3 SBOM are `PROVEN` in
`artifacts/local-release-20260910-current-r2-evidence`, covering the wheel and
all three smoke reports. This is local Windows/CPython 3.14.7 evidence;
signing, hosted parity, Tier-1 wheels and external promotion remain
`NOT VERIFIED`.

**Current bounded verification (2026-09-10):** the combined Python
contract/release/runtime/Lab gate is `390/390` on CPython 3.14.7; current
native GoalContract tests are `5/5`; strict Pyright
reports `0 errors, 0 warnings, 0 informations`; targeted Ruff on the changed
Python files and `cargo fmt --all -- --check` pass. A current-source release
wheel rebuild with pinned Maturin `1.14.1` completed successfully; the current
wheel, three packaged smokes, r2 manifest and SBOM are `PROVEN` for this local
Windows/CPython 3.14.7 snapshot. The current AESE artifacts were
regenerated and their direct validators returned empty error sets: S2 artifact
`a583a36fc762a6a09fd192f83c1ad75b45d040115e9674f5cd60aa9582180ae5`, closure
artifact `fc2738713d034bdd930aa4c74605fc6d8b0321ec7ae55ffc19474b2bb812bae1`,
and shadow-plan artifact
`0bdf857b22c1ce8ff1d079fa8581e9e621a6ee3d1ac681e6cdc49e435a755363`.

**Current-source verification supersession (2026-09-10):** the paragraph above
is retained as a historical bounded snapshot. The later uncontended historical
run was `620 passed, 1 skipped` in `557.96s`; its former ledger path has since
been superseded by the R3 ledger, so it is not current full-suite evidence.
The current focused native Lab gate is `23/23`, and the current
current no-default-features Rust library gate is `497 passed, 0 failed, 3
filtered` with output retained at
`artifacts/verification/rust-lib-without-long-tests-20260910.txt`; the three
long-duration tests remain pending repeat after the lint fix. The
focused Python Goal/Target–Lab contract gate is `23 passed`, and the current
Windows/CPython 3.14.7 r2 wheel plus install, recovery, controller-action,
manifest and SBOM evidence is bound to SHA-256
`16ad346ec0a545f4071cc4552cd9a75f5a85b570a2bfa62b36ec42b2b7f3e4cd`.
These results are local current-source evidence; hosted CI, Tier-1 wheels,
OS-level isolation, signed external attestation, real multi-machine execution,
and production promotion remain `NOT VERIFIED`.

**Historical Goal/Target integration snapshot (2026-09-10):** explicit Goal contracts
now authorize generic external tool effects only through exact
`tool_name::effect_class` keys declared by `TargetDescriptor`; Python admission
and native event binding enforce the same rule. `LabPolicy` must still allow
external writes, and this does not prove provider-side containment or exclusive
resource leasing. The historical focused Python contract/progress gate was
`25/25`, focused Lab runtime `353/353`, focused native Lab `24/24`, and full
Rust `501/501`; the `617 passed, 1 skipped, 5` AESE-drift result and `40/40`
follow-up are retained only as provenance. The later `631 passed, 1 skipped`
clean full-suite result is historical; the current local Python gate is the R4
result recorded in the top amendment.

**Latest Goal/Target hardening update (2026-09-10):** Target read/write roots
are now consumed by the generic local-tool boundary with lexical,
segment-aware containment; literal URI dot segments are normalized for candidate
paths while ambiguous encoded separators/backslashes and dot-segment roots are
rejected; bound Goal evolution rejects silent target unbinding;
malformed or duplicate external-effect keys are rejected by both Python and
Rust target validation. Current local evidence is `29/29` Goal/Target tests,
`355/355` Lab runtime tests, `15/15` GT96 tests, `24/24` native Lab tests and
`508/508` full Rust library tests in `86.48s` at
`artifacts/verification/full-rust-lib-20260910-r3.txt`. This remains
application/native contract evidence, not symlink/kernel/provider/hosted
isolation.

**Local FFI and CLI contract continuation (2026-09-10):** the Rust learning
FFI transition helper now validates its operation discriminator before opening
the memory repository and maps an unexpected value to a typed `PyValueError`;
the invalid-operation feature-gated regression passes `1/1` in
`artifacts/verification/ffi-learning-invalid-operation-20260910.log`. The
canonical CLI now returns status `0` for successful commands, `2` for usage or
unknown-command errors and `1` for missing configuration or configuration
failures. Its direct contract slice passes `5/5` in
`artifacts/verification/cli-contract-20260910.log`; Ruff and strict Pyright
for the changed CLI surface pass in
`artifacts/verification/cli-ruff-20260910.log` and
`artifacts/verification/cli-pyright-20260910.log`. This improves local error
observability and FFI panic containment only. It does not close the remaining
FFI-wide lifetime/soak, hosted authority, provider, cross-platform or release
provenance gates. R3 package artifacts remain historical; the later R4 wheel
and packaged smoke evidence in amendment 0.2 contain this current source.

**Compatibility CLI hardening continuation (2026-09-10):** the independent
`core/python/aegis_cli.py` setup path now writes its legacy YAML and `.env`
files through durable per-file replacement, applies a user-only mode where the
platform supports it and quotes the `.env` value safely. The focused
compatibility regression passes `1/1` in
`artifacts/verification/legacy-cli-config-20260910.log`; Ruff and Pyright
pass in `artifacts/verification/legacy-cli-ruff-20260910.log` and
`artifacts/verification/legacy-cli-pyright-20260910.log`. This preserves the
legacy format and does not make the compatibility CLI the canonical package
owner; platform secret-store integration and migration of existing credentials
remain open. The later R4 wheel above contains the current canonical CLI and
FFI source.

The post-edit bounded Rust library run passes `506/506` with two explicitly
filtered long wrapper tests in
`artifacts/verification/rust-lib-without-long-tests-20260910-r2.log`; the
previous `508/508` ledger remains valid only for the earlier source snapshot.
An attempted current-source release rebuild initially failed: the first
parallel build hit a compiler pipe/Windows resource failure and the bounded
single-worker retry was also terminated during dependency compilation; the
raw first failure is retained at
`artifacts/verification/maturin-current-source-20260910-r4.log`. A later
uncontended cached single-worker rebuild succeeded; the resulting R4 wheel and
three packaged-smoke reports are recorded in amendment 0.2 above.
