# Định nghĩa các mức thích ứng L1–L4

Ngày cập nhật: 2026-10-22. Trạng thái: bản đặc tả dựa trên proposal, chờ nhóm review.

Tài liệu này định nghĩa biến cố định, biến được phép thay đổi và ranh giới của bốn mức collaboration trong RAAC (Requirement-Aware Multi-Level Adaptive Collaboration). Đây là hợp đồng thiết kế thực nghiệm, không phải xác nhận cả bốn level đã hiện thực.

## 1. Căn cứ và phạm vi

Trích nguồn từ bản **Proposal**, đề tài “Tác nhân LLM tự hợp tác cho phân tích yêu cầu đến sinh mã nguồn”, tháng 09/2026:

| Nội dung sử dụng | Vị trí trong proposal |
| --- | --- |
| Bốn mức và phạm vi thích ứng | Mục 2, trang in 6; mục 6.2, Bảng 1, trang in 15 |
| Mục tiêu định nghĩa rõ biến cố định/tự do | Mục 5.4, trang in 13–14 |
| Requirement Profile, chọn level và escalation | Mục 6.3–6.5, trang in 15–17 |
| Chạy độc lập bốn level để xây Oracle Benchmark | Mục 7.2, trang in 18–19 |
| Đánh giá, ranh giới level và khả năng tái lập | Mục 7.5, trang in 21; mục 10.3–10.4, trang in 23 |

Các định nghĩa L1–L4 dưới đây bám theo Bảng 1. Ký hiệu, quy tắc ghi log và tiêu chí kiểm chứng là phần diễn giải vận hành cho repository, cần nhóm review trước khi đóng băng thực nghiệm. Những framework được proposal dùng làm ví dụ không phải đặc tả bắt buộc phải tái lập nguyên hệ thống.

Theo xác nhận của người dùng ngày 2026-10-22, ladder có tính **kế thừa cộng dồn**: L3 giữ quyền routing của L2 rồi thêm quyền điều chỉnh topology/số vòng; L4 giữ toàn bộ quyền L3 rồi thêm team formation. “Tập vai trò cố định” tại L3 được hiểu là danh mục vai trò và hợp đồng của chúng cố định, không phải mọi vai trò đều bắt buộc tham gia mọi task.

**Level collaboration khác layer harness.** L1–L4 mô tả quyền điều chỉnh cách nhiều agent phối hợp. Bốn layer harness mô tả các chức năng bao quanh mỗi agent; mọi level đều dùng cùng harness. Một agent có nhiều tool call hoặc có memory không tự làm level collaboration tăng lên. Xem [kiến trúc](architecture.md).

## 2. Ký hiệu và biến điều khiển

Xét một task có requirement, repository và base commit xác định:

| Ký hiệu | Ý nghĩa |
| --- | --- |
| `R` | Tập định nghĩa vai trò: trách nhiệm, capability, prompt/output contract và quyền tool |
| `T_t` | Thành phần đội nhóm tại thời điểm `t`: các agent instance và vai trò tương ứng |
| `A_t` | Agent/vai trò được kích hoạt ở bước `t`; một agent đang chờ vẫn có thể thuộc đội nhóm |
| `G_t` | Topology trao đổi/chuyển giao công việc giữa các agent, gồm cạnh và quy tắc handoff |
| `P_t` | Chính sách phản hồi, số vòng refinement và điều kiện tiếp tục/kết thúc |
| `B` | Các trần tài nguyên và an toàn: token, thời gian, số vòng, quyền tool và sandbox |

“Cố định” nghĩa là được khai báo và version hóa trước run, không được controller/model thay đổi trong run theo task để mở thêm quyền thích ứng. “Tự do” nghĩa là có thể được chọn theo task hoặc điều chỉnh theo trạng thái thực thi, **trong không gian chính sách và các giới hạn `B` đã công bố**.

Số vòng thực tế khác số vòng tối đa. Một graph có nhánh retry cố định và điều kiện dừng cố định có thể chạy số vòng khác nhau do test pass/fail; điều đó chưa chứng minh controller có quyền thích ứng `P_t` hay `G_t`.

## 3. Bảng định nghĩa tổng hợp

