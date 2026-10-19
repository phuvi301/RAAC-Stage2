# Sửa lỗi môi trường từ log ngày 2026-10-18

Nguồn: preflight `issue-optimized-v1`: 90 pass, 30 lỗi. Pilot: 10/12 pass; full đã bị người dùng/ngắt trước khi xử lý hết. Giữ nguyên manifest/fingerprint cũ. Không sửa repo, test patch, gold patch hoặc dữ liệu nguồn; mạng ngoài vẫn tắt khi test.

## Thay đổi có căn cứ từ log

- Astropy: pin pyparsing2.4.7 để tránh PyparsingDeprecationWarning bị CI lịch sử coi là lỗi khi Matplotlib3.6.3 import.
- scikit-learn: bỏ pin numpydoc xung đột; pytest7.4.4 cho25957/26306 (minimum pytest7.1.2 và ID tham số complex); Python3.7-slim-bullseye cho12972/13110 vì cloudpickle vendored dùng CodeType Python<=3.7.
- Matplotlib20665: cài trước setuptools-scm7.1.0 để setup_requires không tải SCM10.3.4 không tương thích setuptools57.5.
- Pylint3878: thêm appdirs1.4.4 còn thiếu. Xarray3992/4682: thêm Pint0.16.1, sparse0.14.0, numba0.57.1 và numbagg0.2.1 theo module test_units importorskip(pint). Sympy16432/16884/17624/18755: py1.8.2 để cung cấp alias py.test lịch sử.
- Adapter: chuẩn hóa Django ID đã chứa method trong phần ngoặc; thêm alias whitespace của parser upstream cho Sphinx/scikit-learn; lưu rõ các ứng viên khi selector bare bị trùng, báo lỗi collection root sinh tạm bị thiếu. Không tự bỏ selector.

## Các mẫu chưa thể sửa bằng dependency

- `pylint-dev__pylint-1548`: test_patch import get_annotation chưa có trong base; gold mới thêm API.
- `pylint-dev__pylint-4603`: test_patch import IS_PYPY chưa có trong base; gold mới thêm API.
- `pytest-dev__pytest-7517`: P2P chứa test_capsysbinary.py::test_hello (file sinh tạm) và marker [100%], không phải selector repo có thể chạy trực tiếp.
- `sympy__sympy-14237`: Nhiều file trong test_patch có cùng tên test bare; không đủ định danh để chọn duy nhất theo quy tắc hiện tại.

Các task trên vẫn phải được báo chưa đạt. Không copy API gold vào baseline, không tự sửa P2P hoặc chọn tùy ý test trùng tên.

- `pydata__xarray-3992`: sau khi bổ sung đủ dependency, gold pass2400/2400 selector, không skip. Baseline có4 P2P gọi `integrate(coord=...)` trong test_patch trong khi base chỉ nhận `dim`; gold mới đổi API. Giữ validation_failed, không tự đổi nhãn4 P2P thành F2P hoặc sửa baseline. Record5ec533a5584e4d67b7d8dd42db486b84.

## Kiểm chứng

- Framework: full85 tests passed trong108,66s (73 unit +12 Docker integration). Profile check120/120 và Ruff/format/diff qua.
- Đang kiểm chứng Docker thật từng đại diện: preflight rồi full baseline+gold. Nguồn trạng thái `results/envfix-representatives-v1.json`; run/log được liên kết theo từng attempt.
- Django34272 đã full validated: record `results/4f20ed29fda748dda281ee2eaae6fc0e/record.json`. Chưa coi cấu hình các task còn lại đã được kiểm chứng chỉ vì unit/profile check pass.

## Danh sách 30 task lỗi ở lượt nguồn

