"""Flow environment -> role `environment_planner`. Vào: Requirement Spec + tech_stack + architecture đã duyệt + machine.json.

Chuẩn bị: machine.probe() trước khi gọi role (ghi machine.json).
Luật (bằng code, đối chiếu machine.json): runtime `installed` phải có thật trên máy đúng phiên bản và đường dẫn;
runtime `install` phải có lệnh cài dùng trình cài có trên máy; có test_command.
Không cài gì: chỉ quyết định. Lúc export, môi trường đã chốt được đưa vào yêu cầu cho Environment của MAS.
"""


async def run(workspace, *, note=None):
    raise NotImplementedError
