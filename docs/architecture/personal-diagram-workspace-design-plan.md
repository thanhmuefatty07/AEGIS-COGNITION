# AEGIS — Personal Diagram Workspace

Ngày: 2026-09-30. Trạng thái tại thời điểm soạn: đề xuất thiết kế và hướng dẫn triển khai; trạng thái runtime sau ngày đó chưa được xác minh trong tài liệu này.

Tài liệu này điều chỉnh phần trình bày của [kế hoạch Personal Code Map](personal-code-map-implementation-plan.md). Hợp đồng backend, source policy, retrieval, event replay, approval, cancellation, memory lifecycle và benchmark trong A6/C của kế hoạch đó vẫn áp dụng. Khi có xung đột về bố cục giao diện, dùng tài liệu này. Không dùng bản phác hoặc test prototype cũ làm bằng chứng cho sản phẩm mới.

Phạm vi lần này chỉ là nghiên cứu và thiết kế **chức năng sản phẩm AEGIS cho workspace của người dùng**. Đây không phải yêu cầu áp dụng bản đồ/memory mới vào repo đang dùng để phát triển AEGIS. Không đổi source sản phẩm, dependency, runtime, cấu hình, license, cloud, CI hoặc Git remote. Các đoạn contract dưới đây là **proposal cho lần triển khai chức năng sau**, không phải API hiện hữu.

## 1. Quyết định thiết kế và điều còn cần xác nhận

**Hướng người dùng đã chọn:** workspace mở vào **bản đồ code của toàn dự án**, mỗi agent có avatar ổn định ngay trên phần code có hoạt động được ghi nhận. Người dùng bấm agent hoặc vùng code để xem agent đang làm gì, nguồn/thay đổi/bằng chứng và thao tác có thể kiểm soát.

Người dùng làm rõ thêm: chức năng này tích hợp **memory graph của AEGIS** để phục vụ truy xuất ngữ cảnh tiết kiệm token trong các workspace người dùng mở bằng sản phẩm. Vì vậy topology code, memory có provenance và live activity là các lớp của cùng một năng lực sản phẩm. Tên folder/file trong hình chỉ là workspace giả lập; không yêu cầu mỗi dự án phải có module memory hoặc giống cấu trúc AEGIS.

Người dùng đã trả lời: “bản đồ code space của toàn bộ dự án như một map rồi thấy cùng lúc agent làm việc nếu có rồi chúng ta cần biết gì bấm vô cho người dùng xem agent đang làm gì”. Vì vậy **Bản đồ dự án** là chính; **Hoạt động** mở trong inspector/task detail. Không chia màn hình thành hai canvas mặc định. Lựa chọn này chốt preference thiết kế, không tự cấp phép triển khai sản phẩm.

| Phương án | Có lợi khi | Hạn chế | Quyết định tạm thời |
|---|---|---|---|
| Luồng công việc chính; code map riêng | Người dùng muốn hiểu agent đang làm gì, chờ ai, sửa gì | Cần một thao tác để xem cấu trúc code | Không chọn làm màn hình chính; giữ cho chi tiết hoạt động |
| Code map chính; marker agent trên file | Nhìn toàn dự án và thấy các agent cùng lúc | Cần inspector riêng cho approval, retry và việc không gắn file | Người dùng đã chọn; đây là hướng chính |
| Hai canvas cạnh nhau | Màn hình rất rộng và cần đối chiếu thường xuyên | Chia diện tích; tăng tải đọc và đồng bộ selection | Chỉ thêm sau khi người dùng chứng minh nhu cầu |

Không kết luận phương án đã chọn tốt nhất cho mọi người. Cần review phác thảo và thử các hành trình ở mục 16 trước khi khóa UI.

## 2. Mục tiêu phải giữ đúng

1. **Agent tìm nguồn hiệu quả:** nhận map/query/source pack có cấu trúc, giới hạn và provenance. Agent không phải đọc ảnh canvas để tìm code.
2. **Người dùng nhìn thấy công việc:** sơ đồ trình bày thao tác và trạng thái có dữ liệu, không phỏng đoán suy nghĩ bên trong model.
3. **Người dùng kiểm soát:** biết thao tác nào cần duyệt, đang chờ điều gì, đã thay đổi gì và dừng có hiệu lực đến đâu.
4. **Local cá nhân:** một người và nhiều agent/worker được người đó kết nối; không cần AEGIS host workspace.
5. **Memory vẫn là năng lực sản phẩm:** memory liên quan có nguồn, scope, lifecycle và độ mới; avatar/sơ đồ không thay thế memory hoặc engine retrieval.

Giảm token vẫn là giả thuyết cần paired benchmark với workflow hiện có. Diagram và avatar có chi phí render, chưa chứng minh giảm token hoặc tăng năng suất. Không đưa phần trăm tiết kiệm từ fixture, ước lượng một lần, hoặc số lượng file ẩn khỏi canvas vào marketing.

### 2.1 Năng lực sản phẩm: một graph có cấu trúc, hai cách sử dụng

AEGIS mở workspace bất kỳ được người dùng cấp quyền → index cấu trúc nguồn theo policy → liên kết với memory có scope/provenance → agent query graph và lấy source/memory pack bounded → event của các thao tác được projection lên diagram cho người dùng.

Agent dùng các DTO/query của graph; người dùng dùng hình và inspector. Không gửi ảnh diagram hoặc toàn graph vào prompt mỗi lượt. UI phản chiếu nguồn dữ liệu và hoạt động được cung cấp bởi tích hợp; nó không tự tạo memory, chạy tool hoặc đổi policy khi người dùng di chuyển node.

Memory graph giữ tri thức/lịch sử có nguồn và lifecycle; code graph giữ cấu trúc/source revision. Presentation có thể đặt hai loại node cạnh nhau nhưng **không hợp nhất ownership, freshness hay authority của chúng**. Đây là cách mở rộng sản phẩm cho người dùng cá nhân và các agent kết nối, không quy trình tăng tốc riêng cho agent đang phát triển repo này.

### 2.2 Lớp memory trên map chính

Overview hiện cấu trúc dự án đã index. Khi người dùng chọn vùng code/agent/task, chỉ hiện memory liên quan tới scope/query đó; có toggle “Memory liên quan”. Không bung toàn memory store thành hàng nghìn node cạnh code ở mặc định.

Memory node có nhãn loại riêng, title ngắn, scope, lifecycle và freshness; không dùng icon file hoặc status “file đã xong”. Inspector memory phải có memory ID/type, project/session scope, nguồn tạo/cập nhật, references/evidence, revision/hash nếu có, trạng thái active/stale/retracted và reason khi không thể dùng. Trường freshness/lifecycle phải map từ API thật; không thêm trạng thái như thể đã có ở backend.

Link code–memory dùng `references_source` khi có source ref đã kiểm tra; `retrieved_related` là ứng viên liên quan từ retrieval, không causal proof, hiện nét/nhãn riêng. Memory cũ mâu thuẫn source current → marker stale/conflict và revalidate theo lifecycle hiện có; không làm sống lại memory đã retract hoặc tự ghi đè tri thức cũ dựa hình.

Một actor có footprint ở code và memory refs: inspector tách “Nguồn đã đọc” / “Memory đã dùng” / “Mục chưa đưa vào ngữ cảnh”. Có memory retrieval event chưa biết nó được đưa vào prompt thì chỉ ghi “Đã tìm được”, không “Agent đã dùng”. Memory review/forget/update chỉ hiện khi owner có command/capability thật; diagram không cấp quyền mới.