| Task | Trạng thái cũ |
| --- | --- |
| `astropy__astropy-14507` | execution_error |
| `astropy__astropy-8340` | execution_error |
| `astropy__astropy-12906` | execution_error |
| `astropy__astropy-13009` | execution_error |
| `astropy__astropy-13355` | execution_error |
| `astropy__astropy-13488` | execution_error |
| `astropy__astropy-14355` | execution_error |
| `astropy__astropy-14363` | execution_error |
| `astropy__astropy-14523` | execution_error |
| `astropy__astropy-14581` | execution_error |
| `django__django-34272` | collection_error |
| `matplotlib__matplotlib-20665` | preparation_error |
| `pydata__xarray-3992` | collection_error |
| `pydata__xarray-4682` | collection_error |
| `pylint-dev__pylint-1548` | collection_error |
| `pylint-dev__pylint-3878` | collection_error |
| `pylint-dev__pylint-4603` | collection_error |
| `pytest-dev__pytest-7517` | execution_error |
| `scikit-learn__scikit-learn-12972` | collection_error |
| `scikit-learn__scikit-learn-13110` | collection_error |
| `scikit-learn__scikit-learn-14709` | collection_error |
| `scikit-learn__scikit-learn-24860` | preparation_error |
| `scikit-learn__scikit-learn-25957` | collection_error |
| `scikit-learn__scikit-learn-26306` | execution_error |
| `sphinx-doc__sphinx-8618` | collection_error |
| `sympy__sympy-14237` | collection_error |
| `sympy__sympy-16432` | collection_error |
| `sympy__sympy-16884` | collection_error |
| `sympy__sympy-17624` | collection_error |
| `sympy__sympy-18755` | collection_error |


Kết quả thêm đã full validated: Sphinx8618 (2 F2P +31 P2P), Sympy16432 (3 F2P +74 P2P), Pylint3878 (1 F2P, P2P rỗng đúng artifact). Xarray3992 preflight pass/gold2400 pass nhưng baseline chưa đạt vì4 P2P phụ thuộc API gold, như ghi ở trên. Các task khác tiếp tục được kiểm chứng, không suy cả profile đều pass từ một đại diện.


### Archive Git và cache image

Trong khi kiểm chứng Xarray4682, preflight và full đã build image khác nhau dù repo/commit/recipe không đổi. Tái hiện3 lần export có2 SHA source khác nhau; diff tar chỉ khác pack/idx/rev và thứ tự của Git metadata. Sửa pack-objects dùng một thread, không reuse compressed object/delta từ cache, pin compression/window/depth/index, tắt reverse index riêng lệnh và sắp thứ tự file.5 lần export sau sửa có cùng SHA04080ca35724fe6c121218e0397f54ae5fd0b44a66a4a079f30d0d2cf465efc9. Có regression qua repack/cache config khác. Bằng chứng results/source-archive-determinism.json; các tar thử110.960.640 byte đã dọn, không ảnh hưởng Git cache.

Trước sửa canonical archive, Xarray4682 đã full validated (1 F2P +1717 P2P), record107ddde8c7e94cabbe60d7cc47603ca1. Sau sửa engine, không resume batch fingerprint cũ hoặc mặc nhiên reuse validation khác code hash; chạy lượt mới. Source/test/gold vẫn nguyên nội dung, chỉ ổn định cách đóng gói Git metadata. Hàng đợi đại diện được dừng có kiểm soát trước khi sửa engine; các record trước/sau giữ provenance riêng.


Sau canonical archive, framework đầy đủ vẫn pass85/85 trong107,47s. Astropy14507 phát hiện thêm ID tham số bị upstream cắt ở dấu cách (`[home_is_data,`); thêm Astropy vào adapter alias có theo dõi từng case. Unit adapter20/20 pass sau sửa. Preflight recordf3cdd23eaa6b4381ac5bc2eaba5ae858 nhận đủ175 selector; full record6a998e8066454c15a40ebeebe99f890b validated: baseline1 F2P fail +174 P2P pass, gold175 pass; image_cache_hit=true. Không suy ra các task Astropy khác đã validated.

