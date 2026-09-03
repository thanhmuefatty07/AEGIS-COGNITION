---
document_id: AEGIS-LAB-RUNTIME-MASTER-PLAN
document_type: canonical_current_implementation_plan
status: IN_EXECUTION
authority: derived_from_checkout_and_evidence_manifest
applies_to_commit: b6e1acb3784354f2837336a71e4df603f542c238
created_at: 2026-08-26
last_verified_at: 2026-09-03
supersedes: browser-native-proposal-and-cumulative-harness-roadmap-as-execution-authority
evidence_source: docs/architecture/evidence/current.json
execution_scope: clean origin/main checkout
verification_scope: local checkout, origin/main parity, implementation/tests; hosted CI observations retained as NOT VERIFIED (no runner-backed gate)
---

# AEGIS Lab Runtime — Master Implementation Plan

## 0. Quy chế của tài liệu

Đây là kế hoạch triển khai canonical cho AEGIS Lab Runtime. Tài liệu này không
phải bằng chứng rằng các hạng mục đã hoàn thành, không tự nâng trạng thái
`NOT VERIFIED`, và không biến một tuyên bố trong tài liệu cũ thành sự thật.

Tệp `Ultimate Software Engineering Constitution — Maximum-Rigor Prompt.md`
được xem là tài liệu tham khảo. Nó trùng nội dung với
`docs/ENGINEERING_CONSTITUTION.md` sau khi chuẩn hóa newline; SHA-256 UTF-8
chuẩn hóa của cả hai là
`4921db890f7b375341b74a89b6aad2b193b3b268ea5b676803227f52335f80fa`. Các câu
tự yêu cầu áp dụng nó cho mọi request không có quyền ghi đè yêu cầu hiện tại.

Mọi claim trong plan phải được phân biệt bằng ba lớp:

- `FACT`: quan sát được từ code, manifest hoặc artifact có hash.
- `TARGET`: thiết kế cần xây.
- `GATE`: điều kiện phải vượt qua trước khi được đổi trạng thái.

Không dùng các từ `proven`, `production-ready`, `zero-copy`, `bias-free`,
`absolute`, `fully autonomous` nếu không có evidence class, phạm vi, commit,
protocol và validator tương ứng.

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

### 2.1 Evidence hiện có

Các số liệu đầu tiên dưới đây là **historical baseline**, không phải trạng thái
hiện tại và không được dùng để chứng minh checkout mới:

- Snapshot ban đầu ghi nhận HEAD `f9645caf6d17cee2023d52183990ffcf8317e456`;
  snapshot đó dùng Python 3.14 và 93/93 test.
- Artifact Rust ban đầu ghi nhận 440/440 pass; phạm vi khi đó chỉ là
  `LOCAL_CHECKOUT_ONLY` với `independent_verification=NOT VERIFIED`.
- Constitution audit ban đầu là 177/177 và architecture fitness 23/23; đây là
  structural/presence checks, không phải proof rằng Lab behavior hoạt động.

Checkout implementation hiện hành cho migration node này là
`dc1ca14072bfc363f278bced90558e0d1adf590e` (functional FFI change
`e0731f698302a0791c70c24d93b696597e04180d` and session-index hardening
`f95bbcb03c1c8448afbcf392b38dcd964b9a6f3f`), với worktree sạch và
`origin/main` parity được xác nhận sau push. Regression Python trên CPython
3.11 là **427 passed** với deprecation warnings treated as errors; Rust package
lib regression sau FFI hardening là **443 passed**, cùng fmt/check/clippy pass.
Registry AESE hiện có **117** inventoried items, graph **45 claims / 90
verification references** và selection ở `SHADOW`/`NOT_EXECUTED`. Các kết quả này
chỉ là local source-bound evidence; không thay thế hosted, signed-release hay
external-anchor evidence.

`current.json` vẫn cố ý để `commit=CHECKOUT_HEAD`, sáu evidence record là
`NOT VERIFIED`, và 35 requirement là `IMPLEMENTED / NOT VERIFIED`; placeholder
này không được sửa thủ công để tạo release evidence.

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
| Historical plans | `planning pdf/AEGIS-COGNITION_ Agent Harness Continuation Plan.md`, `planning pdf/AEGIS-COGNITION_ Kiến trúc AI Tối ưu.md`, `IMPLEMENTATION_SPEC.md`, `PROJECT_OVERVIEW_DETAILED.md` | không có quyền current status | giữ lịch sử; bị plan này supersede khi nói về Lab |
| Historical reports/proposals | `docs/BROWSER_NATIVE_AGENT_ARCHITECTURE.md`, `*_REPORT.md`, `docs/CLUSTER_SOAK_REPORT.md` | không có quyền promotion | chỉ dùng làm provenance/evidence candidate |
| User attachment | `C:\Users\ADMIN\Downloads\Ultimate Software Engineering Constitution — Maximum-Rigor Prompt.md` | không có quyền repo/runtime | normalized-content duplicate của constitution repo; coi là reference, không là override |

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

**Working-tree disposition:** `GoalContract` hiện dùng domain-separated hash,
field/list lengths và parent/evolution binding; unit tests đã phủ hash
partition và contract evolution. Author/effective-epoch provenance vẫn thiếu
ở contract này; LabController đã được nối cho các lane local hiện có nhưng
chưa phải authority hosted duy nhất của toàn bộ Agent lifecycle (xem B0.6 và
`LAB-AUTH-001`).

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
và uniqueness rules. Payload
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
provenance, nhưng không còn được dùng làm current status. ADR-012 đã được
renumber thành ADR-015 với compatibility stub; phần còn lại của blocker này là
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

## 16. Execution ledger hiện tại

Plan này đã được chuyển sang trạng thái thực thi trong working tree. Các mục
dưới đây là thay đổi đã có code/test, nhưng chưa được gọi là production proof:

| Hạng mục | Implemented change | Verification hiện tại | Trạng thái |
|---|---|---|---|
| Budget lanes | `BudgetLedger` tách exploration/finalizer/recovery, refund atomic, recovery API | Rust counterexample + negative tests | LOCAL-PROVEN |
| Contract/evidence | goal hash có framing/list lengths/parent evolution; evidence bind criterion/contract/validator | Rust GT96 tests | LOCAL-PROVEN |
| PAV semantics | distance watchdog deterministic across process history; `ObjectiveValidationReceipt` tách correctness khỏi PAV | Rust physical tests | LOCAL-PROVEN |
| Task transitions | `mark_status` validates; `complete_task` đi qua legal states | Rust task-ledger suite | LOCAL-PROVEN |
| Lab kernel | mission, source, claim, hypothesis, experiment, unit-aware observation, uncertainty/replication gate, typed blocker record/resolve events, experiment/tool execution attempt admission/settlement, cancellation admission/settlement, atomic rollback on native rejection, event hash-chain, replay check; Python appends/transitions are native-admitted when policy requires | Rust lab tests + Python native-authority, blocker-audit, retry/cancellation and rollback tests | LOCAL-PROVEN (implicit tool planner and hosted authority remain open) |
| Native LabController | Rust `LabController` owns `LabRuntime` + exploration/finalization budget lanes + `FinalizationBoundary`; epoch-bound exploration admission, budget/finalization replay events, bounded snapshot/restore, PyO3 `LabController`, projection-event writer/state sync, typed payload/reference admission, browser/research/skill side-effect receipt admission, experiment and generic tool execution admission/settlement with attempt/lease/effect/role/schema/stop-rule uniqueness, cancellation admission/settlement across abort transition, native projection-payload index, transactional clone-before-commit rejection and fail-closed error surface; Python compatibility dispatcher now resolves all explicit edge runners through `ExecutionCellRegistry`, whose manifest is persisted and hash-bound and the registry is sealed after capture with an immutable lookup snapshot; restored crash prefixes can reconcile every open explicit execution admission as non-success; `_call_fenced` rejects an adapter that swallows cancellation; opt-in `ProcessExecutionCell` terminates its own picklable child on local timeout/cancellation; configured replay directories are serialized by `ReplayWriterLease`; one `LabApplication` cannot overlap active runs in-process; compatibility RAG `memory.search_past`, operator-owned `benchmark_validation` validators and the post-completion `memory.index_session` effect are admitted/settled as typed cells; browser launcher creation is itself admitted as a typed `launch` action before invoking the untrusted launcher and settled on success/rejection/cancellation | Rust controller/projection tests cover generic tool admission/settlement and duplicate rejection, experiment execution binding, duplicate execution/cancellation settlement rejection with unchanged snapshot, cancellation admission→aborted transition→settlement, blocked-state recovery admission and snapshot restore; Python regression covers transient experiment/simulation and generic tool retry as `REJECTED|TIMED_OUT` attempt 1 + `SUCCESS` attempt 2, cooperative and swallowed-cancellation in-flight tool/research cancellation, process-cell timeout/cancellation termination, execution-cell identity/capability/effect/duplicate rejection, strict benchmark-validator fallback rejection and sealed-snapshot mutation resistance, context retrieval admission/hash-only result/prompt-injection rejection and required-cell missing failure, required post-completion effect missing rejection, active-run rejection, replay-writer process contention rejection, literal IPv4/IPv6/decimal-IP unsafe-address rejection, browser prompt-injection rejection with hash-only evidence and controller-stop behavior, hostname-resolution private-address rejection, isolated benchmark validator success/tampered-input/timeout rejection, and mixed-lane crash-prefix recovery after restore; v63 real-wheel smoke imported the packaged wheel outside checkout and executed a bundled typed controller plan containing search, browser, experiment and simulation actions through `rust_native_verified`, with source capture, browser settlement, two distinct observations and no blockers; the same v63 packaged run proves `benchmark_status=PASS`, one successful `benchmark.hidden_validator` settlement, exactly one successful `memory.search_past` context-retrieval receipt and exactly one successful `memory.index_session` receipt; a separate v63 packaged recovery smoke settled five mixed admissions as `REJECTED` with `UNKNOWN_SIDE_EFFECT`; source regression proves gateway retry as `TIMED_OUT` attempt 1 + `SUCCESS` attempt 2, typed controller action plan search/browser/experiment/simulation execution and external-effect rejection; launcher admission precedes launcher invocation and launcher-owned browser session remains bound across the controller loop and is cleaned after it; corrected clean-install remains proven and v62→v63→v62 rollback is locally proven, with v62 retained only as the immediate historical predecessor and v61/v60/v59/v58/v57/v56/v55/v53/v52/v51/v50/v49/v48/v47/v46/v45 and earlier rollback evidence retained as historical evidence | LOCAL-PROVEN (implicit planner/lease for hidden side effects, hosted single-writer service, OS-level process/resource enforcement and cross-platform packaging remain open) |
| Public Lab facade | `Lab`, `LabMissionSpec`, `LabPolicy`, `LabBudget`, `LabSession` với live evidence stream và operator controls; `Agent(..., lab=True)` compatibility path; typed controller action plan; `ExecutionCellBinding`/`ExecutionCellRegistry` for explicit cell identity/policy; explicit `AuthorityMode` for projection-only, optional native admission and native-required contexts; event-log-driven multi-lane crash reconciliation and cancellation fence; opt-in `ProcessExecutionCell` for killable local adapter execution; `ReplayWriterLease` for configured replay-directory writer serialization; per-application active-run guard; compatibility RAG context retrieval admission | Python Lab tests 138/138 targeted; combined regression count recorded below; event cursor/stream, native-authority admission/fail-closed, typed browser actor/observer/research/electrical rejection, experiment/simulation/generic-tool/gateway retry settlement, typed search/browser/experiment/simulation action execution and rejection, unapproved external-effect rejection, gateway admission and cooperative/swallowed cancellation event tests; registry identity/capability/effect/duplicate/late-mutation and sealed-snapshot mutation rejection plus snapshot manifest round-trip; context retrieval admission/hash-only result/prompt-injection and required-cell missing rejection; required post-completion cell missing rejection; explicit-registry legacy search/browser/skill/benchmark-validator fallback rejection, ambiguous-browser-runner rejection and PROD native strict-edge resolution; PROD Agent native-default rejection; process-cell timeout/cancellation termination; replay-writer process contention rejection; concurrent active-run rejection; mixed-lane recovery after restore and abort-order fail-closed behavior; bundled research→preregistration ordering and Agent compatibility replay-default regressions; initial literal/obfuscated research fetch IP guard, hostname resolution preflight rejection and unsafe provider-candidate rejection; same-ID typed projection mutation fails closed; LabPolicy trust propagation/hash-binding matrix for DEV/STAGING/PROD; hash-bound event envelope and execution-cell registry mismatch rejection; explicit AuthorityMode compatibility/conflict/snapshot coverage | LOCAL-PROVEN |
| Skill admission/execution | `SkillManifest`, `SkillRegistry`, capability/precondition admission, validator-backed receipt, mission/replay binding, bounded `skill_requests` timeout, explicit non-success settlement, snapshot persistence | Python skill admission, replay/tamper, timeout/failure settlement and LabApplication integration tests; native smoke reports `rust_native_verified` for admission/execution events | LOCAL-PROVEN (Rust planner/authority and hosted validator isolation remain open) |
| Research executor | typed search IR + HTTPS bounded fetch + initial literal/obfuscated IP rejection, hostname-resolution preflight and redirect/private-IP guard + render/extract/citation spans + freshness window + independent provenance-cluster quota + cross-check/contradiction adapter + source/snapshot hashes; native program intent admission before provider call and explicit settlement; controller-generated typed search action | deterministic provider/fetch/freshness/cluster/redirect tests plus live `example.com` fetch/extract smoke; current regression proves initial unsafe literal, private DNS and unsafe provider-candidate rejection; v15 wheel verified native research admission/settlement; v19 source regression verifies controller action plan executes a search program without a static hook; live provider credentials, freshness quality and semantic quality remain open | LOCAL-PROVEN (adapter + local egress preflight + live fetch smoke + native pre-admission) |
| Benchmark V2 | strict protocol, typed raw item/trial/seed records, retained failure/timeout/refusal records, valid-vs-total counts, artifact hash, `EnvironmentFingerprint` binding (object or explicit hash), CI, preregistered seeds, contamination and fail-closed validator rejection; operator-owned isolated validator subprocess and `benchmark_validation` execution cell use versioned JSON, input/protocol/raw-trial hash binding, argv-only execution, timeout and output limit | Python + Rust benchmark tests, including missing-environment, non-numeric, duplicate-identity, retained-failure, isolated-validator success and tampered-input rejection; v63 packaged controller smoke records `benchmark_status=PASS` and one successful `benchmark.hidden_validator` settlement | LOCAL-PROVEN (hosted scorer secrecy and independent reproduction remain open) |
| Evidence registry parity | `validate_registry_parity()` rejects dropped/unknown NV IDs, NV Markdown ID/status drift, GT96 Markdown status drift and incomplete deployment-policy blocking/non-blocking partition; `materialize_for_head()` derives manifest IDs from the canonical registry; document gate generates the human status view and validates metadata/link parity | parity/materializer/status-drift tests pass; generated checkout-bound artifact and `document_consistency_gate.py` pass; tracked template still fails closed only on placeholder SHA/final evidence | LOCAL-PROVEN (final-SHA artifact remains open) |
| Simulation/physics discipline | dimensional algebra (including V/A/W/J), canonical unit conversion for observations and residual-unit checks, finite uncertainty, held-out RMSE calibration evidence, named constraint residuals, explicit convergence error/step gate, bounded Euler/RK4 ODE primitive with step-doubling local-error and invariant/energy-drift gates, bounded `ElectricalSignalSpec`/`ElectricalSignalCell` for sample-count, Nyquist/anti-alias declarations, sensor correction/calibration digest, Ohm residual and trapezoid V/A/W/J energy, non-degenerate CI reporting, epistemic status and clean replication metadata | 76 targeted Lab tests; calibrated `INFERRED` vs uncalibrated `SIMULATED` path; ODE analytic/invariant and electrical undersampling/residual negative tests; simulation retry admission/settlement; controller-selected bounded simulation action executes the configured cell; bundled research→experiment→simulation ordering regression; single-observation CI is explicitly withheld; electrical result is `MEASURED_INPUTS_ONLY`; no validated solver/hardware-energy claim | LOCAL-PROVEN (contract + bounded primitive) |
| Browser lifecycle | `BrowserCell` lease/quota/relaunch-recovery/cleanup, external-session bind without implicit close, `BrowserObserverView` read-only projection with separate observation quota and replay-visible observer receipts, plus optional Playwright managed context and HTTPS route guard; literal private/loopback/link-local/multicast/reserved/unspecified and legacy obfuscated IPv4 destinations are rejected before browser admission; managed Playwright resolves every hostname request and rejects any private/loopback/etc. address, while browser accessibility/network projections are scanned for prompt-injection markers and rejected with hash-only security evidence | typed lifecycle + observer isolation/receipt tests plus live Chromium `1234` navigation/network capture/unauthorized-route rejection, literal IPv4/IPv6/decimal-IP unsafe-address rejection, hostname-resolution private-address rejection, and browser prompt-injection rejection/retention regression; prior v14 wheel smoke completed actor + observer pre-admission/settlement under native authority; current v15 wheel smoke covers research/experiment/cancel, while source tests cover browser paths; DNS rebinding race, process crash injection, OS/process isolation and hosted cross-domain evidence remain open | LOCAL-PROVEN (adapter + local role boundary + literal/obfuscated-IP guard + hostname-resolution preflight + local prompt-injection marker gate + live smoke) |
| Replay snapshot | JSON snapshot restores reducer and rejects payload tampering; Rust `LabRuntime::snapshot_json/from_snapshot_json` and `aegis_lab_validate_snapshot` fail-closed; Python event hash canonicalization, per-append native admission and pre-mutation transition admission match Rust Lab event schema | Python replay/recovery + native-authority tests; Rust snapshot/tamper/transition test; Rust FFI compiled and clippy-clean | LOCAL-PROVEN (projection bridge) |
| Replay segment archive | Rust `RunEventKind::LabEventRecorded` and `RunEventSegmentArchive::write_lab_events` validate the complete Lab chain before sealed Arrow publication; Python writes a durable snapshot sidecar and, when available, asks the strict native verifier to recover the prefix and match the sealed `manifest_hash` before accepting completion | Rust round-trip plus missing-tail adversarial test; FFI archive + recovery surface compiled; native E2E completed with 1 sealed segment and snapshot restore; Python regression proves strict native verifier preference and restore re-check; v63 packaged controller smoke proves the Agent compatibility lane writes its default archive when native authority is present; mixed-lane open-admission reconciliation is proven after snapshot restore; swallowed cancellation cannot be promoted to success | LOCAL-PROVEN (cross-platform crash-prefix injection and hosted restore evidence remain open) |
| Release install/rollback smoke | Clean venv installs the exact replacement wheel with declared runtime dependencies, imports from outside the checkout, and records wheel SHA-256/platform/CPython metadata; the prior `--no-deps` + checkout-cwd path was corrected because it could test the source tree or fail on intentionally missing dependencies; root clean import/CLI, independent core-bridge import without a console script, and dependency-complete combined-runtime ownership are now reproducibly probed | Replacement wheel SHA-256 `9ed944de33159950f62755ae778e275eb5feec1783c50d433038dd80412ed65b`; Windows/CPython 3.14 disposable environments report `repo_on_sys_path=false`, native extension import success, `aegis --help` exit 0, core compatibility PASS, and exactly one combined `aegis=aegis_cognition.cli:main` entry point; evidence is retained in `docs/architecture/design-closure/packaging_truth.json` and `artifacts/local-runtime/provider-fence-settlement-20260901/provider_fence_settlement_packaging.json`; the referenced v63 directory/records are missing and therefore historical only | LOCAL-PROVEN (one Windows lane; hosted Tier-1 parity, signed final-SHA attestation and rollback subject provenance remain open) |