### 2.3 Cơ chế tiết kiệm token cần triển khai và đo

1. Query theo task và scope → lấy graph summary/neighborhood có cap.
2. Kiểm tra source/memory freshness, quyền đọc và provenance trước admission.
3. Chọn exact source ranges + relevant memory vào context budget hiện có, loại duplicate theo stable refs/hash.
4. Cache/reuse chỉ khi binding/revision/policy còn hợp lệ; thay source hoặc scope phải invalidation.
5. Thiếu dữ liệu → bounded search/read fallback; không bắt agent dùng memory sai chỉ để giảm token.
6. Ghi context manifest và usage đầy đủ, bao gồm retrieval overhead, cache repair và mọi attempt.

Thành công phải đồng thời giữ task quality và giảm tổng usage trên workload định nghĩa. Diagram đẹp, context pack ngắn hoặc memory hit rate cao không tự chứng minh tiết kiệm. Benchmark giữ chất lượng trước rồi mới so efficiency, dùng baseline retrieval hiện hữu theo kế hoạch gốc C14; không coi toàn bộ repo trong prompt là baseline mặc định.

## 3. Đọc ảnh tham khảo như thế nào

Ảnh người dùng đưa có canvas sáng, nhiều khoảng trắng, luồng trái → phải, ô hành động bo góc, điểm quyết định hình thoi, nhãn trên nhánh và đường quay lại. Nhánh lỗi nằm dưới luồng chính, giúp phân biệt trường hợp thường gặp với ngoại lệ.

Áp dụng **cách tổ chức hình**, bỏ toàn bộ nội dung nghiệp vụ của ảnh. Không dùng các bước tải executable, SHA-256 hoặc phân tích malware cho AEGIS.

| Yếu tố | Áp dụng cho AEGIS | Điều phải tránh |
|---|---|---|
| Hướng đọc rõ | Đường chính trái → phải; nhánh duyệt/lỗi ở dưới | Các ô rải thành grid rồi nối tùy ý |
| Action / decision khác hình | Hành động bo góc; nhánh điều kiện hình thoi | Mỗi trạng thái đổi sang một loại hình khác |
| Nhãn nhánh ngắn | “Đã được phép”, “Cần duyệt”, “Lần thử mới” | Mũi tên không có nghĩa hoặc nhãn Yes/No thiếu câu hỏi |
| Khoảng trắng | Edge có hành lang riêng; panel chi tiết mở khi cần | Nhét source, log và token vào tất cả node |
| Avatar nhỏ | Gắn actor vào node/roster | Mascot che nội dung hoặc chạy lang thang không có dữ liệu |
| Phong cách nhẹ | Nền sạch, màu nhạt, nét rõ, chữ dễ đọc | Sao chép font viết tay vào body nhỏ hoặc dùng màu làm tín hiệu duy nhất |

## 4. Hồ sơ minh họa

Các phác thảo hình ảnh, prototype và bản xuất phục vụ nghiên cứu đã được lưu ngoài repo cùng metadata để giữ provenance. Chúng dùng dữ liệu giả lập và chỉ minh họa hướng trình bày; không phải yêu cầu về cấu trúc file của người dùng, bằng chứng runtime hay nghiệm thu accessibility.

Hướng sản phẩm được chốt bằng các tiêu chí ở tài liệu này: bản đồ code toàn workspace là mặt chính; agent, memory có provenance và hoạt động được giải thích trong inspector. Implementation phải được xác minh trên giao diện/runtime hiện tại, độc lập với các hình phác.

## 5. Kiến trúc thông tin

### 5.1 Bản đồ chính và hai cách xem bổ sung, khác nghĩa

| Chế độ | Câu hỏi trả lời | Node / edge | Không được suy ra |
|---|---|---|---|
| Bản đồ dự án — mặc định | Toàn dự án gồm gì, agent nào đang làm ở vùng nào? | Project/directory/file/symbol và quan hệ có provenance; actor overlay riêng | Thứ tự agent thực hiện hay quyền chạy thao tác |
| Hoạt động — mở từ agent/task/inspector | Agent làm gì, bước nào đang chờ, kết quả ra sao? | Task/action/decision/evidence; phụ thuộc/thứ tự/nhánh/lần thử | Call graph code, suy nghĩ bên trong model |
| Lịch sử | Execution/lần thử trước đã ghi nhận gì? | Snapshot + event/evidence tại thời điểm chọn | Run cũ vẫn đang chạy hoặc approval cũ còn hiệu lực |

Chuyển chế độ giữ binding project/checkout, task đang chọn khi phù hợp, filter và camera riêng từng chế độ. Link “Xem trên bản đồ code” dùng source reference được host kiểm tra. Nếu revision không còn khớp, hiển thị “Nguồn đã thay đổi” và cho chọn bản lưu/current, không âm thầm dẫn tới dòng khác.

Memory liên quan mở từ inspector của task/source; không dựng một forest memory node trộn lẫn với flow mặc định. Chỉ mở relation view riêng khi người dùng cần truy nguyên.

### 5.2 Bố cục màn hình rộng

- Header: workspace, checkout ngắn, trạng thái kết nối; nút giao việc thuộc shell hiện có.
- Task strip: yêu cầu ngắn, trạng thái run thật, “Yêu cầu dừng” nếu hỗ trợ.
- Mode strip: Bản đồ dự án / Hoạt động / Lịch sử; Bản đồ dự án selected khi mở workspace; search và “Vừa khung”.
- Agent roster: avatar + tên + vai trò ngắn + trạng thái chữ; chọn để lọc, không tự dispatch.
- Canvas chiếm diện tích chính; legend gọn có thể mở rộng.
- Inspector bên phải chỉ mở khi chọn node; rộng đề xuất 360–420 px.
- Thanh dưới: “Theo dõi bước mới”, zoom, “Danh sách các bước”, số mục đang ẩn có phạm vi rõ.

Kích thước đề xuất: viewport ≥1440 rộng có inspector cùng canvas; 1000–1439 dùng inspector phủ một phần có thể đóng; <1000 dùng canvas hoặc inspector từng trang. Dưới 720 px mặc định cây thư mục/file có actor chips; task detail dùng danh sách theo thứ tự. Nút “Xem sơ đồ” vẫn có pan/zoom. Ngưỡng là thiết kế cần kiểm tra với shell thực, không phải giới hạn OS.

### 5.3 Bản đồ toàn dự án — đủ tổng thể, không nhét mọi file vào một khung

“Toàn dự án” nghĩa overview có các root/folder hợp lệ trong phạm vi index và đường đi tới mọi file đã index, search không giới hạn theo viewport. Folder được gom thành node có số mục/coverage thật; mở folder hoặc zoom vào vùng đó để thấy file/symbol. Không đồng nghĩa đọc mọi file vào prompt hoặc render đồng thời hàng chục nghìn node.

Canonical source identity theo C3–C6 kế hoạch gốc. Khi index PARTIAL/STALE/capped/unsupported: hiện coverage và số/nhóm bị bỏ qua theo policy nếu metadata an toàn; không ghi “toàn bộ code đã phân tích”. Secrets, symlink ngoài root, dependency/generated/build bị policy loại không tự mở chỉ vì user yêu cầu map toàn dự án. Search tôn trọng cùng source policy.

Layer đầu là cấu trúc `contains`, không diamond quyết định cho thư mục. Candidate imports/lineage bật khi chọn vùng/query, có legend riêng; không vẽ mọi cạnh candidate toàn repo ở overview. Các group thu gọn giữ trạng thái bất lợi của children và số actor đang hoạt động.

