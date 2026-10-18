# Sửa nhóm validation_failed

## Kết quả đến 2026-10-19

Trong 24 task từng có record validation_failed: hiện 12 task có record validated mới nhất, gồm Matplotlib5087 đã được sửa từ trước và **11 task được sửa trong đợt này**. Còn 12 task chưa đạt; không nghiệm thu toàn dataset.

11 task vừa đạt: pytest5819/6194/7126/7981/10060, Sphinx9218, Pylint7231, Xarray4049/7036, Astropy13009/13355. Các task này đều chạy Docker thật full F2P/P2P. Không mở mạng test, đổi nhãn, sửa test_patch/gold hoặc code repo.

12 task còn lại:

| Nhóm | Task | Bằng chứng / tồn đọng |
| --- | --- | --- |
| Mạng ngoài | Requests1723/1765/1920/2316/2930/5367 | DNS/network unreachable hoặc test đòi connect timeout nhưng network none trả lỗi ngay. Không retry hàng loạt khi chính sách không đổi. |
| Baseline/selector chưa hợp lệ | Django33552 | Sau sửa addSubTest, đủ80 test thực thi/gold80pass; F2P test_has_keys vẫn pass trên SQLite. |
| Baseline/selector chưa hợp lệ | Matplotlib22640 | 4 P2P rút gọn, mỗi ID khớp3 case với trạng thái mixed. |
| Baseline/selector chưa hợp lệ | Pylint7229 | P2P rút gọn khớp cả case fail của F2P; baseline mixed, gold19pass. |
| Baseline/selector chưa hợp lệ | Xarray3992/4354/6597 | Gold pass toàn bộ; baseline lần lượt4/8/1 P2P fail do hành vi được gold sửa.6597 đã đối chiếu thêm Dask lịch sử, vẫn fail cùng case. Chưa kết luận raw sai nếu chưa so harness tham chiếu. |


Nguồn tổng hợp: results/validation-repairs-summary.json và .csv, mỗi task liên kết record/log gốc. Batch cũ issue-env-repairs-v1-full giữ checkpoint interrupted; không tự resume sau thay đổi profile/adapter. Không commit/push.

Nguồn: 26 record của 24 task; Matplotlib-5087 đã có validated sau đó. Không đổi F2P/P2P, test_patch/gold hoặc nới chính sách mạng. Batch issue-env-repairs-v1-full đã dừng có kiểm soát để ưu tiên nhóm này.

| Task | Hướng xử lý ở bước chẩn đoán ban đầu |
| --- | --- |
| `astropy__astropy-13009` | local current leap-second data config |
| `astropy__astropy-13355` | local current leap-second data config; recheck Time/IERS cascade |
| `django__django-33552` | Phát hiện adapter bỏ addSubTest nên thiếu failure; đã sửa và Docker regression pass, đang chạy task thật |
| `matplotlib__matplotlib-22640` | Kiểm tra nhãn P2P/alias nhiều case; gold pass chưa đủ baseline hợp lệ |
| `matplotlib__matplotlib-5087` | Đã validated ở lượt sau |
| `psf__requests-1723` | Lỗi mạng ngoài bị chặn; giữ giới hạn môi trường, không retry để ép pass |
| `psf__requests-1765` | Lỗi mạng ngoài bị chặn; giữ giới hạn môi trường, không retry để ép pass |
| `psf__requests-1920` | Lỗi mạng ngoài bị chặn; giữ giới hạn môi trường, không retry để ép pass |
| `psf__requests-2316` | Lỗi mạng ngoài bị chặn; giữ giới hạn môi trường, không retry để ép pass |
| `psf__requests-2930` | Lỗi mạng ngoài bị chặn; giữ giới hạn môi trường, không retry để ép pass |
| `psf__requests-5367` | Lỗi mạng ngoài bị chặn; giữ giới hạn môi trường, không retry để ép pass |
| `pydata__xarray-3992` | Kiểm tra nhãn P2P/alias nhiều case; gold pass chưa đủ baseline hợp lệ |
| `pydata__xarray-4049` | install sparse/numba/numbagg |
| `pydata__xarray-4354` | Kiểm tra nhãn P2P/alias nhiều case; gold pass chưa đủ baseline hợp lệ |
| `pydata__xarray-6597` | Kiểm tra nhãn P2P/alias nhiều case; gold pass chưa đủ baseline hợp lệ |
| `pydata__xarray-7036` | Thiếu dependency optional (pint/iris/cdms2/numexpr/flox); cần môi trường đủ, không bỏ skip |
| `pylint-dev__pylint-7229` | Kiểm tra nhãn P2P/alias nhiều case; gold pass chưa đủ baseline hợp lệ |
| `pylint-dev__pylint-7231` | source PYTHONPATH for checker discovery |
| `pytest-dev__pytest-10060` | PYTHONUSERBASE fixed |
| `pytest-dev__pytest-5819` | PYTHONUSERBASE fixed so pytester HOME changes retain dependency site |
| `pytest-dev__pytest-6194` | PYTHONUSERBASE fixed |
| `pytest-dev__pytest-7126` | PYTHONUSERBASE fixed |
| `pytest-dev__pytest-7981` | PYTHONUSERBASE fixed |
| `sphinx-doc__sphinx-9218` | fixture root PYTHONPATH |

