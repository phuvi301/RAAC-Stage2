Hướng điều chỉnh này hợp lý và là bước nâng cấp đúng hướng, nhưng cần chỉnh lại cách định nghĩa: đây không còn là “linear L1” mà là fixed cyclic workflow — topology cố định có vòng lặp, vẫn thuộc L1 vì controller chưa được phép tự chọn role/topology theo task.

## 1. Đánh giá tổng thể

Đề xuất mới:

Requirement Analyst
        ↓
Architect
        ↓
Planner
        ↓
Developer
        ↓
Tester ── test fail ───────┐
        ↓                  │
Code Reviewer ── changes ──┘
        ↓
      END

Với giới hạn:

max_rounds = 3

Trong đó:

- Requirement Analyst, Architect và Planner chạy ở giai đoạn chuẩn bị.
- Developer → Tester → Code Reviewer tạo thành một chu kỳ.
- Nếu Tester phát hiện lỗi ở generated test hoặc test harness, Tester tự sửa và chạy lại trong cùng workflow trước khi chuyển tiếp; lỗi này không được gắn nhãn là lỗi implementation của Developer.
- Tester có thể gửi trực tiếp feedback về Developer.
- Code Reviewer có thể yêu cầu Developer chỉnh sửa.
- Reviewer có quyền kết thúc sớm khi đạt điều kiện.
- Ở vòng cuối, bắt buộc đi qua Code Reviewer trước khi kết thúc.

Đây là mô hình tốt hơn L1 hiện tại vì nó phản ánh đúng quy trình phát triển phần mềm thực tế hơn: implement → test → review → repair.

## 2. Vẫn có thể gọi là L1 không?

Có, nếu giữ các điều kiện sau:

- Danh sách role được cố định trước run.
- Các cạnh topology được khai báo trước.
- max_rounds = 3 là giới hạn cố định trong config.
- Không có agent mới được sinh ra trong lúc chạy.
- Không có role tự chọn topology khác.
- Không có routing tự do ngoài các nhánh đã định nghĩa.

Theo định nghĩa hiện tại trong docs/level_definitions.md, L1 không bắt buộc topology phải là đường thẳng. Tài liệu đã cho phép một workflow L1 có cạnh cố định như:

Coder → Tester → Coder

Vì vậy nên đổi tên khái niệm từ:

L1 Fixed Linear

thành:

L1 Fixed Cyclic

hoặc:

L1 Fixed Feedback Workflow

Điểm quan trọng là feedback loop được khai báo trước, không phải model tự phát minh ra loop.

## 3. Điểm mạnh của đề xuất

### Tách rõ trách nhiệm

Bộ role mới hợp lý hơn bộ cũ:

Requirement Analyst
Architect
Planner
Developer
Tester
Code Reviewer

So với Analyst → Planner → Coder → Tester → Reviewer hiện tại:

- Requirement Analyst tập trung vào yêu cầu và acceptance criteria.
- Architect chịu trách nhiệm thiết kế ảnh hưởng đến module/interface.
- Planner biến thiết kế thành kế hoạch thực thi.
- Developer chỉ tập trung vào implementation.
- Code Reviewer review chất lượng, rủi ro và tính phù hợp của patch.

Việc thêm Architect đặc biệt hữu ích với các task SWE-bench hoặc task ảnh hưởng nhiều module.

### Feedback từ Tester trực tiếp về Developer

Nhánh:

Tester → Developer

là hợp lý khi code không qua test. Tester nên truyền artifact có cấu trúc, ví dụ:

{
  "status": "failed",
  "failed_tests": [],
  "error_summary": [],
  "reproduction": [],
  "suggested_fix_scope": [],
  "blocking": true
}

Developer không nên chỉ nhận một chuỗi log dài. Cần nhận report đã chuẩn hóa, còn traceback chi tiết giữ dưới dạng artifact/reference.

### Feedback từ Code Reviewer về Developer

Nhánh:

Code Reviewer → Developer