Avatar overlay không thay cấu trúc graph: actor gắn file/range có exact source ref từ event. Reference tới folder-only → marker ở folder với nhãn phạm vi; không đoán file. Reference tới source revision cũ → badge “Nguồn đã thay đổi”, giữ vị trí historical có provenance hoặc vùng chưa ghép được. Task không gắn code (provider request/approval/chờ worker) nằm trong roster/activity dock “Chưa gắn vùng code”, không đặt vào file ngẫu nhiên.

Hai actor trên cùng file: cluster `+N` mở danh sách, không che source title. Một actor đang thao tác nhiều file: highlight footprint có refs thật, một anchor được chọn rõ; không nhân avatar như nhiều agent hoặc suy có lock. Group collapsed có badge count actor; chọn actor mở tối thiểu nhánh cần thiết và không bung cả repo.

Click actor → inspector **actor/task/action đang chọn** và highlight source footprint. Click file/folder → inspector **nguồn** cùng danh sách actor/hoạt động tại đó; chọn actor trong danh sách để chuyển actor detail. Actor click không chỉ mở file rồi bỏ mất hoạt động. Hover chỉ preview ngắn; không là cách duy nhất truy cập.

Inspector mặc định trả lời: “Ai?”, “Đang làm gì theo lần quan sát mới nhất?”, “Ở phần nào?”, “Đã đọc/sửa/kiểm tra gì?”, “Có cần tôi duyệt?”. Nguồn, Thay đổi, Kiểm tra, Luồng bước là các mục bổ sung; giữ map/camera khi đóng. Đường nối code không animate như dòng suy nghĩ hoặc đường agent chạy.

Preference zoom/collapse/selection scope theo project/checkout; topology và source revision theo host. Layout tree/layered deterministic, ổn định khi event activity đổi. Không re-index source để animate avatar. Observation paused/disconnected không khiến node nguồn biến thành failed. Minimap chỉ thêm nếu navigation trên fixture lớn cần; breadcrumb/root-reset/search là bắt buộc.

## 6. Ngữ pháp sơ đồ chi tiết hoạt động

Mục này áp dụng task flow khi mở Hoạt động; ngữ pháp bản đồ dự án chính là project/directory/file/symbol, `contains` và candidate relations theo mục 5.3/C4 kế hoạch gốc. Không đặt diamond approval vào cây code như thể nó là file hoặc dependency code.

### 6.1 Node

| Loại | Hình | Nội dung tối thiểu | Khi mở |
|---|---|---|---|
| Task | Pill hoặc ô đầu luồng | Mục tiêu, execution ngắn | Yêu cầu, scope, kết quả |
| Action | Rectangle bo 12 px | Động từ + đối tượng, actor, status | Thao tác, context, source/diff/log đã lọc |
| Decision | Diamond | Câu hỏi ngắn có đáp án ở edge | Điều kiện/nguồn quyết định, nhánh đã ghi nhận |
| Approval | Rectangle có nhãn “Cần bạn duyệt” | Action descriptor, scope, thời hạn nếu có | Đối tượng/diff/quyền, approve/deny thật |
| Evidence | Rectangle với biểu tượng kiểm tra | Loại kiểm tra và kết luận có phạm vi | Lệnh/phiên bản/env/artifact, failed/skipped/unknown |
| Result | Pill/rectangle cuối | Outcome từ host | Thay đổi, bằng chứng, phần còn thiếu |
| Group | Container hoặc collapsed group | Tên nhóm, số bước ẩn | Mở nhóm không đổi runtime |
| Unresolved | Ô “Thiếu dữ liệu liên kết” | Lý do/số mục chưa ghép được | Chi tiết quan sát, resync; không chế tạo parent |

Node hành động đề xuất 176–208 × 104–124 px; title 14–16 px, status 12–13 px; tối đa 2 dòng title, có full title trong inspector. Nếu phải ellipsis, giữ động từ và phân biệt bằng actor/ID. Diamond 112–136 px; câu hỏi dài dùng title ngắn + inspector, không thu chữ xuống để nhét.

### 6.2 Edge — ý nghĩa phải độc lập với nét

| Loại | Nghĩa | Nhãn mặc định | Dữ liệu cần có |
|---|---|---|---|
| `follows` | Thao tác được ghi nhận sau thao tác khác trong cùng luồng | “Sau đó” khi dễ nhầm | Quan hệ có chủ sở hữu; thứ tự event không đủ để chứng minh dependency |
| `depends_on` | B cần kết quả A | “Cần kết quả” | Dependency canonical hoặc plan provenance rõ |
| `branch` | Nhánh của một điều kiện | Đáp án cụ thể | Decision/action outcome hoặc đề xuất có nhãn |
| `retry_of` | Attempt mới nối với attempt cũ | “Lần thử mới” | Hai attempt IDs khác nhau |
| `produced` | Action tạo evidence/artifact | “Tạo kết quả” | Ledger/artifact reference |

Parent task thể hiện bằng grouping/lane hoặc inspector, không mặc định biến `parent_task_id` thành edge chạy trước. Timestamp gần nhau không chứng minh concurrency; biết hai task cùng RUNNING thì có thể thể hiện overlap được quan sát, không suy ra cùng CPU.

Nét liền: thao tác/quan hệ được ghi nhận. Nét đứt: đề xuất chưa chạy. Quan hệ thiếu dữ liệu: nhãn rõ “Chưa xác định”, không dùng nét đứt chung cho cả planned và unknown. Luồng code map giữ certainty của chính nó; `import_candidate` không đổi thành `depends_on` task.

Một decision là **điểm điều kiện được trình bày**, không phải mặc định thêm một lượt LLM “ra quyết định”. Diamond chỉ hiện khi có nhánh có nghĩa; không thêm cổng “đủ bằng chứng?” cho mỗi run nếu runtime không có quy tắc/plan/evidence tương ứng.

### 6.3 Sơ đồ sống khác sơ đồ mẫu

Sơ đồ mẫu ở FigJam giải thích use case. Sơ đồ sống không bắt mọi execution đi qua tám bước cố định. Agent chỉ đọc source có thể kết thúc mà không sửa/test; agent khác có thể không cung cấp tool events. Không vẽ bước chưa có như thể đã xảy ra.

Mặc định: observed topology + planned overlay có nhãn riêng nếu plan thật đã được công bố. Không có plan thì không vẽ sẵn “Sửa → Test → Hoàn tất”. Luồng không rõ liên kết có thể dùng cột theo thứ tự ghi nhận và ghi rõ “Thứ tự ghi nhận”, tuyệt đối không tạo dependency giả.

## 7. Trạng thái và cách giải thích trung thực

Ba chiều riêng: **run/action state**, **chất lượng quan sát**, **kết quả kiểm tra**. Không gộp thành một dấu xanh.

