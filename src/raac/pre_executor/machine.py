"""Dò máy cho flow environment: code thuần, chỉ chạy lệnh dạng --version, không cài gì.

Triển khai (tham khảo software-flow/swflow/machine.py ở commit trước bf0342c):
- Các bản Python (py -0p + PATH, đánh dấu exists=False cho mục đăng ký hỏng), node, npm, pnpm, git, java, go,
  dotnet, docker, ...; trình cài có sẵn (winget, py, nvm, choco, scoop, uv ...); OS và shell.
- Kết quả ghi machine.json, làm đầu vào cho role environment_planner.
- Hàm chạy lệnh truyền vào được để test bằng bản giả.
"""


def probe(run=None) -> dict:
    raise NotImplementedError