## Thay đổi và bằng chứng bổ sung

- Adapter unittest ghi failure từ addSubTest; subtest pass không ghi đè failure trước đó. Docker regression gồm baseline có subtest fail/pass rồi candidate fixture toàn pass: 1 passed trong 23,55s.
- Lượt đầu sửa pytest/Sphinx đặt nhầm profile không dùng đã được phát hiện và gỡ; chuyển sang pytest-compatible.yaml và sphinx-compatible.yaml đúng mapping benchmark. Record3341b3f0b611441c89ff778a4da48c36 vẫn giữ lỗi cũ, không coi là kiểm chứng bản sửa.
- Hàng đợi kiểm chứng mới ưu tiên Django33552 rồi 5 task pytest, Sphinx9218, Pylint7231, Xarray4049/4354/6597, Matplotlib22640, Pylint7229, Astropy13009/13355. Mỗi task preflight rồi full, giữ nguyên artifact và ngân sách.

Django33552 sau sửa subTest: recordace790d9275242bdbfed42327a1ccadd, baseline đủ80/80 executed, F2P test_has_key_number fail đúng, test_has_keys vẫn pass; P2P78/78 pass và gold80/80 pass. Giữ validation_failed vì1 F2P không tái hiện trên SQLite, không đổi nhãn.

Pytest5819 đã validated thật sau sửa đúng active profile PYTHONUSERBASE, record807585dd96f94deebbe01e5ab7c2af04; baseline2F2P fail/51P2P pass, gold53selector pass. Lỗi subprocess thiếu py đã hết.

`pytest-dev__pytest-6194` full validated: record `2f1a0c78b0ba47ab85e2d297caeddd2b`; gold pass 147 selectors.

`pytest-dev__pytest-7126` full validated: record `9e4de37323364f34b8d88f74a7314097`; gold pass 26 selectors.

`pytest-dev__pytest-7981` validated: record `5828946cefcd4517b3e18d5148a64678`; gold 79 selectors.

`pytest-dev__pytest-10060` validated: record `f72bcb1654104a38b86f6761757ef84d`; gold 64 selectors.

`sphinx-doc__sphinx-9218` validated: record `81a82c961d1b409fb27d1e95888f3978`; gold 14 selectors.

Pylint7231 full validated sau source PYTHONPATH, recordd683818c8ef843a18161298eca4bede4. Không thay checker hoặc test; subprocess đăng ký được checker từ đúng source.

Xarray4049 full validated sau bổ sung sparse/numba/numbagg: record4af1c71f3eb947b491abe815b45ce819; gold863selectors pass, không skip.

Xarray4354 rerun record1888d1d3fde84c2fa024d92d24bee8a5: found/executed612/612, baseline8F2Pfail đúng nhưng8P2P min_count_nd[sum/prod-True-*] fail do base ValueError reduction nhiều chiều; gold612pass. Giữ validation_failed, không đổi8P2P thànhF2P.

Xarray6597 current-environment rerun fde6a3546fbb4261be0aa7a8512cf48b vẫn validation_failed ở timedelta-Dask; CI min-all-deps tại base ghi Python3.8/NumPy1.19/Pandas1.2/Dask2021.04. Chuẩn bị override Python3.9/NumPy1.21.6/Pandas1.3.5/SciPy1.7.3/Dask2021.4.1 để kiểm chứng lịch sử, chưa coi đã sửa.