> **Evidence supersession (2026-08-27):** any v39/v40/v42/v43/v44/v54/v55/v56/v57/v58/v59/v60 references retained in
> historical Native LabController/Replay rows are superseded for current release
> evidence by v63 and its records below; v62 remains the immediate rollback
> predecessor.

> **Evidence erratum (2026-08-27):** the Native LabController row above mentions the
> earlier v27 wheel smoke for historical continuity. The superseding packaged
> controller-action evidence is now v63 (SHA-256
> `e2ae96f1c7886e3f2f7a1e522cb5e2d783b90df96db8caed2ef44eca2afd41bf`) and is
> the current release-candidate local evidence. v55 was superseded because its
> build invocation used maturin 1.13.3 despite the build-system requirement;
> v62 is retained as the immediate rollback predecessor and v27–v61 as
> historical predecessors.

> **Evidence reconciliation correction (2026-09-01):** the v63 wheel and its
> install/controller/recovery/rollback records referenced by the historical
> paragraphs above are absent from the current machine and are not replayable.
> The current local package subject is the replacement wheel with SHA-256
> `9ed944de33159950f62755ae778e275eb5feec1783c50d433038dd80412ed65b`; its
> clean root, independent core-bridge and dependency-complete combined-runtime
> probes are recorded in `docs/architecture/design-closure/packaging_truth.json`.
> This is bounded Windows/CPython local evidence only, not a v63 replay or a
> signed release attestation. The v63 phrases in the Native LabController,
> Benchmark V2, Replay segment archive, Operations and Release rows above are
> historical descriptors only and must not be read as current verification.

### 16.1 Latest local verification record (2026-08-27)

The explicit execution-cell contract now also has an opt-in local process
boundary: `ProcessExecutionCell` runs a picklable adapter in a child process and
the regression suite proves that timeout and task cancellation terminate that
child. This is deliberately scoped as local evidence; it does not claim
descendant cleanup, OS-level resource enforcement, provider-side-effect
reversal, or hosted multi-process single-writer authority.

Configured replay directories additionally use `ReplayWriterLease`, an advisory
descriptor-held OS lock. A spawned-process regression proves contention is
rejected and the next writer can acquire the directory after release; this is
local writer serialization only.

Benchmark validation can additionally run through an operator-owned isolated
subprocess. The input/result envelopes bind protocol, raw-trial and metric
hashes; the benchmark validator invocation is admitted and settled as a typed
`benchmark.hidden_validator` compute cell. Local tests prove success, tampered
input rejection and timeout rejection; this remains process separation, not
hosted scorer secrecy.

Trusted execution-cell manifests are now sealed after capture; a late
registration attempt is rejected so the runtime cannot execute a binding that
is absent from the replay-bound manifest. Lookup and manifest generation use an
immutable sealed snapshot, so post-seal mutation of the construction table
cannot replace the runner or alter replay identity. This is local configuration
immutability, not a hosted authority or protection against mutation of the
runner object's own external side effects.

Browser observer projections are now scanned in memory for the same bounded
prompt-injection markers used by search ingestion. A match records
`browser_prompt_injection_detected`, a digest-bound security event and a
`REJECTED` observer settlement; the page/accessibility/network text is not
copied into the event ledger. This proves a local marker gate only: it is not a
complete hostile-content classifier and does not close DNS rebinding or OS
containment.

Compatibility RAG retrieval is now also inside the Lab boundary. The
`memory.search_past` read is admitted/settled through a `context_retrieval`
read-only cell before controller/provider calls; only a bounded context digest
and character count are persisted. Retrieved context containing a bounded
prompt-injection marker is rejected with hash-only security evidence. This
closes the previous pre-Lab implicit memory read for `Agent(..., lab=True)` but
does not prove hosted memory authority or semantic retrieval quality.

The following results were observed in the same uncommitted working tree; they
are repeatable local evidence, not a release attestation:

**M2 provider-route hardening (2026-08-31, current working tree):**
`LabApplication._run_admitted_gateway` now requires native-required adapters to
return a typed provider route whose ordered `attempted_providers` and selected
provider reconcile with the nested `provider.*` admission/settlement receipts.
An adapter that accepts the callback but never invokes it is settled
`REJECTED`, and the provider-side effect is not reclassified as successful.
The focused no-hook regression, provider retry regression and cancellation
regression pass; the complete Python gate
`.venv\\Scripts\\python.exe -m pytest tests core\\python\\tests.py -q -W error::DeprecationWarning`
passes **255 tests**. Ruff passes for the changed runtime/test files,
`scripts/document_consistency_gate.py` passes, and
`scripts/architecture_fitness.py` passes all 23 checks after recognizing the
current explicit bridge include patterns in `pyproject.toml`. A project-wide
Pyright run now passes with **0 errors, 0 warnings, 0 informations** after a
narrow JSON/native-boundary typing hardening in `aegis_cognition/lab.py`; no
diagnostic suppression or runtime-behavior change was introduced. Ruff remains
clean for the changed boundary. The guard is local fail-closed
evidence only; it does not prove provider SDK idempotency, process-level
containment, hosted writer authority or that an adapter did not perform an
untracked effect before returning. The rebuilt wheel/import record is retained
at `artifacts/local-runtime/provider-fence-settlement-20260901/provider_fence_settlement_packaging.json`
with wheel SHA-256
`9ed944de33159950f62755ae778e275eb5feec1783c50d433038dd80412ed65b`. The
native-required Lab retry guard also rejects adapter-owned `max_retries > 1`
before provider invocation; its focused regression is included in the 255-test
gate. This remains local fail-closed evidence only.

**M4 settlement-fence hardening (2026-09-01, current working tree):** the
Python reducer now binds gateway, generic-tool, experiment, research-program,
browser action, browser observation, skill, and cancellation settlements to
exactly one event-ledger admission. Gateway, generic-tool, experiment and
simulation attempts each require a finite positive local deadline and derive
a deterministic 64-hex idempotency key from mission/execution/input/policy
identity; both fields are retained in admission and settlement receipts, and
cooperative `asyncio.wait_for` expiry settles `TIMED_OUT`. Durable snapshot
restore applies the same semantic duplicate/orphan check. Duplicate admission
identities, unknown/stale admissions, mismatched input/policy hashes, and
duplicate settlements fail closed before another event is appended; the
projection event count remains unchanged on rejection. Focused adversarial
coverage and the complete Python gate pass **255 tests**. This closes the
locally observable duplicate/stale receipt and cooperative-deadline paths
only: external idempotency, opaque SDK retries, user-runner retries,
non-cooperative/synchronous work, timeout ambiguity after an external effect,
and hosted multi-writer authority remain unverified.

**P4 adaptive-progress hardening (2026-09-01, current source):** the
bounded controller now records an explicit unweighted progress-potential tuple
for each decision. The tuple covers missing evidence planes, clean-replication
and uncertainty gaps, and blocker count without inventing calibrated weights.
Plateau detection hashes the immutable evidence content (including record
identity and payload fields), rather than only collection sizes, so a same-size
replacement or tampered projection cannot be mistaken for progress. Regression
coverage proves potential emission and content-sensitive plateau reset. This is
local controller evidence only; P4 still lacks calibrated information-gain
selection, full falsifier/blind-replicator roles and multi-agent coordination
efficiency evidence.

The prior v63 release-candidate paragraph is historical and is not replayable
from the current machine: the referenced directory and all four records are
absent, so the documented v63 wheel hash
`e2ae96f1c7886e3f2f7a1e522cb5e2d783b90df96db8caed2ef44eca2afd41bf` is not
current executable evidence. The design-closure collector therefore classifies
the v63 subject as `V63_ARTIFACT_MISSING`; those historical `PROVEN` labels must
not be used for current release promotion. The justified replacement wheel
built from this working tree is recorded separately at
`artifacts/local-runtime/provider-fence-settlement-20260901/provider_fence_settlement_packaging.json`
with SHA-256
`9ed944de33159950f62755ae778e275eb5feec1783c50d433038dd80412ed65b`.

| Gate | Result | Scope qualification |
|---|---|---|
| Rust unit/integration | `cargo test --workspace --no-default-features --quiet -- --test-threads=1` passed; core crate specifically: 439 unit tests + 2 integration tests, 0 doctests | local checkout, `--no-default-features`; sequential test execution is required because two legacy Windows tests share process-global temporary resources |
| Rust quality | `cargo fmt --check`, Clippy `-D warnings` passed | compile/static gates, not hosted platform proof |
| Python regression | 255 tests passed with `-W error::DeprecationWarning`; no warnings | exact gate: `.venv\\Scripts\\python.exe -m pytest tests core\\python\\tests.py -q -W error::DeprecationWarning`; local `.venv`, no external provider/browser evidence |
| Python static quality | Pyright 0 errors/0 warnings for `aegis_cognition`, `core/python/browser_playwright_runtime.py`, `tests`, `scripts/document_consistency_gate.py`, `scripts/release_controller_action_smoke.py` and `scripts/release_recovery_smoke.py`; Ruff clean for `core/python`, `tests`, and all `scripts` | direct-file entrypoints retain their bootstrap behavior through a shared runtime-dependency loader; no lint suppression was added; `pytest-asyncio 1.4.0` also passes the warning-as-error regression |
| Constitution audit | 180 passed, 0 failed | structural/presence audit |
| Architecture fitness | 23 passed, overall `true` | architectural consistency checks |
| Document consistency | generated Lab status view, scoped metadata inventory and repository Markdown-link lint passed; 3 regression tests passed | local checkout; external links and artifact research corpus are intentionally outside this gate |
| Lab replay | typed search + browser lifecycle + literal/obfuscated unsafe-address rejection + hostname-resolution private-address rejection + browser prompt-injection hash-only rejection + browser launcher pre-admission + unit/uncertainty/replication and electrical signal checks passed; public `Lab` under `PROD` policy completed a native-authority E2E with `event_chain_authority=rust_native_verified`, 13 events, one archive entry, native archive verification `True`, durable snapshot sidecar, and recovered event stream; source regression proves experiment/simulation/generic-tool/gateway retry ordering plus cooperative and swallowed in-flight tool/research/gateway cancellation and typed controller search/browser/experiment/simulation action execution/rejection; sealed execution-cell snapshot resists post-seal construction-table mutation; compatibility `memory.search_past` context retrieval is admitted/settled as a hash-only read-only cell and bounded prompt-injection context is rejected; dedicated post-completion effect cell rejects missing required persistence instead of returning silently; the prior v63 packaged controller/recovery/rollback claims are historical only because the referenced v63 artifacts are missing; current source regression and the replacement-wheel import record remain local-only evidence; execution-cell manifest is persisted, strict benchmark-validator registry mismatch is fail-closed, and registry mismatch tests are fail-closed; strict archive verifier preference is covered by regression; opt-in `ProcessExecutionCell` timeout/cancellation termination, `ReplayWriterLease` process contention and isolated benchmark-validator protocol rejection are proven by local regression; bounded electrical energy remains `1.0 J` with `MEASURED_INPUTS_ONLY`; native `LabController` FFI smoke rejected invalid epoch/finalizing admission, reopened only through critical-gap path, accounted budget and restored a replayable snapshot, and accepts the post-completion synthesis review record | local wheel build with pinned maturin 1.14.1 via `uvx`; v63 artifact replay, cross-platform hosted packaging parity, typed-record/budget full-lifecycle delegation and hosted single-writer service proof remain open |

The latest v28 package was built from the current working tree with pinned
maturin `1.14.1`; its SHA-256 is
`9523d6605834b8ca5ba415d2c16e47b970124b5112902a5867fe3d8a98898a01`.
The retained install record is
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v28\install-smoke.json`;
the reproducible typed-controller action record is
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v28\controller-action-smoke.json`;
the rollback record is
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v28\rollback-v27-v28.json`.
Clean-install outside the checkout passed on Windows 11/CPython 3.14.0. A
second outside-checkout smoke used the packaged extension with
`lab_require_native_authority=True`: the model emitted one typed action plan,
the native chain executed a `search_program` and a bound `browser_action`,
captured one source, settled both action records, and returned
`event_chain_authority=rust_native_verified`; a v28 outside-checkout smoke
also executed one bundled response containing search, browser, experiment and
simulation actions, kept the bound browser session, and produced two distinct
observations with no blockers. The
v27→v28→v27 rollback drill is `PROVEN`; earlier rollback drills are retained
as historical evidence. This is stronger local
packaging/action evidence, not hosted or production proof.

The v37 package was built after provider-attempt fencing, compatibility-factory
handshake, crash-prefix reconciliation, strict persisted-manifest matching and
the post-completion memory-index fence were added. Its clean-install record is
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v37\install-smoke.json`;
the packaged controller-action record is
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v37\controller-action-smoke.json`;
and the rollback record is
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v37\rollback-v36-v37.json`.
The v37 controller smoke additionally proves four provider-attempt settlements
(`REJECTED`, `SUCCESS`, `REJECTED`, `SUCCESS`) across the primary/fallback route
and exactly one `memory.index_session` receipt with `SUCCESS`, while preserving
the same native authority, replay archive and typed action invariants. The v36
records remain valid immediate-predecessor local evidence; v35/v34/v33 and
earlier records remain historical local evidence.

