# Pipeline sandbox độc lập

Python >=3.11 trên host điều phối Git và Docker SDK. Code repo, cài dependency và test chỉ chạy trong Docker. Docker Desktop phải chạy Linux containers; framework và repo không chia sẻ môi trường Python.

## CLI

Sau `python -m pip install -e ".[dev]"` và kích hoạt .venv:

```powershell
raac sandbox check
raac sandbox prepare --task-id psf__requests-6027
raac sandbox run --task-id psf__requests-6027
raac sandbox run --task-id psf__requests-6027 --patch candidate.patch
```

Mọi lệnh nhận `--config`. Prepare/run nhận `--dataset`. Đường dẫn cấu hình và các đường dẫn trong YAML tương đối với gốc project. Patch tương đối với thư mục terminal. CLI xuất JSON; exit code 0 cho ready/prepared/passed, 1 cho trạng thái khác.

Dataset mặc định nằm ngay trong project tại `data/tasks/cleaned_benchmark.json`, khai báo bằng đường dẫn tương đối trong configs/benchmark.yaml. Đây là bản sao nguyên vẹn từ Stage1 (120 issue/12 repo); pipeline không còn phụ thuộc đường dẫn Stage1 trên máy. Dataset này được phép theo dõi bằng Git, còn repo cache và kết quả chạy vẫn được bỏ qua. Loader kiểm tra trường bắt buộc, ID trùng, repo URL GitHub và SHA commit đầy đủ; không sửa file nguồn.

## Profile và vòng đời

configs/repos/requests.yaml khai báo base_image, system_packages, install_commands và test_command dạng danh sách đối số. task_overrides ánh xạ task ID sang trường cần thay thế; danh sách được thay toàn bộ. Profile là cấu hình đáng tin cậy do người vận hành quản lý; không lấy lệnh từ issue. Không đặt secret trong profile hoặc URL. Container không nhận API key, proxy từ Docker client hoặc Docker socket.

Git bare cache ở data/repos/<owner>/<repo>.git có khóa liên tiến trình, khởi tạo tạm rồi rename; fetch commit khi cần. Source xuất đúng commit với LF. Context chứa Git metadata đã làm sạch: HEAD/tree/blob của base; profile source_history=versioned giữ ancestry và các tag tổ tiên để SCM tính version đúng, không đưa commit/tag tương lai, hook hoặc credential của host vào image. Chưa hỗ trợ submodule hoặc Git LFS.

Dockerfile.sandbox là template được render từ profile. Context tạm chỉ có source.tar và Dockerfile. Cache image theo repo, commit, profile hiệu lực và Dockerfile, lưu image ID thực tế. Build có mạng; bước cài repo chạy UID 1000. Base image dùng tag và một số dependency trực tiếp/bắc cầu chưa khóa hoàn toàn nên rebuild khi mất cache chưa bảo đảm byte-identical.

Baseline và candidate dùng container/volume mới từ cùng image; Docker chép /workspace vào volume rỗng. Candidate chạy git apply --check rồi git apply. Patch lỗi không chạy test. Baseline không áp candidate patch. Container/volume được dọn trong finally; cache/image được giữ. Nếu host bị kill hoặc daemon mất kết nối trong cleanup, tài nguyên có thể còn lại, kiểm tra bằng label raac=<run_id>.

Container test tắt mạng, bỏ capabilities, bật no-new-privileges, UID 1000, giới hạn 2 CPU, 4 GiB RAM, 256 PID và 600 giây. Không fallback chạy trên host. Test cần Internet sẽ fail; pipeline không mở mạng để làm test pass.

Requests có commit cũ dùng test_requests.py và commit mới dùng tests/. Profile chỉ định tests/ cho commit mới, override test_requests.py cho commit cũ, dùng Python 3.9 và dependency dựa trên requirements-dev của commit pilot 6027. Đã chạy Docker thật cho task 6027: 544 passed, 4 failed do chính sách chặn mạng ngoài; cần override và kiểm chứng khi task khác cần môi trường khác. Chưa coi mọi commit Requests đã được hỗ trợ.

## Kết quả

results/<run_id>/ chứa record.json, candidate.patch nếu có, build.log khi build mới, baseline/candidate.log và XML khi pytest tạo được. Bản ghi gồm task/base commit, hash patch, hash dataset/config, registry VERSION, snapshot cấu hình hiệu lực, image ID, thời gian, exit code, timeout và thống kê pytest nếu có.

Trạng thái: profile_missing, preparation_error, execution_error, prepared, passed, test_failed, collection_error, pytest_error, test_command_error, no_tests, patch_error, timeout. Status cấp run phản ánh candidate khi có patch, ngược lại là baseline; luôn xem cả hai. Dataset chưa có regression test riêng: baseline fail không mặc nhiên tái hiện issue, suite pass không chứng minh issue đã sửa.

## Kiểm thử

```powershell
python -m pytest -q
$env:RAAC_DOCKER_TESTS = "1"
python -m pytest tests/integration/test_docker_sandbox.py -q
Remove-Item Env:RAAC_DOCKER_TESTS
```