Astropy12906 full validated (2 F2P +13 P2P), recordfb781e89890549459e4b16c6c0827c8b, preparation11,547s/cache hit, baseline3,797s, gold70,265s. Đã đọc detail F2P:14507 fail VerifyWarning card bị truncate;12906 fail assertion separability compound_model6/9, gold pass cùng selector.

Scikit-learn26306 full validated (1 F2P +188 P2P), recordb4c4a6749d9948be9064c8e4cd30e757, cache hit/full16,906s. F2P baseline fail dtype bool/int64 của DataFrame tại test_remainder_set_output; gold pass189 selector. Task24860 sau gỡ pin xung đột lộ lỗi setuptools59.8/Python3.10 `KeyError: unix_user` ở editable develop (recordf8be6b0d23914f7f94410633e2e09d91); thêm SETUPTOOLS_USE_DISTUTILS=stdlib riêng override24860, đang kiểm chứng lại.

Scikit-learn24860 full validated (61 selector), recorde20829e9648748f490df975424c00da1. Legacy12972 gặp apt404 với các gói libc/liblinux trong Bullseye ở recordd7b68ab18a9445d7ac49702e779fae36; đã xác nhận manifest Python3.7-slim-bookworm tồn tại và đổi hai override12972/13110 sang Bookworm, giữ Python3.7/dependency.

Scikit-learn12972 full validated với Python3.7-slim-bookworm, recordb404add16ac2488fbcfac289558fd48d (1 F2P +32 P2P,11,812s/cache hit). F2P fail assertion copyX behaviour, gold pass33. Lỗi cloudpickle do CodeType3.8 đã hết; không sửa vendored code.

Scikit-learn13110 full validated, record9ea65b74496b4f49816462a071dc33e5; Python3.7-slim-bookworm đã được kiểm chứng ở cả hai task legacy.

Matplotlib20665 full validated sau pin setuptools-scm7.1.0, recorde776e08f38c94acd96746bb0891ca2c5. Build từ basePython3.8/bookworm thật, preflight pass rồi baseline/gold đạt; giữ test đồ họa và bundled FreeType.

Scikit-learn14709 full validated, record0ad499014d014a3385037229d7f3fe7d. Task25957 lần đầu preparation_error do Docker Hub auth TLS handshake timeout (3a091e05e11a437482937fc77fd2c33d); chạy lại không thay profile, đã qua metadata image và đang build.

Scikit-learn25957 đã full validated sau retry lỗi TLS, record71f7e28c6da940069e92f16b221e217f, gold73 selector pass. Pin pytest7.4.4 nhận đúng parameter ID1j. Lượt đại diện kết thúc, tiếp theo batch mới issue-env-repairs-v1-full cho30 task nguồn, không sửa fingerprint cũ.

Kiểm tra cuối sau các sửa profile/adapter:86/86 tests pass trong140,42s (74unit +12Docker thật), Ruff/format trước đó qua. Batch issue-env-repairs-v1-full đã khởi chạy trong WindowsTerminal riêng cho30task lỗi, full F2P/P2P và reuse có kiểm tra provenance. Chưa kết luận cả30/120đạt; không commit/push.

Batch mới đã xác nhận lại Django34272 và Sphinx8618 validated dưới cấu hình cuối; đang xử lý Sympy16432. Tổng14task từng lỗi đã có full validated ở lượt đại diện/historical attempts, không đồng nhất các identity trước/sau adapter. Xem manifest batch mới để biết kết quả cùng fingerprint.


## Tiếp tục ngày 2026-10-19

9/11task môi trường/ngắt còn lại đã fullvalidated qua batchremainingv2/repairsv3: Astropy14355/14363/14523/14581,Matplotlib25329,sklearn10284/12650/14059/14893. Các Gitfetcherror cũ không tái diễn. Batchv3 dừng trước đổi profile, giữ checkpoint.