The v38 package was built after the all-lane crash-prefix reconciliation and
blocked-state native admission were added. Its SHA-256 is
`822268bdc6f0b0b5be3c6d729de5b6a1fda4a8f988d0be17ac929f5ae8bcdc04`; the
clean-install, controller-action and rollback records are
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v38\install-smoke.json`,
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v38\controller-action-smoke.json`
and
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v38\rollback-v37-v38.json`.
All three are `PROVEN` on Windows 11/CPython 3.14.0; the controller scenario
retains the previous native/provider/browser invariants while the source
regression adds mixed-lane recovery evidence.

The v39 package was rebuilt after the cancellation fence was added to every
high-level edge invocation. Its SHA-256 is
`439f4e6224158c85853822b59a5b7113acfea29a92fb127834531a8483635461`; clean
install, controller-action, recovery and v38→v39→v38 rollback records are
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v39\install-smoke.json`,
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v39\controller-action-smoke.json`,
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v39\recovery-smoke.json`
and
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v39\rollback-v38-v39.json`.
The recovery record proves native authority, five recovered mixed-lane
admissions, zero open admissions and five `REJECTED` settlements with
`UNKNOWN_SIDE_EFFECT`.

The v40 package then superseded v39 as the local release-candidate artifact at
that stage, after the explicit-registry strict-allowlist and
`ProcessExecutionCell` changes; v44 was a later superseding artifact before v46.
Its SHA-256 is
`5c182bfef407fb25c5a08a30304a344e4db34d821337b149f2b98476c0a73952`; clean
install, typed-controller, mixed-lane recovery and v39→v40→v39 rollback records
are
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v40\install-smoke.json`,
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v40\controller-action-smoke.json`,
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v40\recovery-smoke.json`
and
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v40\rollback-v39-v40.json`.
All four are `PROVEN` on Windows 11/CPython 3.14.0; the controller scenario
retains native authority, default replay archive, four fenced provider attempts,
one successful memory-index receipt and no blockers, while recovery settles all
five open admissions as `REJECTED`/`UNKNOWN_SIDE_EFFECT`.

The tracked evidence-consistency gate was also run against
`docs/architecture/evidence/current.json` and correctly returned `REJECTED`:
the registry/Markdown/policy inventory now has parity, while the tracked
template still contains `CHECKOUT_HEAD`/placeholder final-SHA values. This is
the expected fail-closed signal for final-SHA evidence, not a reason to rewrite
the manifest by hand.

An earlier local regression passed 199/199 with a `pytest-asyncio`
deprecation warning (`asyncio.get_event_loop_policy`). That historical warning
was removed by upgrading the dev lock to `pytest-asyncio 1.4.0`; the current
201-test suite also passes with `-W error::DeprecationWarning`, including the
isolated validator subprocess protocol.

The added local regressions cover per-candidate provider fallback fencing,
provider-attempt cancellation settlement before abort, replay reconciliation
of open admissions across research/experiment/browser/skill/tool lanes as
`REJECTED`/`UNKNOWN_SIDE_EFFECT` with an explicit blocked dossier, preference
for strict archive verification bound to the sealed manifest identity, and
compatibility post-completion memory indexing as an admitted/settled effect
whose required-policy failure blocks the dossier.
The native strict path rejects a recoverable prefix whose manifest hash no
longer equals the persisted completion manifest.

`LOCAL-PROVEN` chỉ có nghĩa là chạy trong checkout này ở commit/working-tree
scope hiện tại. Nó không đóng các external blockers trong Section 3/B4 và không
cho phép claim `production-ready`.

**Hosted CI observation (2026-09-02, commit `b6ac809bf782e34f3e88b9108b509ae1a8536d90`):**
the push-triggered GitHub Actions runs `33585530719` (`CI`) and `33585530740`
(`aegis-plugins`) were recorded as `failure` within 3–5 seconds. The CI run
created 13 matrix jobs; every job had `steps=0`, `runner_id=0`, an empty
`runner_name`, no check-run output, and `0 ms` billable time. The repository
Actions API still reports `enabled=true` and `allowed_actions=all`, so this
observation does not identify a source-level failure or prove that any test
step executed. A successful historical CI run (`32781549128`, commit
`0ebef3e382fba167b76674b39c66f6301619087f`) had allocated runner IDs and
6–18 executed steps, which is the decisive contrast. The hosted gate is
therefore `NOT VERIFIED`/`BLOCKED_EXTERNAL` pending a runner-backed rerun or
an owner-side GitHub Actions billing/permission/service diagnosis; workflow
files were intentionally not changed to mask a pre-step infrastructure
failure. Local verification remains valid only for the local scope recorded
above.

**Hosted CI rerun confirmation (2026-09-02):** rerunning the failed CI attempt
with `gh run rerun 33587211175 --failed` produced attempt `2` on the same
commit. The rerun again ended in `failure` before any step: all 13 jobs had
`steps=0`, `runner_id=0`, empty runner names, and zero billable milliseconds.
This repeat rules out treating the first result as a transient source-test
failure; hosted CI remains `NOT VERIFIED` until a runner-backed attempt is
available.

**Hosted CI post-push confirmation (2026-09-02, source SHA observed by the
push-triggered workflows
`fee817dc6cbd2e977cf3cd2fc0d591c4a0919b44`; the subsequent documentation-only
follow-up is `976ceebb2198df289dcf82bc05286ed9fb2249c5`):** the runs
`33587436331` (`CI`) and `33587436193` (`aegis-plugins`) reproduced the same
pre-run failure. CI created all 13 matrix jobs, but every job reported
`steps=0`, `runner_id=0`, an empty runner name, and `0 ms` billable duration;
the plugin workflow's single Windows job had the same zero-step signature.
No source test, wheel build, or release step therefore executed for the final
SHA. This is retained as additional external-blocker evidence, not as a
workflow or source failure diagnosis; hosted verification remains
`NOT VERIFIED`/`BLOCKED_EXTERNAL`.

**Hosted CI post-fix confirmation (2026-09-02, source SHA
`e8fbf0b88c8d68e6bc50e2398ce4dcaff98aeb5e`):** the workflows triggered by the
benchmark-validator receipt fix (`33588332942` `CI` and `33588332930`
`aegis-plugins`) also completed as failures before runner allocation. The CI
matrix again had 13/13 jobs with `steps=0`, `runner_id=0`, empty runner names,
and `0 ms` duration; the plugin workflow's one job had the same signature.
This confirms that the new source was not exercised by hosted tests; local
456-test, Ruff, Pyright, architecture-fitness and constitution evidence is
therefore the only current implementation evidence, while hosted verification
remains `NOT VERIFIED`/`BLOCKED_EXTERNAL`.

### 16.2 Blocker disposition sau vòng thực thi hiện tại

**Execution delta (2026-08-27):** provider fallback attempts, all-lane
crash-prefix reconciliation, strict persisted-manifest matching, native-required
gateway handshake, the compatibility Agent's post-completion memory-index write
and pre-controller compatibility RAG memory retrieval, plus the explicit
execution-cell registry, are now locally implemented and
regression-tested. The latter is an explicit
`memory.search_past` read and `memory.index_session` admission/settlement; a rejected optional DEV cache
write remains visible without blocking scientific completion, while PROD or an
explicit required policy fails closed. A constitution audit integration gap was
also closed by exposing the canonical production closure helpers through
`run_checks.py` (177/177 audit checks now pass). The blocker table remains
conservative: the explicit provider-attempt lane, post-completion
compatibility write fence, execution-cell registry and local archive identity
check moved from unbounded compatibility behavior to a native-fenced local
proof; core/python, tests and
all scripts now pass the strict Ruff gate;
31 earlier script findings are fixed; the remaining 42 direct-file E402 bootstrap
findings were closed by moving runtime imports behind explicit loaders while
preserving direct-file execution (the direct `smoke_check.py` path was rerun).
The development lock now requires `pytest-asyncio>=1,<2`; after resolving
`1.4.0`, the complete Python suite passes with
`-W error::DeprecationWarning`, so the former local warning is no longer waived.
The CI and release workflows now run the same warning-as-error flag instead of
allowing a green suite to hide a deprecation regression.
Hosted, cross-platform and non-cooperative guarantees remain open. An explicit
`lab_execution_cells` mapping is now a strict allowlist: missing search/browser,
skill or required post-completion cells cannot fall back to legacy callables,
and the new regression cases verify that those adapters/effects are not invoked
or silently skipped.
The compatibility `Agent` path also requires native authority by default for
`trust_level="PROD"`; a missing extension fails before the first model/provider
side effect. A `LabApplication` now rejects a second non-terminal run on the
same instance, preventing shared gateway/provider context from being used as an
implicit concurrent writer; this is only an in-process guard, not hosted
multi-process single-writer proof.
Configured replay directories now add `ReplayWriterLease`, an advisory
descriptor-held OS lock; a local cross-process contention regression proves that
two processes cannot append to the same directory concurrently. It narrows the
local replay-writer race, but does not cover hidden side effects, a hosted lock
service, or the complete Agent lifecycle.
The benchmark path now also supports an operator-owned isolated validator
subprocess. Its versioned JSON envelope binds the verdict to the protocol, raw
trial and metric input hashes; non-zero exit, timeout, malformed output, hash
mismatch and oversized output all fail closed. This is local process separation,
not hosted validator secrecy or contamination-resistant benchmark evidence.
Browser admission also rejects literal unsafe IPv4/IPv6 destinations before a
session action; research fetch now rejects literal/obfuscated private IPs before
connection and performs a fail-closed hostname-resolution preflight. DNS
rebinding and OS-level browser containment remain explicitly external blockers.
The former pre-Lab RAG read now occurs
inside the `context_retrieval` cell and is bounded/hash-only; its marker-gated
rejection is local evidence, not hosted memory authority or semantic quality.

| ID | Phạm vi còn mở | Vì sao chưa đóng | Điều kiện đóng bắt buộc | Trạng thái hiện tại |
|---|---|---|---|---|
| LAB-AUTH-001 | Native single-writer chưa bao phủ toàn bộ Agent/tool lifecycle | Browser/research/skill/experiment, explicit generic tool calls, typed controller search/browser/experiment/simulation action plans, gateway/controller model calls, compatibility Agent `memory.search_past` context retrieval and post-completion memory index hiện đã pre-admit + settle native; generic tool có lease/effect/role/schema/stop-rule, retry fence; các explicit lanes có cooperative `CANCELLED` settlement trước abort; high-level `_call_fenced` còn chặn adapter nuốt cancellation; launcher-owned browser session hiện giữ bound suốt controller loop rồi cleanup; `Agent(..., lab=True)` trong packaged native lane hiện tự ghi durable replay archive; strict archive verifier đối chiếu persisted `manifest_hash` nên tail thiếu không được coi là completion; native-required gateway có post-construction callback handshake và route/receipt reconciliation, nên factory nhận nhưng bỏ qua fence hoặc trả route không có nested provider receipts đều bị fail-closed; explicit edge runners giờ được gom vào `ExecutionCellRegistry`, kiểm tra `cell_id`/capability/effect/trust`, seal sau khi chụp manifest event và lưu canonical inventory/hash vào native event ledger cùng snapshot/dossier projection; sealed snapshot lookup không bị thay thế bởi mutation hậu cấu hình; event-log-driven recovery hiện settle được mọi admission mở của research/experiment/browser/skill/tool/context retrieval thành `REJECTED` với `UNKNOWN_SIDE_EFFECT` và chuyển dossier sang `blocked`. Tuy nhiên planner/lease của side effect phát sinh ngầm, adapter ngoài registry contract, process-level non-cooperative interruption, retry policy xuyên adapter và hosted multi-process single-writer chưa được native controller sở hữu/chứng minh; route reconciliation không thể hoàn tác effect đã xảy ra trước khi adapter trả kết quả | Một seed Agent lifecycle chạy qua native controller duy nhất; replay fresh process cho cùng prefix/verdict; duplicate, timeout, cancellation, in-flight interruption (kể cả adapter nuốt cancellation và process-level non-cooperative) và partial failure đều fail-closed; proof không chỉ là hash-chain của Python projection; packaged generic-tool + gateway smoke, typed controller action replay, launcher browser continuity, experiment/simulation action replay, context-retrieval and completion-effect rejection/required-policy tests, mixed-lane recovery replay, registry manifest replay and hosted writer evidence | `OPEN_LOCAL` |
| LAB-BROWSER-002 | Browser process/OS isolation và continuous hostile-content boundary | Local `BrowserCell` là capability/role boundary và nay chặn literal cùng legacy decimal/octal/hex IPv4 private/loopback/link-local/multicast/reserved/unspecified IP; managed Playwright có resolution preflight và route-time kiểm tra mọi địa chỉ phân giải, nhưng chưa có DNS rebinding race proof, crash injection, kernel/job/cgroup enforcement và cross-domain hosted witness | Hosted Linux/Windows/macOS runs với process crash, DNS rebinding/egress/private-IP/redirect/prompt-injection corpus, retained raw witness và independent verifier | `OPEN_EXTERNAL` |
| LAB-RESEARCH-003 | Live research quality | Search IR/fetch/citation/contradiction adapter đã bounded, nhưng semantic extraction, freshness quality, provider drift và independent contradiction retrieval chưa có evidence ngoài fixture/live example.com | Real provider snapshots với timestamps, provenance clusters, exact spans, contradiction recall/precision protocol, redacted raw responses và replay | `OPEN_EXTERNAL` |
| LAB-PHYS-004 | Validated scientific simulation/hardware energy | Euler/RK4, units, Nyquist, calibration và residual gates mới là bounded contracts; chưa chứng minh PDE/stiffness/circuit solver, sensor model hay calibrated hardware energy | Multi-problem solver validation, convergence/discrepancy report, blind rerun, instrument calibration/uncertainty and independent replication; no promotion from `MEASURED_INPUTS_ONLY` without witness | `OPEN_EXTERNAL` |
| LAB-BENCH-005 | Benchmark generalization | Benchmark V2 giữ raw/failed trials và hiện có operator-owned isolated validator subprocess với protocol/input hash binding, nhưng chưa có hosted hidden scorer/secrecy, contamination-resistant corpus và independent reproduction | Sealed protocol/hash, hidden validator outside candidate process and outside candidate-controlled infrastructure, answer-lookup adversary, multiple runs with dispersion/outliers, raw artifact and independent reproduction | `OPEN_EXTERNAL` |
| LAB-RELEASE-006 | Final-SHA/release evidence | Tracked `current.json` còn `CHECKOUT_HEAD`; generated temp artifact pass chỉ chứng minh working tree; signed attestation and hosted CI IDs absent | Final commit SHA, generated registry/Markdown/policy parity, signed subject hash, hosted CI/release/deployment run IDs and rollback record | `BLOCKED_EXTERNAL` |
| LAB-OPS-007 | Operational warning and platform parity | The Python suite is warning-free under `-W error::DeprecationWarning` after upgrading `pytest-asyncio` to `1.4.0`; corrected wheel v63 clean-install, bundled packaged typed controller search/browser/experiment/simulation smoke with default replay archive and provider fallback settlement, packaged mixed-lane recovery smoke, plus v62→v63→v62 rollback are proven on Windows/CPython 3.14; a Windows Job Object live probe proves child assignment, active-process quota rejection, termination/deadline cancellation and configured memory limits, but memory-pressure kill was not observed; no cross-platform install/soak or hosted telemetry/restore | Clean Linux/Windows/macOS wheel import/runtime, multi-run soak, structured metrics/log/trace, restore evidence and dependency-drift check; memory enforcement must remain `NOT VERIFIED` until a deterministic pressure-kill witness exists | `OPEN_EXTERNAL` |
| LAB-DOC-008 | Historical authority cleanup | Canonical plan, registry and document inventory are aligned; ADR-012 is a compatibility stub superseded by ADR-015; generated status view and metadata/link lint now pass locally | Repeat `scripts/document_consistency_gate.py` on final SHA and preserve ADR-012 compatibility note; fail closed if generated view or scoped metadata/link parity drifts | `LOCAL-PROVEN` |
| LAB-QUALITY-009 | Repository-wide Python lint closure | Direct-file bootstrap previously delayed imports and produced 42 E402 findings. The affected entrypoints now load their runtime dependencies through explicit loaders/local imports, preserving direct invocation without `noqa` suppression; the full `ruff check scripts` gate is clean | Keep the loader path covered by direct-entrypoint smoke plus module-import tests; fail closed if a future script adds an import after executable bootstrap code | `LOCAL-PROVEN` |

### 16.3 Giải thích blocker: nguyên nhân, tác hại và cách đóng

Bảng trên là trạng thái máy đọc được; phần này là diễn giải vận hành để không
nhầm giữa một local proof và một guarantee rộng hơn. Một blocker chỉ được đổi
sang `CLOSED` khi điều kiện ở cột **Điều kiện đóng bắt buộc** được ghi lại bằng
artifact có hash, đúng scope và có verifier độc lập tương ứng.

1. **LAB-AUTH-001 — authority chưa phủ mọi side effect.** Nguyên nhân gốc là
   một planner hoặc adapter có thể phát sinh network, browser, filesystem,
   process hoặc provider effect mà không đi qua admission/lease của native
   writer. Khi đó replay vẫn có hash-chain hợp lệ nhưng không chứng minh rằng
   effect đã được settle đúng một lần; các failure nguy hiểm là false success,
   duplicate side effect, retry sau timeout mơ hồ, hoặc crash giữa admission và
   receipt. Local mitigation hiện tại là native admission cho mọi lane explicit,
   registry hash-bound, retry/cancellation fence, crash-prefix reconciliation
   thành `REJECTED`/`UNKNOWN_SIDE_EFFECT` và opt-in `ProcessExecutionCell` cho
   timeout/cancellation của child runner không hợp tác; đây là fail-closed,
   không phải proof rằng effect bên ngoài đã được hoàn tác hoặc descendants đã
   bị thu hồi. Để đóng cần route planner/lease ẩn và adapter ngoài registry vào
   cùng writer, fresh-process replay, duplicate/timeout/cancellation/process-kill
    tests và hosted multi-process single-writer evidence. `ReplayWriterLease` đã
    bổ sung serialization cho replay directory giữa các local process bằng
    descriptor-held OS lock, nhưng không sở hữu side effect bên ngoài directory
    và không thay thế hosted single-writer service.

2. **LAB-BROWSER-002 — capability boundary không đồng nghĩa OS isolation.**
   `BrowserCell` local kiểm tra capability, allowlist, witness và nay loại literal
   cùng legacy decimal/octal/hex IPv4 private/loopback/link-local/multicast/
   reserved/unspecified IP trước admission;
   `ProcessExecutionCell` chỉ chứng minh được kill child runner opt-in, chưa chứng
   minh browser process bị giới hạn bằng Windows Job Object, Linux cgroup/namespace
   hoặc macOS control tương ứng. Managed Playwright hiện resolve hostname trước
   initial navigation và trước mỗi intercepted route, từ chối mọi address private/
   loopback/etc.; điều này giảm SSRF nhưng chưa chặn được race DNS rebinding giữa
   lần resolve và kết nối. Browser observer projections hiện quét marker
   prompt-injection bounded trong memory và settle
   `REJECTED` bằng security event hash-only, nhưng marker list không phải classifier
   đầy đủ. Nếu bỏ qua, một trang độc hại vẫn có thể SSRF/private-IP, redirect vượt
   policy, dùng biến thể prompt-injection chưa biết, giữ process sau crash hoặc làm
   lộ dữ liệu/credential. Interim posture là xem mọi page/network content là
   untrusted data, không nâng claim thành isolated/secure; closure cần crash
   injection, DNS/egress/redirect/prompt-injection corpus, process/resource
   enforcement trên Linux/Windows/macOS và independent verifier.

3. **LAB-RESEARCH-003 — bounded search plumbing chưa chứng minh chất lượng tri
   thức trực tiếp.** Fixture, `example.com` hoặc một adapter trả kết quả đúng
   schema không chứng minh freshness, semantic extraction, provider drift,
   citation span hay contradiction recall trên nguồn thật. Nếu đóng sớm, Lab có
   thể biến kết quả cũ/thiếu ngữ cảnh thành `VERIFIED` và làm mất dấu bất đồng.
   Interim posture là giữ provenance, timestamp, exact span và epistemic status;
   chỉ cho phép `SOURCE_BACKED`/`INFERRED` khi đủ witness. Closure cần snapshot
   provider thật, redacted raw response, provenance clusters, contradiction
   protocol có precision/recall và replay độc lập.

4. **LAB-PHYS-004 — primitive toán học bị giới hạn không phải calibrated science.**
   Unit algebra, Euler/RK4, Nyquist, residual và `1.0 J` hiện chứng minh contract
   bounded với input đã biết; chúng chưa chứng minh PDE/stiff solver, circuit
   model, sensor bias, calibration hoặc năng lượng phần cứng. Overclaim ở đây có
   thể dẫn tới quyết định kỹ thuật sai dù test số học vẫn xanh. Interim posture
   giữ `MEASURED_INPUTS_ONLY`/`SIMULATED`, chặn promotion thành validated physical
   result. Closure cần multi-problem convergence/discrepancy report, blind
   rerun, instrument calibration + uncertainty và independent replication.

5. **LAB-BENCH-005 — local benchmark không đủ chống contamination.** Validator
   callback trong cùng process không chứng minh candidate không thể sửa validator;
   protocol subprocess mới chạy command bằng argv, bind verdict vào protocol/raw
   trial/input hash và fail-closed trước timeout, output bẩn hoặc schema sai. Tuy
   vậy command local vẫn do operator cùng harness cung cấp, nên chưa chứng minh
   validator bí mật hay corpus không bị lookup. Nếu đóng sớm, số điểm/latency đẹp
   có thể chỉ phản ánh overfit và không tổng quát. Interim posture là lưu raw
   trials kể cả failure, gắn benchmark là exploratory và không claim improvement
   từ một run. Closure cần sealed hash protocol, validator ngoài candidate-
   controlled infrastructure, answer-lookup adversary, nhiều run có
   dispersion/outlier và independent reproduction.

6. **LAB-RELEASE-006 — provenance cuối chưa bất biến.** `CHECKOUT_HEAD` trong
   tracked `current.json` là placeholder, còn working-tree artifact có thể thay
   đổi sau lúc test; vì vậy wheel hash, registry, generated docs và evidence
   manifest chưa cùng một final commit. Sửa JSON bằng tay chỉ làm mất tính trung
   thực của gate. Interim posture là release-blocked dù local wheel/rollback đã
   pass. Closure cần final commit SHA, regenerated parity, signed subject hash,
   hosted CI/release/deployment IDs và rollback record trỏ đúng artifact.

7. **LAB-OPS-007 — một Windows run không phải platform/operations proof.**
   v63 clean-install/controller/recovery/rollback chứng minh một CPython 3.14 /
    Windows lane; nó không bao phủ Linux/macOS, soak, telemetry, restore, quota
    hay dependency drift. Local `pytest-asyncio` warning đã được loại bỏ bằng
    version `1.4.0` và suite warning-as-error đã pass; interim posture vẫn là
    không gọi production-ready. Closure cần cross-platform install/runtime,
    multi-run soak, structured logs/metrics/traces và restore test.

