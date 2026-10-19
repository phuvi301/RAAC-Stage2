# Đối chứng các task còn lại — 2026-10-19

Sau Requests-2316, chạy tiếp 9 task bằng harness SWE-bench chính thức. Nhóm 5 task vướng dữ liệu trước đó vẫn được bỏ qua theo yêu cầu. Đây là kiểm tra đối chứng, không tự đổi nhãn hoặc tiêu chí validation của RAAC.

## Điều kiện chạy và bằng chứng

- Harness `v3.0.0`, commit `d83e1001739f4d96add351d9ec0d56f4594a0d82`, không sửa mã nguồn tracked.
- Ubuntu WSL2, Docker Desktop Linux; image namespace `swebench`, ghi image ID và digest cho từng task. Dùng đúng mapping task ID → instance ID và base commit; đọc raw Stage1, không sửa nguồn.
- Một task tại một thời điểm trong Windows Terminal riêng. Pull tối đa 1800 giây; mỗi phase test 1800 giây. Gold và baseline dùng container riêng từ cùng image base.
- Gold dùng patch trong raw. Baseline chỉ thêm `.raac-reference-baseline.txt` để tránh harness bỏ qua patch rỗng, không sửa production code hoặc test. Test patch do harness chính thức áp.
- Mạng mặc định của harness chính thức, tách khỏi policy mạng RAAC. Không đưa kết quả này vào checkpoint hoặc fingerprint batch RAAC.
- Xóa image mới tải sau mỗi task; giữ image có trước và cache RAAC. Clone/venv WSL tạm được dọn khi hàng đợi kết thúc.

Evidence trong `results/official-reference-remaining-20261019/` (Gitignored): `manifest.json`, `comparison.json`, `summary.csv`, raw subset, predictions, console log, `eval.sh`, report và test output. Có snapshot mã parser/grading và `.parser.json` để kiểm tra ID bị mất khi parse. Checkpoint sau mỗi phase/task; một task lỗi không dừng các task tiếp theo.

## Phân biệt kết quả official và RAAC

Harness chính thức chấm patch theo kết quả sau áp patch. RAAC hiện còn yêu cầu mọi F2P fail và mọi P2P pass tại baseline, đồng thời không tính skip/xfail/xpass là pass.

Trong bản official đã chọn:

- `grading.test_passed()` tính `XFAIL` là pass.
- Parser pytest dùng `line.split()[1]`, có thể cắt parameter tại dấu cách và ghi đè các case cùng key.
- Parser Django có thể mất ID khi hai dòng test bị ghép. Grading tính ID không tìm thấy là failure.

Vì vậy, phải đọc output đầy đủ; không dùng riêng `resolved`, exit code hoặc số failure trong report để suy ra testcase thực sự đã chạy và fail. Không tái lập lỗi parser official trong RAAC, không chọn riêng case pass, không đổi raw để ép đạt tiêu chí.

## Kết quả từng task

| Task RAAC | Instance SWE-bench | Kết quả đối chứng |
| --- | --- | --- |
| Requests-1723 | psf__requests-1724 | Gold: 6 F2P + 79 P2P pass. Baseline: 6 F2P pass, một P2P fail đúng issue. |
| Requests-1765 | psf__requests-1766 | Gold: 6 F2P + 79 P2P pass. Baseline: chỉ 1/6 F2P fail, P2P pass hết. |
| Requests-1920 | psf__requests-1921 | Gold: 6 F2P + 107 P2P pass. Baseline: 2 F2P fail, gồm một lỗi HTTP 502; P2P pass hết. |
| Django-33552 | django__django-15503 | Gold: 2 F2P + 78 P2P pass. Report baseline ghi 2 failure do parser mất ID; thực tế một F2P vẫn pass. |
| Pylint-7229 | pylint-dev__pylint-8898 | Gold: 1 F2P + 18 P2P pass. Baseline: F2P fail và một P2P chứa cả case pass/fail. |
| Matplotlib-22640 | matplotlib__matplotlib-22719 | Official gold resolved; baseline F2P fail. P2P alias chứa xfail, official tính là pass; RAAC không chấp nhận. |
| Xarray-3992 | pydata__xarray-3993 | Gold: 2 F2P + 2398 P2P pass. Baseline: 2 F2P và 4 P2P fail. |
| Xarray-4354 | pydata__xarray-4356 | Gold: 8 F2P + 604 P2P pass. Baseline: 8 F2P và 8 P2P fail. |
| Xarray-6597 | pydata__xarray-6599 | Gold: 1 F2P + 264 P2P pass. Baseline: 1 F2P và 1 P2P fail. |

### Requests-1723: Python và nhãn của test tái hiện issue

Image published thực tế chạy Python 2.7.18/pytest 4.6.4. Baseline fail `test_unicode_method_name` với `UnicodeDecodeError` trong httplib; gold pass. Đây là test mới trong test patch, đúng lỗi Unicode method/Python 2 mô tả trong issue, nhưng raw lại xếp test này vào P2P. Cả 6 F2P đều pass ở baseline.

