"""Flow task_decomposer -> role `task_decomposer`. Vào: Requirement Profile + Tech Stack + Architecture + Environment Plan đã duyệt. Ra: tasks.json/.md.

Luật: id Tn duy nhất; depends_on chỉ trỏ id có thật, không vòng (thứ tự topo); mọi AC của requirements có ít
nhất một task phụ trách (đúng id); có task dựng khung/cấu hình đầu tiên; risk thuộc low|medium|high.
"""


async def run(workspace, *, note=None):
    raise NotImplementedError