8. **LAB-DOC-008 và LAB-QUALITY-009 — local đã đóng có điều kiện.** Hai hàng này
   không còn blocker trong checkout hiện tại vì document gate, metadata/link
   parity và full Ruff gate đều pass. Chúng vẫn phải chạy lại trên final SHA;
   nếu generated view, ADR compatibility note hoặc direct-entrypoint loader drift
   thì trạng thái phải quay về mở, không giữ nguyên bằng lịch sử cũ.

Các blocker external không được “đóng” bằng mock, loopback, fixture, local wheel,
hay sửa status file. Những artifact đó chỉ là evidence cho contract local và phải
được ghi đúng scope như ở Section 16.1.

Các mục `OPEN_LOCAL` có thể tiếp tục thực thi trong checkout nhưng không được
gắn nhãn release-closed. Các mục `OPEN_EXTERNAL`/`BLOCKED_EXTERNAL` cần host,
provider, hardware, repository owner hoặc release signer ngoài quyền của vòng
thực thi này; mock, loopback và local wheel không thay thế được evidence đó.

Dependency còn lại để tiếp tục P1–P5:

1. hoàn thiện replay archive thành production lifecycle cho mọi compatibility
   lane (public facade và `Agent(..., lab=True)` hiện tự cấu hình durable path
   khi native authority có mặt; explicit opt-out vẫn được hỗ trợ).
   Cross-language hash, snapshot sidecar và local longest-valid-prefix recovery
   đã có; cross-platform hosted packaging parity, crash injection và
   final-prefix equivalence trên hosted runner còn mở;
2. hoàn thiện browser/search adapters với live provider snapshots, freshness,
   semantic extraction, independent contradiction retrieval và prompt-injection
   adversarial suite (typed program, HTTPS/allowlist/quota, launcher lease,
   citation spans và local marker gate đã có);
3. hoàn thiện simulation cell với calibrated solver, discrepancy model,
   dimensional analysis đầy đủ và independent blind reruns (scalar
   unit/uncertainty/replication gates đã có);
4. xuất benchmark raw artifacts + hidden validator trên hosted runner và chứng
   minh reproduction độc lập;
5. giữ `pytest-asyncio>=1,<2` và chạy biến thể warning-as-error trong CI;
6. cập nhật evidence registry sau mỗi gate, không sửa status bằng tay.

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

## 18. Final local closure report (2026-08-27)

Phần này là bản chốt duy nhất cho vòng thực thi hiện tại. Nó không thay thế
`current.json`, không tạo một kế hoạch thứ hai và không biến artifact tạm thành
release attestation. `AEGIS_LAB_RUNTIME_MASTER_PLAN.md` là canonical plan duy
nhất; `docs/ENGINEERING_CONSTITUTION.md` là policy; `current.json`,
`not_verified_registry.json` và `deployment_policy.json` là machine authority.

### 18.1 Phạm vi và trạng thái bất biến

- **Checkout HEAD:** `f9645caf6d17cee2023d52183990ffcf8317e456`.
- **Working tree:** dirty/uncommitted; các thay đổi hiện hữu của người dùng
  được bảo toàn, chưa có commit mới và chưa có signed attestation.
- **Execution scope:** local working tree trên Windows 11,
  CPython `3.14.0`; đây không phải hosted Tier-1 release proof.
- **Current package:** replacement wheel được build bằng `maturin==1.14.1`
  qua `uvx`; SHA-256
  `9ed944de33159950f62755ae778e275eb5feec1783c50d433038dd80412ed65b`.
- **Current artifact record:**
  `artifacts/local-runtime/provider-fence-settlement-20260901/provider_fence_settlement_packaging.json`,
  cùng `docs/architecture/design-closure/packaging_truth.json`; root, core
  bridge và combined-runtime probes đều là local PASS.
- **Historical v63 records:**
  `C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v63\install-smoke-v63.json`,
  `controller-smoke-v63.json`, `recovery-smoke-v63.json` và
  `rollback-v62-v63.json` hiện đều absent; các claim/status của chúng không
  replayable và không được dùng làm current evidence.
- **Evidence class:** mọi kết luận dưới đây ghi rõ `LOCAL-PROVEN`,
  `SOURCE-BACKED`, `VIRTUALIZED-PROVEN`, `OPEN_EXTERNAL` hoặc
  `BLOCKED_EXTERNAL`; không dùng “production-ready”, “secure”, “scalable”,
  “exactly once” hay “validated physics” nếu chưa có evidence tương ứng.

### 18.2 Local blockers đã đóng hoặc đã có bằng chứng quyết định

| Mục | Kết luận | Bằng chứng quyết định | Giới hạn không được suy diễn |
|---|---|---|---|
| `LAB-DOC-008` | `LOCAL-PROVEN` | `scripts/document_consistency_gate.py` pass; canonical plan, inventory, ADR compatibility note và Markdown link/metadata parity không drift trong checkout | Chưa phải final-SHA attestation vì tracked template vẫn giữ `CHECKOUT_HEAD`, chưa có signer/hosted release IDs |
| `LAB-QUALITY-009` | `LOCAL-PROVEN` | full `ruff check scripts` pass; direct-entrypoint loader và warning-as-error regression pass | Chỉ áp dụng checkout hiện tại |
| Browser launch admission | `LOCAL-PROVEN` | browser launcher được ghi `browser_action_admitted` với `action_kind=launch` **trước** khi gọi launcher; success/rejection/cancellation đều settle; regression launcher order pass; v63 wheel smoke pass | Không chứng minh browser process bị kernel-isolated |
| Provider-attempt route reconciliation | `LOCAL-PROVEN` | native-required gateway bắt buộc route typed, selected provider và ordered attempted list khớp nested `provider.*` receipts; adapter cài nhưng bỏ qua hook bị settle `REJECTED`; regression retry + no-hook pass | Chỉ phát hiện sau khi adapter trả kết quả; không thu hồi được provider effect ngoài contract; provider HTTP/SDK/hosted evidence vẫn mở |
| Research egress preflight | `LOCAL-PROVEN` | literal/obfuscated private-IP, private DNS resolution và unsafe provider-candidate đều bị reject trước fetch; redirect/final-host check vẫn giữ | DNS rebinding race vẫn mở |
| Explicit execution cells | `LOCAL-PROVEN` | registry identity/capability/effect/trust, seal và snapshot hash; context retrieval, benchmark validator, post-completion effect và controller lanes đều fail-closed khi thiếu cell | Không kiểm soát code ngoài registry hoặc process descendant |
| Crash-prefix recovery | `LOCAL-PROVEN` | v63 recovery smoke restore và settle 5 mixed admissions thành `REJECTED`/`UNKNOWN_SIDE_EFFECT`, `open_after_recovery=0`, `event_chain_valid=true`, state `blocked` | Không biết side effect ngoài log đã xảy ra tới đâu |
| Local replay writer | `LOCAL-PROVEN` | `ReplayWriterLease` process contention regression; writer thứ hai bị reject, writer kế tiếp acquire sau release | Advisory local lock không phải hosted single-writer |
| Child timeout/cancellation | `LOCAL-PROVEN` | `ProcessExecutionCell` local child termination; Windows Job Object live probe chứng minh assignment, active-process limit, deadline cancellation và termination | Memory-pressure kill chưa quan sát được; descendants chưa chứng minh |

> **Current-evidence note (2026-09-01):** any `v63 wheel smoke` wording in the
> historical rows above refers to the prior recorded run only. The current
> replayable package evidence is the replacement-wheel/root-core-combined
> probe in Section 18.7 and `docs/architecture/design-closure/packaging_truth.json`;
> the missing v63 records cannot be replayed.

### 18.3 Blocker ledger cuối cùng và lý do không được “đóng giả”

| ID | Phân loại cuối | Blocker thực tế | Evidence/điều kiện đóng bắt buộc |
|---|---|---|---|
| `LAB-AUTH-001` | `OPEN_LOCAL` (residual authority; closure evidence is external) | Các lane explicit trong checkout đã pre-admit/settle; native-required gateway còn fail-closed khi route/provider receipts bị thiếu hoặc lệch. Phần còn lại là arbitrary adapter side effect, hidden planner/lease, non-cooperative process/descendant và multi-process authority ngoài một host; route reconciliation chỉ phát hiện sau khi adapter trả kết quả và không hoàn tác được effect ngoài contract | hostile adapter/process corpus, fresh-process replay, duplicate/timeout/cancellation/process-kill evidence và hosted single-writer service. Local explicit coverage không đủ để nâng thành universal authority |
| `LAB-BROWSER-002` | `OPEN_EXTERNAL` | DNS có thể đổi giữa preflight và connect; chưa có crash injection, egress firewall, browser process containment trên Linux/macOS/Windows và hostile cross-domain corpus | hosted Linux/Windows/macOS runs, DNS-rebinding race witness, OS/job/cgroup/namespace enforcement, prompt-injection corpus và independent verifier |
| `LAB-RESEARCH-003` | `OPEN_EXTERNAL` | Plumbing/fetch/citation/rejection đã có, nhưng semantic freshness, provider drift, contradiction recall/precision và quality của nguồn thật chưa được chứng minh | real provider snapshots, timestamps/provenance clusters, exact spans, contradiction protocol, redacted raw responses và independent replay |
| `LAB-PHYS-004` | `OPEN_EXTERNAL` | Euler/RK4, unit algebra, Nyquist, calibration/residual gates chỉ là bounded contracts; electrical output vẫn `MEASURED_INPUTS_ONLY`; chưa có PDE/stiff/circuit solver và calibrated hardware energy | multi-problem convergence/discrepancy, blind rerun, instrument calibration/uncertainty, hardware witness và independent replication |
| `LAB-BENCH-005` | `OPEN_EXTERNAL` | isolated validator subprocess chứng minh protocol/hash/timeout/output fail-closed, không chứng minh hidden scorer secrecy, contamination resistance hay generalization | sealed hidden validator ngoài candidate-controlled infrastructure, lookup adversary, multiple runs with dispersion/outliers và independent reproduction |
| `LAB-RELEASE-006` | `BLOCKED_EXTERNAL` | tracked `current.json` vẫn có `CHECKOUT_HEAD`; `main` đã có commit và đồng bộ `origin/main`, nhưng signer, hosted CI/release/deployment IDs và final-SHA-bound retained evidence vẫn thiếu. Gate đúng khi từ chối manifest này | regenerate parity artifacts from the final commit, signed subject hash, hosted IDs và rollback record trỏ đúng final artifact; tuyệt đối không sửa placeholder bằng tay |
| `LAB-OPS-007` | `OPEN_EXTERNAL` | v63 chỉ là một Windows/CPython lane. WSL2 không khởi động được (`HYPERV_NOT_INSTALLED`); Job Object memory-pressure kill cũng chưa observed; chưa có soak, telemetry, restore, Linux/macOS hoặc dependency-drift evidence | cross-platform package/runtime, deterministic pressure-kill witness, multi-run soak, structured metrics/logs/traces, restore drill và dependency-drift check |

**WSL/virtualization classification:** `wsl.exe` có mặt và default version là 2,
nhưng `wsl python3 --version` trả về
`Wsl/Service/CreateInstance/CreateVm/HCS/HCS_E_HYPERV_NOT_INSTALLED` (WSL2
không được hỗ trợ với cấu hình hiện tại).
Vì vậy không có `VIRTUALIZED-PROVEN` Linux evidence; việc này được giữ là
`OPEN_EXTERNAL`, không được ghi thành Linux pass.

**Windows resource probe classification:** artifact
`C:\Users\ADMIN\AppData\Local\Temp\aegis-windows-job-object-v62.json`
chỉ là `PARTIALLY LIVE VERIFIED`: limits/assignment/active quota/rejection/
termination/deadline và configured memory limits là true;
`memory_pressure_kill_observed` và `memory_pressure_process_terminated_by_job`
là false. Do đó không được dùng artifact này để claim memory enforcement hoàn
chỉnh hoặc production isolation.

### 18.4 Verification ledger thực sự đã chạy

Các lệnh/gates dưới đây đã chạy trong checkout hiện tại; “pass” chỉ áp dụng
đúng scope đã ghi:

1. Python exact regression:
   `.venv\Scripts\python.exe -m pytest tests core\python\tests.py -q
   -W error::DeprecationWarning` → **221 passed**.
2. Lab-focused regression:
   `.venv\Scripts\python.exe -m pytest tests/test_lab_runtime.py
   -W error::DeprecationWarning --disable-warnings -q` → **120 passed**.
3. Rust:
   `cargo test --workspace --no-default-features --quiet -- --test-threads=1`,
   core **437 unit + 2 integration**; `cargo fmt --check`; Clippy với
   `-D warnings` → pass.
4. Static/document gates: Ruff affected files and full scripts gate, Pyright
   selected/runtime closure, `document_consistency_gate.py`, Constitution audit
   (**177 passed, 0 failed**) và architecture fitness (**23 passed,
   `overall_ok=true`**) → pass ở scope tương ứng; `git diff --check` không có
   whitespace error.
5. Package evidence: v63 clean install, controller/action, recovery và
   v62→v63→v62 rollback đều `PROVEN`; controller scenario có
   `rust_native_verified`, no blockers, default replay archive,
   `benchmark_status=PASS`, provider statuses
   `[REJECTED,SUCCESS,REJECTED,SUCCESS]`, context-retrieval và
   post-completion memory-index receipts đều `SUCCESS`.
6. Negative/failure evidence: strict registry mismatch, unsafe literal/private
   DNS/provider candidate, prompt-injection marker, swallowed cancellation,
   timeout/cancellation child termination, isolated-validator tampering/timeout,
   concurrent replay writer và mixed-lane crash recovery đều fail-closed theo
   test/artifact tương ứng.

### 18.5 Stop condition và residual risk

Vòng local dừng ở trạng thái
`LOCAL_IMPLEMENTATION_COMPLETE_EXTERNAL_VERIFICATION_PENDING`: các gap còn
lại không còn được giải quyết trung thực bằng thêm mock, fixture, loopback,
wheel rebuild hoặc sửa status file. Chúng cần ít nhất một quyền/điều kiện ngoài
checkout: hosted runner và scorer, real provider, Linux/macOS/Windows OS
containment, hardware/instrument, signer/final commit hoặc multi-process
deployment. Tiếp tục viết code local ở thời điểm này có VOI quyết định thấp và
có nguy cơ làm lệch evidence; khi một dependency external xuất hiện, quay lại
đúng blocker ID và acceptance gate tương ứng.

Residual risk phải giữ nguyên trong mọi bản phát hành trước closure external:
DNS rebinding, unknown descendants, arbitrary adapter effects, provider semantic
quality/drift, calibrated physical validity, benchmark contamination/secrecy,
hosted writer/recovery/telemetry và final provenance. Không có claim nào ở đây
được nâng thành `PRODUCTION-READY`.

### 18.6 Evidence reconciliation erratum (2026-08-31)

