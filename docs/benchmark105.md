# Tập benchmark 105 task — phiên bản v1

Ngày cố định: **2026-10-19**, theo yêu cầu dùng các task đã full strict
`validated`. Đây là **tập con từ SWE-bench Verified**, không phải toàn bộ
SWE-bench Verified. Tập gốc của project vẫn có120task/12repo.

- Manifest: `data/tasks/benchmark105-v1.json`.
- Danh sách15task loại, lý do và record: `data/tasks/benchmark105-v1-exclusions.json`.
- Dataset gốc giữ nguyên: `data/tasks/cleaned_benchmark.json`.
- Cấu hình chạy tập105: `configs/benchmark105.yaml`.

105task được chọn từ kết quả full baseline/gold mới nhất khi cố định. Không
dùng kết quả candidate hoặc model để chọn.15task bị loại do nhãn baseline,
selector/parser, xfail hoặc test patch không collection được trên base. Log
đối chứng official và lý do chi tiết vẫn được giữ. Coverage setup là
**105/120=87,5%**; không tuyên bố120/120môi trường đã nghiệm thu.

## Chạy

```powershell
raac sandbox profiles check --config configs/benchmark105.yaml
raac sandbox run --config configs/benchmark105.yaml --task-id <ID-trong-manifest> --patch <candidate.patch>
```

Chỉ khi chủ động cần validation lại trên cấu hình mới:

```powershell
raac sandbox validate --config configs/benchmark105.yaml --batch-id benchmark105-validation-v1 --reuse-validated
```

Manifest là đầu vào dataset hợp lệ cho CLI; loader kiểm tra hash dataset gốc,
số lượng, ID trùng và ID không tồn tại. ID bị loại không chạy được khi dùng
cấu hình105. Sau đợt dọn môi trường15task, cả config mặc định
`configs/benchmark.yaml` và `configs/benchmark.compatibility.yaml` cũng chọn105.
Dataset gốc120 vẫn giữ để đối chiếu; các override riêng cho15task đã gỡ.
Không resume checkpoint cũ bằng tập105 hoặc cấu hình đã thay đổi.

Hash evaluator được kiểm tra trước khi chạy issue tests. Dữ liệu evaluator,
gold patch và test patch không được đưa vào Task cho agent. Batch fingerprint
bao gồm manifest, dataset nguồn, config/profile, evaluator và code adapter.

## Chấm và so sánh L1–L4

Điểm chính: **resolved_rate = số task candidate resolved /105 ×100%**.
Không thu nhỏ mẫu số theo số task đã chạy. Task chưa có candidate hợp lệ hoặc
lỗi hạ tầng chưa giải quyết vẫn chưa được tính resolved; báo riêng incomplete,
test fail và lỗi hạ tầng, không gọi chúng là cùng một loại lỗi model.

Mọi level dùng đúng105ID, base commit, evaluator, môi trường và ngân sách đã
đóng băng giống nhau. Báo thêm thời gian, token, số lần gọi model và thử patch.
Không thay task sau khi thấy kết quả model. Thay membership phải tạo phiên
bản mới và chạy các level trên cùng tập mới.

Manifest cố định **membership và bằng chứng setup**, chưa phải xác nhận mọi
task chạy trên cùng phiên bản adapter: record lịch sử có nhiều fingerprint.
Hash log/JSON của record gốc (kể cả record được tái sử dụng) đã được kiểm tra
khi tạo manifest; record cũ thiếu hash được hash tại lúc cố định và ghi rõ.
Trước so sánh các level, cần đóng băng cấu hình thực thi dùng chung. Không
nghiệm thu benchmark L1–L4, evaluator tổng thể hoặc A5 chỉ nhờ tập105này.

## Giữ bằng chứng khi dọn results

Giữ record, checkpoint, baseline/gold log, per-test JSON, báo cáo framework,
artifact đối chứng official và log lỗi giúp chẩn đoán. Dọn script hàng đợi đã
kết thúc, bản console/terminal dư thừa và file rỗng; audit xóa ở `results/`.
Không xóa cache/image trong đợt dọn log này. Khoảng319MiB results trước dọn
không phải phần chiếm dung lượng chính của Docker.

Đợt dọn ngày2026-10-19 đã xóa37file dư,225415byte (khoảng220KiB), kiểm tra
hash từng file trước xóa. Audit: `results/cleanup-benchmark105-20261019.json`.
Bằng chứng của cả105task được kiểm tra lại sau dọn và còn nguyên.

Kiểm chứng thay đổi selection:93unit test liên quan pass; CLI profilecheck
105/105 với config mới và120/120 với config gốc, Ruff/format/diffcheck qua.
Đợt này không chạy lại validation105task: bằng chứng setup là các record đã
kiểm tra và đóng băng trong manifest, không phải một batchvalidation mới.

## Dọn môi trường nhóm15task

Theo yêu cầu ngày2026-10-19, đã gỡ11override dành riêng cho nhóm bị loại
(4Requests ở cả2profile,2Xarray và1Sympy). Không phải cả15task đều có override
riêng. Giữ các profile repo chung vì tập105 vẫn dùng đủ12repo. So sánh trước/sau
xác nhận recipeDocker của cả105task giữ nguyên.

Đã xóa12image `raac-sandbox` chỉ được record của nhóm bị loại tham chiếu,
khôngforce và khôngimageđangdùng. DockerLayersSize giảm10.145.152.134byte,
khoảng9,45GiB; đây là số liệu Docker, không cam kết VHDXWindows co lại tương ứng.
Các task còn lại trong nhóm15 không có image riêng đang tồn tại để xóa.
Gitcache theo repo và Buildxcache dùng chung/không xác định được chủ sở hữu
task được giữ; không prune toànbuilder khiến105task phải cài lại dependency.
Không xóa Python2base vì Requests1051 trong tập105 vẫn cần Python2.

Audit: `results/retired15-environments-20261019.json`, gồm override đã gỡ,
imageID, before/afterDocker và lỗi cleanup (không có). Record, log và metadata
frozenbenchmark được giữ; xóaimage không làm thay đổi bằng chứng test đã chạy.