| Biến/khả năng | L1: Fixed | L2: Adaptive Routing | L3: Adaptive Collaboration | L4: Adaptive Team Formation |
| --- | --- | --- | --- | --- |
| Định nghĩa vai trò `R` | Cố định | Cố định | Cố định | Được chọn/thêm/bớt/thay vai trò theo chính sách formation |
| Thành phần đội nhóm | Cố định | Chọn tập con từ danh mục vai trò/agent có sẵn | Kế thừa L2: chọn tập con từ danh mục cố định | Được thêm/bớt/thay vai trò hoặc agent instance theo chính sách formation |
| Kích hoạt `A_t` | Theo workflow định trước | Động: bật/bỏ vai trò không cần thiết | Kế thừa routing L2, kết hợp topology/phản hồi | Kế thừa L3, áp dụng cho đội nhóm được hình thành |
| Topology `G_t` | Cố định | Cố định quy tắc giao tiếp giữa các agent được kích hoạt | Được chọn/điều chỉnh | Được chọn/điều chỉnh |
| Chính sách vòng phản hồi `P_t` | Cố định | Cố định | Được chọn/điều chỉnh | Được chọn/điều chỉnh |
| Trần tài nguyên và quyền an toàn `B` | Cố định | Cố định | Cố định | Cố định |

Proposal mô tả chi phí kỳ vọng lần lượt là thấp nhất, thấp–trung bình, trung bình và cao nhất. Đây là **kỳ vọng cần kiểm chứng**, không phải kết luận token/thời gian tăng đơn điệu: routing có thể tiết kiệm tài nguyên, còn một run L4 có thể dừng sớm hơn run L1.

Thang này sắp theo phạm vi quyền điều chỉnh có tính kế thừa, không chỉ theo số agent:

```text
L1: workflow cố định
L2: L1 + routing trong danh mục có sẵn
L3: L2 + điều chỉnh topology và số vòng phản hồi
L4: L3 + hình thành/thay đổi đội nhóm
```

Kết hợp routing và topology adaptation là quyền bình thường của L3. Ranh giới với L4 là L3 chỉ chọn/kích hoạt trong danh mục cố định; thêm agent instance, bổ sung/thay vai trò hoặc thay hợp đồng trách nhiệm thuộc team formation.

## 4. L1 — Fixed

**Định nghĩa:** tập vai trò, thành phần đội nhóm, luồng giao tiếp và chính sách phản hồi được xác định trước. Controller không chọn workflow khác cho từng task.

Ví dụ minh họa với các role lõi của repository:

```text
Analyst → Planner → Coder → Tester → Reviewer → Kết quả
```

Nếu baseline có vòng sửa lỗi `Coder → Tester → Coder`, các cạnh, điều kiện lặp và trần vòng phải được định nghĩa trước và dùng nhất quán. Có thể kết thúc sớm theo tiêu chí chung hoặc dừng do guard; không phát sinh agent/topology mới trong run.

**Không thuộc L1:** bỏ Planner do task dễ; chọn Debate thay Chain; thêm Security Agent khi phát hiện rủi ro.

**Bằng chứng cần có:** graph/config trước run, danh sách role cố định và trace thực thi chỉ đi qua các cạnh/điều kiện đã khai báo. Nội dung suy luận và patch được khác nhau giữa task; cấu trúc điều phối vẫn cố định.

## 5. L2 — Adaptive Routing

**Định nghĩa:** giữ nguyên tập vai trò thiết kế sẵn, cho phép quyết định vai trò nào cần được kích hoạt cho task. Quy tắc thứ tự/giao tiếp và chính sách phản hồi giữa các vai trò được chọn vẫn cố định.

Ví dụ:

```text
Danh mục: Analyst → Planner → Coder → Tester → Reviewer
Router chọn: Analyst → Coder → Tester
```

Việc nối qua node bị bỏ phải theo quy tắc bypass đã khai báo; không tùy ý thêm cạnh như Reviewer trao đổi trực tiếp với Analyst. Danh sách router chọn là một tập con hợp lệ của registry, không phải các role mới sinh ra. Theo checklist triển khai hiện tại, cấu hình L2 giữ tối thiểu Coder và Tester; đây là ràng buộc repository, không phải ngưỡng được proposal quy định.