Phần 18.1–18.4 ở trên là historical snapshot ngày 2026-08-27, không phải
current executable evidence. Bốn record được dẫn dưới
`C:\Users\ADMIN\AppData\Local\Temp\aegis-lab-native-wheel-v63\` và wheel có
SHA-256 `e2ae96f1c7886e3f2f7a1e522cb5e2d783b90df96db8caed2ef44eca2afd41bf`
không còn tồn tại trong máy hiện tại; vì vậy các claim v63 đó không replayable
và không được dùng để đóng release hoặc nâng trạng thái `PROVEN`.

Lần reconciliation đầu đã tạo một wheel thay thế ngoài repository vì
`ARTIFACT_MISSING`, tại
`C:\Users\ADMIN\AppData\Local\Temp\aegis-empirical-execution-20260831\wheel\aegis_cognition-0.1.0-cp314-cp314-win_amd64.whl`, SHA-256
`d9c5aeef1c948b186a0e02a46fc7b3b44d543035ea63470c39cb08c2e9d2a8ac`. Artifact
này đã được supersede bởi root wheel sạch hiện tại (SHA được ghi ở M1 record)
và không được chọn làm subject hiện hành. Cả hai chỉ chứng minh bounded local
install/import/runtime probe trên Windows/CPython 3.14.0; không tái lập v63
records, không phải v64, không phải signed release artifact và không xoá các
blocker `LAB-AUTH-001`, `LAB-RELEASE-006` hoặc external boundary trong
`external_boundary.json`.

Packaging truth tại thời điểm snapshot này là
`PACKAGE_OWNER_MIGRATION_PARTIAL_LOCAL`: root distribution
`aegis-cognition` vẫn là owner dự kiến của command `aegis`, và wheel bridge mới
không phát hành command trùng tên. Phân loại này được supersede bởi fresh
dependency-complete reprobe ngày 2026-09-01 ở mục 18.7 bên dưới; các lỗi
`blake3`/core CLI của những combined environment cũ chỉ còn là historical
evidence. Final-SHA provenance, signed release attestation và các external
boundary vẫn chưa đóng. Mọi phần mô tả “current v63”, “all four records
PROVEN” hoặc rollback v62→v63 trong snapshot trên phải được đọc như
historical evidence only.

### 18.7 Fresh local packaging-owner reconciliation (2026-09-01)

The final replacement wheel was rebuilt from the current worktree with pinned
`maturin 1.14.1` and is recorded at
`C:\Users\ADMIN\AppData\Local\Temp\aegis-empirical-execution-20260901\experiment-deadline-fence-v3-20260901\aegis_cognition-0.1.0-cp314-cp314-win_amd64.whl` with SHA-256
`9ed944de33159950f62755ae778e275eb5feec1783c50d433038dd80412ed65b`.
The clean root wheel environment imported `aegis_cognition` and the native
`aegis_nerve` extension with `repo_on_sys_path=false`, and `aegis --help`
exited 0. The independently installed core bridge wheel
`C:\Users\ADMIN\AppData\Local\Temp\aegis-empirical-execution-20260831\m1-core-dirty-hook-20260831\aegis_cognition_core_python-0.1.0-py3-none-any.whl`
also imported cleanly, passed the offline browser/batch/learning compatibility
probe, and exposed no console script. A new dependency-complete combined
environment at
`C:\Users\ADMIN\AppData\Local\Temp\aegis-empirical-execution-20260901\combined-runtime-reprobe-20260901`
selected exactly one `aegis=aegis_cognition.cli:main` entry point, imported the
root package/native extension from its own `site-packages`, and `aegis --help`
exited 0. The design-closure collector therefore records
`classification=PACKAGE_OWNER_LOCAL_PASS` with secondary classifications
`ROOT_CANONICAL_AEGIS`, `CORE_NO_DUPLICATE_AEGIS`, and
`CORE_BRIDGE_API_COMPATIBILITY_PROVEN_OFFLINE`.

This closes only the local package/CLI ownership question. It does not recreate
the missing v63 wheel/records, prove signed final-SHA provenance, or close
hosted, cross-platform, provider, browser, hardware, scientific, or
multi-machine boundaries; those remain explicit blockers in the current
design-closure artifacts.

## 19. Target architecture design và migration graph sau design-closure

### 19.0 Trạng thái, phạm vi và cơ sở quyết định

Phần này là **target design**, được viết sau design-closure pass gần nhất; epoch
được tham chiếu duy nhất từ `docs/architecture/design-closure/design_closure.json`
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

### 19.9 Gate matrix và trạng thái ban đầu

| Gate | Evidence bắt buộc | Trạng thái sau closure |
|---|---|---|
| Package owner | clean root/core/combined install, one `aegis` owner | `PASS_LOCAL` |
| Reducer authority | native single-writer toàn Agent lifecycle + replay | `OPEN_LOCAL` |
| Trust owner | one policy hash từ mission tới Rust/evidence | `OPEN_LOCAL` |
| Retry bound | cell-owned finite attempt bound + idempotency | `OPEN_LOCAL` |
| Side-effect fence | every reachable sink admission/lease/settlement | `PARTIAL_LOCAL` |
| Replay crash recovery | seeded crash-boundary recovery, valid-prefix/hash invariants, IO/write evidence | `PARTIAL_LOCAL` |
| Research quality | live snapshots, freshness, contradiction precision/recall | `OPEN_EXTERNAL` |
| Browser containment | DNS race, redirect, crash, OS enforcement | `OPEN_EXTERNAL` |
| Physics validity | convergence, calibration, uncertainty, replication | `OPEN_EXTERNAL` |
| Benchmark generalization | hidden scorer, contamination resistance, reproduction | `OPEN_EXTERNAL` |
| Release provenance | final SHA, signed subject, hosted IDs, rollback | `BLOCKED_EXTERNAL` |

**M1 local execution record (2026-08-31):** core bridge source no longer
registers the canonical `aegis` console script. A clean core wheel was built
from `core/python/pyproject.toml` plus the narrow `core/python/setup.py`
staging-cleanup hook with SHA-256
`c74341ca05907002ff65fb60765930775124c7a1476bcdfcc03ea07f82f6532e`;
the direct build was run against the existing dirty `core/python/build` tree
and still produced 18 files, zero recursive `build/` entries, the seven
declared top-level bridge modules, and no `entry_points.txt`. Its independent
venv imports `Agent`, resolves all 14 `aegis.__all__` exports, verifies the DEV
policy hash and hot-commit handle, completes a bounded DEV `run_sync`, and
passes an offline compatibility probe covering all eight browser artifact kinds,
the predicate, bridge batch/zero-copy frame, Playwright runtime session and
`LearningManager` construction without creating `aegis.exe`. A fresh
dependency-complete combined venv installed the root wheel plus this exact core
wheel; it contains exactly one `aegis` entry point
(`aegis_cognition.cli:main`), has no repository path on `sys.path`, and passes
`aegis --help`. This closes the M1 owner/runtime and local bridge-compatibility
gate on Windows/CPython 3.14. It does not prove Linux/macOS enforcement,
live-browser/provider behavior, signed release provenance or the remaining
authority/trust/retry migration gates.

A fresh root wheel was then built with the pinned maturin toolchain and the
current narrow core-source include globs, clean-installed in an isolated
Windows/CPython 3.14 environment, and inspected for recursive staging. Its
SHA-256 is
`97e51ebdc02c420de5e4e5fbabe4449475e1eb03dee61d4a588588a0e44422f0`;
the wheel contains zero `core/python/build` entries. The probe imported the
package and native extension from the venv, observed no repository path on
`sys.path`, found exactly one `aegis` console entry point, and completed
`aegis --help`. This closes the previously `NOT_VERIFIED` root clean-install
subcheck and the local root staging-containment check. The core-wheel
compatibility probe above is offline and does not prove live browser/provider
behavior, signed release provenance, or the remaining authority/trust/retry
migration gates.

The bounded packaging smoke gate also passed all 19 local checks, including
the two owner checks; its `package_surface_hash` is
`b161fb888e5f842a251c243db90060738b67a18fd49dcec1dc9d353b95ae003a` and its
`smoke_evidence_hash` is
`0f9741a1ff543f864ef093aba4d004c10f3c04bb019b690bd5dfcc471dfdee36`.

**M2 local hardening record (2026-08-31):** `LabRun` now performs a bounded
native snapshot-consistency check before dossier construction and snapshot
serialization. The check compares native event identity/state/epoch,
projection record IDs and projection payloads with the Python view; a direct
Python projection mutation is rejected as `native projection diverged` instead
of being serialized as authoritative. The Rust projection admission path now
also conditionally materializes valid hex-digest Source/Claim/Hypothesis/
Experiment/Observation records into typed native runtime maps; opaque fixture
labels remain projection-only for compatibility, and adapter-only fields still
remain lossy. The regression test
`test_native_projection_drift_fails_closed_at_snapshot_boundary`, the full
`tests/test_lab_runtime.py` run (`138 passed`), `cargo check --features
python-extension`, the Rust `lab::tests` run (`15 passed`), and a
dependency-complete Windows/CPython 3.14 wheel probe all pass for this bounded
guard/materialization slice. The probe shows clean snapshot round-trip,
typed-runtime source materialization for valid digests, and rejection after
the Python dictionary is mutated while the native snapshot remains unchanged
and no compensating event is emitted. The packaged probe also reloaded the
same snapshot in a fresh process with matching state/epoch/event/projection
counts. The full typed-chain regression covers Source → Claim → Hypothesis →
Experiment → Observation materialization and snapshot restore; a same-ID
record replacement is also rejected against the immutable admission payload.
`AuthorityMode` is now explicit (`projection_only`, `native_admitted`,
`native_required`) and serialized in snapshots/manifests; the legacy boolean
maps deterministically, while `LabPolicy` overwrites compatibility options so
PROD cannot be silently downgraded. This is a containment and partial
materialization improvement, not proof of one lossless reducer: M2 remains
`OPEN_LOCAL` until typed native authority owns the complete lifecycle and
fresh-process replay proves the cross-language contract.

**M2 fresh-process replay continuation record (2026-08-31):** without rebuilding
or modifying the repository, a bounded parent/child probe was run from the
disposable packaged environment
`C:\Users\ADMIN\AppData\Local\Temp\aegis-empirical-execution-20260831\combined-venv-authority-final-20260831\`
with the working directory outside the checkout. The parent constructed a
`LabRun` in `native_required`/`PROD`, admitted one digest-backed `SourceRecord`,
and serialized its snapshot; a fresh child Python process loaded that snapshot.
Both processes reported `state=researching`, `state_epoch=2`, `events=3`,
`sources=1`, `verify_event_chain=true`, and
`event_chain_authority=rust_native_verified`; the child exited `0` with empty
stderr. This is `LOCAL-PROVEN` for one packaged native snapshot prefix and
one typed source chain. It does not prove replay of every execution-cell,
browser, provider, skill, cancellation or adapter-only field, nor hosted
single-writer/restart semantics; M2 therefore remains `OPEN_LOCAL`.

**M2 full typed-chain fresh-process record (2026-08-31):** a bounded offline
probe using the current Python source with the packaged native extension
admitted one digest-backed `SourceRecord`, `ClaimRecord`, `HypothesisRecord`,
`ExperimentSpec` and `ObservationRecord` under `native_required`/`PROD`.
Parent and child process snapshots both reported `state=experimenting`,
`epoch=7`, `events=8`, `verify=true`, `authority=rust_native_verified`, and
matching native/Python counts of `1/1/1/1/1`. Child return code was `0`, with
empty stderr. This closes a stronger local typed-chain replay prefix than the
single-source probe, but it is still not full side-effect lifecycle,
multi-process writer, hosted recovery or final-SHA evidence; M2 remains
`OPEN_LOCAL`. The raw record is retained at
`artifacts/local-runtime/fresh-typed-chain-20260831/typed_chain_replay.json`
(SHA-256 `bcd177f59d9944ef4f3595dc899f5ccd97124f4d514d7fd9ba5d108dc8e66903`);
the fresh wheel subject is SHA-256
`a51a4b12f473411c9d30d6b51f9ca484be17b090029d549ca5e5ca6d52258ddf`.
The disposable venv reused dependency packages from the existing local
`.venv` through `PYTHONPATH`; dependency provenance is therefore not an
independent clean-install proof.

**M2 six-lane fresh-process replay continuation (2026-09-01):** a bounded
native-required parent/child probe using the replacement wheel created one
snapshot prefix with six open execution-cell admissions: experiment, generic
tool, research program, browser action, browser observation and skill. The
child process restored the prefix with `verify_event_chain=true`, reconciled
all six admissions to explicit `REJECTED`/`UNKNOWN_SIDE_EFFECT`, reached
`state=blocked`, left `open_after=0`, and exited with return code `0` and empty
stderr. The raw witness is retained at
`artifacts/local-runtime/m2-all-lane-fresh-replay-20260901/m2_all_lane_fresh_replay.json`
(SHA-256
`76650fbea44947f16b305479d58ebd6ed4852224d662295d772cd097aa07057b`). This
strengthens local same-prefix recovery evidence across the explicit cells; it
does not prove hidden planner/lease ownership, external-effect reversal,
hosted single-writer behavior, or full Agent lifecycle authority, so M2 remains
`OPEN_LOCAL`.

**M2 native rollback continuation (2026-09-01):** the Python projection
rollback boundary now captures the native `LabController` snapshot whenever
the controller exposes the validated snapshot API. A new in-place PyO3
`restore_snapshot_json` method restores the Rust reducer, budget, event chain,
state epoch and projection indexes after an event rejection; an unavailable
or rejected restore fails closed. The regression
`test_native_event_rejection_rolls_back_nested_native_transition` exercises
an accepted nested `StateChanged` followed by a rejected `SourceCaptured` and
proves that both Python and native state return to the mission-created prefix.
The `Source`, `Claim`, `Hypothesis`, `Experiment`, `Observation`,
`SecurityEvent`, blocker ledger, Skill admission and Tool admission/settlement
lanes now use a native-first
projection callback; `test_claim_projection_applies_only_after_native_admission`
proves the ordering directly for the first migrated lane. The focused Lab file
has 151 passing tests, the full Python cross-language gate has 257 passing
tests, `pyright` reports zero errors, Rust
`cargo check --features python-extension`, feature-scoped Clippy,
`ffi_smoke_checks`, and Cargo formatting all pass. This closes one local
atomicity hazard only; the Python projection still mutates before admission in
other lanes, the real compiled extension was not loaded in the test venv,
snapshot-per-boundary cost is unbenchmarked, and M2 remains `OPEN_LOCAL`.

**M2 native-first completion pass (2026-09-01):** every `LabRun` event append
outside the mission bootstrap now supplies an explicit next `state_epoch`; the
sink commits that epoch only after native admission succeeds. Transition state
is applied through the same callback boundary, and the rollback snapshot now
includes the finalization-started flag. Research, browser action/observation,
experiment execution, cancellation, budget, finalization, and skill execution
settlement no longer mutate the Python epoch before admission. The Rust typed
browser validator now accepts the `launch` action kind already exposed by the
Python contract, with a native projection regression covering launch admission
and settlement. The focused Lab file has 154 passing tests, the full Python
cross-language gate has 260 passing tests, Pyright reports zero errors/warnings,
and the Rust projection test, full workspace test (439 core unit tests plus two
integration tests), feature check, feature-scoped Clippy, FFI smoke, and
formatting pass. This closes the remaining local pre-admission epoch/state
hazards in the LabRun façade, but that source-only baseline run did not load a
fresh extension built from the current source, and it did not close hosted
authority, external-effect containment, benchmark, or release gates. An AST
audit finds 30 `_append` call sites with only the mission bootstrap
intentionally omitting an explicit next epoch. M2 remains `OPEN_LOCAL`.

**M2 real-extension probe (2026-09-01):** the existing release-built Rust
extension (`target/release/aegis_nerve.dll`, SHA-256
`2972a11451103b6bedfe8d55b85900b62c45adba4323ed4f58dd7d2971152a30`,
22,190,592 bytes) was loaded as an isolated Python 3.14 extension from a
temporary directory without changing the checkout or rebuilding. The real
native module exposed `LabController` and the event-chain verifier; the Lab
suite passed `154` tests and the full Python cross-language suite passed `260`
tests with that module actually imported. This is stronger than a source-only
or fake-controller check, but the binary predates the new in-place
`restore_snapshot_json` method (the method is absent) and has no source-epoch
attestation. Therefore it validates the compatible native event/record path,
not the latest rollback implementation; the current source still requires a
fresh release build and source-bound wheel probe before M2 can advance.

**M2 restore-verification hardening and serial-gate record (2026-09-01):**
`LabRun._restore_projection` now verifies that the native snapshot returned
after `restore_snapshot_json` is canonical-JSON equal to the pre-attempt
snapshot. A compatibility adapter that exposes the method but silently
ignores or alters the payload therefore fails closed instead of allowing the
Python projection and native reducer to continue with different prefixes.
`test_native_rollback_rejects_silent_restore_mismatch` covers that adversarial
case and confirms the Python side remains at the prefix while refusing to
claim rollback success. The focused Lab file passes `155` tests and the full
cross-language Python gate (`tests` plus `core/python/tests.py`) passes `261`
tests both without an extension and with the isolated existing release DLL.
Those gates were run serially because concurrent processes share the default
Windows replay-writer lease; an earlier parallel invocation produced two
lease-acquisition failures, while each test and the complete gate pass when
run serially. This is test-orchestration evidence, not a product regression.
The release DLL still predates `restore_snapshot_json` and is not
source-epoch-attested, so fresh source-bound extension evidence remains
required and M2 remains `OPEN_LOCAL`.

**M2 source-bound build resource blocker (2026-09-01):** a read-only storage
check measured `target/` at `24,427,822,224` bytes while the system volume
had `427,147,264` bytes free. A previous clean release attempt already
stopped with OS error 112; with the current margin, another clean build or
wheel rebuild is not a safe local action. No `cargo clean`, recursive delete,
or replacement build was performed. The fresh source-bound extension gate is
therefore a genuine environment blocker, not an omitted verification step;
the existing release DLL remains retained only as compatibility evidence.

**Closure-artifact epoch reconciliation (2026-09-01):** the required
`docs/architecture/design-closure/` set is complete, but its `22` JSON
closure records remain bound to the earlier epoch
`58f454a05d959d75bf89f423f7bc22784fdfd5c21d4f32687fb00a38ca02d22`; the
separate dependency-role record is bound to `549114d2…`. The current source
and M2 evidence are bound to `a59d93c3…`. These records are therefore retained
as historical design-closure inputs, not silently relabeled as current proof;
regeneration is deferred until a controlled evidence run can complete without
mixing epochs.

**Supply-chain gate revalidation (2026-09-01):** the bounded internal supply
chain gate reports `20` passed checks and `0` failed checks for the current
lockfiles, SBOM index, WASI deny-by-default controls, and provenance hash
construction. It explicitly records `external_signed_attestation_present=false`
and `production_release_blocked_without_external_attestation=true`; the
attestation artifacts are missing rather than silently treated as valid. The
current report is retained at
`artifacts/supply_chain_gate_report.json`. This closes only the internal
structural gate; it does not create an external signature or release subject.

**Retained-wheel source-bound inspection (2026-09-01):** a read-only ZIP
inspection found `10` retained wheel paths under `artifacts/`; `9` are valid
ZIP wheels and all `9` contain a native `aegis_nerve` payload, but `0` contain
`aegis_cognition/lab.py`. One zero-byte path is an invalid partial artifact and
one valid wheel also lacks the bridge payload. None of these wheels can prove
the current Lab implementation or `restore_snapshot_json`; they remain
historical packaging evidence only. A new wheel built from the current source
is required after a safe build environment is available.

**Packaging-configuration reachability check (2026-09-01):** the current root
`pyproject.toml` selects pinned `maturin==1.14.1`, sets
`python-source = "."`, targets `aegis_cognition.aegis_nerve`, and declares no
root-package exclusion; `aegis_cognition/lab.py` is present under that source
root. This makes inclusion of the Lab module plausible by configuration, but
not proven without a successful wheel build and clean import. The stale
`aegis_cognition.egg-info/SOURCES.txt` predates `lab.py` and is therefore not
treated as current packaging truth.

**M2 fresh source-bound build and clean-wheel probe (2026-09-01):** the
source-bound closure requires a release build with the project interpreter
explicitly selected, an isolated wheel containing the current Lab module and
native restore API, and a clean CPython 3.14 import outside the checkout.
The final build-input epoch, binary/wheel hashes, clean-probe paths and
runtime results are retained in the M2 evidence artifact; no stale wheel or
system Python binding may substitute for that record.

**M2 final source-bound local closure (supersedes the earlier storage-blocker
paragraph, 2026-09-01):** after the prior OS error 112, a read-only check
showed approximately 14.25 GB free on the system volume. A bounded `-j 1`
release build with `PYO3_PYTHON` explicitly set to the project CPython 3.14
completed from worktree epoch
`f2b07ff04af07f782686ef874a8efad381be2ad4acc6d016ef23fc60914d4f3f`, producing
`target/release/aegis_nerve.dll` (SHA-256
`9f35bc22d51bb0f63a1d2610e532d7a75f94e3ff916a6c322595f94d8cd98a6d`,
22,197,760 bytes) linked to `python314.dll`. Pinned `maturin 1.14.1` then
produced the current wheel at
`artifacts/local-runtime/m2-native-rollback-20260901/wheel-current-final/`
(SHA-256
`e383a79f4ad4d765b4bc614bd8a0f0d127d6fe6bda3eaed6690af2f2301809cf`,
7,796,484 bytes, 49 files). A clean CPython 3.14 environment outside the
checkout imported Lab and the packaged native module with no repository path
on `sys.path`, and exposed both `restore_snapshot_json` and the native event
verifier. The final DLL probe passed 155 Lab tests and the full Python
cross-language gate passed 261 tests. This supersedes the earlier local
source-build/storage blocker; dependency-complete wheel installation,
embedded source-epoch/signature, hosted authority, and external signed
attestation remain open, so M2 is still `OPEN_LOCAL` rather than release
closed.

**M3 local characterization record (2026-08-31):** the Lab constructor matrix
now has regression coverage proving that one `LabPolicy.trust_level` propagates
unchanged into `AgentConfig`, that native-authority admission defaults to
`False` for DEV/STAGING and `True` for PROD, and that the Lab policy hash is
the same 64-hex subject as `core.python.aegis.evidence.trust_policy_snapshot`
for all three levels. The gateway receives that hash; native-required mode
rejects a compatibility factory that drops or changes it; `AegisAdapter`
rejects an explicit mismatch. A hash-bound LabRun includes the policy in the
native mission contract hash and round-trips through the snapshot schema,
while a missing hash deliberately preserves legacy direct snapshots. Bound
event envelopes now carry the same subject hash, and hash-bound execution-cell
manifests plus lookup reject a missing or mismatched policy subject before
invocation. This closes the local Lab-path binding fact but does not unify direct compatibility
entrypoint defaults: config and Lab still default to DEV while the friendly
adapter/evidence and Rust hot engine default to PROD when omitted. No default
was changed without an owner-approved policy decision. The local gates are
`tests/test_lab_runtime.py` 138 passed, `core/python/tests.py` 84 passed,
Ruff PASS, Rust `lab::tests` 15 passed, and Cargo check/fmt PASS; the broader
M3 exit remains OPEN because no provider/browser/process/benchmark cross-cell
policy hash receipts or external environment witnesses are present.

**M3 local continuation record (2026-08-31):** `AgentConfig` now creates the
same canonical trust-policy subject at the public mission boundary and stores
its 64-hex hash. `Agent(lab=True)` binds that hash into
`lab_trust_policy_hash` before compatibility options reach `LabApplication`;
the non-Lab gateway path also receives the hash, and the existing adapter
rejects a level/hash mismatch. This closes the previously unbound Agent
compatibility mission path without changing DEV/STAGING/PROD defaults. It is
still only local constructor/propagation evidence: the direct compatibility
defaults remain intentionally divergent and the provider/browser/process
cross-cell receipts, external witnesses and final provenance required by the
M3 exit gate are NOT VERIFIED.

**M3 canonical primitive continuation (2026-09-01):** the trust-policy schema
and BLAKE2b-256 subject digest are now implemented once in
`core/python/aegis/trust_policy.py`. `aegis_cognition.config` and the core
evidence bridge consume that primitive, while each compatibility boundary
passes its explicit legacy default (`DEV` for the friendly mission boundary,
`PROD` for the standalone evidence bridge). Regression coverage compares the
canonical payload/hash with all three `TrustPolicySnapshot` levels and checks
both explicit defaults. This removes one local field/hash-drift path; it does
not close the M3 exit: direct defaults remain contextually different, and
cross-cell receipt propagation, Rust ownership and external witnesses remain
NOT VERIFIED.

**M3 policy-owned capability continuation (2026-09-01):** `Lab.start()` now
rejects a caller-provided `browser_policy` or
`lab_allow_external_writes` when it conflicts with `LabPolicy`; an equivalent
list/tuple representation is accepted and rewritten to the policy-owned
canonical value. The regression gate covers both rejection paths, and the
current full Python regression is 251 passed. This closes a local option
precedence gap only; it does not prove hosted capability containment or
external side-effect control.

**Architecture freeze and audit-gate record (2026-08-31):**
`docs/ARCHITECTURE_FREEZE.md` now freezes the P0 module ownership map,
Sprint A evidence contracts, AF-001–AF-007 decisions, and the explicit
no-build list. `scripts/constitution_audit.py` now exposes
`TruthSchemaGate` (versioned schema identity plus Rust owner markers) and
`NoOverclaimGate` (qualified readiness language plus an explicit freeze-status
disclosure). The focused gate suite has 3 passing tests and the full
constitution audit has 180 passing checks. These are local structural and
documentation controls; the TruthSchemaGate still requires the Rust compile
and replay-hash runtime lanes, and NoOverclaimGate cannot substitute for
external deployment, provider, browser, hardware or signed-provenance
evidence. M2–M4 therefore remain `OPEN_LOCAL`.

**Replay chaos local evidence record (2026-08-31):** the bounded command
`cargo run --quiet --bin aegis-nerve-cli -- replay-chaos-scorecard
C:\Users\ADMIN\AEGIS-COGNITION\artifacts\local-runtime\replay-chaos-20260831\replay_chaos_scorecard.json`
completed successfully on the Windows checkout. The emitted artifact
`C:\Users\ADMIN\AEGIS-COGNITION\artifacts\local-runtime\replay-chaos-20260831\replay_chaos_scorecard.json`
has SHA-256
`282a5ca04b4a2f05ba1f59943137f698db0aa8456eb72d75f7528d9fc531b0bc` and
reports `success=true`, `crash_recovery_passed=true`, `crash_points_exercised=128`,
`expected_event_count=43`, `all_recoveries_valid=true`, `min_recovered_event_count=0`,
`max_recovered_event_count=43`, and `witness_coverage_ppm=1000000`. The same
scorecard binds equal replay and physical-witness hashes, records zero policy
violations/hard blocks, and includes mmap-backed read evidence plus staged,
synced, write-through publish evidence. This is `LOCAL-PROVEN` for the seeded
local crash-prefix/IO packet only; it is not proof of 100-hour endurance,
cross-process or hosted single-writer enforcement, OS descendant containment,
randomized latency/copy-count distributions, external independent verification,
or final release provenance. Therefore the replay/recovery gate remains
`PARTIAL_LOCAL`, M7 is not passed, and no release status changes.

**Repository synchronization continuation (2026-09-02, latest):** the local
browser-gateway cancellation hardening is committed in source history after
the preceding `acf4e1c6ce29b4f6e7f575b847c8a4de298e68cc` fence commit; the
native-edge registry hardening is committed as
`81115432fca328ba6f7776a6cb3ad6d89a9679a6`. The
accompanying plan, contract inventory and regenerated closure views are kept
in the same pushed `main` line; the final parity check records
`HEAD == origin/main`, ahead/behind `0/0`, and a clean working tree. This
closes repository synchronization for the current slice only. The tracked
evidence template still deliberately contains `CHECKOUT_HEAD`, and no signer,
hosted CI/release/deployment IDs or independent final-SHA attestation exist,
so `LAB-RELEASE-006` remains `BLOCKED_EXTERNAL` and the evidence-consistency
template must continue to fail closed rather than be edited by hand.

**M7 manifest-version continuation (2026-09-02):** the segmented replay
manifest now serializes the explicit schema marker
`aegis-run-event-segment-manifest-v1` and version `1`; both values are bound
into the manifest hash, so changing the marker cannot preserve an old identity
accidentally. The Python archive writer rejects missing, partial, future or
lossy manifest metadata before accepting a new archive, while snapshot restore
continues to read pre-marker manifests only when both fields are absent for
rollback compatibility. Rust round-trip coverage checks the serialized marker;
Python negative coverage covers missing, partial, future and type-drift
metadata plus the legacy read path and explicit legacy-verifier dispatch.
Focused Lab coverage is **326 tests** and the combined Python/cross-language
gate is **432 tests**; Rust workspace tests remain
**439 unit + 2 integration**, Ruff, Pyright and Clippy are clean. This closes
the local manifest-marker ambiguity and provides a dedicated
`aegis_lab_verify_archive_against_legacy_manifest` FFI path; old-format
migration fixtures, cross-version archive replay, crash injection across
supported platforms and hosted restore remain NOT VERIFIED, so P1 migration/M7
does not pass.

**M4 global observed-attempt continuation (2026-09-02):** Lab-owned execution
edge execution admissions now carry one mission-bound finite
`max_external_attempts` envelope (control-only cancellation admissions are not
charged against that effect budget, so emergency abort remains available).
The default conservative envelope is `8 * (max_steps + 1)^3`; an operator may
set a stricter positive integer through `LabBudget.max_external_attempts`.
`LabRun` persists both the bound and its exact admission count, binds the count
to the event log during snapshot restore, and rejects the next admission before
projection mutation when the envelope is exhausted. The Rust mission contract
serializes the same bound, retains it in the mission hash, rejects zero bounds,
enforces the counter in its atomic projection-record clone, and rejects an
over-budget restored controller. Provider route metadata is additionally
bounded to at most primary plus `max_steps` fallback candidates. Python
regression is **327 Lab tests / 348 repository tests**, and the new Rust
native budget regression passes alongside the existing workspace suite.
This closes the previously missing finite bound for **observed Lab-owned
admissions** locally; it does not prove the number of physical network/browser
requests, opaque SDK or user-runner retries, provider idempotency, external
effect reversal, descendant containment, or hosted multi-process writer
authority. M4 therefore remains `OPEN_LOCAL`, while the remaining gap is now
explicitly narrowed to unobservable/external attempts rather than an absent
runtime admission budget.

**Design-closure regeneration continuation (2026-09-02):** the bounded
`design_closure_collect.py` audit is rerun after each source/documentation
slice while documentation changes are in the working tree; the generated
Markdown and 23 JSON views retain the exact source `HEAD` and collector-
computed `WORKTREE_EPOCH` in the artifacts rather than duplicating them here.
`scripts/document_consistency_gate.py` and JSON parsing pass. The regenerated
retry view records the new mission-bound observed-admission envelope while
retaining `NOT VERIFIED` for physical requests, provider/SDK/user-runner
retries, descendant effects and external idempotency. The collector observed no
reusable temporary wheel in the current machine, so packaging is reported as
`UNKNOWN` rather than inheriting a stale probe; this is not release evidence and
does not lower or raise any external blocker. The evidence-consistency gate
continues to fail closed on the deliberate `CHECKOUT_HEAD` template and
`LAB-RELEASE-006` remains `BLOCKED_EXTERNAL`.

**M7 recovery-metadata continuation (2026-09-02):** crash-prefix recovery now
passes through the exact optional `idempotency_key` and `timeout_seconds`
retained in modern tool and experiment admissions. The same preservation is
applied to the legacy `reconcile_unsettled_tool_executions()` helper, so a
recovered admission is settled as `REJECTED`/`UNKNOWN_SIDE_EFFECT` instead of
being left open merely because its metadata-bearing settlement contract was
not replayed. Regression coverage exercises generic recovery, experiment
recovery and the compatibility helper; the full Python suite is **352 passed**
under `-W error::DeprecationWarning`, and focused Ruff/Pyright checks are
clean. This closes a locally observed recovery-contract defect only; it does
not prove that an external effect was reversed, that an opaque adapter did not
retry, or that a hosted writer is authoritative. M7 remains `PARTIAL_LOCAL`
and `LAB-AUTH-001` remains `OPEN_LOCAL`.

**M7 browser-quota continuation (2026-09-02):** an actor action admitted before
an in-flight cancellation now consumes the `BrowserCell` action quota, matching
the existing rejected-action path. Without this, a cancelled action left
`action_count` unchanged and a later lease could retry beyond `max_actions`;
the regression reproduces that boundary and proves relaunch is rejected at
`max_actions=1`. Both the typed controller action-plan lane and the
compatibility browser-action lane now apply the same fail-closed accounting.
The full Python suite is **353 passed** under
`-W error::DeprecationWarning`; focused Ruff and Pyright are clean. This closes
one local quota-accounting defect only; it does not prove browser process/OS
containment, DNS-rebinding resistance, or hosted cross-domain enforcement.
`LAB-BROWSER-002` remains `OPEN_EXTERNAL` and `LAB-AUTH-001` remains
`OPEN_LOCAL`.

**M5 provider-timeout continuation (2026-09-02):** `SearchProgramExecutor`
now applies its finite `timeout_seconds` to async query-provider calls with
`asyncio.wait_for`, so a cooperative hanging provider cannot leave a research
operation pending indefinitely. Regression coverage uses a bounded
0.01-second hanging provider and expects `TimeoutError`; this is an
async/cooperative deadline only and does not prove interruption of a blocking
synchronous provider, arbitrary descendants or hosted-provider behavior. The
full Python suite is **354 passed** under
`-W error::DeprecationWarning`; focused Ruff and Pyright checks are clean. Live
provider semantics remain external, so `LAB-RESEARCH-003` remains
`OPEN_EXTERNAL`.

**M2/M5 adapter-timeout continuation (2026-09-02):** the Lab context-retrieval
cell and both search-program dispatch lanes now apply one finite
`context_retrieval_timeout_seconds`/`search_timeout_seconds` deadline to the
whole cooperative adapter call, including custom async executors that do not
implement their own timeout. Timeout is carried into admission/policy and
terminal `TIMED_OUT` settlement for context retrieval; search timeout fails
closed before candidate ingestion. Regression coverage uses bounded hanging
async adapters and the full Python suite is **356 passed** under
`-W error::DeprecationWarning`; focused Ruff and Pyright checks are clean.
This is cooperative async containment only: a blocking synchronous provider,
thread that ignores cancellation, descendant process or hosted adapter can
still outlive the deadline and remains `NOT VERIFIED` under
`LAB-AUTH-001`/`LAB-RESEARCH-003`.

**M2 application-failure continuation (2026-09-02):** direct
`LabApplication.run()` callers now reconcile any still-open execution
admissions and append the native cancellation fence before propagating an
unexpected exception or task cancellation. This closes the compatibility
boundary that `LabSession` already handled: a gateway-construction or other
between-lane failure cannot leave a direct application run non-terminal. The
regressions prove both a failed gateway admission and direct task cancellation
are settled, the run reaches `aborted`, and no execution admission remains
open; the complete Python gate is **455 passed** under
`-W error::DeprecationWarning`, with Ruff and Pyright clean. This is local
application-boundary evidence only: it does not prove external-effect
reversal, descendant termination or hosted single-writer authority, so
`LAB-AUTH-001` remains `OPEN_LOCAL`.

**M4 post-completion effect continuation (2026-09-02):** the required
`memory.index_session` compatibility effect now carries a finite positive
`post_completion_effect_timeout_seconds` policy (default `30.0`), a
mission-bound idempotency key, and the same timeout metadata through admission
and settlement. Cooperative timeout settles `TIMED_OUT` and required policy
keeps the dossier blocked; cancellation and ordinary failure preserve the
identity/deadline fields. Regression coverage proves the bounded timeout and
the complete Python gate is **456 passed** under
`-W error::DeprecationWarning`, with Ruff and Pyright clean. This is still
cooperative/local containment: a blocking or external persistence effect may
outlive the deadline, so M4 and `LAB-AUTH-001` remain open.

**M4/M7 benchmark-validator continuation (2026-09-02):** the Lab-owned
`benchmark.hidden_validator` lane now derives a mission-bound idempotency key,
normalizes and validates one finite positive validator deadline, and carries
both fields through admission, success/rejection settlement, and crash-prefix
recovery. Previously the deadline was present only inside the policy payload,
so a recovered validator admission could lose the retry/timeout fence. The
strict-registry regression now checks the 64-hex key and `5.0`-second default;
the complete Python gate remains **456 passed**, Ruff and Pyright report no
findings. This closes the observed local receipt-contract gap only; hidden
validator secrecy, isolated-process containment, and independent benchmark
reproduction remain `OPEN_EXTERNAL` under `LAB-BENCH-005`.

**M1 packaging-smoke continuation (2026-09-02):** the bounded
`scripts/production_packaging_smoke_gate.py` rerun passed all **19/19** local
checks at the current source, including canonical root `aegis` ownership,
core bridge no-console-script ownership, aligned Python support versions,
Python smoke, Rust CLI smoke, service-manifest and artifact-write checks.
Recorded local digests are `package_surface_hash=
688471a82094396c61970a9e924936b275ddf80da883c06220c029ec53ff26b7` and
`smoke_evidence_hash=
09c1064ef896c5b8a52150214b70ba0ca99b177760e66fbc846ef69db6b5046b`.
This refreshes source-level packaging evidence only; clean dependency-complete
cross-platform installs, signed final-SHA provenance and external deployment
remain outside local proof, so `LAB-RELEASE-006`/`LAB-OPS-007` stay open.

**AESE Phase 0 inventory continuation (2026-09-02):** the attached
`AEGIS_ADAPTIVE_EVIDENCE_SYSTEM_IMPLEMENTATION_DIRECTIVE.md` was reconciled as
an implementation directive, not as current status authority; its recorded
GitHub HEAD is historical and is not substituted for the checkout state. The
new bounded generator `scripts/aese_inventory.py` emits
`quality/registry/current_inventory.json` with a source-tree digest and
provenance. It inventories **116** tracked evidence surfaces: 10 Python test
files, 24 Rust unit-test files, 1 Rust integration test, 5 Rust benchmarks,
5 Python benchmarks, 4 fuzz targets, 52 Python scripts/gates, 4 hosted
workflows and 11 workflow jobs; no scope is missing. Every item is
conservatively `RETAIN_UNCHANGED`, with claim/owner/cost/platform/failure
mapping left `UNKNOWN`/`NOT_MAPPED`; direct cutover and deletion remain
prohibited. The generator's `--check` drift gate and four regression tests pass,
and this closes only Phase 0 discovery. Phase 0 itself does not prove claim
coverage, affected-closure completeness, shadow non-inferiority or cost
savings; later phase artifacts remain advisory and no legacy check is
suppressed.

**AESE Phase 1 shadow graph continuation (2026-09-02):**
`scripts/aese_claim_graph.py` now materializes the source-backed GT96
traceability into a deterministic shadow graph with **35 claims, 35 contracts,
35 invariants, 70 test/benchmark references, 23 resolved code nodes and 20
future obligations**. The graph is linked to the Phase 0 inventory and the
canonical not-verified registry; 115 of 116 inventory surfaces remain
explicitly `NOT_MAPPED` because the current inventory does not yet prove a
complete test-to-claim or workflow-job mapping, and 59 of 70 verification
references remain unresolved until concrete files/jobs are identified. The
graph drift gate is wired into CI and release verification, but selection,
skipping, promotion and deletion are disabled in `SHADOW`; unknown dependency
must invalidate conservatively in the later affected-closure phase.

**AESE Phase 2 shadow preflight continuation (2026-09-02):**
`scripts/aese_preflight.py` now classifies explicit changed paths against the
inventory and graph, records affected code/claim IDs where the source mapping
is exact, and widens empty or unmapped input to the retained legacy suite.
Its fixed evidence order is static metadata, document/architecture, security
static checks, Python quality, Rust quality, platform anchors, performance and
release certification. Every stage is marked `may_skip = false`; the output is
advisory `SHADOW` data only. Savings, cost, statistical precision, OOD status
and external-anchor sufficiency remain `NOT_MEASURED`/`NOT_STARTED`, so this
does not enable selective testing or change release authority.

The Phase 2 output now also materializes the graph affected closure from
surface through contract, invariant, verification, evidence and claim. An
exactly mapped source path receives `EXACT_SOURCE_CLOSURE_SHADOW`; an empty,
unknown or known-but-unmapped path receives `WIDENED_ALL_RETAINED` and marks
all 70 verification references affected. This is an invalidation witness, not
permission to skip any retained runner.

**AESE Phase 3–7 local primitive continuation (2026-09-02):**
`aegis_cognition/aese.py` adds the smallest reusable, side-effect-free
foundation for adaptive measurement and model metadata. `AdaptiveMeasurementSpec`
requires a named mean estimand, warmup/sample/block floors, a bounded maximum,
and preregistered precision/autocorrelation/drift limits. The evaluator keeps
malformed observations visible, uses complete blocks and a deterministic
Student-t Cornish–Fisher interval approximation, and emits only
`PASS`/`FAIL`/`CONTINUE`/`UNSTABLE`/`CONTAMINATED`/
`INSUFFICIENT_EVIDENCE`; this is statistical-method implementation evidence,
not calibrated coverage or a benchmark result. `HardwareCapabilityVector`
records CPU/cache/memory/storage/process/FFI/serialization/OS/virtualization
fields with explicit `UNKNOWN` values; it does not infer electrical power or
cross-platform behavior. `WorkloadSignature` uses bounded intensity fields and
an explicit deterministic regime classifier, returning `UNKNOWN` when the
shape is under-specified. `SimulationEvidence` is schema-bound to
`SIMULATION_ONLY`/`SIMULATED` and cannot be relabelled as observation.
`AnalyticPredictionModel` and `predict_cross_hardware` require a complete
validated domain, at least two reconstructible anchors, and residual evidence
declared `MEASURED` with at least 30 samples; missing features and out-of-domain
targets are rejected and carry `OOD_STATUS`, nearest-anchor distance,
prediction interval and model version metadata. `AdaptiveMeasurementSession`
provides immutable append-only checkpoints and refuses appends after a terminal
measurement result; `HardwareCapabilityVector.from_runtime_profile` projects
only fields explicitly reported by the existing runtime profile. Eleven
focused tests cover these phases and the full local regression suite passes
with 485 tests. No model fit, physical-power measurement, external anchor,
independent replication or release promotion is claimed.

**AESE Phase 8 local anchor-planning continuation (2026-09-02):**
`select_anchor_plan` adds a bounded, deterministic high-fidelity anchor planner
without contacting GitHub Actions. It preserves mandatory anchors even when a
budget is exceeded, reports unavailable mandatory anchors as
`EXTERNAL_VERIFICATION_BLOCKED`, and ranks optional anchors by declared
changed-boundary, OOD, periodic-sentinel and uncertainty signals. The plan is
always `PLANNED_NOT_EXECUTED`; it cannot spend hosted minutes or certify a
platform. `CoverageVector` reports contract, state, failure-mode, schedule,
hardware-domain, platform-semantic and statistical-precision dimensions
independently and carries no aggregate percentage. Three additional focused
tests pass. Anchor availability, high-fidelity execution and all
cross-platform/release evidence remain external obligations.

**AESE Phase 2 provenance continuation (2026-09-03):** the shadow preflight
artifact now carries an explicit, non-promotable provenance envelope: checked
out source SHA, optional `origin/main` SHA, clean/dirty worktree state, epoch,
inventory and graph input digests, Python/OS runtime identity, validator name,
and the fixed labels `PLANNING_ONLY`, `LOCAL_CHECKOUT_ONLY` and
`DISABLED_IN_SHADOW`. The envelope is part of the preflight record and is
checked by `validate_preflight`; an unavailable remote ref is
recorded as `NOT_AVAILABLE` rather than inferred. Seven focused preflight tests
pass, including provenance assertions. This makes the local planning chain
reconstructible without implying observed benchmark, hosted, signed-release
or production evidence; selective testing and release promotion remain
disabled.

**Current local-phase closure record (2026-09-02):** at the bounded closure
point before this AESE-1 delta, `HEAD == origin/main` at
`640390214d8fe012ba0f027e7a45301a433ca876` with a clean worktree and the
retained local verification suite valid. Therefore
`CURRENT_LOCAL_PHASE = CLOSED`, `DEVELOPMENT_BLOCKED = NO`, and
`SAFE_TO_BEGIN_AESE = YES`. Hosted execution remains
`EXTERNAL_VERIFICATION = BLOCKED_EXTERNAL` because both latest workflows
failed before runner allocation; `RELEASE = NOT_READY` and
`PRODUCTION_COMPLETE = NO`. This closure record does not promote any local
result to CI or release evidence.

**AESE current verification snapshot (2026-09-03):** after the provenance
and direct-entrypoint hardening commit
`6fbfb1071c8752071912b0d01d1156dd64a6f912`, `HEAD == origin/main` and the
worktree is clean. The full Python/cross-language regression suite passes
**485 tests** on CPython 3.11; the AESE inventory, claim-graph and preflight
focused suite passes **15 tests**; targeted Ruff and Pyright for the changed
AESE surface pass; architecture fitness, constitution audit (180 checks),
document consistency and a generated checkout-bound evidence manifest pass
locally. Rust evidence was not rerun because no Rust source changed and prior
source-bound evidence remains scoped to its recorded checkout. Hosted CI run
`33661148485` created 13 jobs, all with zero executed steps, and failed before
runner allocation; therefore `HOSTED_VERIFICATION = BLOCKED_EXTERNAL`, source
execution was not performed, and no hosted test result is claimed. Release
attestation, external anchors and selective-test promotion remain disabled.

**AESE shadow-selection and Python compatibility continuation (2026-09-03):**
commit `93f124c2350b897653fdc612d90e4bbd01d7ff7c` makes the Phase 2 plan
explicit about its hypothetical selection: all **116** retained inventory
items would run, no item would be reused or skipped, and no external anchor is
executed when candidates were not supplied. The record is `SHADOW`,
`RETAINED_LEGACY_AUTHORITY` and `NOT_EXECUTED`, so this is a comparison
description rather than selective-test permission. A full Python compile pass
also found and corrected a legacy CLI exception-syntax incompatibility;
CPython 3.11 CLI import/help smoke and the full **485-test** regression pass.
The subsequent CI run `33662324679` again created 13 zero-step jobs and failed
before runner allocation; hosted source execution remains unavailable and no
CI result is inferred.

**AESE Phase 7 OOD-anchor hardening continuation (2026-09-03):**
`predict_cross_hardware` now excludes an anchor whenever any feature used by the
analytic model falls outside the model's declared `validated_domain`. A target
with fewer than two in-domain, reconstructible anchors remains
`INSUFFICIENT_EVIDENCE`; an explicit `anchor_outside_validated_domain` reason is
retained instead of treating the out-of-domain record as supporting evidence.
This closes a local evidence-integrity gap in the prediction primitive only. It
does not validate a model, add physical-energy measurement, or promote any
cross-hardware claim. The focused AESE suite passes **18 tests**, and the full
Python/cross-language regression passes **489 tests** with deprecation warnings
treated as errors. Registry/graph counts remain unchanged; shadow selection,
external anchors, independent replication, and release promotion remain
disabled.

The same continuation rejects duplicate `anchor_id` values before counting the
two-anchor minimum. Repeating one observation therefore remains
`INSUFFICIENT_EVIDENCE` with `duplicate_anchor_id`, rather than manufacturing
independent replication from a duplicated record. This is a local identity and
evidence-integrity guard; it is not independent benchmark replication or model
calibration.

Anchor planning also rejects non-boolean `available` metadata instead of
coercing values such as the string `"false"` to a truthy availability flag.
The planner remains request-only and `PLANNED_NOT_EXECUTED`; this hardening
protects selection provenance without asserting that any external runner is
available.

Prediction artifacts now expose the sorted identities and evidence hashes of
the in-domain anchors considered. The artifact hash changes when an anchor's
validated hardware, workload, or observed value changes, while reordering the
same set remains deterministic. This improves local provenance reconstruction;
it does not establish model calibration or external evidence quality.

**AESE adaptive-measurement protocol hardening continuation (2026-09-03):**
implementation commit `ee14f3c` makes `evaluate_adaptive_measurement` fail
closed when a malformed protocol object or field would otherwise escape during
stopping-rule arithmetic. Invalid protocols now return the typed
`INSUFFICIENT_EVIDENCE` status with a protocol failure reason; they can never
be promoted to `PASS`, while raw observations remain hash-bound and visible.
Two negative tests cover malformed dataclass fields and a non-dataclass input.
The focused AESE primitive suite passes **23 tests** and the full
Python/cross-language regression passes **500 tests** with deprecation warnings
treated as errors; `compileall` also passes. The installed Ruff is too old to
parse this repository's `py314` target and Pyright is not installed, so those
two checks are `NOT VERIFIED` in this environment rather than inferred green.
This closes an input-integrity gap in the local statistical primitive only; it
does not establish calibrated stopping error, independent replication,
autocorrelation validity beyond the declared rule, external anchors, or release
authority.

**AESE malformed-input fail-closed continuation (2026-09-03):**
implementation commit `8837d02fb762afea305a64c30e386b043e56ad25` closes a
runtime gap in `predict_cross_hardware`: malformed model, target hardware,
target workload, non-sequence anchor containers, and invalid anchor entries now
return a typed `INSUFFICIENT_EVIDENCE` result instead of escaping through
metadata or feature extraction. Invalid anchor records are retained as a
failure reason, so they cannot be silently discarded to manufacture the
two-anchor minimum. The guard is covered by focused negative tests for each
boundary and the existing OOD, duplicate-identity, availability, and
provenance cases. The AESE primitive plus preflight suite passes **28 tests**
with deprecation warnings treated as errors; Ruff and Pyright pass for the
touched files. This is local input-integrity evidence only: it does not prove
model validity, calibration, physical-energy measurement, external-provider
freshness, independent replication, or release authority.

**AESE planner and vector boundary hardening continuation (2026-09-03):**
implementation commit `f924d3108f77f781b7b7a6ce7186bc400f764d69` closes three
additional local fail-open paths without changing shadow authority. A malformed
`contamination_flags` container can no longer accompany otherwise precise
observations to a `PASS`; the result is typed `INSUFFICIENT_EVIDENCE` while the
computed estimate and raw-observation hash remain visible for diagnosis.
`HardwareCapabilityVector.from_mapping` now rejects a non-mapping input and
renders mixed-type unknown keys deterministically instead of leaking an
ordering `TypeError`. `select_anchor_plan` rejects non-sequence candidate
containers as `EXTERNAL_VERIFICATION_BLOCKED` and records the exact input
failure; it never invokes a runner or treats absent candidates as a valid
empty plan. Three negative tests cover these boundaries. The focused
AESE/preflight suite passes **33 tests** and the full local Python regression
passes **412 tests** with deprecation warnings treated as errors. This remains
local input-integrity evidence: Ruff cannot parse the repository's `py314`
target in the installed binary, Pyright is unavailable, and no calibration,
external anchor, cross-platform, hosted, signed-release or selective-test
promotion claim is made.

**M6 FFI transaction-policy boundary continuation (2026-09-03):**
implementation commit `e0731f698302a0791c70c24d93b696597e04180d` closes a
stringly-typed fail-open path in `core/rust/src/ffi/eac.rs`: `aegis_eac_batch`
now accepts only the two declared `TransactionPolicy` values,
`HaltOnFailure` and `ContinueOnFailure`, and returns a typed `PyValueError` for
any other policy instead of silently selecting `ContinueOnFailure`. Direct Rust
tests cover both rejection and explicit-policy execution. The complete Rust
package library suite passes **443 tests** with `cargo fmt --all -- --check`
and `cargo clippy -- -D warnings`; the inventory consequently records the new
Rust unit-test surface (**117** items total, **116** unmapped). This is a local
FFI contract hardening only: it does not prove whole-FFI lifetime/allocation
safety, hidden adapter effects, cross-process authority, external containment,
or release certification, so M6 and `LAB-AUTH-001` remain `OPEN_LOCAL`.

**M6 FFI session-index initialization continuation (2026-09-03):**
implementation commit `f95bbcb03c1c8448afbcf392b38dcd964b9a6f3f` removes the
remaining process-initialization `unwrap()` at the Python/Rust session-index
boundary. `SessionSearchIndex::new` is now retained as a fallible, once-only
initialization result; all learning FFI callers propagate initialization or
lock failures as typed `PyRuntimeError` values instead of panicking the native
process. `cargo fmt --all -- --check`, `cargo check -p aegis-nerve
--no-default-features --lib`, `cargo clippy -p aegis-nerve --no-default-features
--lib -- -D warnings`, and the complete package library suite pass **443
tests** (0 failed). This hardens one local panic boundary without changing the
public Python schema or proving FFI-wide lifetime/allocation, cross-process
authority, soak, external-containment, or release evidence; M6 and
`LAB-AUTH-001` remain `OPEN_LOCAL`.

**AESE forged-input boundary continuation (2026-09-03):**
implementation commit `1d383a0eaaacf00bb5a2693d5bdc0272fc61e01e` closes three
runtime type-confusion paths in the shadow primitives. Runtime-profile
projection now rejects non-mappings; cross-hardware prediction rejects
duck-typed or forged anchor objects before reading identity/features; and
anchor planning rejects forged candidate objects and classifies any invalid
candidate as `EXTERNAL_VERIFICATION_BLOCKED` rather than
`INSUFFICIENT_BUDGET`. Three adversarial regressions cover these paths. The
focused AESE/preflight suite passes **36 tests** and the full Python regression
passes **415 tests** with deprecation warnings treated as errors. This is local
input-integrity evidence only: the AESE layer remains `SHADOW`, no selection or
promotion is enabled, and calibration, external anchors, hosted isolation,
independent replication, and release authority remain unproven.

**AESE claim-graph and affected-closure continuation (2026-09-03):**
implementation commit `fcbfc3e5522d10963ff0d8a3f18f58655c8973aa` extends the
source-backed graph with ten explicit AESE claims (`AESE-001` through
`AESE-010`) covering adaptive measurement, hardware/workload modeling,
simulation separation, OOD prediction, anchor planning, coverage, preflight,
inventory and graph governance. Verification paths now bind explicit AESE test
files to those claims, increasing the graph to **45 claims, 45 contracts, 45
invariants and 90 verification references**; **9/117** evidence surfaces are
mapped exactly and 108 remain unmapped rather than guessed. The preflight
closure now preserves multiple edges per relation (an invariant may have many
verification refs) and seeds the closure from changed mapped test surfaces;
unknown/unmapped paths still widen to all retained evidence. Focused inventory,
graph and preflight tests pass **16 tests**. This improves traceability and
prevents under-approximate future selection, but remains `SHADOW`: no test is
skipped, promoted or deleted, and calibration, external anchors, hosted
verification and release authority remain unproven.

**AESE milestone closure recorded at source state `8fd3493` (2026-09-03):**

The following envelope was captured immediately after the single push of
source state `8fd3493`; later documentation-only commits do not rewrite this
historical evidence record.

```text
SOURCE_HEAD: 8fd349305f4fb4ddc9ab193e5adbef63bcbf0810
BRANCH: main
ORIGIN_MAIN: 8fd349305f4fb4ddc9ab193e5adbef63bcbf0810
WORKTREE_STATUS: CLEAN
WORKTREE_EPOCH: 75d238151e635de6492e609449a421e9ae77651035316d2c68855139ea885c59