Matplotlib22640 rerun52ad3e0067364b0d9b2d9439724da493:4P2P ID bị cắt ở [number/[string, mỗiID khớp3case có kết quả mixed cảbaseline/gold; không âm thầm chọn1case. Giữ validation_failed.

Astropy13009 full validated bằng leap-second file cục bộ của astropy-iers-data và astropy.cfg ngoài checkout: recordbca58711312346b48dd081f2a3f14a30. test_fold baseline/gold pass, giữ nguyên F2P và không truy cập mạng test.

Astropy13355 full validated sau cấu hình leap-second local: record3f91566714894dffa5dbb8c7ab103ca0. Lỗi IERS/Time trong baseline và gold đã hết, không sửa repo hoặc bỏ test. Tổng10task validation_failed cũ đã validated sau sửa; còn chạy override lịch sử Xarray6597.

Xarray6597 override lịch sử đã chạy thật: record6a6f6c20338942e39b155dd130032e93, baseline1F2P fail đúng nhưngP2P timedelta-True vẫn TypeError; gold265/265pass, khôngskip. Không giữ override thử nghiệm vì không sửa được lỗi; active profile trở lại cấu hình trước thử. Giữ cả2record để đối chiếu, không đổi P2P thànhF2P.

## Tiếp tục Xarray7036

CI environment.yml tại base45c0a114e2b7b27b83c9618bc05b36afac82183c khai báo conda-forge với cdms2/iris/flox/pint/numexpr. Thêm override7036 tạo prefix conda riêng ngoài /workspace, micromamba2.0.5-1 được kiểm tra SHA256 ff1a10b46e9ee673bb672ac464d4787b4e9c3569b75816a56f76dddc0b1f07fc. Pin Python3.10/NumPy1.23.5/Pandas1.5.3 cùng dependency native/optional; preflight kiểm tra import cả6module. Chưa nghiệm thu trước khi Docker full chạy thật.

Nguồn dependency: [CDMS2 conda-forge feedstock](https://github.com/conda-forge/cdms2-feedstock), [Iris installation](https://scitools-iris.readthedocs.io/en/v3.6.1/installing.html). Không thay source/tests; install package repo tại base bằng editable sau dependency environment.

2026-10-19: thử conda full-pins tại runaf29719699774b0abf92a9626be7e25b được dừng có kiểm soát sau solver chạy CPU nhiều phút không ra transaction. Thu gọn conda xuống Python/NumPy/CDMS2/Iris; pip cài các dependency còn lại với pin, vẫn kiểm tra import native và toàn selector. Lịch sử giữ interrupted, không sửa record/fingerprint.

Cập nhật solver7036 sang micromamba2.6.0-0, SHA256 e9482483cd42cb28a936298419e47a9411a60b4595c767b3c6b5967d9ef64633 từ binary phát hành chính thức GitHub. Bản2.6 hỗ trợ sharded repodata; dừng có kiểm soát lượt2.0 trước khi thay config. Giữ conda core4spec rồipip dependencies, không đổi test. [Release](https://github.com/mamba-org/mamba/releases/tag/2.6.0).

## Xarray-7036 đã kiểm chứng đầy đủ

- Override riêng trong `configs/repos/xarray.yaml`: conda-forge cài Python3.10/NumPy1.23.5/CDMS2/Iris vào `/home/runner/conda-env`; pip cài dependency còn lại với pin. Giữ user1000, mạng test none, CPU/RAM/PID và timeout1800s.
- Micromamba2.6.0-0 được pin SHA256; không đưa bootstrap vào checkout repo. Hai lượt solver2.0 đã dừng có kiểm soát, giữ record interrupted.
- Preflight `b47e09e8d4254f0c81ab37da043ab146`: import đủ native/optional và tìm đủ selector; preparation271,078s.
- Full `0c936822a6e947099d16380e0725d94c`: **validated**, baseline12F2P fail/945P2P pass, gold957/957pass,0skip, tổng41,5s với image reuse.
- Lỗi F2P baseline là assertion về MultiIndex/coordinates tại reset_index, không phải thiếu module. Các test CDMS2/Iris/Pint/NumExpr/Flox trước đây skip đã chạy.


Kiểm tra cuối ngày 2026-10-19: **86/86 tests passed trong 106,72s**, gồm74unit +12Docker integration. Ruff/format/diff check và profile check120/120 qua; quét mẫu credential phổ biến trong diff không phát hiện. Hàng đợi sửa đã processed; batch cũ giữ interrupted, không tự resume sau đổi fingerprint. Không commit/push.
