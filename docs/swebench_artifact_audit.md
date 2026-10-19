# Đối chứng 5 task artifact và hướng xử lý tiếp — 2026-10-19

Người dùng yêu cầu mở lại nhóm 5 task trước đây bỏ qua. Chạy harness SWE-bench
chính thức v3.0.0, commit `d83e1001739f4d96add351d9ec0d56f4594a0d82`, trong
Ubuntu WSL2/Windows Terminal, một task mỗi lượt. Giữ raw Stage1, mapping
repo/base/instance ID và nhãn F2P/P2P; gold và baseline dùng container riêng.
Baseline chỉ thêm marker file ngoài code/test. Mạng mặc định official,
1800 giây pull và 1800 giây mỗi phase; không thay policy RAAC.

Evidence: `results/official-reference-artifacts-20261019/` (Gitignored), gồm
manifest, comparison, raw subset, predictions, digest image, report, eval.sh
và output test. Cả 5 task/10 phase đã processed trong 13,42 phút; có đủ report,
không timeout. Official chấm 5 gold resolved và 5 baseline unresolved.

## Kết quả và giới hạn

| Task RAAC → instance SWE-bench | Gold theo report official | Baseline / điều còn vướng |
| --- | --- | --- |
| Django-29834 → django__django-10554 | 2 F2P + 23 P2P success | In lỗi subtest gọi repr(QuerySet), SQL lỗi lần nữa làm unittest crash. Một F2P cũ vẫn ok; parser nhận nhầm dòng ghép. |
| Pylint-1548 → pylint-dev__pylint-4551 | 10 F2P success; P2P rỗng | Collection lỗi vì test patch import get_annotation, API chỉ có sau gold. Không có testcase nào được thực thi. |
| Pylint-4603 → pylint-dev__pylint-4604 | 21 F2P success; P2P rỗng | Collection lỗi vì test patch import IS_PYPY, constant chỉ có sau gold. Không có testcase nào được thực thi. |
| Pytest-7517 → pytest-dev__pytest-7521 | 2 F2P + 125 P2P success | Raw chứa test sinh tạm và marker [100%], không phải toàn bộ là ID collection cấp ngoài. |
| Sympy-14237 → sympy__sympy-14248 | 2 F2P + 433 P2P success | Tên test bare trùng giữa nhiều printer; report tổng mất định danh file. |

`Success` và `resolved` ở bảng là cách parser/grading official chấm, không tự
chứng minh từng ID tương ứng một testcase thực thi và pass. Report baseline
Pylint liệt kê missing IDs thành failure sau collection error; không được gọi
đây là 10 hoặc 21 F2P đã chạy và fail. P2P rỗng được giữ nguyên, không tạo thêm.

### Pytest: test lồng và metadata bị nhận thành test

`test_capsysbinary.py::test_hello` xuất hiện thật trong output của test do
pytester sinh tạm bên trong test cha. RAAC không thể coi nó là file tĩnh ở base
để collection trực tiếp; muốn hỗ trợ cần nhận diện test cha và ghi kết quả test
lồng thực sự, không giả lập pass.

Output official còn có dòng `PASSED [100%]`; parser lấy token thứ hai làm ID,
nên marker tiến độ `[100%]` cũng được tính success trong raw P2P. Đây không phải
một testcase. Cần bản hiệu chỉnh evaluator có lịch sử/nguồn và phân biệt
metadata khỏi test, không âm thầm xóa selector hoặc sửa raw.

### Sympy: cần định danh theo file

Đọc AST trong container (không chạy source repo trên host) xác nhận
`test_MatrixElement_printing` có ở 9 file: pretty, ccode, fcode, jscode,
julia, latex, octave, rcode và str. Snapshot có 43 tên bare khớp nhiều file.
`selector-source-map.json` là bằng chứng định nghĩa, không phải manifest
collection đã nghiệm thu; script harness có bước reset test patch khi phase
kết thúc. Những test mới thêm bởi test patch còn cần đối chiếu patch/collection.
Không chọn tùy ý file hoặc dùng kết quả cuối cùng cùng tên để chấm các case khác.