LOCAL_IMPLEMENTATION: COMPLETE_FOR_CURRENT_SCOPE
LOCAL_VERIFICATION: PASS_FOR_DECLARED_LOCAL_SCOPE
HOSTED_VERIFICATION: BLOCKED_EXTERNAL
RELEASE_STATUS: NOT_READY / BLOCKED_EXTERNAL

CURRENT_PHASE_BLOCKERS: NONE_FOR_LOCAL_AESE_DEVELOPMENT
PROJECT_WIDE_UNRESOLVED: LAB-AUTH-001 OPEN_LOCAL; LAB-BROWSER-002,
  LAB-RESEARCH-003, LAB-PHYS-004, LAB-BENCH-005, LAB-OPS-007 OPEN_EXTERNAL;
  LAB-RELEASE-006 BLOCKED_EXTERNAL

AESE_PHASE: PHASE_0/1/2_SHADOW_CONTINUATION
AESE_MODE: SHADOW
EXISTING_TESTS_MAPPED: 9/117 evidence surfaces exact; 108 remain NOT_MAPPED
EXISTING_TESTS_SUPERSEDED: 0
EXISTING_TESTS_DELETED: 0
KNOWN_REGRESSIONS_OLD_SYSTEM_CAUGHT: NOT_MEASURED — no paired campaign
KNOWN_REGRESSIONS_AESE_CAUGHT: NOT_MEASURED — no paired campaign
CRITICAL_MISSES: NOT_MEASURED — shadow comparison has not run
LOCAL_CPU_COST_OLD: NOT_MEASURED
LOCAL_CPU_COST_NEW: NOT_MEASURED
WALL_TIME_OLD: NOT_MEASURED — no paired baseline
WALL_TIME_NEW: NOT_MEASURED — grouped regression timing is not a paired saving claim
HOSTED_MINUTES_OLD: NOT_MEASURED — hosted jobs had no allocated runner
HOSTED_MINUTES_NEW: NOT_MEASURED — no hosted execution requested
MODEL_STATUS: local schemas and fail-closed guards only; calibration NOT_STARTED
OOD_STATUS: local classification is fail-closed; external domain validation NOT_VERIFIED
STATISTICAL_VALIDATION: stopping rules implemented; calibrated error/coverage NOT_MEASURED

