# Nhật ký pilot sandbox

## 2026-10-17 — Requests / psf__requests-6027

- Dataset Stage1: 120 issue thuộc 12 repo; không thay đổi file nguồn.
- Base commit: `0192aac24123735b3eaf9b08df46429bb770c283`.
- Docker Linux Engine 29.1.2, profile Python 3.9 / Debian Bookworm, pytest 6.2.5.
- Pipeline đã mirror clone/cache, archive đúng commit, build image, tạo volume sạch, chạy baseline, ghi JSON/log/XML và dọn container/volume.
- Kết quả cuối: **544 passed, 4 failed, 12 skipped, 1 xfailed**, không collection error, không timeout; 561 test cases trong JUnit (13 skipped bao gồm xfailed). Thời gian phase 68.547 giây.
- Bản ghi: [record.json](../results/6330c88344534bb4a0157412f480795b/record.json), [baseline.log](../results/6330c88344534bb4a0157412f480795b/baseline.log). results/ được gitignore, bằng chứng này nằm trên máy đã chạy.
- Bốn failure đều thuộc TestTimeout (test_connect_timeout/test_total_timeout_connect, hai tham số mỗi test): test chờ ConnectTimeout khi gọi 10.255.255.1 nhưng sandbox không có mạng ngoài nên nhận ConnectionError / Network is unreachable. Giữ nguyên test và chính sách mạng; không suy ra lỗi agent hay issue completion từ baseline này.
- Dataset không có regression test riêng cho issue proxy authentication; không khẳng định issue đã được tái hiện hay sửa. Chưa áp candidate patch vào Requests; đường áp patch đã kiểm chứng bằng fixture Docker thật.

### Các lỗi môi trường đã xử lý

- Docker Desktop ban đầu chưa chạy; đã khởi động và xác nhận Linux Engine.
- Git archive trên Windows chịu ảnh hưởng core.autocrlf; export hiện ép LF, fixture xác nhận đúng nội dung commit.
- Debian Bullseye apt trả HTTP 404; chuyển profile sang Bookworm.
- Pytest discovery thu thập cả docs và thiếu pygments; chọn tests/ và override test_requests.py cho các task cũ.
- network_disabled làm localhost không phân giải trong container; chuyển network_mode=none, giữ loopback nhưng chặn Internet. Failures giảm từ 32 + 1 error xuống 4 failure do chính sách mạng nêu trên.

### Kiểm chứng framework

`RAAC_DOCKER_TESTS=1 python -m pytest -q`: **21 passed**, gồm 17 unit/Git tests và 4 integration Docker thật. Bao phủ base commit khác HEAD, baseline fail → patch pass, patch lỗi, timeout, loopback hoạt động, chặn Internet, hai workspace song song, patch thử ghi host không thay đổi sentinel và cleanup. Ruff/check format qua.

Đây là pilot hạ tầng sandbox, chưa phải pilot thực nghiệm L1–L4. Profile các repo khác, batch/resume, LLM và evaluator chưa hiện thực trong phạm vi này.


## 2026-10-17 — Mở rộng 120 task / 12 repo (đang kiểm chứng)

- Đã đọc manifest tại base commit cho 120 task, tạo 12 profile và kiểm tra cấu hình: covered=120, errors=0. Chưa coi đây là bằng chứng chạy test thành công.
- Suite framework đạt 32 passed (26 unit + 6 Docker integration) trước khi bổ sung kiểm tra ancestry; kiểm tra unit mới nhất đạt 27 passed.
- smoke-v1 phát hiện SCM thiếu Git metadata; smoke-v2 bổ sung HEAD/object và build được Astropy nhưng SCM tạo version 0.1.dev do thiếu tag. Đã dừng lượt Astropy này và không dùng làm bằng chứng môi trường hợp lệ.
- Source versioned hiện fetch tag tips và đủ ancestry để bảo toàn version/commit distance. Unit test Git xác nhận describe v1.0.0-1-g<hash> từ snapshot, không tạo version giả trong profile.
- smoke-v3 kiểm chứng lần lượt các repo đại diện với ngân sách 30/30 phút. Kết quả và tiến độ ở results/batches/smoke-v3/. Các batch chẩn đoán có cấu hình bị thay đổi sẽ bị ngắt bằng kiểm tra fingerprint; không dùng chúng làm batch chuẩn cho 120 task.
- Benchmark L1–L4, test tái hiện từng issue và evaluator chưa nằm trong phạm vi này.