| Tình huống | Hiển thị | Quy tắc |
|---|---|---|
| Planned | “Dự kiến — chưa chạy”, nét đứt | Không đếm như completed |
| Running | “Đang thực hiện”, actor | Chỉ khi host đã xác nhận; duration không tự kết thúc |
| Waiting approval | “Cần bạn duyệt”, badge/nút mở | Không auto-approve theo timeout |
| Blocked | “Đang chờ …” | Lý do dependency/quota/input nếu được cung cấp |
| Cancelling | “Đang yêu cầu dừng” | Nonterminal; không biến thành CANCELLED vì mất event |
| Completed action | “Đã xong thao tác” | Không đồng nghĩa test pass hoặc task đạt yêu cầu |
| Failed | “Thao tác lỗi”, mở chi tiết | Error class + summary đã lọc; giữ evidence |
| Unknown outcome | “Chưa xác nhận kết quả” | Lost ACK/restart/in-flight side effect; reconcile trước retry |
| Disconnected observer | Banner “Mất kết nối; trạng thái lần cuối …” | Không đổi run thành Failed hoặc freeze avatar rồi gọi Idle |
| Partial events | “Thiếu một phần dữ liệu; đang đồng bộ” | Gap → snapshot/replay; chưa vẽ câu chuyện đầy đủ |
| Test passed/skipped/not run | Nhãn tách biệt và scope | Skipped hoặc Not run không màu xanh PASS |
| Run terminal | Outcome canonical | Run COMPLETED không chứng minh production-ready |

Tóm tắt mẫu: “Agent đã mở 3 đoạn nguồn để xác định chỗ xử lý quyền.” Con số chỉ từ validated source refs. “Đang chờ bạn duyệt ghi vào 2 file.” Chỉ dùng sau action descriptor thật. “Không quan sát được bước bên trong công cụ này.” Không bịa thêm bước đẹp.

Không hiển thị chain-of-thought. “Vì sao?” dùng mục tiêu do người dùng cung cấp, lý do công khai do agent công bố, hoặc quan hệ được ghi nhận; đánh dấu rõ nguồn của lời giải thích. Mặc định dùng template local, không gọi LLM mỗi event.

## 8. Avatar cho agent

### 8.1 Vai trò

Avatar trả lời **ai đang thực hiện**, không chứng minh agent đáng tin, quyền lớn hơn, model tốt hơn hoặc hoạt động đã thành công. Agent identity ≠ provider/model ≠ task ≠ attempt. Đổi model giữ actor identity khi runtime giữ cùng actor; nếu runtime tạo actor mới, tạo mapping mới.

Mỗi actor có tên chữ và ID ngắn; màu/hình có thể trùng khi đông agent. Khi trùng, suffix ID và accessible name phải phân biệt được. Nếu chưa biết actor, dùng “Agent chưa xác định”, không gán root hoặc model theo phỏng đoán.

Vị trí:

1. Roster 32–40 px + tên và status.
2. Node 24–32 px hoặc actor chip; avatar nằm trong vùng header riêng, không che title/edge.
3. Inspector 48–64 px khi cần nhận diện, không phải mascot full-screen.
4. Bản đồ code dùng marker khi có thao tác source reference hợp lệ; nhiều agent cùng file dùng stack + số lượng và popup danh sách. Marker nghĩa “hoạt động được ghi nhận tại file này”, không nghĩa đang giữ lock.

Mặc định không có bot chạy dọc edge, theo trỏ chuột hoặc nhảy khi người dùng click node. Có thể chọn avatar/style trong setting cá nhân sau; không tạo marketplace hoặc upload ảnh ở lát cắt đầu.

### 8.2 Nghiên cứu Libraries.dev

