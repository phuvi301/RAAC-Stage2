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

Dockerfile.sandbox là template được render từ profile. Context tạm chỉ có source.tar và Dockerfile. Cache image theo repo, commit, hash source và Dockerfile đã render, lưu image ID thực tế. Lệnh test/selector không làm mất image nếu công thức build không đổi. Build có mạng; bước cài repo chạy UID 1000. Base image dùng tag và một số dependency trực tiếp/bắc cầu chưa khóa hoàn toàn nên rebuild khi mất cache chưa bảo đảm byte-identical.

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

Build dùng builder riêng của sandbox `raac-sandbox-managed-v1`, được khóa độc quyền. Build thành công giữ cache nội bộ; timeout, lỗi hoặc ngắt sẽ hủy CLI và xóa đúng builder này để dừng công việc phía daemon. Không còn export/import toàn bộ OCI cache sau mỗi task. Bước `--load` vẫn cần thiết; log giữ số đo từng bước BuildKit. Cache cũ `data/build-cache` được đưa vào retention để thu hồi an toàn. Test log cập nhật trong lúc chạy. Git context có HEAD và object của đúng commit (profile versioned còn có ancestor commit và release tag), không có hook hoặc cấu hình host, để hỗ trợ các backend cần Git. Không tải submodule/Git LFS tự động. Cache Git mới fetch commit cần dùng; profile source_history=versioned còn lấy tag và lịch sử commit đầy đủ để giữ đúng version của setuptools-scm/versioneer. Cache mirror cũ vẫn được tái sử dụng.

Profile hỗ trợ environment, dependency_commands (trước source), install_commands (sau source), test_framework (pytest/django/command), report_format (junit/none), report_path và timeout. Biến môi trường chứa secret bị từ chối. Pytest dùng JUnit; Django lấy số test/failure/error từ bản tổng kết Ran N tests của runner gốc. Khi không có báo cáo hay tổng kết hợp lệ, thống kê để null và statistics_available=false; không suy diễn số test từ exit code. Xem [sandbox_profiles.md](sandbox_profiles.md) để biết các manifest đã đối chiếu.

Batch xuất batch_id ngay lúc bắt đầu lên stderr, tạo manifest và summary trước task đầu tiên. Batch trả exit code 0 khi mọi task đã chạy suite (passed hoặc test_failed); trả 1 khi còn timeout/lỗi môi trường/chưa chạy, 130 khi Ctrl+C. Lệnh đơn lẻ giữ exit code 1 cho test_failed.

Resume không truyền bộ lọc sẽ tự dùng danh sách/thứ tự task trong manifest; bộ lọc truyền lại phải chọn đúng cùng tập task. Bộ lọc --task-id giữ thứ tự người dùng cung cấp trong lần tạo batch.


Astropy chạy cả astropy/ và docs/ theo tox gốc; Matplotlib chạy toàn lib/ (gồm mpl_toolkits); Xarray dùng testpaths của từng commit để bao gồm cả properties/ khi có. Không dùng cờ quickcheck, bỏ test hay mở mạng để ép baseline pass.

Profile native có post_patch_commands: sau git apply, candidate rebuild extension offline từ dependency đã chuẩn bị, rồi mới chạy test; lỗi rebuild trả patch_setup_error. Astropy, Matplotlib và scikit-learn dùng pip editable --no-index --no-deps --no-build-isolation. Các phase vẫn chịu cùng giới hạn thời gian/tài nguyên; baseline và candidate có volume riêng.


## Test theo issue từ SWE-bench Verified

Mặc định mới của `sandbox run`, `batch` và `validate` là `--test-mode issue`.
Chạy toàn suite cũ phải dùng `--test-mode suite`. Dataset cleaned và Task dành cho
agent giữ nguyên; evaluator riêng tại `data/evaluation/swe_bench_verified.json`
chứa 120 ánh xạ issue ID → instance ID (PR), test patch, gold patch và F2P/P2P.
Không cung cấp file evaluator, gold patch hoặc container validation cho agent.
File raw Stage1 không bị thay đổi. Import khớp repo + base commit và xác nhận PR URL;
thiếu hoặc nhiều kết quả sẽ bị từ chối. Hash raw và artifact được lưu để truy vết.

```powershell
raac sandbox import-verified --raw C:/Users/Admin/Desktop/DACN/Stage1/prototype/data/raw/swe_bench_verified.jsonl
raac sandbox validate --config configs/benchmark.compatibility.yaml --task-id psf__requests-6027 --batch-id issue-requests
raac sandbox validate --config configs/benchmark.compatibility.yaml --batch-id issue-all
raac sandbox validate --config configs/benchmark.compatibility.yaml --batch-id issue-all --resume
raac sandbox run --config configs/benchmark.compatibility.yaml --task-id psf__requests-6027 --patch candidate.patch
raac sandbox run --task-id psf__requests-6027 --test-mode suite
```

Validation chạy baseline + test patch, rồi gold + test patch trong volume mới từ
cùng image. Gold không nằm trong build context/image; chỉ nạp vào container gold.
Candidate sửa bất kỳ file thuộc test patch sẽ nhận `protected_test_error`. Patch
code được áp trước test patch; lỗi test patch nhận `test_patch_error`. Native code
rebuild offline sau khi áp cả hai patch. Mỗi phase giữ 1800 giây khi chạy batch.