nên dùng cho các lỗi khác với test failure:

- sai acceptance criteria;
- patch quá rộng;
- vi phạm architecture;
- thiếu backward compatibility;
- chất lượng code kém;
- thiếu test;
- có security/regression concern.

Điều này tách được hai loại feedback:

Tester       → lỗi hành vi/thực thi
Code Reviewer → lỗi thiết kế/chất lượng/điều kiện nghiệm thu

### Early stopping

Cho phép kết thúc sớm là cần thiết. Không nên bắt mọi task chạy đủ ba vòng, vì:

- task đơn giản có thể pass ngay vòng đầu;
- chạy thêm vòng làm tăng token và latency;
- reviewer đã có đủ bằng chứng để kết luận.

Tuy nhiên, early stopping phải là quyết định có schema, không nên suy ra từ text tự do của reviewer.

Ví dụ:

{
  "decision": "approve",
  "completion": "accepted",
  "reason": "all acceptance criteria satisfied",
  "stop": true
}

## 4. Điểm cần làm rõ nhất: vòng cuối còn test failed

Đây là phần có rủi ro thiết kế lớn nhất.

Bạn nói vòng cuối:

> bắt buộc phải qua Code Reviewer để kết thúc mặc cho vẫn còn test failed

Điều này có thể hiểu theo hai cách khác nhau:

### Cách A — kết thúc run nhưng không công nhận thành công

Đây là cách mình khuyến nghị.

Ở vòng 3:

Tester failed
    ↓
Code Reviewer
    ↓
END

Nếu reviewer không thể chấp nhận patch, kết quả là:

{
  "status": "completed_with_failures",
  "verdict": "REJECT",
  "stop_reason": "max_rounds_reached",
  "tests_passed": false,
  "review_completed": true
}

Nghĩa là workflow đã kết thúc đúng quy trình, nhưng patch không được coi là thành công.

Không nên dùng:

status = "completed"
verdict = "PASS"

khi vẫn còn test failed, vì sẽ làm sai metric benchmark.

### Cách B — reviewer được phép approve dù test failed

Nếu bạn thực sự muốn cho phép, cần thêm một trạng thái rõ ràng:

{
  "decision": "approve_with_known_failures",
  "accepted_failures": [],
  "risk_level": "medium",
  "waiver_reason": "...",
  "stop": true
}

Nhưng đây không còn là “test pass”. Nó là explicit waiver. Chỉ Code Reviewer được phép đưa ra quyết định này, và phải ghi rõ:

- test nào vẫn fail;
- vì sao failure được chấp nhận;
- failure có phải do môi trường hay không;
- ảnh hưởng còn lại;
- ai/điều kiện nào cho phép waiver.

Nếu không có cơ chế waiver, nên giữ quy tắc:

test failed ⇒ không được PASS

## 5. Topology đề xuất nên được đặc tả như thế nào

Nên dùng topology có các node điều khiển rõ ràng:

requirement_analyst
    ↓
architect
    ↓
planner
    ↓
developer
    ↓
tester
    ├── tests_passed → code_reviewer
    ├── tests_failed && round < 3 → developer
    └── tests_failed && round == 3 → code_reviewer
                              ↓
                            END

Reviewer:

code_reviewer
    ├── approve → END
    ├── request_changes && round < 3 → developer
    └── request_changes && round == 3 → END

Điểm cần chú ý: nhánh từ Tester về Developer nên tăng round trước khi Developer chạy lại, hoặc định nghĩa round thật rõ. Nếu không, một vòng lặp có thể bị đếm sai.

Mình khuyến nghị định nghĩa:

round = số lần Developer tạo hoặc cập nhật patch

Khi đó:

- Developer lần đầu: round 1;
- Developer sau test fail: round 2;
- Developer sau review request: round 3;
- không được gọi Developer lần thứ tư.

Architect và Planner chỉ chạy một lần, trừ khi sau này bạn muốn mở rộng sang L3.

