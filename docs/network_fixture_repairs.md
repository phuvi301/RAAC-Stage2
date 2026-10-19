# Sửa môi trường mạng của Requests

## Phạm vi ngày 2026-10-19

Đã push phần sửa trước lên `origin/Vinh`, commit `a589606`. Các thay đổi mạng trong tài liệu này được thực hiện sau mốc push đó và chưa được push tiếp.

Sáu task được bật fixture theo profile; các task khác vẫn dùng `network=none`:

| Task | Fixture | Mục đích |
| --- | --- | --- |
| Requests1723/1765/1920/2316 | `requests_httpbin` | HTTP/HTTPS httpbin nội bộ; dùng biến HTTPBIN_URL có sẵn trong test và CA tạm cho HTTPS. |
| Requests2930/5367 | `requests_tarpit` | Giữ fixture httpbin loopback sẵn có của repo; tạo connect timeout TCP thật tới địa chỉ test hard-code. |

Không sửa mã Python, test, gold hoặc nhãn F2P/P2P. Không sửa socket, không monkeypatch exception hoặc giả kết quả timeout. Fixture chỉ bổ sung public CA thử nghiệm vào dữ liệu trust PEM trong checkout tạm và dependency certifi; ghi hash trước/sau, không ghi thay đổi đó vào image hoặc repo cache. Candidate sửa requests/cacert.pem bị từ chối.

## Cơ chế cô lập

Mỗi phase tạo bridge riêng `internal=true`, gateway mode IPv4/IPv6 `isolated`, không publish port. Subnet `10.255.255.0/24`, gateway `.254` không được gán lên bridge host; IP `.1` được dự trữ bằng IPAM aux_addresses và không có endpoint trả lời. Kernel thực hiện kết nối TCP/ARP bình thường rồi timeout theo thời hạn trong test. Không có route ra Internet; DNS upstream chỉ trỏ loopback. Nếu Docker không hỗ trợ isolated hoặc subnet bị trùng, báo lỗi chuẩn bị, không chuyển sang bridge có Internet.