**Không thuộc L2 thuần:** thay trách nhiệm Coder; thêm chuyên gia mới; cho các role thảo luận ngang hàng bằng topology mới; điều chỉnh chiến lược số vòng theo nhu cầu task.

**Bằng chứng cần có:** quyết định router, role bật/bỏ và lý do, tập role hợp lệ, trace theo đúng thứ tự/bypass. Parse lỗi hoặc quyết định không hợp lệ phải được ghi rõ và dùng fallback đã công bố; không âm thầm coi fallback là quyết định thích ứng thành công.

## 6. L3 — Adaptive Collaboration

**Định nghĩa:** kế thừa Adaptive Routing của L2, đồng thời cho phép thay đổi topology giao tiếp và số vòng phản hồi cần thiết. Danh mục vai trò/agent có sẵn và quyền/trách nhiệm của chúng vẫn cố định; tập được kích hoạt cho từng task có thể thay đổi.

Ví dụ minh họa: từ danh mục Analyst, Planner, Coder, Tester, Reviewer, controller có thể bỏ Planner cho một task đơn giản rồi chọn Chain giữa các role còn lại; với task khác, kích hoạt nhiều role hơn, chọn Debate và điều chỉnh số vòng. Ví dụ này mô tả thiết kế, không xác nhận Debate đã được hiện thực.

Không yêu cầu mọi role đều được kích hoạt, hoạt động đồng thời hoặc có số lần gọi bằng nhau. Một role có thể bị router bỏ qua hoặc chỉ đang chờ đến lượt. Khi run dừng lỗi trước một role, log phải phân biệt chưa đến lượt với chủ động bỏ role. Quyết định kích hoạt lại một role có sẵn là routing; tạo thêm một agent instance là team formation của L4.

Thay topology có thể thực hiện bằng chọn từ các graph template đã kiểm chứng, không bắt buộc để LLM sinh graph tùy ý. Chọn tại đầu task và thay đổi trong runtime là hai phạm vi khác nhau: cấu hình phải công bố cho phép loại nào. Một cấu hình chỉ chọn `max_rounds` nhưng luôn dùng Chain cần ghi rõ mới thích ứng số vòng, chưa kiểm chứng quyền chọn topology.

**Không thuộc L3:** thêm Security Agent hoặc tạo thêm Coder theo subtask; đổi năng lực/quyền tool của một role để biến thành role khác. Chỉ lặp trên Chain cố định khi test fail cũng chưa đủ chứng minh Adaptive Collaboration.

**Bằng chứng cần có:** danh mục role/agent và hợp đồng không đổi; quyết định routing, topology/vòng kèm thời điểm và lý do; thay đổi cạnh/handoff thực tế, số vòng đã chạy và nguyên nhân dừng. Cần các tình huống kiểm thử thể hiện controller sử dụng những quyền thích ứng được công bố. Chỉ sử dụng routing trong một run vẫn nằm trong quyền L3, nhưng chưa đủ kiểm chứng toàn bộ khả năng L3.

## 7. L4 — Adaptive Team Formation

**Định nghĩa:** kế thừa routing, topology và số vòng phản hồi của L3, rồi bổ sung quyền hình thành/thay đổi đội nhóm: thêm/bớt/thay vai trò, thêm agent instance hoặc tái phân công trách nhiệm theo chính sách.

Ví dụ minh họa: bổ sung Security Reviewer cho task xác thực; tạo hai Coder phụ trách hai subtask và một Integrator; thay một vai trò tổng quát bằng chuyên gia phù hợp rồi cập nhật luồng trao đổi. Đây là các khả năng thiết kế, không mặc định mỗi run L4 phải dùng tất cả.

Formation có thể chọn từ extended roles đã có trong registry hoặc tạo role theo schema/chính sách đã định nghĩa. Không bắt buộc sinh system prompt không giới hạn. Đổi tên role mà không đổi trách nhiệm, hay gọi lại cùng một Coder, không tự động được tính là hình thành đội nhóm mới.

