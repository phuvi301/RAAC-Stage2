# Đối chứng bằng harness SWE-bench chính thức

Ngày 2026-10-19, chạy đối chứng riêng task RAAC `psf__requests-2316`, ánh xạ
SWE-bench `psf__requests-2317`, base `091991be0da19de9108dbe5e3752917fea3d7fdc`.
Không dùng task ID RAAC thay cho instance ID SWE-bench.

## Môi trường và cách chạy

- Ubuntu WSL2, Docker Desktop Engine29.1.2 Linux.
- Harness tag `v3.0.0`, commit `d83e1001739f4d96add351d9ec0d56f4594a0d82`;
  không sửa file tracked của harness. Venv Python3.12 chạy điều phối;
  Python3.9.20 trong image chạy test.
- Image chính thức
  `swebench/sweb.eval.x86_64.psf_1776_requests-2317:latest`, digest
  `sha256:c6b5c8846507b5e48e307decca28dbdfa4f5ef17070c1087aacde765e8c4b178`.
- Đọc raw Stage1, trích đúng một instance thành JSONarray, đối chiếu base commit
  với evaluator artifacts hiện có. Ghi hash raw/gold/test patch; không sửa Stage1.
- Hai phase riêng: gold dùng raw `patch`; baseline không thay đổi production/test
  code. `test_patch` do harness chính thức áp theo `eval.sh`.
- Chạy một instance/lượt, timeout1800s cho test; pull có timeout1800s riêng.
  Windows Terminal chạy WSL độc lập Codex.
- Cấu hình mạng Docker mặc định của harness chính thức, không gắn fixture
  RAAC hoặc đổi policy của RAAC. Đây là lượt đối chứng có điều kiện khác,
  không tự động chuyển kết quả sang `validated` của RAAC.

Lệnh chung sau khi cài harness bằng venv riêng:

```bash
python -m swebench.harness.run_evaluation \
  --dataset_name reference-data/task.json \
  --predictions_path reference-data/gold.jsonl \
  --instance_ids psf__requests-2317 \
  --namespace swebench --cache_level instance \
  --max_workers 1 --timeout 1800 \
  --run_id raac-reference-requests2317-gold-20261019
```

Baseline dùng prediction tương ứng và run_id khác. Không dùng `model_patch=""`
vì harness bỏ qua prediction rỗng. Cách dùng newline-only đã thử nhưng image
không áp được, dù harness thử `git apply --allow-empty`: lượt đầu chỉ là
`patch apply error`, không có bằng chứng baseline. Lượt baseline-v2 dùng diff
thêm duy nhất `.raac-reference-baseline.txt` chứa ghi chú, không đụng code/test.
File này không nằm trong collection `pytest -rA test_requests.py`.

## Bằng chứng

Lưu trong `results/official-reference-requests2316-20261019/` (Gitignored):
source provenance, predictions, pinned harness dependency snapshot, image
inspect/digest, console logs, `eval.sh`, test output và report chính thức.
Bản v3.0.0 ghi log ở `logs/run_evaluation/`, khác đường dẫn tài liệu main hiện tại.

Gold lần đầu: harness báo `resolved=true`, 8/8F2P và133/133P2P pass.
Thời gian CLI275,848s (bao gồm điều phối/cleanup), pull41,718s riêng.
Baseline-v2 hoàn tất, CLI273,513s; report `resolved=false`, không có lỗi hạ tầng:
7F2Ppass/1F2Pfail và133/133P2Ppass. F2P duy nhất fail là
`test_requests.py::TestTimeout::test_encoded_methods`. Gold pass test này.
Các kết quả này trùng kết quả nhóm F2P/P2P trong batch RAAC network-v3.

Phân biệt nhóm chấm với cả filetest: gold có142pass/1fail, baseline141pass/2fail,
đều chạy143test. `test_conflicting_post_params` fail ở cảhai nhưng không thuộc
F2P/P2P của mẫu, nên không làm gold mất `resolved`. Không gọi đây là toàn suitepass.
Thời gian pytest gold239,48s, baseline238,14s; CLI còn gồm cài package/điều phối/cleanup.

Điều này tái lập hiện tượng F2P pass một phần trong chính harness chính thức,
không phải bằng chứng cần sửa môi trường RAAC để ép cả8F2Pfail. Harness chính thức
chấm patch theo kết quả sau áp patch; quy tắc RAAC kiểm tra mọiF2Pfail tại baseline
là tiêu chí bổ sung của kế hoạch hiện tại. Giữ nguyên `validation_failed` RAAC,
không tự đổi nhãn, tiêu chí hoặc dữ liệu. Một task đối chứng chưa đại diện cho15task
còn lại; cần đối chứng riêng các task khác nếu tiếp tục phạm vi đó.

## Cleanup

Wrapper chỉ xóa container mang tên run cụ thể, image chính thức nếu chưa tồn tại
trước lượt chạy, và thư mục WSL tạm có absolute path cố định sau khi sao lưu log.
Không prune Docker chung, không đụng image/cache RAAC. Lượt đầu đã dọn image
0,95GiB và `/home/phuvi301/raac-reference-tmp-20261019`; v2 cũng đã xóa image
và `/home/phuvi301/raac-reference-tmp-20261019-v2`. Docker inspect xác nhận image
không còn, không còn container mang tênrun. Raw Stage1 được hash lại, không thay đổi.
Giữ report/log cần thiết, không giữ bản clone hoặc venv để chiếm dung lượng.

Nguồn: [harness v3.0.0](https://github.com/SWE-bench/SWE-bench/blob/v3.0.0/swebench/harness/run_evaluation.py).