Task25844 có artifactversion1.3/base e3d1f9ac39e4bf0f31430e779acc50fb05fe1b64. `sklearn/_min_dependencies.py` yêu cầu joblib>=1.1.1 vànumpydoc>=1.2; pinlegacy1.0.1/1.1.0 không đáp ứng. Logrun4d25f84dcabd41d493cdcd39c6187515 xác nhận pipgỡjoblib1.0.1 khi editableinstall. Sửa riêng override25844 thànhjoblib1.2.0/numpydoc1.5.0, giữPython3.8vàcácdependencykhác; batchmớiissue-repairs-v4-full chạy25844/9287. Chưa nghiệm thu2task trước fullresult.


## Kết quả cuối lượt sửa và kiểm chứng 2026-10-19

Cả **11/11 task môi trường hoặc bị ngắt** đã chạy full baseline/gold trong Docker và đạt validated. Các record được tạo qua nhiều batch có fingerprint riêng; không ghép chúng thành một batch đồng nhất hoặc ghi đè lịch sử.

| Task | F2P baseline fail | P2P baseline pass | Gold pass | Record |
| --- | ---: | ---: | ---: | --- |
| astropy__astropy-14355 | 3 | 732 | 735 | `0d13bef89f4c43c78f088527a4a4b20e` |
| astropy__astropy-14363 | 1 | 8 | 9 | `4dfcb75cad354fe38761a506386ed174` |
| astropy__astropy-14523 | 2 | 46 | 48 | `49272a5a48444ebda971394541c603e0` |
| astropy__astropy-14581 | 1 | 175 | 176 | `e093c1a28ffb4a7d8912c6605d68f063` |
| matplotlib__matplotlib-25329 | 1 | 181 | 182 | `986da35d67b046658d9d73a7e2847bf0` |
| scikit-learn__scikit-learn-10284 | 1 | 28 | 29 | `cb93b3a3a682413b839ce48e986a679c` |
| scikit-learn__scikit-learn-12650 | 1 | 66 | 67 | `542c48e3947748baaafd3320e789f7b8` |
| scikit-learn__scikit-learn-14059 | 3 | 172 | 175 | `80feb1f3dd4545ff88d996af32ba140e` |
| scikit-learn__scikit-learn-14893 | 1 | 85 | 86 | `dcebe36d1b984b3189dd17008882ffa6` |
| scikit-learn__scikit-learn-25844 | 1 | 21 | 22 | `18708d41cafb4997aaad72d870fcd1c2` |
| scikit-learn__scikit-learn-9287 | 1 | 123 | 124 | `4262fce45354432b8ecb88a31fc811c7` |

Tổng hợp record full mới nhất của 120 task: **103 validated, 12 validation_failed, 4 collection_error, 1 execution_error**. Năm lỗi collection/execution thuộc đúng nhóm artifact đã được yêu cầu bỏ qua (Django29834, Pylint1548/4603, pytest7517, Sympy14237). Không còn task môi trường đang chạy hoặc preparation_error trong bảng mới nhất.

Trong 12 validation_failed: 6 Requests vướng network none giữ kết quả có bằng chứng; 6 baseline/selector (Django33552, Pylint7229, Matplotlib22640, Xarray3992/4354/6597) đã chạy lại full và vẫn không đáp ứng tiêu chí hiện tại. Không sửa nhãn, test, gold hoặc mở mạng để ép pass. Xem validation_failure_repairs.md và results/validation-failed-v2-diagnostics.json.

Báo cáo theo task: results/current-validation-summary.json và .csv. Dọn 43 log điều phối rỗng hoặc trùng hoàn toàn record, 720627 byte; giữ log phase, record và checkpoint. Framework sửa adapter qua 89/89 tests (76 unit +13 Docker); sau đổi pin25844, 22 unit evaluator và full Docker25844/9287 tiếp tục qua. Không commit/push.