### Chẩn đoán đại diện và môi trường đã sửa

- Lượt chẩn đoán tuần tự lưu tại `results/diagnostics/smoke/`; mỗi attempt giữ record riêng. Đây không phải batch đóng băng đủ 120 task.
- Requests 6027: 561 test cases, 4 failure do kết nối bên ngoài bị chặn. Seaborn 2967 và Pylint 1548 đã pass suite. Pytest 5786 và Sphinx 4258 đã chạy suite có failure.
- Xarray 3150: build đầu lỗi khi Versioneer gọi Git lấy ngày commit. Snapshot được bổ sung ancestor tree objects; unit test bảo toàn Git metadata/version/date và loại commit/tag tương lai.
- Matplotlib 5087: build đầu thiếu pybind11; thêm phiên bản 2.10.4. Scope chạy toàn bộ `lib`, gồm mpl_toolkits.
- Astropy 14507: lượt core đầu chạy 25.611 test cases, có 3 failure và 4 error liên quan dữ liệu IERS/thời gian hiện tại. Profile sau đó bổ sung scope docs đúng tox cùng dependency plotting; lượt cũ chưa xác nhận profile mới.
- Django 23740: runner gốc chạy 15.824 test, 3 failure, 0 error, 1.289 skip và 5 expected failure. Adapter đọc footer cuối để tránh nhầm output từ test kiểm thử chính runner.
- scikit-learn 9287: build native thành công, chạy 12.996 test cases; fixture dùng `matplotlib.use(..., warn=False)` không tương thích Matplotlib 3.3. Đã đổi override cũ sang 3.2.2, bổ sung pandas/numpydoc; cần kiểm chứng profile mới trong batch.
- Flask 5010: dependency Werkzeug 2.3 tạo failure deprecation cookie; ghim 2.2.3 để phù hợp thế hệ Flask tại commit.
- Bộ kiểm thử framework trước kiểm tra tổng deadline: **41 passed**, gồm **7 integration Docker thật**. Unit mới kiểm tra lấy source tiêu thụ ngân sách build: **35 passed**. Không dùng kết quả mock để nghiệm thu các profile thực tế.
- Các dependency/profile đã chỉnh phải được chạy lại. Chưa nghiệm thu 120/120; batch chuẩn `baseline-all-v1` được xếp chạy sau lượt chẩn đoán hiện tại. Giữ nguyên ngân sách 30 phút chuẩn bị + 30 phút test và mạng ngoài bị chặn.

- 2026-10-17 | Kiểm tra cuối sau bổ sung tổng deadline: `RAAC_DOCKER_TESTS=1 python -m pytest -q --tb=short` đạt **42 passed** trong 121,44 giây (35 unit + 7 integration Docker thật); Ruff, format và `git diff --check` qua.

- Lượt Sympy eefec15bf3df4285893a154fd068fd88 được dừng chủ động để thay profile một worker bằng hai worker; không tính là suite hoàn tất hoặc timeout. Batch chuẩn chạy lại task với đủ ngân sách 1800 giây.


### Batch chuẩn đang chạy

`baseline-all-v1` đã tạo manifest đủ **120 task** với ngân sách build/test **1800/1800 giây**, fingerprint `4def93745df10dc88fae8e6622d96a6a45a0f5a64b303eaf9cd21ac196492c72`. Batch chạy tuần tự, ưu tiên Requests và các repo nhẹ trước nhóm scientific/Django/Sympy. Kết quả trực tiếp: `results/batches/baseline-all-v1/summary.json`, `summary.csv`, `repos.csv`; tiến độ CLI ở `results/baseline-all-v1.progress.log`.

Chưa có kết quả cuối cho 120 task. Chỉ các dòng passed/test_failed mới được tính là đã chạy suite; không nghiệm thu hỗ trợ toàn dataset khi còn not_run, lỗi chuẩn bị/collection/runner hoặc timeout. Tài liệu này ghi thời điểm khởi chạy, không thay cho summary trực tiếp.