Mỗi selector F2P phải fail ở baseline và pass sau gold; mọi P2P phải pass ở cả hai.
Skip, xfail/xpass, thiếu test, collection error và timeout không tính là pass.
`validated` xác nhận chuyển trạng thái test; log assertion vẫn cần được xem khi
nghi ngờ failure không phản ánh issue. Candidate chỉ `resolved` nếu baseline hợp
lệ và mọi test yêu cầu pass; đây là tiêu chí test, không phải chứng minh mọi hành vi.
P2P rỗng được giữ nguyên và không suy ra đã kiểm tra regression đầy đủ.

Pytest thu thập các file liên quan, plugin chọn test qua manifest, ghi per-test
JSON (`*.tests.json`) và node ID thực tế. Các tên rút gọn của parser SWE-bench được
chuẩn hóa theo repo; khi một alias khớp nhiều testcase, chạy tất cả và chỉ xác nhận
outcome đồng nhất. Sympy tên trần phải khớp duy nhất trong file test patch. Django
chuyển nhãn unittest và ghi kết quả bằng runner gốc với SQLite.

Record/summary thêm chế độ test, hash artifact/test/gold patch, metadata môi trường,
số selector yêu cầu/tìm thấy/đã chạy, baseline/gold/candidate validation và thời gian
chuẩn bị/từng phase. Fingerprint bao gồm evaluator/selector/adapter và chế độ chạy.
Không resume batch suite cũ bằng issue mode. Batch cũ được giữ làm lịch sử, không
coi kết quả suite là validation issue. Chỉ nghiệm thu 120 task sau Docker validation
thực tế; cấu hình đủ profile không thay thế bằng chứng.


## Chuẩn bị nhanh và retention (2026-10-17)

```powershell
raac sandbox preflight --task-id psf__requests-6027 --batch-id requests-preflight-v1
raac sandbox validate --stage pilot --repo psf/requests --batch-id requests-pilot-v1
raac sandbox validate --stage full --reuse-validated --batch-id issue-full-v2
raac sandbox cache status
raac sandbox cache prune --max-gib 30          # chỉ xem trước
raac sandbox cache prune --max-gib 30 --apply
raac sandbox stop --batch-id issue-full-v2
# Resume tự xóa cờ STOP, vẫn kiểm tra fingerprint:
raac sandbox validate --stage full --reuse-validated --batch-id issue-full-v2 --resume
```

Preflight chạy import/version và collect selector sau test patch trong Docker, không thực thi test, không có gold. `preflight_passed` chỉ xác nhận môi trường/collection tại base. Pilot chạy toàn bộ F2P baseline+gold, bỏ P2P khỏi lượt chẩn đoán này; `pilot_passed` không phải `validated`. Full là mặc định và giữ nguyên F2P/P2P. Các chế độ có fingerprint/checkpoint khác nhau; không resume batch cũ sau khi thay mã/config.

`--reuse-validated` chỉ dùng bằng chứng full có fingerprint từng task khớp task/profile/evaluator/adapter/template/registry/policy và đúng image ID còn tồn tại. Hash cả hai log và hai report phải khớp. Record cũ chưa có bằng chứng này được chạy lại; image cũ vẫn có thể tái sử dụng nếu hash source và công thức build giống hệt. Record mới liên kết `reused_from`; thời gian baseline/gold trong record được tham chiếu là thời gian lượt gốc, không phải phase mới vừa chạy.

Repo native khai báo `native_rebuild_paths` và `native_safe_paths`. Generator/build config/extension luôn rebuild; chỉ các đường dẫn Python đã cho phép mới được bỏ rebuild, đường dẫn chưa biết vẫn rebuild. Xét cả code patch và test patch. Image và checkout candidate không chứa gold.

Batch tự dọn tại đầu mỗi task, bảo vệ image của task đó và mọi image còn được container tham chiếu. Ngân sách mềm 30 GiB gồm Git cache, image và cache build. Giữ Git cache; chỉ xóa tag image thuộc sandbox và cache do sandbox quản lý, không force image đang dùng. Size image có layer chia sẻ nên báo upper bound bảo thủ; phần không đo được ghi null, không coi là 0. Có thể vượt ngân sách khi tài nguyên được bảo vệ lớn hoặc không thể thu hồi. Docker Desktop có thể chưa giảm ngay kích thước VHDX trên host.

Tiến độ stderr và `results/<run_id>/progress.json` ghi phase, thời gian, đường dẫn log; build/test cập nhật khoảng 10 giây. Build và test có hủy thật, source fetch vẫn chịu deadline chuẩn bị. `sandbox stop` dừng ở lần kiểm tra tiếp theo, cleanup và giữ checkpoint; không gửi taskkill thông thường.

Để chạy hàng đợi kiểm chứng ngoài Codex, từ gốc project:

```powershell
.venv/Scripts/python.exe -X utf8 scripts/validate_environments.py --prefix issue-optimized-v1
```

Hàng đợi chạy preflight 120 task, pilot một task mỗi repo rồi full 120 task; một task lỗi không chặn các task còn lại. Chạy lại cùng prefix để resume, fingerprint đổi thì phải chọn prefix mới. STOP hoặc Ctrl+C dừng cả hàng đợi, không tự khởi động phase tiếp theo. Các lỗi môi trường vẫn cần đọc log và sửa bằng batch mới; hàng đợi không tự sửa profile hoặc mở mạng. Kết quả ở `results/<prefix>-queue.json` và ba manifest riêng. Không có lịch tự động định kỳ.
