---
trigger: always_on
---

# Quy tắc cập nhật tiến độ — Tuần 8–9

## Nguồn theo dõi và phạm vi

- Đọc [AGENTS.md](../../AGENTS.md) và [implementation_checklist.md](../../docs/implementation_checklist.md) trước khi làm việc. Các đường dẫn code trong checklist được tính từ gốc repository.
- Checklist là nguồn theo dõi chính: Phần A gồm harness dùng chung (A0–A5); Phần B chia công việc theo Lư Chấn Vũ, Nguyễn Phú Vinh và Nguyễn Minh Tuấn trong tuần 8–9; cuối file có mốc kiểm tra chung và nhật ký.
- Giữ nguyên cấu trúc, phân công và lịch tuần 8–9. Không áp đặt lại D01–D14, tự tạo ID cho mọi dòng hoặc thêm bảng phần trăm mà checklist chưa yêu cầu.
- Dự án dùng Python và LangGraph. Khi cần đối chiếu thiết kế harness, đọc [architecture.md](../../docs/architecture.md); phân biệt bốn layer harness với các level điều phối L1–L4.

## Trước khi thực hiện

1. Xác định dòng công việc bằng đường dẫn mục đầy đủ: phần → người phụ trách nếu có → tuần → nhóm việc → nội dung checkbox. Ví dụ: Phần B → Lư Chấn Vũ → Tuần 8 → L2 Adaptive Routing → Dùng conditional edge.
2. Kiểm tra code, phụ thuộc, tiêu chí thành công và trạng thái thực tế; không coi checkbox là bằng chứng duy nhất.
3. Ưu tiên việc chặn đường trong ngày 1–2 của tuần 8: LLM client/mock, schema registry và interface Engine. Sau đó triển khai theo phụ thuộc và phân công của checklist.
4. Tôn trọng chủ sở hữu ghi trong ngoặc hoặc tiêu đề thành viên. Agent hỗ trợ không tự nhận thay người phụ trách; nếu người dùng yêu cầu hoán đổi, cập nhật tên và ghi lý do vào nhật ký.
5. Với phần việc có liên quan cả Phần A và Phần B, xác định các dòng cần đối chiếu để không bỏ sót hoặc đánh dấu trùng thiếu căn cứ.

## Ràng buộc thiết kế thực nghiệm

- Tuân thủ mục “Lưu ý thiết kế cho benchmark tuần 8–9” trong architecture.md: tắt RAG, reset short-term memory sau mỗi run và chỉ resume dữ liệu thuộc cùng run.
- Layer 1 quản lý vòng đời từng agent; state LangGraph collaboration quản lý luồng giữa các agent. Không dùng một state thay thế cho cả hai trách nhiệm; kiểm tra checkpoint/resume cho cả hai phạm vi.
- Connectors chỉ cần interface; adapter MCP tối thiểu chỉ khi thật sự cần tool ngoài, A2A giữ stub. Không mở rộng thành triển khai giao thức đầy đủ trong hai tuần.
- Mỗi bản ghi kết quả phải truy được registry VERSION và version/hash các config đã dùng, kèm snapshot cấu hình hiệu lực không chứa secret. Không nghiệm thu logging/resume nếu thiếu thông tin này.

## Trạng thái và bằng chứng

- `[ ]`: chưa bắt đầu, hoặc chưa có bằng chứng cho thấy đã thực hiện.
- `[~]`: đang thực hiện, hoàn tất một phần hoặc đã bắt đầu nhưng bị chặn. Ghi ngắn phần còn lại/lý do/vướng mắc ngay cạnh dòng đó.
- `[x]`: đã hoàn thành toàn bộ nội dung dòng và được người khác kiểm tra **hoặc** có test chứng minh, đúng theo cách dùng checklist.
- Nếu việc chưa bắt đầu bị chặn, giữ `[ ]` và ghi phụ thuộc cần giải quyết.
- Với tài liệu, cấu trúc thư mục và quy ước nhóm, ghi bằng chứng kiểm tra phù hợp; không viết test hình thức chỉ để tích checkbox. Nếu chưa có người khác kiểm tra và chưa có test chứng minh, dùng `[~]` và ghi chờ review.
- Không coi file rỗng, TODO hoặc stub là tính năng đã hiện thực. Riêng các dòng yêu cầu stub/interface (long-term memory, WASM, A2A) được nghiệm thu đúng phạm vi đó; MCP chỉ thêm adapter tối thiểu khi thật sự cần tool ngoài.
- Mock LLM được dùng làm bằng chứng cho unit test hoặc các mục yêu cầu mock; không thay thế yêu cầu API thật, Docker, task thật hay pilot.
- Ghi bằng chứng ngay dưới checkbox khi cần: ngày YYYY-MM-DD, người/agent thực hiện, file liên quan, lệnh và kết quả test hoặc người review/kết quả review. Không ghi đã chạy/đã duyệt khi chưa có bằng chứng.
- Nếu một thay đổi làm mất hiệu lực mục đã xong, chuyển về `[~]` hoặc `[ ]` theo tình trạng thực tế và ghi lý do.

