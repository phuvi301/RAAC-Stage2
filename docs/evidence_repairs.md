# Sửa theo bằng chứng đối chứng — 2026-10-19

Lưu ý sau khi cố định benchmark105: người dùng đã yêu cầu gỡ setup riêng của
15task bị loại. Override Python2 Requests1723 và expansion Sympy14237 mô tả
dưới đây là lịch sử đã kiểm chứng, hiện không còn bật trong profile production.
Record/log vẫn giữ; xem [dọn môi trường](benchmark105.md#dọn-môi-trường-nhóm15task).

Phạm vi hiện tại là sửa môi trường và định danh test đã có bằng chứng; giữ
nguyên raw, F2P/P2P và điều kiện strict `validated`. Không ghi đè record cũ.
Xem [đối chứng artifact](swebench_artifact_audit.md) và
[đối chứng baseline](swebench_remaining_audit.md).

## Sympy-14237

Official instance `sympy__sympy-14248` dùng tên hàm bare bị trùng giữa các
printer. Override task khai báo `selector_policy: sympy-all-matches-v1`.
Evaluator chỉ thu thập các module Python trong test patch như trước, nhưng
chạy **tất cả** testcase có tên khớp trong các module đó. Report giữ nguyên
alias nguồn cùng mọi node ID đầy đủ, trạng thái và traceback từng case.

Một alias chỉ pass khi mọi case khớp đều pass; thiếu, skip, xfail hoặc hỗn hợp
pass/fail không được tính pass. Không chọn case cuối cùng như parser official.
Đây là tập đánh giá bảo thủ rộng hơn alias mơ hồ, không tuyên bố khôi phục được
một testcase duy nhất vốn không có trong raw. Task khác vẫn dùng `unique-v1`
và từ chối bare ID mơ hồ. Override có version nằm trong snapshot/fingerprint;
không ảnh hưởng recipe build. Candidate dùng cùng tập và không nhận gold.

## Requests-1723

Official image chạy Python 2.7.18; lỗi Unicode method chỉ tái hiện trên Python2.
Override riêng task dùng Python2.7, pip/setuptools/pytest và dependency còn
hỗ trợ Python2. Fixture HTTPbin giữ mạng isolated, bổ sung tương thích thread,
TLS context và urllib readiness cho Python2; không mở Internet khi test.
PyPI cung cấp wheel Python2 cho [httpbin0.7.0](https://pypi.org/project/httpbin/0.7.0/)
và [trustme0.7.0](https://pypi.org/project/trustme/0.7.0/).

`test_unicode_method_name` nằm trong P2P của raw dù là test tái hiện lỗi;
chuyển Python không tự làm task đạt strict validation. Giữ nhãn và báo kết quả
thực tế, không thay dependency để ép những F2P đã pass trở thành fail.

## Kết quả kiểm chứng

Unit regression selector bao phủ mọi case và kiểm tra không che failure.
Docker fixture tương tự chạy hai module trùng tên, một pass/một fail.
105/105 framework test qua trong157,61s (86unit+19Docker thật). Sau sửa chuỗi
Unicode cho trustme Python2, kiểm tra lại35unit liên quan qua. Profile120check,
Ruff/format/diffcheck qua. Raw SHA256 vẫn
`7303cc5795e3707162f9b0ffcc5694f3fd67e20bd9d514cfdce63146fdebc196`.

Hai task chạy thật tuần tự trong Windows Terminal,1800s chuẩn bị/1800s mỗi
phase. Batch `evidence-repairs-v1-preflight`: Sympy qua, Requests lỗi hostname
trustme yêu cầu Unicode trên Python2. Dừng batchfullv1 có kiểm soát trước sửa;
record Requests bị ngắt được giữ nguyên. Sau sửa dùng batch Requestsv2 mới,
không resume fingerprint cũ.

| Task | Record full | Baseline | Gold | Trạng thái strict |
| --- | --- | --- | --- | --- |
| Sympy14237 | `c90aac7b3f154f1a82e73409ef469931` | 11casefail/495pass;435aliasfound,433P2Ppass; F2P MatrixElement mixed,MatrixSymbol failed | 506casepass,435aliaspass | validation_failed |
| Requests1723 | `115a953d3dbc4246a44d960546e429af` | 84pass/1fail UnicodeDecodeError;6F2Ppass,78P2Ppass/1fail | 85pass | validation_failed |

Sympy preflight đã xác nhận collection đủ435alias;44tên trùng được mở rộng,
không có selector missing. MatrixElement có9case: pretty pass,8printer fail;
MatrixSymbol có3casefail. Gold pass mọicase. Collection lỗi cũ đã xử lý nhưng
không đổi mixed thành failed để đạt gate. Full40,078s, test7,24/7,27s.
Requests preflightv2 qua; full14,797s,test0,80/0,75s. LỗiUnicode đúng issue và
đúng officialreference; nhãn raw vẫn mâu thuẫn như bảng. Mạng vẫn isolated.
Container/volume/network của các lượt đã được cleanup; cacheimage giữ lại.
Không thêm task validated hoặc push. Latest120task:105validated/12validation_failed/2collection_error/1execution_error.
Không coi đã xử lý hàng đợi là120/120đạt.

Các Pylint baseline import API chỉ tồn tại sau gold, pytester test lồng/marker
`[100%]`, xfail và nhãn P2P/F2P mâu thuẫn không được sửa bằng bỏ test hoặc gán
pass. Chúng cần protocol/annotation đánh giá riêng có bằng chứng collection;
chưa thay đổi grading của các task đó trong đợt sửa môi trường này.