Unit test và Git fixture chạy không cần Docker. Integration opt-in dùng Docker thật, báo lỗi nếu daemon không sẵn sàng. Kiểm tra baseline fail/patch pass, patch lỗi, timeout, chặn mạng, ghi file không đổi host, hai run không chia sẻ workspace và cleanup. Batch/resume thuộc sandbox; chưa kết nối LLM/L1–L4.

Bằng chứng kiểm chứng pipeline ban đầu: **21 passed**, gồm 4 integration Docker thật. Sau khi mở rộng batch và kiểm tra tổng ngân sách source + build, bộ kiểm thử đạt **42 passed** (35 unit và 7 integration Docker thật). Ruff và format được kiểm tra cùng lượt. Chi tiết pilot Requests xem [pilot_log.md](pilot_log.md).


## Batch toàn dataset

```powershell
raac sandbox profiles check
raac sandbox batch --batch-id baseline-all
raac sandbox batch --batch-id baseline-all --resume
raac sandbox batch --batch-id baseline-all --resume --retry-errors
# Lọc repo hoặc task (có thể lặp tùy chọn); resume tự khôi phục bộ lọc và thứ tự đã lưu:
raac sandbox batch --batch-id requests-only --repo psf/requests
```

Mặc định batch chạy tuần tự, mỗi task tối đa 1800 giây build và 1800 giây test. Profile có thể đặt thời hạn thấp hơn. CLI đơn lẻ mặc định vẫn 600 giây test. `--retry-errors` cần `--resume`, thêm attempt cho lỗi hạ tầng/timeout và giữ lịch sử; không tự chạy lại test fail. Task bị ngắt chưa hoàn tất được tiếp tục bằng lượt mới.

Manifest được ghi atomic trước khi chạy và sau mỗi task ở results/batches/<batch_id>/manifest.json. `summary.json` và `summary.csv` tổng hợp từng task, `repos.csv` tổng hợp từng repo. Báo cáo tách not_run, preparation_error, collection_error, test_failed, passed, build_timeout và timeout. `executed` chỉ đếm passed/test_failed; xử lý hết danh sách chưa chắc đã chạy được test trên toàn dataset. Fingerprint kiểm tra dataset, registry, config, profile, template và code sandbox; đổi bất kỳ thành phần nào phải tạo batch mới. Không dùng batch này thay runner L1–L4.

Build dùng builder Docker riêng từng lượt, log ghi trực tiếp ra build.log, hủy CLI và xóa builder khi hết hạn hoặc bị ngắt. Cache build tại data/build-cache giúp dùng lại các layer dependency trước khi thêm source. Test log cập nhật trong lúc chạy. Git context có HEAD và object của đúng commit (profile versioned còn có ancestor commit và release tag), không có hook hoặc cấu hình host, để hỗ trợ các backend cần Git. Không tải submodule/Git LFS tự động. Cache Git mới fetch commit cần dùng; profile source_history=versioned còn lấy tag và lịch sử commit đầy đủ để giữ đúng version của setuptools-scm/versioneer. Cache mirror cũ vẫn được tái sử dụng.

Profile hỗ trợ environment, dependency_commands (trước source), install_commands (sau source), test_framework (pytest/django/command), report_format (junit/none), report_path và timeout. Biến môi trường chứa secret bị từ chối. Pytest dùng JUnit; Django lấy số test/failure/error từ bản tổng kết Ran N tests của runner gốc. Khi không có báo cáo hay tổng kết hợp lệ, thống kê để null và statistics_available=false; không suy diễn số test từ exit code. Xem [sandbox_profiles.md](sandbox_profiles.md) để biết các manifest đã đối chiếu.

Batch xuất batch_id ngay lúc bắt đầu lên stderr, tạo manifest và summary trước task đầu tiên. Batch trả exit code 0 khi mọi task đã chạy suite (passed hoặc test_failed); trả 1 khi còn timeout/lỗi môi trường/chưa chạy, 130 khi Ctrl+C. Lệnh đơn lẻ giữ exit code 1 cho test_failed.

Resume không truyền bộ lọc sẽ tự dùng danh sách/thứ tự task trong manifest; bộ lọc truyền lại phải chọn đúng cùng tập task. Bộ lọc --task-id giữ thứ tự người dùng cung cấp trong lần tạo batch.


Astropy chạy cả astropy/ và docs/ theo tox gốc; Matplotlib chạy toàn lib/ (gồm mpl_toolkits); Xarray dùng testpaths của từng commit để bao gồm cả properties/ khi có. Không dùng cờ quickcheck, bỏ test hay mở mạng để ép baseline pass.

Profile native có post_patch_commands: sau git apply, candidate rebuild extension offline từ dependency đã chuẩn bị, rồi mới chạy test; lỗi rebuild trả patch_setup_error. Astropy, Matplotlib và scikit-learn dùng pip editable --no-index --no-deps --no-build-isolation. Các phase vẫn chịu cùng giới hạn thời gian/tài nguyên; baseline và candidate có volume riêng.