## 6. Artifact handoff vẫn nên giữ

Việc giữ trao đổi dựa trên artifact là đúng. Không nên chuyển sang shared free-form chat chỉ vì có loop.

Nên phân biệt artifact hiện tại và lịch sử:

current:
  requirement_analysis
  architecture
  plan
  patch
  test_report
  review

history:
  iterations:
    - round: 1
      patch_ref: ...
      test_report_ref: ...
      review_ref: ...
    - round: 2
      patch_ref: ...
      test_report_ref: ...
      review_ref: ...

Nếu chỉ ghi đè patch, test_results, review trong state hiện tại, bạn sẽ khó:

- phân tích vì sao agent sửa sai;
- đo số vòng thực tế;
- resume chính xác;
- so sánh token từng iteration;
- hiển thị trace;
- xác định feedback nào đã được xử lý.

Do đó nên giữ:

latest artifact
+
immutable iteration history

## 7. Những file/hợp đồng hiện tại sẽ bị ảnh hưởng

Hiện tại L1 đang khóa các phần sau:

- Role list trong src/raac/collaboration/levels/l1_fixed.py:13.
- Topology tuyến tính tại src/raac/collaboration/levels/l1_fixed.py:15.
- max_rounds và hard_max_rounds đều bị giới hạn bằng 1 tại src/raac/collaboration/levels/l1_fixed.py:45.
- Validator từ chối mọi cấu hình khác tại src/raac/collaboration/levels/l1_fixed.py:58.
- Reviewer chỉ trả PASS khi cả review và test đều pass tại src/raac/collaboration/levels/l1_fixed.py:221.
- State hiện tại mới có các artifact đơn analysis, plan, patch, test_results, review trong src/raac/collaboration/state.py:8.

Vì vậy thay đổi này không chỉ là sửa YAML. Nó cần cập nhật đồng bộ:

1. Registry role IDs, prompt và output schema.
2. L1 config.
3. Topology và conditional edges.
4. Collaboration state.
5. Round counter và termination policy.
6. Result schema/status.
7. Trace event cho feedback loop.
8. Unit test cho early stop, retry và final forced review.
9. Checklist và level definition.

## 8. Các test case bắt buộc

Nên có ít nhất các case sau:

| Case | Luồng mong đợi |
|---|---|
| Pass ngay vòng 1 | Developer → Tester → Reviewer → END |
| Tester fail vòng 1 | Tester → Developer → Tester → Reviewer |
| Reviewer request changes vòng 1 | Reviewer → Developer → Tester → Reviewer |
| Tester fail vòng 2 | Tester → Developer lần 3 |
| Reviewer approve vòng 2 | Kết thúc sớm, không gọi Developer lần 3 |
| Test fail vòng 3 | Tester → Reviewer → END |
| Reviewer reject vòng 3 | Kết thúc với REJECT, không loop tiếp |
| Reviewer approve nhưng test fail | Từ chối hoặc yêu cầu explicit waiver |
| Agent lỗi trong loop | Aborted, giữ lại iteration artifacts |
| Resume ở vòng 2 | Không chạy lại Analyst/Architect/Planner |
| Resume sau final Reviewer | Không tạo thêm Developer call |

## Kết luận

Mình đánh giá hướng này nên triển khai, với ba điều chỉnh quan trọng:

1. Gọi L1 mới là fixed cyclic workflow, không còn là linear workflow.
2. Định nghĩa round là số lần Developer tạo/cập nhật patch, giới hạn tối đa 3.
3. Tách rõ:
   - completed — workflow kết thúc thành công;
   - completed_with_failures — đã đi qua Reviewer nhưng còn test fail;
   - rejected — Reviewer không chấp nhận;
   - aborted — lỗi hệ thống hoặc agent.

Nếu giữ được ba điểm này, L1 mới vẫn là baseline cố định hợp lệ, nhưng thực tế và có giá trị hơn nhiều so với pipeline một chiều hiện tại.