Mỗi agent mới vẫn chịu sandbox, schema, quyền tool và trần tài nguyên chung. Formation không được tự cấp quyền host/network hoặc truy cập gold/test đánh giá. Các subtask cần phân công, hợp nhất artifact và xử lý xung đột; không coi nhiều patch độc lập là một kết quả đã tích hợp.

**Bằng chứng cần có:** trạng thái đội nhóm trước/sau, agent ID và role/capability, lý do thêm/bớt/thay, phân công subtask, topology/vòng tương ứng, kết quả hợp nhất và chi phí formation. Chi tiết L4 cần đặc tả và review riêng trước khi hiện thực; file [l4_spec.md](l4_spec.md) hiện chưa cung cấp hợp đồng đầy đủ.

## 8. Phân biệt chọn level, thích ứng trong level và escalation

Requirement Profile trong proposal gồm `(complexity, ambiguity, security sensitivity, architecture impact)`. Adaptivity Controller ánh xạ profile sang L1–L4; proposal chưa ấn định các ngưỡng tối ưu. Không đặt quy tắc như “security ≥ một giá trị thì L4” thành kết luận nghiên cứu khi chưa có dữ liệu/rubric đã duyệt.

Ba quyết định phải tách riêng trong log:

1. **Chọn level:** chọn không gian quyền thích ứng cho một run.
2. **Thích ứng trong level:** sử dụng quyền đó, chẳng hạn router L2 bỏ Planner, hoặc L3 chọn topology/vòng mới.
3. **Escalation:** chuyển từ level hiện tại sang level cao hơn, chẳng hạn L2 → L3 khi cần phối hợp phức tạp hơn, hoặc L3 → L4 khi cần chuyên gia mới.

Runtime Monitor có thể quan sát bất đồng agent, xung đột mã, test fail lặp lại, thông tin requirement mâu thuẫn, vấn đề bảo mật/kiến trúc, quá nhiều vòng refinement hoặc độ tin cậy evaluator thấp (proposal mục 6.5). Đây là tín hiệu đầu vào; không mặc định một test fail là phải escalation.

Trong **Giai đoạn 2 — Oracle Benchmark**, mỗi task phải chạy độc lập trên từng level, không dùng Adaptivity Controller chọn level và không escalation sang level khác giữa run. Chính sách thích ứng nội bộ của L2/L3/L4 vẫn được phép hoạt động đúng định nghĩa. Run có escalation thuộc thí nghiệm controller/runtime adaptation sau này, không gắn toàn bộ tài nguyên/kết quả của nó cho level khởi đầu để xây oracle.

## 9. Điều kiện so sánh và đo lường

Biến nghiên cứu là quyền điều phối. Các điều kiện còn lại cần cố định hoặc kiểm soát và ghi rõ:

- Cùng task, requirement, repository/base commit, thông tin đầu vào và evaluator độc lập. Không đưa gold patch hoặc test_patch đánh giá cho MAS.
- Cùng model/profile theo role dùng chung, temperature, prompt/registry version, tool schema, harness và execution environment. Prompt điều phối đặc thù từng level phải version hóa; không giả định mọi prompt giống nhau tuyệt đối.
- Cùng chính sách token/time/hard-round limit và cách tính tài nguyên. Số lần gọi/số agent/số vòng thực tế là kết quả cần đo, không ép bằng nhau rồi triệt tiêu khác biệt giữa level.
- Cùng hợp đồng candidate, hiện là `implementation-only-v1`: Coder sửa implementation, Tester viết test phát triển mới riêng; không sửa protected test để nâng kết quả. Các run dùng hợp đồng cũ phải tách báo cáo.
- Fresh workspace và short-term memory theo run/attempt, tắt RAG trong benchmark tuần 8–9; thứ tự chạy, repetitions và seed nếu provider hỗ trợ phải được ghi lại. Không chuyển patch, memory hoặc kết quả từ level trước sang level sau.

Theo proposal mục 7.2, ba nhóm metric gồm chất lượng (completion, test pass rate, requirement coverage, code quality), tài nguyên (LLM calls, token, latency/thời gian) và tương tác (messages, rounds, conflict). Topology/team changes cần thêm khi đánh giá quyền thích ứng tương ứng. Theo xác nhận phạm vi dự án ngày 2026-10-22, tài nguyên hiện so sánh bằng **token/thời gian**, không quy đổi tiền.