Nguồn: [Docker gateway modes](https://docs.docker.com/engine/network/port-publishing/) và [internal network](https://docs.docker.com/reference/cli/docker/network/create/). Internal đơn thuần vẫn có thể truy cập gateway host, vì vậy phải dùng thêm isolated.

Httpbin chạy bằng package đã pin trong cùng image base, trong container dịch vụ riêng, working directory /tmp. Không nhận checkout/patch của baseline, gold hoặc candidate; không mount source hay Docker socket. HTTP80/HTTPS443 dùng sysctl riêng namespace để user1000 bind port, vẫn drop ALL capabilities/no-new-privileges. CA được tạo tạm bên dịch vụ; chỉ public CA được đưa vào container test. Không đưa private key vào log/config/candidate.

Giới hạn container test vẫn 2CPU/4GiB/256PID. Container dịch vụ bổ sung tối đa0,5CPU/512MiB/64PID. Các phase dùng checkout sạch riêng và dọn cả checkout volume, container test, dịch vụ và network khi hoàn tất/lỗi/timeout/ngắt. Fixture không mount dữ liệu host. Runtime network ID, service image ID và địa chỉ nội bộ được ghi trong kết quả từng phase; effective policy và hash network.py nằm trong provenance/fingerprint. Selector thay đổi không tự làm mất build image; profile fixture chỉ ảnh hưởng fingerprint đánh giá.

## Bằng chứng đang có

- Probe Docker thật: `.1` timeout0,151s, `8.8.8.8:53` Network unreachable, gateway `.254:80` timeout0,151s. File results/network-topology-probe.json.
- 3 integration Docker mới đã pass: HTTP/HTTPS/redirect nội bộ, TCP connect timeout, chặn egress/gateway và cleanup sau timeout. Kết quả 3passed/13deselected trong85,34s.
- Unit cho cấu hình, recipe cache và cleanup; full framework đang kiểm tra. Chưa nghiệm thu task thật trước khi có baseline/gold.

## Điều kiện baseline và selector

Baseline hợp lệ khi mọi F2P fail do lỗi cần sửa, mọi P2P pass. Gold phải pass toàn bộ. Mất mạng làm F2P fail không chứng minh đã tái hiện issue; sau khi phục hồi httpbin nội bộ có thể lộ F2P đã pass ngay ở base. Giữ validation_failed khi đó, không sửa nhãn để được validated.

Selector là ID dùng chọn test. Một ID bị upstream cắt ở dấu cách có thể khớp nhiều testcase. Ví dụ Pylint7229 `[foo,` khớp cả case pass và case regex có quantifier fail ở base. Matplotlib22640 alias khớp case scatter/bar pass và plot xfail. Không được chọn riêng casepass hoặc tính xfail là pass. Django33552 có F2P test_has_keys đã pass tại base; Xarray3992/4354/6597 có P2P cần hành vi gold mới bổ sung. Đây là các điều kiện đánh giá chưa đạt, khác lỗi build/cài dependency. Xem validation_failure_repairs.md để đối chiếu từng record.


Framework:97/97passed trong183,80s (81unit+16Docker) trước guard phiên bản; sau guard Docker28+,8unit mạng và8test chọn lọc (gồm4Docker) tiếp tục qua,42,07s. Profile120check/Ruff qua. Batch mới requests-internal-network-v1-full đã khởi chạy trong WindowsTerminal riêng, full F2P/P2P,1800s build+1800s mỗi phase,một task/lượt. Chưa kết luận validated trước record thực tế.


## Lượt chạy thật đầu tiên và sửa TLS

Batch requests-internal-network-v1-full đã processed: Requests2930 validated (1F2P/84P2P, gold85pass), record c7f59a8105c941308d83ccfa317de851; Requests5367 validated (1F2P/130P2P, gold131pass), record668413c5ef6343c38324ad24abf9e24e. TCP timeout được thực hiện thật, không sửa test/socket và không mở Internet.

Bốn task cũ đã hết lỗi DNS/HTTP nhưng còn P2P gọi Session.send trực tiếp, không áp REQUESTS_CA_BUNDLE như request(). Requests1723/1765/1920 dùng requests/cacert.pem trong checkout;2316 ưu tiên certifi.where(). Fixture nạp public CA vào cả file PEM vendored (nếu có) và bundle certifi trong user site-packages của container. Chỉ sửa dữ liệu trust, không sửa mã thư viện hoặc tắt xác minh TLS. Script chạy bằng python -E -s, không import code repo/dependency, chống ảnh hưởng PYTHONPATH/user site trong bước cấu hình trust. Audit baseline.network.json/gold.network.json ghi đường dẫn và SHA256 trước/sau; volume riêng bị xóa sau phase. Source archive/base commit không thay đổi. Không khẳng định checkout Git còn sạch sau overlay CA; khác biệt PEM được ghi rõ là dữ liệu môi trường.

Requests1723 sau khi có mạng nội bộ: cả6F2P đã pass ngay baseline; do đó vẫn chưa hợp lệ theo tiêu chí hiện tại kể cả khi gold pass hết. Không tái phân loại F2P/P2P. Chưa kết luận artifact sai nếu chưa đối chiếu harness/log tham chiếu. Tiếp tục batchv2 cho4task với trust store đã cấu hình.


Bản có trust overlay cuối đã kiểm tra toàn bộ: **102/102passed trong186,05s**,86unit+16Docker integration. Có ca HTTPS bằng Session.send trực tiếp với bundle vendored và certifi, CAaudit trướcsau, chặn candidate sửaPEM và phân loại setupfail. Ruff/diffcheck qua; batch requests-internal-network-v2-full đang chạy4task từterminal riêng, không resume fingerprintv1.

### 2026-10-19 — Giữ thứ tự collection và redirect khác hostname

Batch v2 processed: 1723 baseline0/6F2Pfail, 1765 và1920 baseline1/6F2Pfail; cả ba P2Ppass vàgold85/85/113pass. Chưa validated vì yêu cầu mọiF2Pfail.2316 còn2P2Pfail: adapter đưa test sửa global get_netrc_auth lên trước theo thứ tự manifest; redirect off-host tới www.google.co.uk chưa được khai báo nội bộ.

Sửa adapter chỉ lọc, giữ nguyên thứ tự pytest collection; không sửa test hoặc nhãn. Khai báo đúng hai hostname httpbin.org/www.google.co.uk cùng sidecar nội bộ và SAN TLS, ghi aliases trong record. Khác hostname vẫn kiểm tra auth stripping qua redirect, không truy cập Google/Internet. Thêm regression cho manifest đảo thứ tự với test chia sẻ trạng thái và Docker redirect kiểm tra Authorization bị bỏ ở host đích. Batch mới v3 chạy lại đủ6task, giữ lịch sử v1/v2.

### Kết quả v3 full — 2026-10-19

Kiểm thử framework:104/104passed trong133,23s (87unit+17Docker thật); Ruff vàdiffcheck qua; profilescheck120/120. Batch requests-internal-network-v3-full chạy trong WindowsTerminal riêng, processed6/6, không còn tài nguyên fixture container/network/volume sau cleanup. Ngân sách1800s chuẩn bị và1800s mỗi phase; một task/lượt.

| Task Requests | F2P fail tại baseline | P2P pass tại baseline | Gold pass | Kết quả |
| --- | --- | --- | --- | --- |
| 2930 | 1/1 | 84/84 | 85/85 | validated |
| 5367 | 1/1 | 130/130 | 131/131 | validated |
| 1723 | 0/6 | 79/79 | 85/85 | validation_failed |
| 1765 | 1/6 | 79/79 | 85/85 | validation_failed |
| 1920 | 1/6 | 107/107 | 113/113 | validation_failed |
| 2316 | 1/8 | 133/133 | 141/141 | validation_failed |

Mọi P2P baseline và toàn bộ gold đều pass sau sửa môi trường. Bốn task cũ còn F2P pass ngaybase, không phải lỗi mạng/build/collection. Không đổi F2P/P2P hoặc phiên bảnPython để ép fail; cần đối chiếu điều kiện lịch sử/harness tham chiếu trước quyết định tiếp. Record giữ riêngv1/v2/v3; record2316v3:3b32c36b4c184c5092473635b3deb6b3. Thứ tự collection được giữ nguyên, không thay bằng thứ tựF2P/P2P.

Tổng hợp120record full mới nhất:105validated/10validation_failed/4collection_error/1execution_error. Nhóm5artifact giữ nguyên theo yêu cầu. Đây là tổng hợp bằng chứng từngtask ở các fingerprint khác nhau, không phải một batch120task đã nghiệm thu trên cấu hìnhmới. results/current-validation-summary.json và.csv lưu liên kết record; log/checkpoint được giữ. Phần mạng hiện chỉ local, chưa commit/push; push theo yêu cầu trước đó chỉ gồm a589606.