Nếu tiến trình bị ngắt: `raac sandbox batch --batch-id baseline-all-v1 --resume`. Retry lỗi hạ tầng/timeout cùng fingerprint: thêm `--retry-errors`; chỉnh profile/code/template phải tạo batch mới. Không chạy resume trong lúc batch còn giữ khóa.

- 2026-10-17 | Tiếp tục baseline-all-v1: 8/120 task Requests đã chạy suite (test_failed, không collection/timeout). Requests-1051 có lỗi tương thích HTTPConnection strict trên Python 3.9; chuẩn bị configs/repos/requests-legacy.yaml dùng Python 2.7, chưa nghiệm thu profile này. Tiến trình cũ mất khi build Flask (BuildKit context canceled); lưu interrupted record và dọn builder tồn dư, resume giữ 8 kết quả cũ. Flask dựng lại thành công, đang chạy tiếp. | Agent hỗ trợ Vinh | Chưa nghiệm thu toàn dataset.

- 2026-10-17 | Resume baseline-all-v1: Flask 5010 pass 481 test, 3 skip; tổng 9/120 suite đã chạy (8 test_failed Requests, 1 passed), Seaborn đang chạy. Đã khởi động driver nền PID 16080 để chờ khóa/resume batch nếu bị ngắt và chạy validation requests-1051-legacy-v1 sau batch chính. Profile legacy Python 2.7 và config riêng chưa được kiểm chứng Docker; không thay fingerprint batch đang chạy. Kết quả driver ở results/baseline-driver.json và *.log. | Agent hỗ trợ Vinh | Đang chạy, chưa nghiệm thu 120/120.

- 2026-10-17 | Heartbeat: batch baseline-all-v1 đã chạy suite 26/120 task, 5 passed và 21 test_failed; không collection/preparation/timeout ở các task đã hoàn tất. Pytest có subprocess ModuleNotFoundError pytest, setuptools deprecation và attrs không tương thích; chuẩn bị profile pytest-compatible cùng config compatibility riêng, chưa kiểm chứng Docker. Không đổi fingerprint batch đang chạy, chưa push. | Agent hỗ trợ Vinh | Đang chạy, cần kiểm chứng lại môi trường pytest và Requests-1051.

- 2026-10-17 | Pylint-4901 phát hiện collection_error do thiếu package py._path. Chuẩn bị pylint-compatible.yaml thêm py==1.11.0 và config compatibility; các profile sửa cần kiểm chứng bằng batch riêng sau batch chính/legacy. Tổng hiện tại 26 suite đã chạy, 1 collection_error, 93 chưa chạy. | Agent hỗ trợ Vinh | Đang xử lý, chưa nghiệm thu/push.

- 2026-10-17 | Heartbeat 18:22: baseline-all-v1 đã chạy suite 40/120 (5 passed, 35 test_failed), 6 Pylint collection_error thiếu py, 1 Sphinx preparation_error thiếu flit_core, 73 chưa chạy. Đối chiếu pyproject.toml Sphinx-10678 yêu cầu flit_core>=3.7; thêm flit_core==3.9.0 vào sphinx-compatible.yaml/config compatibility. Queue kiểm chứng sau batch chính và legacy gồm pytest/Pylint/Sphinx; chưa coi profile sửa là đã nghiệm thu. | Agent hỗ trợ Vinh | Đang chạy, chưa push.

- 2026-10-17 | Người dùng yêu cầu push ngay bản triển khai hiện có. Snapshot trước push: baseline-all-v1 đang chạy, 41 suite đã chạy (5 passed, 36 test_failed), 6 collection_error Pylint, 1 preparation_error Sphinx, 72 chưa chạy. Profile compatibility/legacy còn chờ Docker kiểm chứng; chưa nghiệm thu hỗ trợ 120/120. Chỉ commit source/config/test/tài liệu và dataset; cache/results/log không đưa lên Git. | Agent hỗ trợ Vinh | Push bản triển khai đang kiểm chứng theo yêu cầu; tiếp tục theo dõi batch.