## Cập nhật checklist trong cùng lượt thay đổi

1. Tự cập nhật mọi dòng liên quan sau khi hoàn tất hoặc thay đổi trạng thái công việc, không chờ người dùng nhắc.
2. Đối chiếu các mục giao nhau giữa Phần A và Phần B (LLM client, guardrails, sandbox…). Chỉ đổi trạng thái từng dòng khi phạm vi và bằng chứng của chính dòng đó đáp ứng yêu cầu; một mục con xong không làm cả nhóm việc tự động xong.
3. Giữ nguyên nội dung tiêu chí và tên người phụ trách trừ khi phạm vi/phân công được thay đổi theo yêu cầu người dùng. Ghi phần hoàn tất một phần ở ghi chú thay vì xóa bớt yêu cầu.
4. Cập nhật dòng “Cập nhật gần nhất ngày …” theo ngày thực tế YYYY-MM-DD mỗi khi sửa checklist.
5. Ghi tiến độ/tồn đọng vào “Ghi chú tiến độ phần A” hoặc “Ghi chú tuần 8/9” đúng thành viên. Mọi vấn đề, quyết định, đổi phạm vi hoặc phân công được ghi thêm vào “Nhật ký vấn đề và quyết định”.
6. Nhật ký giữ dạng từng dòng: ngày | vấn đề hoặc quyết định | người xử lý | trạng thái. Có thể bổ sung bằng chứng và bước tiếp theo ngay trong dòng; không sửa lịch sử để che lỗi.
7. Không tự tính tổng công việc bằng cách cộng Phần A, Phần B và các mốc: chúng có mục giao nhau và các tiêu chí nghiệm thu tổng hợp.

## Mốc nghiệm thu và quyết định của nhóm

- A5, “Tiêu chí thành công tuần 8/9” và “Mốc kiểm tra chung” chỉ được đánh dấu khi đáp ứng đầy đủ yêu cầu tích hợp tương ứng.
- Việc tài liệu architecture đã có mô tả 4 layer chưa đủ để đánh dấu dòng yêu cầu cả sơ đồ và collaboration.
- Đặc tả L4 phải được cả nhóm review/duyệt theo checklist trước khi hiện thực L4 ở tuần 9.
- Các mục họp, chia sẻ interface, thống nhất quy ước, duyệt ngân sách hoặc đóng băng cấu hình chỉ được đánh dấu khi có bằng chứng hành động/đồng thuận thực tế. Agent không tự suy diễn người khác đã duyệt và không tự gửi thông báo cho thành viên nếu chưa được người dùng cho phép.
- Mốc tuần 9 yêu cầu cả bốn level, evaluator, pilot và cấu hình đóng băng; không đánh dấu chỉ dựa trên smoke test hoặc kết quả mock.

## Trước khi trả kết quả

- Đối chiếu thay đổi với checkbox, ghi chú và ngày cập nhật; nêu nhóm việc đã hoàn tất, đang làm dở, bằng chứng kiểm tra và trở ngại còn lại.
- Không sửa trạng thái các mục ngoài phạm vi chỉ vì thấy chúng có vẻ đã xong.
- Không ghi secret/API key hoặc dữ liệu nhạy cảm vào checklist và log.
- Việc tự cập nhật là nghĩa vụ trong lượt làm việc của agent, không phải một tiến trình nền hoặc hook tự chạy.


## Commit và push Git — yêu cầu người dùng ngày 2026-10-22

Không tự động commit hoặc push. Chỉ chạy `git commit` hoặc `git push` khi người dùng yêu cầu rõ hành động tương ứng cho lần đó. Yêu cầu commit không mặc nhiên cho phép push; yêu cầu push không mặc nhiên cho phép tạo commit mới. Các ủy quyền trước yêu cầu này không còn áp dụng; hoàn tất triển khai, kiểm thử, cập nhật tài liệu hoặc dọn log không mặc nhiên cho phép commit/push.