RAAC dùng Python 3.9 nên không tái hiện lỗi đặc thù Python 2 này. Cần kiểm tra profile lịch sử nếu tiếp tục dùng task; chỉ đổi Python vẫn không giải quyết được điều kiện nhãn raw hiện tại. Không tự chuyển P2P thành F2P. Không suy Python của image published từ recipe: recipe Requests 1.2 trong v3.0.0 ghi 3.9, nhưng image SWE1724 chạy 2.7.18; digest thực tế được giữ để đối chiếu.

### Requests-1765 và 1920

1765 fail đúng `test_DIGESTAUTH_QUOTES_QOP_VALUE`; 5 F2P còn lại pass, khớp RAAC. Không có bằng chứng cần sửa môi trường để ép các test đó fail.

1920 fail `test_headers_on_session_with_None_are_not_sent` đúng assertion về header `None`. `test_basicauth_with_netrc` còn fail vì httpbin trả **502**, không phải bằng chứng issue. RAAC nội bộ tránh lỗi live httpbin nhưng vẫn có F2P pass tại baseline.

### Django-33552: report failure do mất ID

Baseline có dòng:

```text
test_has_key_number (...) ... test_has_keys (...) ... ok
```

`test_has_keys` thực tế pass; failure traceback chỉ có 9 subtest của `test_has_key_number`. Gọi trực tiếp parser official xác nhận cả hai ID đầy đủ thành `MISSING_FROM_PARSER`, rồi grading liệt kê vào failure. Đây không phải bằng chứng hai testcase đều fail. Runner official dùng SQLite, chạy 88 test và skip 8 test ngoài 80 target. RAAC ghi trạng thái từ unittest callback, không cần bắt chước lỗi parser.

### Pylint-7229: selector trùng do parameter bị cắt

P2P `test_csv_regex_comma_in_quantifier[foo,` khớp `foo, bar-expected2` pass và `foo, bar{1,3}-expected3` fail. Output chính thức xác nhận cả hai kết quả, khớp RAAC mixed. Không chọn case pass hoặc đổi nhóm để bỏ failure.

### Matplotlib-22640: xfail nằm trong P2P alias

Gold thực tế có 90 pass/8 xfail; baseline 89 pass/1 fail/8 xfail. Bốn P2P alias của `test_mixed_type_exception` và `test_mixed_type_update_exception`, với `[number`/`[string`, đều khớp scatter/bar pass và plot xfail. Official ghi đè alias và tính xfail là pass; RAAC giữ mixed theo tiêu chí đã chốt.

Image native làm Docker tạm lên 35,74 GB, vượt ngân sách mềm 30 GiB. Image này đã được xóa sau hai phase, không xóa cache RAAC để bù.

### Xarray-3992 và 4354: P2P cần hành vi gold

3992: bốn P2P `TestDataArray::test_computation[*-method_integrate-*]` fail `TypeError: DataArray.integrate() got an unexpected keyword argument 'coord'`. Base chưa có API mà test gọi; gold bổ sung hành vi này. Khớp RAAC, không phải lỗi collection hay thiếu dependency.

4354: tám P2P `test_min_count_nd[sum/prod-True-*]` fail tại base với `ValueError: min_count is not available for reduction with more than one dimensions.` Gold pass toàn bộ. Khớp RAAC; không đổi nhãn P2P thành F2P.

6597: P2P `test_polyval[timedelta-True]` fail `TypeError` từ NumPy/Dask khi trừ datetime khỏi timedelta array. Gold pass đủ 265 test thuộc nhóm chấm. Cùng hiện tượng đã thấy ở RAAC, kể cả lượt thử dependency lịch sử; chưa có bằng chứng đổi dependency sẽ sửa được điều kiện P2P này.

Các file test official có thể có failure ngoài F2P/P2P. `Resolved` không đồng nghĩa toàn file hoặc toàn suite repo pass.

## Trạng thái nghiệm thu

Hàng đợi đã `processed` đủ 9/9 task trong 32,51 phút. Có đủ report cho cả 18 phase gold/baseline, không có phase timeout hoặc thiếu report. Official chấm cả 9 gold `resolved`, cả 9 baseline `unresolved`; phải đọc cùng các giới hạn parser/xfail và lỗi mạng ghi ở trên.

Kể cả Requests-2316 đã đối chứng trước đó, đã có bằng chứng official cho cả 10 task `validation_failed`. Không phát hiện sửa adapter hoặc dependency mới nào đủ cơ sở để chuyển thêm task thành `validated` trong lượt này. Riêng Requests-1723 có khác biệt Python thực tế và nhãn test cần xử lý riêng trước khi benchmark. Không đổi dữ liệu raw hoặc nới tiêu chí để ép đạt.

Trạng thái RAAC vẫn **105 validated / 10 validation_failed / 4 collection_error / 1 execution_error**. Nhóm 5 artifact giữ nguyên theo yêu cầu. Chưa nghiệm thu 120/120, L1–L4 hoặc A5; không push trong lượt này.

Cleanup đã kiểm tra: cả 9 image mới tải đều được xóa; không còn container official của lượt chạy; thư mục `/home/phuvi301/raac-reference-remaining-20261019` (clone/venv) không còn. Docker image trở về 25,09 GB như trước lượt chạy. Hash raw Stage1 không đổi. Giữ evidence/log và script tái hiện; không đụng cache/image RAAC.