## Sửa runner Django có căn cứ

Log cũ của Django-29834 cho thấy unittest Python3.9 in lỗi subtest bằng
`repr()` của QuerySet. Nó lại thực hiện truy vấn `ORDER BY` đang lỗi và làm
reporting crash, khiến RAAC ghi collection_error thay vì giữ lỗi test gốc.

Runner mới giữ ID parent khi in subtest, không đánh giá giá trị tham số chỉ để
in báo cáo. Không sửa test patch, code Django, SQL, nhãn hoặc điều kiện pass.
Unit fixture đã tái hiện lỗi trước sửa và xác nhận assertion gốc còn nguyên,
test ổn định vẫn pass, repr của tham số không bị gọi. **106/106 test đã pass**
trong159,20giây (88unit+18Docker thật), gồm regression trên Python3.9.
Ruff/diffcheck và profilecheck120/120 qua.

Batch mới `django-subtest-reporting-v1-full` đã chạy task thật, record
`69d1e48cfbdb47dfbc87005f65554e41`: baseline và gold đều found/executed25/25.
Baseline ghi đúng1F2Pfail/1F2Ppass và23P2Ppass; gold25/25pass. Reporting không
còn crash. Task chuyển từ `collection_error` sang `validation_failed` vì F2P
`test_union_with_values_list_on_annotated_and_unannotated` đã pass ởbase.
Không tính đây là taskvalidated; thời gian tổng155,797giây gồm build mới.

## 10 task đã đối chứng trước còn xử lý được không?

Có, nhưng không phải bằng retry hoặc ép môi trường làm mọi F2P fail:

1. **Điều kiện tái hiện:** Requests-1723 cần kiểm tra profile Python2 để đúng
   issue; raw lại xếp test tái hiện vào P2P nên chỉ sửa Python chưa đủ. Với
   Requests1765/1920/2316 và Django33552, một số F2P thực sự pass ngay base;
   giữ kết quả đó và lỗi parser/mạng riêng.
2. **Định danh/nhãn evaluator:** Pylint7229, Matplotlib22640 và các Xarray có
   alias, xfail hoặc P2P cần hành vi gold. Cần mapping/annotation có version,
   bằng chứng source và collection, giữ raw bất biến. Không đổi nhóm hoặc bỏ
   case chỉ để đạt chỉ tiêu.
3. **Tách kiểm tra baseline và chấm candidate:** kế hoạch RAAC hiện yêu cầu
   mọi F2P fail/P2P pass tại baseline, mạnh hơn gate official. Có thể giữ
   baseline làm chẩn đoán/tái hiện và chấm candidate sau patch trên tập test
   đã xác minh, báo riêng kết quả strict và official. Nếu áp dụng phải đặt
   protocol/fingerprint mới, cố định trước so sánh L1–L4; không sửa lịch sử.
4. **Bảo toàn yêu cầu sau patch:** candidate vẫn phải chạy checkout sạch,
   không có gold, không sửa test đánh giá; missing/collection/timeout không
   được tính pass. Chính sách xfail phải khai báo rõ; không tự chấp nhận
   xfail mới chỉ vì official tính nó là pass.

Đây là hướng xử lý đề xuất, chưa thay tiêu chí hiện hành. Quyết định mới về
protocol hoặc dữ liệu evaluator cần được chốt trước khi triển khai; không tự
chuyển 15 task còn lại thành validated.

## Cleanup và bàn giao

Cả 5 image mới tải đã xóa sau từng task; thư mục WSL clone/venv
`/home/phuvi301/raac-reference-artifacts-20261019` đã dọn. Giữ report/log cần
thiết và script tái hiện; không prune cache RAAC. Hash raw Stage1 không đổi.
Phần sửa runner đã kiểm chứng như trên. Trạng thái mới nhất120task:
**105validated/11validation_failed/3collection_error/1execution_error**.
Không nghiệm thu120/120, L1–L4 hoặcA5. Không push trong lượt này.
