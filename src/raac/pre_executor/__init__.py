"""Các flow trước khi chạy MAS của quy trình A-Z, dùng lại hạ tầng MAS.

Mỗi bước là một FLOW RIÊNG, giống một role: một role + một prompt trong `registry/roles/pre_executor/` và
`registry/prompts/pre_executor/`, chạy bằng `AgentRunner` của harness. Các flow KHÔNG gộp thành một topology.

    clarify -> spec -> tech_select -> architecture -> environment -> task_decomposer -> (tasks.json cho MAS)

Người dùng duyệt (approve) đầu ra của một flow thì flow sau mới chạy; đầu ra đã duyệt của flow trước là đầu vào
của flow sau. Xem README.md.
"""