Đã xem [playground](https://libraries.dev/bots) trong trình duyệt: avatar có hình khối mềm, mắt tối và màu dễ phân biệt; có ví dụ roster. Phong cách này có thể tạo cảm giác agent hiện diện; tính phù hợp với AEGIS vẫn cần review.

[Package manifest](https://github.com/Jakubantalik/Libraries.dev/blob/main/packages/bot-avatars/package.json) tại lần kiểm tra khai báo `bot-avatars` 0.1.1, peer React ≥18, license MIT. Repo desktop hiện dùng React 19.2.8; range peer bao gồm phiên bản này, **chưa chứng minh runtime/Tauri tương thích** và chưa xác nhận tarball npm trùng source đang xem.

[README package](https://github.com/Jakubantalik/Libraries.dev/blob/main/packages/bot-avatars/README.md) mô tả 18 hình, ba state API `default`/`working`/`sleeping`, canvas 2D, `paused`, `interactive`, `seed`, reduced motion và offscreen handling. Đây là tài liệu nhà cung cấp, chưa benchmark tại AEGIS. README gốc ghi “four states” khác README package; kế hoạch dựa vào manifest/README package và yêu cầu kiểm tra types ở bản pin trước khi code.

Playground có Sleeping bị khóa Pro trong giao diện. [README repository](https://github.com/Jakubantalik/Libraries.dev) phân biệt library MIT với nội dung Studio/Pro theo plan. Không lấy asset/preset/export Pro hoặc vượt khóa. Khi dùng code MIT phải giữ [copyright/license](https://github.com/Jakubantalik/Libraries.dev/blob/main/LICENSE) và third-party notices tương ứng; không đổi license của AEGIS để che quyền của upstream. Đây là đối chiếu văn bản, không bảo đảm pháp lý cho mọi asset trong website.

### 8.3 Lựa chọn có điều kiện

| Lựa chọn | Lợi ích | Chi phí/rủi ro | Điều kiện |
|---|---|---|---|
| Avatar SVG tĩnh do dự án thiết kế | Bounded render, dễ nhìn nhỏ, không dependency mới | Ít chuyển động/cá tính hơn | Fallback bắt buộc; dùng để phác thảo |
| `bot-avatars` sau audit/pin | Hình khối/animation gần ý người dùng | Dependency mới, canvas cost, packaging/license/version phải kiểm tra | Chỉ adopt nếu visual review + packaged 3OS + cost comparator đạt |
| Sprite/pre-render từ nguồn đã có quyền | Tránh nhiều animation loop live | Asset/build pipeline, DPR/theme/state variants | Chỉ cân nhắc nếu live canvas cost không đạt và giấy phép rõ |

Không quyết định thêm package trong lần nghiên cứu này. SVG trong bản phác là hình đại diện tự vẽ đơn giản, không phải asset lấy từ website; không quảng bá chúng như avatar chính thức Libraries.dev.

### 8.4 Mapping và motion policy đề xuất

| Runtime observation | Avatar pose | Nhãn có thẩm quyền |
|---|---|---|
| Idle thật | Static/default | “Sẵn sàng” |
| Running thật + connected | Working, chuyển động nhẹ nếu được bật | “Đang thực hiện …” |
| Waiting approval/blocked | Static/default | Badge và chữ nguyên nhân |
| Cancelling | Static | “Đang yêu cầu dừng” |
| Failed/completed | Static/default | Badge lỗi/hoàn tất riêng |
| Unknown/disconnected | Static neutral | “Chưa xác nhận”/“Mất kết nối” |
| Sleeping | Chỉ khi backend có trạng thái nghỉ rõ | Không map disconnected/cancelled sang sleeping |

Nếu dùng library: `interactive=false`; pause ở history, hidden tab, offscreen, reduced motion và chế độ tắt chuyển động. Hoạt động runtime không phụ thuộc pose. Không sửa mapping để làm avatar trông vui hơn ở failed/unknown.

Giới hạn proposal: tối đa 4 avatar chuyển động thấy được cùng lúc; actor selected/running ưu tiên, phần còn lại static. Bắt đầu motion-off cho canvas dày, chỉ bật sau kiểm tra. Không render một canvas sống cho mọi node đã hoàn tất. Default pose hoặc cached static có cùng tên/status, không làm biến mất actor khi renderer lỗi.

## 9. Use case và hành vi cụ thể

| ID | Người dùng làm gì | Điều thấy/kiểm soát | Điều kiện nghiệm thu |
|---|---|---|---|
| U01 | Mở workspace chưa có run | Onboarding, map coverage, nút giao việc | Không fixture giả thành run thật |
| U02 | Giao việc nhỏ chỉ đọc | Flow đọc → kết quả có actor | Không ép sửa/test/approval không cần |
| U03 | Agent cần nguồn | Node tìm/đọc có exact refs | Context list không đọc filesystem trực tiếp |
| U04 | Mở node đang chạy | Summary, actor, source/context, latest observation | Không hứa có reasoning/tool output chưa có |
| U05 | Agent đề xuất ghi | Approval branch + descriptor/diff | Approval bind action/revision/attempt thật |
| U06 | Từ chối hoặc sửa yêu cầu | Rejected action giữ trong history; plan mới có ID/revision | Không chỉnh tên node cũ thành “approved” |
| U07 | Hai worker song song | Actor lanes/groups, dependency labels | Không suy ra concurrency từ timestamps |
| U08 | Hai agent đụng cùng nguồn | Marker stack, scope/diff/lock info nếu có | Không tự merge/lock code ở UI |
| U09 | Test lỗi và thử lại | Failed attempt giữ nguyên; attempt mới nối `retry_of` | Không overwrite artifact/test result cũ |
| U10 | Yêu cầu dừng | CANCELLING rồi terminal nếu host xác nhận | Không restart/retry vì cancel ACK chậm |
| U11 | Mất kết nối/restart | Banner, stale timestamp, resync/unknown outcome | Không duplicate dispatch hoặc approve từ cache |
| U12 | Đổi workspace khi run còn active | Explicit root binding; warning trạng thái run đúng nơi | Event cũ không vào canvas mới |
| U13 | Xem lịch sử | Snapshot/evidence của execution chọn | Controls live approval bị tắt ở historical view |
| U14 | Source đã đổi/xóa | Stale/not found; bản lưu nếu thật có | Không dẫn silently tới code current khác |
| U15 | Code lớn/nhiều bước | Group, search, hidden count, list mode | Không load/unhide mọi source để vẽ |
| U16 | Dùng keyboard/màn hình hẹp | Danh sách và inspector đầy đủ | Không bắt drag/pinch/hover để duyệt |
| U17 | Agent ngoài không tích hợp event | Card “Chỉ quan sát được …” | Không vẽ hoạt động model giả |
| U18 | Token counter thiếu | “Chưa có số liệu” | Unknown không bằng 0 hoặc % tiết kiệm |
| U19 | Memory liên quan | Source/scope/lifecycle/freshness | Retrieved result không giả thành causal proof |
| U20 | Người dùng tắt motion/giữ hình | Canvas ổn định, runtime vẫn tiếp tục | Tách giữ khung quan sát với dừng execution |

### 9.1 Click node và inspector

Một click/Enter: chọn node, mở inspector; không thực hiện tool hoặc cấp permission. Escape đóng inspector và trả focus về node/chip nguồn. Không cần double-click để khám phá action quan trọng.

Inspector thứ tự: title/status → actor và last observation → “Đã làm gì / Đang chờ gì” → phạm vi source/context → kết quả/evidence → action có capability. Tab chi tiết có thể gồm Nguồn, Thay đổi, Kiểm tra; không đặt JSON/raw stack ở mặc định.

Source click đi qua loader/checks kế hoạch C5/C6. Diff phải có base/current binding; nếu không có base thật, ghi “Chưa có diff xác nhận”. Tool log chỉ phần đã redaction và capped; expand không mở full raw payload hoặc shell passthrough.

### 9.2 Approval

Review hiện đối tượng, path tương đối, action type, permission scope, argument summary, revision, attempt, effects và evidence diff nếu có. “Đồng ý thao tác này” / “Từ chối” không cấp blanket access. Nút chỉ active nếu native capabilities + current binding hợp lệ.

Sau click gửi command có operation key; disable duplicate submit; state resolving cho đến receipt. Timeout: “Chưa xác nhận yêu cầu”; lookup receipt trước resend. Switch root/cancel/revision change/expired approval → refresh descriptor, không dùng grant cũ. Approve là khả năng runtime, không capability của SVG/Figma/preview.

### 9.3 Điều hướng canvas

- Search tên task/actor/path; kết quả có scope và action “Đưa vào khung”. Không chỉ search node đang thấy.
- “Vừa khung” fit nhóm đang lọc; có padding và zoom minimum đọc được; không auto-fit mọi event.
- “Theo dõi bước mới” là opt-in; thao tác pan/select tạm ngắt follow, có nút quay lại. Node mới không cướp focus.
- Zoom buttons, pan bằng background, danh sách thay thế. Wheel thường scroll trang/panel; zoom bằng modifier/explicit controls, kiểm tra trackpad ba OS.
- Nếu node đang chọn bị collapse/filter: giữ inspector với breadcrumb + “Hiện bước”, không chọn ngầm node khác.
- “Giữ khung để đọc” đóng băng presentation snapshot, ghi thời điểm; event intake vẫn tiếp tục có bound. Approval và terminal safety notice vẫn hiển thị live; thao tác control refresh descriptor trước gửi. “Về hiện tại” lấy snapshot latest, không replay animation từng event.

## 10. Layout, grouping và độ lớn

### 10.1 Thuật toán đề xuất, không force layout

Flow là directed graph, không physics/force-directed mặc định. Ranking theo dependency/follows đã validate; sibling tie-break bằng sequence rồi stable ID. Group root task và task con; cạnh retry đặt ở lane riêng. Nếu graph có cycle retry, layout trên DAG đã tách attempts hoặc collapse strongly-connected component cho presentation, **không đổi graph canonical**.

Spacing proposal: cột 224–256 px, hàng 152–184 px; edge elbow có gutter ≥24 px; label nền canvas để đọc và không che port. Edge nối port trái/phải cho trunk, bottom cho approval/retry; tránh xuyên node. Render arrows tại destination. Branch labels không tự đổi chỗ giữa updates.

Append task mới vào slot ổn định, không re-layout toàn graph mỗi token delta. Layout chỉ tính lại khi topology/group/size thay đổi. Status đổi không đổi vị trí node. User pin là preference presentation, không task dependency; “Sắp xếp lại” có preview/reset.

Nếu routing không tìm được đường tốt trong bound, collapse nhóm và cho list; không chạy tìm đường vô hạn. Nhiều edge song song có bundling chỉ với aggregate label và khả năng mở từng edge; không giấu dependency thất bại bên trong nhóm xanh.

### 10.2 Bounds khởi đầu cần đo

| Hạng mục | Budget thiết kế ban đầu | Khi vượt |
|---|---|---|
| Project map overview | ≤60 visible groups/files, ≤120 visible edges | Aggregate folder; index/search vẫn phủ scope thật, hiện coverage |
| Flow task detail | ≤80 visible nodes, ≤160 visible edges | Collapse theo task/phase; hiện số ẩn |
| Expanded task detail | ≤40 nodes | Page/group + list đầy đủ qua bounded API |
| DOM/source labels | Text có cap; full detail on demand | Ellipsis + inspector; không shrink vô hạn |
| Avatar moving | ≤4 onscreen | Static fallback còn nguyên identity |
| Flow snapshot DTO | ≤256 KiB serialized cho page | Paged snapshot; truncation + continuation rõ |
| Retained UI detail | ≤500 step summaries/execution trong view cache | Bounded history pages; không xóa canonical ledger |
| Summary refresh | Coalesce noncritical updates, tối đa khoảng 10 Hz | Không drop approval/error/terminal; overflow → resync |

Đây là **budget đề xuất**, không số capacity đã benchmark. C17/source pack limits của kế hoạch gốc vẫn giữ; bounds flow không nới source access. Cache eviction không được làm mất receipt/evidence canonical. Nếu DTO/page không chứa edge endpoint, dùng endpoint reference + “Ngoài trang” chứ không vẽ tới actor khác.

## 11. Contracts và sở hữu dữ liệu

### 11.1 Không tạo scheduler hoặc authority mới

Rust/current runtime sở hữu task/attempt/resource/permission/cancellation transitions. Python projection tạo redacted observation. React giữ view state. Avatar registry chỉ là mapping hiển thị, không định tuyến permission, provider hoặc runtime actor.

`aegis-workspace-event-v1` ở C10 vẫn là proposal normative envelope. Actor/flow cần additive payload unions hoặc version negotiation rõ; **không thêm fields tùy ý vào strict decoder rồi coi tương thích**. Contract tests bao gồm TS decoder, Python producer, native routing, preview fixtures và snapshot/replay.

### 11.2 Dữ liệu tối thiểu đề xuất

| Entity | Fields cần thiết | Invariant |
|---|---|---|
| Actor presentation | `actor_key`, display_name, role_label, avatar_key, capability_summary | Actor key do adapter gắn identity canonical; tên/model không là key |
| Step | `step_key`, task/attempt/action refs, actor_key nullable, kind, title, canonical_state nullable, observation_quality, source/evidence refs | Không tạo canonical action ID khi owner không cung cấp |
| Relation | `relation_key`, from/to step refs, relation_kind, provenance, observed/planned | Không suy causal edge từ proximity/LLM guess |
| Plan overlay | plan_id/revision, proposed step/relation IDs, origin | Không reuse observed step ID cho planned node |
| View preference | schema_version, scoped project/checkout, mode, camera, filters, pinned positions, avatar choices | Không persist authority/approval grant/raw secrets |
| Snapshot | stream/epoch/binding + snapshot_at_sequence, nodes/relations, page cursor, coverage | Capture consistent boundary; watermark không dùng tail |
| Source activity overlay | actor_key, task/attempt/action refs, source_reference, source_revision, observed_sequence, operation_kind, canonical_state nullable, observation_quality | Source ref được host kiểm tra; không join file bằng basename/tên model |
| Memory presentation / links | memory_id, scope, lifecycle/freshness theo DTO owner, provenance refs, relation_kind, retrieval/admission evidence | Memory label không là permission; retrieved ≠ admitted; source stale phải đánh dấu |

`actor_key` là định danh hiển thị có scope theo connection/session canonical; root actor và subagent phải phân biệt. `step_key` composite deterministic từ stream/execution/task/attempt + operation/action ledger ref nếu có. Nếu chưa có action ref, adapter cấp projection-only observation ID theo event identity và ghi rõ không dùng ID đó để execute. ID không nhúng API key/email/path absolute.

Một worker có thể làm nhiều task và một task có nhiều action; không ép `actor_key=task_id`. Nếu tích hợp hiện tại chỉ biết subagent/task mà không biết worker ổn định, hiển thị actor có scope task/session và ghi giới hạn, không hứa identity bền qua restart.

Step schema nên là discriminated union, title/redacted summary là display data. Không nhận executable expression, callback hoặc command từ title/edge. SVG/HTML từ model/tool không được render như markup; escape text.

Overlay join phải kiểm tra project/checkout/workspace_generation và source revision trước khi gắn lên map. Schema cụ thể reuses exact source-reference DTO ở kế hoạch C5; nếu cần thêm discriminator hoặc owner binding, version/contract tests đồng bộ cả producer/consumer. Không tạo API đọc path từ actor label. Unknown source, stale revision hoặc incomplete reference → roster/unresolved activity, không fallback tới file cùng tên.

Operation kind phải là allowlist (`read`, `proposed_write`, `write`, `verify`, `other` trong proposal), không quyết định permission. Write thành công chỉ từ ledger/outcome có evidence; proposed_write chưa phải modification. Actor snapshot gồm observation timestamps/sequence và capability coverage; không có event chỉ được ghi “Chưa quan sát được”, không tự ghi “Không có hoạt động”.

Visible footprint cap proposal ≤20 source refs/actor; vượt cap aggregate theo folder với total/omitted count thật và fetch detail bounded. Mặc định tối đa 12 actor chips trong roster, overflow/search có danh sách; đây là display bound, không runtime concurrency limit. Hidden/collapsed actors không bị dừng. Snapshot actor/source pages cùng binding/watermark hoặc ghi rõ metadata không atomic; không join từ hai epoch thành live map.

### 11.3 Event projection

1. Validate schema, binding, epoch, sequence và payload trước reducer.
2. Replay/dedupe/gap theo C10; emitted_at chỉ để hiển thị, không sort xuyên stream như một thứ tự global có bảo đảm.
3. `actor_registered`/task admission/projected action events chỉ từ adapter đáng tin; unsupported adapter ghi thiếu metadata.
4. Upsert step theo stable key; preserve attempt history. Progress summary chỉ thay text/status, không tạo node mỗi token/chunk.
5. Relation requires explicit provenance: dependency ID, plan revision, operation parent/sequence owner hoặc evidence production record.
6. Unknown endpoint giữ pending bounded, sau snapshot vẫn thiếu thì Unresolved group + quality flag. Không tự tạo completed placeholder.
7. Run terminal đến sớm hơn detail page: show terminal outcome + detail loading/partial; không fabricate missing test result.
8. Observer disconnected/paused/history là view status, không transition canonical run.
9. Approval/cancel commands lấy current authoritative refs; không dispatch từ stale node screenshot/history.
10. Preview fixtures dùng cùng schema và feature gates, luôn có nhãn minh họa; không giả ACK thật.

Flow data có thể chứa path, source snippets và tool output nhạy cảm. Redact/cap trước renderer; snapshot/export không gửi qua cloud mặc định. Screenshot/Figma dùng fixtures an toàn, không thật workspace/private prompt/key.

### 11.4 Failure/recovery

| Failure | Phản ứng | Phải kiểm tra |
|---|---|---|
| Gap/out-of-order | Bounded hold + resync | Cursor chỉ advance đến page đã giao |
| Restart | Epoch mới, snapshot từ host | Không replay approval/write mơ hồ |
| Cancel và approval race | Host fence/action checks | Approval vừa bấm không vượt cancel |
| Lost dispatch ACK | Receipt lookup + unknown UI | Không chạy action lần hai |
| Source changed | Hash/range/revision revalidation | Không overwrite current dựa diff cũ |
| Avatar renderer fail | Static identity fallback | Canvas công việc vẫn đọc/điều khiển được |
| Invalid flow relation | Reject/partial diagnostic đã lọc | Không crash shell hoặc sửa authority |
| Very large graph | Group/page/search/list | Explicit truncation; bound memory |

## 12. Frontend kỹ thuật và file ownership cho agent triển khai

Source đã đọc ở lần lập kế hoạch: `desktop/src/workspace_graph.ts` có graph project/directory/file, edge `contains/import_candidate/lineage_candidate`; `WorkspaceGraphView.tsx` dùng grid bốn cột với visible bound. Đây là hiện trạng đọc source, không đo performance hoặc xác nhận source không đổi sau đó. `aegis_cognition/subagents.py` có task/parent/dependency/attempt identities; không được suy mọi schema actor/action mới đã có.

| Trách nhiệm | Nơi bắt đầu | Hướng thay đổi dự kiến |
|---|---|---|
| Workspace mode/shell | `desktop/src/App.tsx` | Tích hợp surface, không sửa mọi screen |
| Code structure graph | `desktop/src/workspace_graph.ts` | Giữ semantics; reuse source refs, không đổi thành task flow |
| Memory graph/query/lifecycle | Primitive memory/context hiện hữu theo C12 kế hoạch gốc | Dùng scope/provenance/admission thật; chỉ thêm projection/link DTO cần thiết, không dựng kho memory thứ hai |
| Flow model/reducer | Module nhỏ mới nếu cần | Pure transform validated DTO → view model; unit tests |
| Flow canvas/layout | Component riêng nếu trách nhiệm khác code map | DOM nodes + SVG edges/ports; isolate layout function |
| Inspector | Share primitive hiện có | Typed selection target flow/code/evidence |
| ActorAvatar | Component adapter nhỏ | Fallback static; library optional sau gate |
| Native/host projection | `main.rs`, `desktop_service.py`, `desktop_projection.py`, `desktop_protocol.py` | Commands/DTO/events đúng owner, version/allowlist |
| Settings | `desktop_settings.py` | Chỉ persisted preferences cần thiết, migration explicit |
| Theme/icons | `desktop-theme.css`, `styles.css`, `capability-icons.tsx` | Reuse semantic token/icon registry, không fork theme |
| Preview | `preview_backend.ts` | Safe typed fixtures; unknown/partial/race states |

Đặt tên mới theo domain, không thêm một file cho từng shape/status. Agent thực hiện phải kiểm tra AGENTS.md trong desktop và delta working tree tại thời điểm bắt đầu. Không ghi đè thay đổi khác hoặc restructure repo cho đẹp trong task này.

### 12.1 Renderer comparator

Khởi đầu DOM button nodes + SVG connectors cho tree/layered project map và task detail bounded trong stack React hiện có. Không dùng một SVG bitmap cho UI runtime: node cần focus/accessible names, inspector/control semantics. Camera transform chung giữ node/edge coordinates nhất quán; screen overlay menus nằm ngoài transformed layer. Project map layout dựa source topology; actor update chỉ đổi overlay, không làm cây code nhảy vị trí.

React Flow là đối thủ hợp lý nếu cần selection, pan/zoom, ports, accessibility/navigation và virtualization mà implementation hiện có không đáp ứng. [Tài liệu performance của React Flow](https://reactflow.dev/learn/advanced-use/performance) cảnh báo subscription/render không cần thiết và khuyến nghị memoization/collapse cho graph lớn; dùng framework không tự bảo đảm performance.

Comparator bounded, cùng fixture 20/80/200 visible nodes cho code map và task detail, cùng labels/edges/avatar policy, same viewport/machine, warm/cold và theme. Thêm metadata tree 1k/10k indexed files với grouping/search để kiểm tra overview không render toàn index. Đánh giá: keyboard, focus, selection preservation, layout stability, edge clarity, package size, frame time/heap, maintainer burden. Chỉ thêm dependency nếu lợi ích có evidence; không xây hai production renderers hoặc một framework custom mới.

## 13. Accessibility, motion và màu

- Target WCAG 2.2 AA cho UI được triển khai, chưa claim bản phác đạt AA.
- Status/edge nghĩa có chữ/hình/nét; màu agent identity khác màu trạng thái.
- Body normal text target ≥4.5:1; meaningful controls/graphical boundaries target ≥3:1 theo [Non-text Contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html). Màu pastel fill không tự chứng minh contrast.
- Node dùng button có accessible name: step title + actor + state; selection/focus tách. Roving focus hoặc list alternative tránh Tab qua hàng trăm node; arrows không chiếm text selection của inspector.
- Decision/edge labels có representation đọc được ở list/inspector; không bắt screen reader đọc SVG path.
- Có toggle motion và “Giữ khung để đọc”; [Pause, Stop, Hide](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html) liên quan chuyển động/auto-update song song với nội dung. Giữ khung không dừng task.
- Respect reduced motion; bỏ pan animation, hop/spin/trail. [Animation from Interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html) là hướng dẫn SC mức AAA; áp dụng preference như yêu cầu thiết kế bổ sung, không ghi nhầm đây là AA.
- Announce approval/failure/terminal thay vì live region mỗi token. Thông báo critical không tự biến mất hoặc nhấp nháy liên tục.
- Màn hình hẹp/zoom 200%: inspector đọc/duyệt được; canvas có alternative list. Touch target thiết kế 44 px cho control chính; marker 24 px nằm trong hit area lớn hơn.
- Tooltips không là nơi duy nhất có status/permission; hover không dispatch. High contrast/theme phải kiểm tra native shell thật.

Màn hình tối chính dùng surface/text của `desktop/src/styles.css`: `#191918`, `#20201F`, `#282827`, `#F4F3EE`, success `#40C977`, warning `#FF8549`, error `#FF6764`; active source/memory relations use distinct line pattern and labels. Màu memory/card và actor identity là semantic extensions, không thay host status. Bản sáng còn lại là phương án thay theme, chưa phải bằng chứng contrast/accessibility. Kiểm tra contrast với mọi trạng thái và native shell trước implementation.

## 14. Những gì không đưa vào lát cắt đầu

Không node editor có thể đổi quyền/dependency runtime bằng drag edge. Không map 3D/WebGL, tự động đi quanh canvas, chạy animation mọi actor, workflow builder mới, remote team sync, cloud storage, code auto-merge từ diagram, scoring agent bằng avatar, implicit test-skip bằng AESE hoặc LLM giải thích mỗi event.

Export PNG/SVG của view đã redaction có thể là follow-up sau privacy review. Export không mặc định bao gồm prompt, absolute root, code snippets, tool logs hoặc identity nhạy cảm. Không dùng Figma làm kho source/workspace.

## 15. Hồ sơ minh họa và giới hạn bằng chứng

Các bản vẽ, ảnh chụp, prototype và export được tạo trong nghiên cứu trước đã được lưu ngoài repo để bảo toàn provenance. Chúng chỉ dùng dữ liệu giả lập và không chứng minh component native, interaction hoàn chỉnh, accessibility, runtime hoặc hành vi trên ba hệ điều hành.

Yêu cầu sản phẩm và tiêu chí review trong tài liệu này là nguồn tham chiếu. Trước khi triển khai, agent phải đối chiếu chúng với source, callers, tests và UI hiện tại. Mọi kết luận PASS phải dựa trên evidence mới, gắn revision và môi trường; phần chưa kiểm tra ghi NOT VERIFIED.

## 16. Kiểm tra thiết kế và kế hoạch triển khai

### 16.1 Nghiệm thu nghiên cứu/phác thảo lần này

- Có phân tích ảnh dựa hình/layout, không dùng nghiệp vụ ảnh.
- Có đối chiếu live avatar page, package manifest/license/docs; tách observed/documented/unmeasured.
- Có project code + memory map/actor inspector và flow detail, multiagent, narrow/failure sketches; đều có fixture label. Project map chính dùng workspace giả lập, không áp dụng tính năng vào repo phát triển AEGIS.
- Có contracts/projection/controls/recovery kế thừa đúng authority.
- Phạm vi nghiệm thu phân biệt proposal, behavior hiện có và behavior đã được kiểm tra; phần chưa kiểm tra ghi NOT VERIFIED.
- Đợt nghiên cứu ban đầu không thay đổi product runtime; đề xuất này tự nó không cấp quyền triển khai.

### 16.2 Design walkthrough trước code

Review bằng các câu hỏi đơn giản: “Toàn dự án gồm những vùng nào?”, “Agent nào đang làm ở đâu?”, “Bấm vào đâu để biết nó đang làm gì?”, “Cần tôi làm gì?”, “Nếu dừng lúc này thì điều gì chưa chắc?”, “Tôi xem thay đổi ở đâu?”. Thử map overview và actor/file inspector không đọc hướng dẫn trước, rồi approval/retry/disconnected/narrow.

Ghi câu trả lời/điểm nhầm, không tự chấm confidence/UX score. Nếu người dùng nhầm planned là completed, edge dependency là source import, hoặc mất kết nối là agent nghỉ: sửa design trước triển khai. Nếu flow không giúp hiểu hơn list với việc nhỏ, cho list làm mặc định của trường hợp đó.

### 16.3 Milestones phụ thuộc, không triển khai ngay

| Gate | Công việc khi được triển khai | Bằng chứng để qua |
|---|---|---|
| G0 Review thiết kế | Primary mode map-first đã chốt; review phác, drill-down và avatar motion preference | Recorded choices, còn-open list |
| G1 Contract trước UI | Actor/action/relations capability audit, strict DTO/binding/snapshot/replay | Contract tests + schema examples, unsupported adapter handling |
| G2 Read-only vertical slice | Workspace người dùng → indexed code + related memory → bounded context → actor/source overlay → inspector/evidence | Native provenance/source/memory scope; full-scope coverage/search; retrieval≠admission; no write controls until host ready |
| G3 Controls | Approval/cancel/receipt/restart đúng C9–C11 | Race/lostACK/idempotency tests; history không dispatch |
| G4 Multiagent | Fan-out/dependency/join, retries, conflicts | Deterministic reducer/property fixtures, concurrency integration |
| G5 Avatar/renderer | Comparator, fallback, reduced motion/native shell | Actual render/performance/a11y evidence, pin/license review nếu add package |
| G6 Product validation | Paired retrieval benchmark + cross-OS critical journeys | Quality nonregression, actual usage/limits, retained artifacts |

### 16.4 Test matrix cho agent triển khai

Pure reducer/layout: permutation/gap/duplicate/stale epoch/wrong checkout; unknown endpoint/cycle/oversized labels; actor name collision/model switch; status updates stable positions; retry attempt history; hidden selected node; group chứa failed/unknown không xanh hết. Map-specific: all admitted folders reachable by search/drill-down, partial coverage rõ, activity refs stale/unknown không gán sai file; collapsed folder aggregate, multiple actors per file, one actor/multiple files, activity không source, code candidate edge không biến thành task dependency.

Host contracts: permission deny/root fence, source changed/hash mismatch, cancel-before-approve dispatch, stale descriptor, lost ACK receipt lookup, unsupported adapter, inconsistent snapshot rejected, partial pages không nhảy cursor. Test invariant trực tiếp, không chỉ snapshot pixel.

UI journeys: idle map/full-project groups, drill-down/source search, actor vs file selection, activity chưa gắn code, live markers, approval/deny, failure/new attempt, parallel/conflict, cancel/disconnect/restart/history, 200% zoom/keyboard/narrow source tree. Control identity phải match selected action trước dispatch. Tests không “retry until green”.

Avatar comparator: static vs live, 0/1/4/12/40 visible identities; 12/40 chỉ tối đa 4 moving. Record cold/warm p50/p95 render/interaction, CPU/heap/DPR/theme; hidden/offscreen/reduced-motion có pause thật. Initial goal selection/update p95 <100 ms và không tăng >10% so static cho same workload; mục tiêu đề xuất cần reference machine/CI env rõ, không dùng shared-runner jitter làm bằng chứng thắng thua hoặc gate production vô điều kiện.

Windows/macOS/Linux: deterministic tests cùng assertions; packaged Tauri critical smoke riêng vì WebView khác nhau. Windows WebView2/macOS WKWebView/Linux WebKitGTK cần kiểm tra canvas/text/trackpad/focus/theme; screenshot khác không nới semantic tests. GitHub Actions chạy phù hợp các lane sẵn; GCP Linux VM dùng cho native Linux nếu cần, không chứng minh macOS/Windows. User approval/reality operational checks vẫn riêng.

Token benchmark theo C14/D4 kế hoạch gốc: cùng tasks/repo/model/tools/quality criteria, count mọi request/attempt, map overhead và repair, actual usage tách estimate. UI/avatars không được gửi lại vào prompt mỗi lần; không dùng toàn repo giả baseline. Không cần provider/cloud test cho task thiết kế này.

## 17. Sổ bằng chứng và nguồn

| Claim | Evidence | Giới hạn |
|---|---|---|
| Bố cục map và inspector | Yêu cầu người dùng và phân tích ví dụ minh họa | Ý tưởng UX, không chứng minh product behavior |
| Avatar roster | Tài liệu/sản phẩm tham khảo công khai | Không chứng minh tích hợp AEGIS |
| Package version, peer và license | Manifest, README và license upstream | Cần xác minh lại đúng bản package trước khi thêm dependency |
| Các trạng thái của library | README upstream | Chưa chứng minh types/runtime bằng kiểm thử trong sản phẩm |
| Hình phác, prototype và export nghiên cứu | Bản lưu riêng ngoài repo | Chỉ minh họa; không phải source hay evidence runtime |
| Tiết kiệm token và hiệu năng | Chưa đo trên workload/revision mục tiêu | UNKNOWN; không được tuyên bố cải thiện |

Nguồn primary đã đọc ngày 2026-09-30:

1. [Bot avatars playground](https://libraries.dev/bots) — tham khảo hình/avatar roster.
2. [Upstream repository](https://github.com/Jakubantalik/Libraries.dev) — packaging và phân biệt library/Pro content.
3. [Package manifest](https://github.com/Jakubantalik/Libraries.dev/blob/main/packages/bot-avatars/package.json), [package README](https://github.com/Jakubantalik/Libraries.dev/blob/main/packages/bot-avatars/README.md), [MIT license](https://github.com/Jakubantalik/Libraries.dev/blob/main/LICENSE) — version, peer, license và interface.
4. [React Flow performance](https://reactflow.dev/learn/advanced-use/performance) — subscription behavior và graph-size considerations.
5. [W3C Non-text Contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html), [Pause, Stop, Hide](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html), [Animation from Interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html) — design constraints and review criteria.

## 18. Hướng dẫn bắt đầu cho agent khác

Đọc mục 1–2 để giữ đúng **chức năng AEGIS cho workspace của người dùng, tích hợp memory graph và bounded retrieval**; mục 5.3 để làm đúng map toàn dự án với actor overlay, mục 6–11 để không kể sai runtime, kế hoạch gốc A6/C9–C12 để giữ authority/replay/control/memory. Kiểm tra delta source/instructions trước sửa. Mode chính đã chốt map-first; không hỏi lại preference đó và không suy approval triển khai từ việc người dùng chọn một bố cục.

Thực hiện G1 → G2 trước animation và write controls. Chỉ tạo file/component theo trách nhiệm cần thiết. Giữ primitives source/memory và benchmark thật. Mỗi claim PASS phải có artifact/env/revision, mỗi unavailable lane ghi NOT VERIFIED. Không dùng vẻ ngoài sơ đồ, số lượng node hoặc avatar chuyển động để kết luận task/production đã hoàn tất.