Giữ riêng `agent_status`/verdict của MAS và `evaluation_status` của evaluator. MAS PASS chưa đủ kết luận resolved. Lỗi setup, timeout, candidate không hợp lệ và protected-test rejection cần báo riêng; không xóa task khỏi mẫu số, không retry kết quả không đạt để chọn lượt tốt nhất. Usage thiếu phải là unknown/incomplete; tài nguyên mọi attempt cần được giữ khi có retry.

Oracle level được lựa chọn sau khi có kết quả các level bằng utility hoặc Pareto analysis và quy tắc xử lý trường hợp gần ngang nhau đã công bố (proposal mục 7.2 và 10.3). Không suy oracle từ độ phức tạp task hoặc mặc định L4 tốt nhất. Tài liệu này chưa chốt trọng số utility, ngưỡng, số vòng hay kích thước dataset.

## 10. Hợp đồng quan sát và tiêu chí nghiệm thu

Đây là yêu cầu dữ liệu thiết kế cho Engine/level adapter, không khẳng định schema hiện tại đã đủ mọi field:

| Đầu ra/trace | Thông tin cần quan sát |
| --- | --- |
| Identity và cấu hình | Run/task/level/attempt, base commit, model/registry/prompt/config hash và snapshot không chứa secret |
| Trạng thái đội nhóm | Role definitions, agent instances, vai trò được chọn và được kích hoạt thực tế |
| Quyết định điều phối | Loại quyết định, trước/sau, thời điểm, lý do và evidence; topology/version, chính sách vòng và hard limits |
| Thực thi | Handoff/messages, role activation, tool/test observations, rounds, formation/conflict events nếu có |
| Kết quả | Candidate patch/hash, agent verdict, evaluator outcome, stop reason và lỗi phân loại |
| Tài nguyên | Calls/token và completeness, latency từng call, setup/agent/evaluation/total wall time |

Một level chỉ được nghiệm thu theo định nghĩa khi: (1) không dùng quyền bị cấm; (2) các quyền thích ứng được công bố có case kiểm chứng và trace; (3) chạy qua hợp đồng benchmark chung với artifact/evaluator độc lập; (4) điều kiện đối chứng và giới hạn được ghi lại để tái lập. Việc có thư mục `l3_collaboration`, một node `router`, hay nhiều role chưa đủ làm bằng chứng.

## 11. Đối chiếu hiện trạng repository

Tại ngày 2026-10-22:

- Registry/harness và Chain nhiều role đã có; đường benchmark L3 tạo candidate trong Docker, chấm độc lập và ghi token/thời gian đã có pilot thật. Xem [hướng dẫn và evidence](candidate_benchmark.md).
- `coordinate_chain` trong `src/raac/collaboration/levels/l3_collaboration/l3_collaboration.py` nhận topology và số vòng **do người vận hành đặt tay**: `topology`/`max_rounds` trong `configs/levels/l3_collaboration.yaml`, hoặc `--topology`/`--max-rounds` của `raac-mas run-chain`; `source="fixed_default"`. Hai template có sẵn: `chain` và `chain_review_loop` (cùng 6 role và hợp đồng, thêm cạnh Reviewer → Coder khi Reviewer từ chối và còn vòng, event `review_feedback_sent`). Chưa có controller tự chọn topology/vòng, đổi topology giữa lúc chạy hay Debate.
- Vì vậy, pilot chứng minh đường thực thi Chain/candidate/evaluator, **chưa chứng minh L3 Adaptive Collaboration đầy đủ theo proposal**. Các nhánh feedback/test gate hiện có không thay thế bằng chứng chọn topology/vòng thích ứng.
- L1/L2, L4 và Engine chung còn cần hoàn thiện theo [checklist](implementation_checklist.md). Tài liệu định nghĩa này không tự nâng trạng thái nghiệm thu những phần đó hoặc xác nhận nhóm đã duyệt đặc tả L4.

Các định nghĩa nghiên cứu là căn cứ để tiếp tục hiện thực; tên thư mục hoặc nhãn CLI hiện tại không thay thế ranh giới biến cố định/tự do trong Bảng 1 của proposal.