SAFE_TO_CONTINUE = YES
SAFE_TO_ENABLE_SELECTIVE_TESTING = NO
SAFE_TO_DELETE_LEGACY_TESTS = NO
SAFE_TO_ENABLE_RELEASE_AUTHORITY = NO
```

The local Python regression is **416/416 PASS** with deprecation warnings
treated as errors: 350 Lab-runtime tests, 50 gate/contract tests and 16 AESE
inventory/graph/preflight tests. Inventory, graph, document-consistency and
diff gates pass; the Rust 443/443 package-library evidence remains valid from
the prior source-bound run because this milestone changed no Rust source.
This record is local evidence only. No hosted workflow was rerun, no CI result
is inferred, no benchmark savings are claimed, and all legacy runners remain
authoritative while unknown dependencies widen conservatively.

**M2 process-containment continuation (2026-09-03):** implementation commit
`fec53d1bd6bbc4eacaf004f07ed07bb4b1ee7582` hardens the opt-in local
`ProcessExecutionCell`. POSIX workers enter a private process group and
timeout/cancellation cleanup terminates that group; Windows uses the scoped
`taskkill /PID <worker> /T /F` operation when available, with the existing
direct terminate/kill fallback. A descendant-process regression proves the
bounded local witness on this Windows checkout; the process cell remains
opt-in and no host-wide PID is ever targeted. The Lab-runtime suite passes
**351 tests**, including the new descendant case, and the complete local
Python count is **417 tests** when combined with the unchanged 50 gate/contract
and 16 AESE inventory/graph/preflight tests. Inventory and claim graph were
regenerated from the new test surface and both drift gates pass. This closes
the observed local descendant-cleanup gap only; arbitrary adapter side
effects, resource quotas, kernel Job Object/cgroup enforcement, hosted
multi-process single-writer authority and rollback of already-emitted external
effects remain unproven under `LAB-AUTH-001`/`LAB-OPS-007`.

**AESE anchor-plan fail-closed continuation (2026-09-03):** implementation
commit `8660d56b7ebc56332e679d289a317da8806c8756` hardens
`select_anchor_plan`. Invalid budget metadata, a mixed valid/invalid candidate
container, mandatory-anchor unavailability and planned-cost floating-point
overflow now invalidate the whole plan as `EXTERNAL_VERIFICATION_BLOCKED` and
clear `selected_anchor_ids`/planned spend; a seemingly executable subset is
never exposed after a control-input failure. The planner remains
`PLANNED_NOT_EXECUTED`, does not contact a runner and preserves all legacy
tests. Two new adversarial tests pass; the AESE primitive suite is **31
passed**, and the grouped local Python evidence is **419 tests** total when
combined with 351 Lab-runtime, 8 inventory/graph and 8 preflight tests. This
is input-integrity evidence only: it does not establish anchor availability,
hosted execution, model calibration, external OOD coverage or release
authority.

**AESE forged-protocol/container boundary continuation (2026-09-03):**
implementation commit `4dfc15e864f03b26e553ddbd8514ea29477f83a1` tightens the
shadow trust boundary to exact runtime types for prediction inputs and for
anchor/candidate containers. Duck-typed model, hardware and workload objects
now return `INSUFFICIENT_EVIDENCE` with typed `*_invalid:TypeError` reasons
before any identity, feature or execution-relevant property is read; custom
sequence-like containers are rejected rather than iterated implicitly. The
string metadata fallback remains sanitized for malformed exact model instances,
and invalid containers cannot expose a partial anchor plan. Two new adversarial
test functions cover five rejection cases; an existing malformed-model
regression now also asserts metadata sanitization. The full local Python
regression is **421/421 PASS**, and `ruff` passes for both changed files.
The strict `pyright` run still reports **16 pre-existing** unnecessary-
`isinstance` diagnostics in unrelated pre-existing branches; no new diagnostic
was introduced by this continuation. Inventory and claim graph were regenerated
at source head `4dfc15e...` and both `--check` gates pass. This is local
input-integrity evidence only: AESE remains `SHADOW`, no selection/promotion is
enabled, and calibration, external anchors, hosted isolation, independent
replication and release authority remain unproven.

**AESE measurement/profile boundary continuation (2026-09-03):**
implementation commit `5629579f6bf2bb993c318cc61907b14f48504872` extends the
fail-closed rule to adaptive measurement and hardware-profile ingestion.
Measurement evaluation now accepts only canonical list/tuple containers and
the exact `AdaptiveMeasurementSpec` type; forged subclasses or containers with
hostile iteration are rejected before invocation, with explicit invalid-input
reasons and `INSUFFICIENT_EVIDENCE` status. Runtime profiles reject malformed
CPU/OS nested values instead of silently projecting them to `UNKNOWN`.
`AnchorObservation` now rejects subclassed hardware/workload vectors, and
analytic models reject non-canonical coefficient/domain containers before
iteration. Three new adversarial tests plus nested-vector/model coverage bring
the AESE primitive suite to **36 passed**. After regenerating the registries
from this source head, the full local Python regression is **424/424 PASS**.
The changed AESE file passes `ruff` and strict `pyright` with **0 errors**.
This remains local input-integrity evidence only: no adaptive stopping claim,
hardware calibration, cross-platform measurement, external anchor, hosted
isolation or release authority is established.

**Historical checkout identity for the prior continuation (2026-09-03):**
`SOURCE_HEAD = 0d1e9c3eb4db86aea0fee40134f5711395e55e27`,
`WORKTREE_STATUS = CLEAN`, `WORKTREE_EPOCH =
ddad61ab234932d25c4459a8106140b4c04e688fb3e397d86381c12629bc9301`, and the
recorded inventory/graph source tree digest is
`7cca1399da41e6cb968e0a9368d0d1882e312eeb327accd997b916d152cbe5ac`.
`origin/main` was still at the prior documentation closure while this snapshot
was prepared; parity must be rechecked after the normal single push. These
identifiers bind the local evidence and are not hosted or signed-release
attestation.

**AESE adaptive stopping validity continuation (2026-09-03):** implementation
commit `104105632427d3adf259d24aede039e965b2d28a` closes a local fail-open
boundary in the Phase 3 shadow primitive. An input longer than the declared
`max_observations` can no longer return `PASS`, even when all retained values
have zero variance; a trailing partial block also cannot return `PASS` after
silently dropping its remainder. Sequential checkpoints now use a
pre-registered Bonferroni alpha allocation over the maximum checkpoint budget,
and the result records
`student_t_cornish_fisher_bonferroni_peek_v1`. The async test configuration
sets `asyncio_default_fixture_loop_scope=function`, so the declared
deprecation-warning-as-error gate reaches test execution instead of failing
during pytest configuration. Two adversarial regressions were added. The AESE
primitive suite passes **38/38**, the full Python suite passes **427/427** with
`-W error::DeprecationWarning`, `ruff` and strict `pyright` pass for the changed
Python surface, and inventory/claim-graph drift checks pass after regeneration.
This is local protocol-integrity evidence only: it does not calibrate the
interval under arbitrary dependence, prove model accuracy, or enable selective
testing/promotion; external anchors, hosted verification, independent
replication and release authority remain unavailable.

**Architecture-validator ownership continuation (2026-09-03):** implementation
commit `eb852553897e34f6fe0ff4b23d7f0ffbfd800aa8` updates the two structural
validators to follow the canonical FFI module split (`ffi/compat.rs`,
`ffi/hot.rs`, and `ffi/runtime.rs`) instead of treating the facade file as the
implementation owner. The checks remain fail-closed and now pass **23/23
architecture-fitness checks** and **180/180 constitution checks**; no runtime
claim is inferred from this structural correction. This removes validator
drift only. Hosted execution, cross-platform containment, model calibration,
independent replication and release authority remain unresolved.

**AESE Phase 0 inventory-schema continuation (2026-09-03):** implementation
commit `8e2215cac6388cb40a0e6376c6147624b5815331` makes the directive's
required per-item metadata explicit in the generated inventory. Every retained
surface now carries `current_status`, `risk`, `cost`, `platform`, and
`release_critical`; unknown values remain `UNKNOWN`, while `current_status`
remains `NOT_VERIFIED` and is explicitly not a test result. The default
disposition is still `RETAIN_UNCHANGED`, no legacy runner is superseded or
deleted, and the registry/graph remain `SHADOW`. Inventory, graph and preflight
regressions pass **16/16** after regeneration; the inventory remains **117
items** with **108** unmapped surfaces requiring conservative widening. This
closes a metadata-completeness gap only; it does not map every test function,
prove runner execution, or enable selective testing.

**AESE session-boundary continuation (2026-09-03):** implementation commit
`dc1ca14072bfc363f278bced90558e0d1adf590e` applies the same canonical-container
rule to `AdaptiveMeasurementSession.append_warmups` and
`append_observations`. Hostile or duck-typed sequences are rejected before
iteration instead of being coerced into session state; two adversarial
regressions pass, bringing the primitive suite to **39/39**. This is local
input-boundary evidence only and does not change SHADOW authority, statistical
calibration, external-anchor status or legacy-runner retention.

**AESE/Rust startup-boundary continuation (2026-09-03):** implementation commit
`cad537e42ee3a1a48e07d1d63801076a947812e4` hardens
`AsyncShadowSealer::start_with_queue_depth` by constructing the Tokio runtime
before the worker thread is spawned. Runtime construction failure now returns
the typed `HotEngineError::SealFailed` from `start()` instead of allowing a
background `expect` to panic after startup reported success. The success path,
queue semantics, replay contract and public ABI are unchanged. The source-bound
Rust library suite passes **443/443**; `cargo fmt --all -- --check`,
`cargo check -p aegis-nerve --no-default-features --lib`, and
`cargo clippy -p aegis-nerve --no-default-features --lib -- -D warnings` pass.
This is a local startup-failure containment proof only: it does not prove
universal process containment, cross-platform runtime behavior, hosted
verification or release authority.

**Memory budget-boundary continuation (2026-09-03):** implementation commit
`8aef2ea4c419ae0463334ad9124ed230b7040b31` closes a fail-open path in
`CogniFoldStore::ingest`. A budgeted `GenerationalSlab` is now accessed through
`try_insert`; slot/payload exhaustion returns the existing typed error instead
of panicking through the compatibility `insert` method. The failed admission
does not append to `order` or alter payload accounting, proven by a regression
that fills a one-slot/four-byte budget and attempts a second frame. The full
Rust library suite passes **444/444**, with format, check and clippy warning
gates passing. This is local resource-boundary evidence only; it does not
prove global memory enforcement, process containment, or release authority.

**Memory-fold complexity continuation (2026-09-03):** implementation commit
`02e35c192e6142ed5a771d8c5fd0b543522d460f` removes a statically identified
quadratic rescan from `deduplicate_edges`. Because the function sorts by
`(from_node, to_node, edge_id)`, equal endpoint pairs are contiguous; merging
only with the last compacted edge preserves the existing maximum-weight and
latest-access selection while reducing the compaction pass from a prior
worst-case `O(n²)` scan to `O(n)` after the required `O(n log n)` sort. The
existing duplicate-edge regression and the complete Rust library suite pass
**444/444**; format/check/clippy gates pass. No runtime speedup is claimed
without a paired benchmark; this is a source-level complexity proof only.

**AESE anchor-planner duplicate-input continuation (2026-09-03):**
implementation commit `4c0a3a4a045fcfd48144b7f50298b97a07b87620` closes a
fail-open planner boundary. Duplicate `anchor_id` candidates now mark the
input invalid, clear all selected anchors and planned spend, and return
`EXTERNAL_VERIFICATION_BLOCKED` rather than silently deduplicating into a
seemingly executable plan. A direct regression covers the duplicate case;
the AESE primitive suite passes **40/40**, the full Python regression passes
**520/520** with deprecation warnings treated as errors, and changed-file
Ruff/Pyright pass. This proves input-integrity behavior only; it does not
execute hosted anchors, validate model calibration, or authorize selective
testing.

**AESE prediction-anchor provenance continuation (2026-09-03):**
implementation commit `86f58b8df19985689b2a7e13be29cb27a9590787` closes the
corresponding cross-hardware fail-open path. A prediction is now refused when
duplicate anchor identities appear even if two distinct anchors remain after
deduplication; the result is `INSUFFICIENT_EVIDENCE` with
`duplicate_anchor_id`, while unique anchor evidence hashes remain visible for
diagnosis. The focused AESE suite passes **41/41**, the full Python regression
passes **521/521** with deprecation warnings treated as errors, and changed
file Ruff/Pyright pass. This protects provenance integrity only; it does not
validate model calibration, OOD generalization or external anchor execution.

**M2 sandbox memory-bound arithmetic continuation (2026-09-03):** implementation
commit `d76d523142ce8f8b726b84c692f3b20a7948ab08` removes unchecked page-to-byte
multiplication from both the Wasmtime and QuickJS bridge store-limit paths.
`memory_limit_bytes` now uses checked conversion and multiplication, returning
`TrapReason::InvariantViolation` instead of wrapping on a narrow target or
accepting an impossible memory limit. A direct overflow regression and a
valid-page boundary assertion pass; the complete Rust library suite passes
**445/445**, with `cargo fmt --all -- --check`, package `cargo check`, and
`cargo clippy -- -D warnings` passing. This is arithmetic/input-boundary
hardening only: it does not prove OS-enforced quotas, non-cooperative process
containment, cross-platform execution, or release authority.

**M2 candidate-capsule parser arithmetic continuation (2026-09-03):**
implementation commit `91e10584a09b791f4fcf6ee9720a354033532faa` checks the
`candidate_count * CANDIDATE_BYTES` multiplication before adding the capsule
header length. A malformed count therefore returns `InvalidCapsule` instead of
wrapping on a narrow target and reaching an inconsistent length or allocation
path. The existing canonical capsule round-trip/tamper regression passes; the
complete Rust library suite remains **445/445**, with format/check/clippy gates
passing. This is binary-parser arithmetic hardening only: it does not prove
cross-platform archive recovery, memory quotas, process containment or release
authority.

**M7 binary-replay width-boundary continuation (2026-09-03):** implementation
commit `5cdaa7ca57a297ac7d10083d51d2b1ce9efb312e` replaces unchecked `u64` to
`usize` casts in the binary replay segment readers with fallible conversions.
File length, header size, record size and declared event count now fail closed
when they exceed the target width; mapped length comparisons also preserve the
full `u64` value. The existing bad-header/tamper regression passes, and the
complete Rust library suite remains **445/445** with format/check/clippy gates
passing. This closes a parser portability boundary only; it does not prove
crash-prefix recovery under hostile files, cross-platform archive behavior,
concurrent writers or release authority.

**M7 manifest-count allocation continuation (2026-09-03):** implementation
commit `8f1043b21abaefb712092971f84a4ff469b5bdfc` removes pre-allocation from
persisted manifest event counts and replaces remaining `u64` to `usize` casts
with checked conversions across column scans, deterministic replay, full mmap
reads, prefix recovery and replay-chaos accounting. Manifest validation now
rejects counts that cannot fit the target width and fails closed when the
event-id chain would overflow; a `u64::MAX` declared count is rejected against
a real one-row archive without a giant allocation or process panic. The new
regression and complete Rust library suite pass **446/446**, with
format/check/clippy gates passing. This is resource/parser-boundary hardening;
it does not prove hostile archive recovery, cross-platform behavior, concurrent
writer authority or release certification.

**AESE provenance-ledger continuation (2026-09-03):** implementation commit
`5c2b4e5382fe0fdcbc53294e3386daf2f5508cc3` adds immutable
`EvidenceLedgerEntry`/`EvidenceLedger` primitives. Each entry binds a claim,
protocol hash, observation/proof hash, source Git SHA, environment hash and
validator identity; duplicate identities, malformed digests, forged types and
promotion outside `DISABLED_IN_SHADOW` are rejected. Ledger and entry hashes
are deterministic and append-only, while `claimable_as_observed` is always
false so simulation or local evidence cannot silently become real-world
observation. The focused AESE/graph/preflight suite passes **63/63**, the full
Python regression passes **521/521**, and Ruff/Pyright pass for the changed
surface. This establishes a local provenance contract only; it does not prove
validator independence, calibrated statistics, external anchors, selective
test non-inferiority or release authority.

**AESE numeric-stability continuation (2026-09-03):** implementation commit
`00029d8179d8d5b44e962c5a1ab9b442fc5161e5` makes the adaptive measurement
evaluator fail closed when finite inputs overflow statistical reduction or
produce non-finite derived values (`estimate`, interval, precision,
autocorrelation or drift). Raw observations and their hash remain available,
but the result is `INSUFFICIENT_EVIDENCE` with `numeric_overflow`; no malformed
numeric result can reach `PASS` or `UNSTABLE` as if it were valid evidence.
Two adversarial regressions pass, and the full Python suite is **529/529** with
deprecation warnings treated as errors; Ruff and Pyright pass. This closes a
local numerical-integrity boundary only; it does not calibrate error coverage
under arbitrary dependence, prove model validity, or enable selective testing
or release promotion.

**AESE finite-feature/prediction boundary continuation (2026-09-03):**
implementation commit `8473b1e511496eb6d8b4078626b182a8d09df47f` makes
`_is_finite` tolerate conversion overflow and rejects integer capability or
working-set values that cannot become finite numeric features. The
cross-hardware predictor now refuses a model whose finite inputs produce a
non-finite estimate, interval, distance or half-width, returning
`INSUFFICIENT_EVIDENCE` with `numeric_overflow` instead of
`PREDICTED_IN_DOMAIN`. The focused AESE suite passes **51/51**, the full
Python regression passes **531/531** with deprecation warnings treated as
errors, and Ruff/Pyright pass. This closes local numeric-input/output
integrity only; it does not prove model calibration, OOD domain validity,
independent replication, physical energy measurement, external anchors or
release authority.

**M7 segmented-writer exclusivity continuation (2026-09-03):** implementation
commit `39b7b38ff169e80920512e12eba30018de50d40d` adds a bounded regression
for `SegmentedArrowAuditStream`: a second writer for the same directory/run is
rejected by the exclusive lock, the lock is released on `drop`, and a later
writer can acquire it without leaving the scoped temporary lock file. The
complete no-default-features Rust library suite passes **447/447**;
`cargo fmt --all -- --check`, `cargo check` and `cargo clippy -- -D warnings`
pass. This proves same-host lock exclusivity and normal-drop cleanup only; it
does not prove crash-stale-lock recovery, hosted multi-process authority,
cross-platform filesystem semantics or release certification.

**M6 FFI hot-batch arithmetic continuation (2026-09-03):** implementation
commit `b37b998d7a85ba41df9d63546b6adcd675479a73` replaces unchecked hot-arena
batch byte summation with `checked_add` and reports a typed `PyOverflowError`
if the payload count or index cannot be represented by the hash-bound `u64`
fields. Existing successful batch semantics and JSON schema remain unchanged;
the complete no-default-features Rust library suite passes **447/447** with
`cargo fmt --all -- --check`, `cargo check` and `cargo clippy -- -D warnings`.
This closes a local FFI arithmetic fail-open path only; it does not prove
FFI-wide lifetime/allocation/soak safety, cross-process authority, or release
certification.

**M6 FFI session-count boundary continuation (2026-09-03):** implementation
commit `57cc015183566f9675f6a2fe683b2711beca08d7` replaces the remaining
`usize`-to-`u64` cast in the learning/session statistics binding with a
fallible conversion and typed `PyOverflowError`. Existing learning-stat
semantics and Python schema remain unchanged; the complete no-default-features
Rust library suite passes **447/447** with formatting, compilation and
`clippy -D warnings` gates passing. This closes one local FFI representation
boundary only; FFI-wide lifetime/allocation/soak safety, cross-process
authority and release certification remain unproven.

Không được gọi toàn hệ thống “production-ready” khi bất kỳ gate bắt buộc nào
ở trên còn `OPEN_*`, `BLOCKED_*`, `UNKNOWN` hoặc chỉ có fixture/mock evidence.

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
