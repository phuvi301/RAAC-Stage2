"""answers.md: người dùng đọc câu hỏi của flow clarify và sửa câu trả lời ngay trong file.

Định dạng (mỗi câu một khối, phần trả lời là mọi dòng sau "A:" tới câu kế tiếp):

    ### Q3: Tích hợp thanh toán qua cổng nào?
    _Vì sao hỏi: ..._
    A: VNPay
"""

import re

HEADER = """# Làm rõ yêu cầu

<!-- Sửa câu trả lời sau "A:". Giữ nguyên các dòng "### Qn: ...".
     Câu để nguyên đáp án gợi ý sẽ được ghi là giả định. Trả lời xong thì chạy lại clarify (hoặc dùng tui)
     để agent hỏi tiếp; khi agent thấy đủ, clarify tự chốt. -->
"""
QUESTION = re.compile(r"^### (Q\d+):[ \t]*(.*)$", re.MULTILINE)
CATEGORY_LABELS = {"functional_scope": "Phạm vi chức năng", "domain_data": "Miền & dữ liệu",
                   "ux_flow": "Luồng tương tác & UX", "quality_attributes": "Chất lượng (NFR)",
                   "integrations": "Tích hợp ngoài", "edge_cases": "Trường hợp biên & lỗi",
                   "constraints": "Ràng buộc & đánh đổi", "terminology": "Thuật ngữ",
                   "completion_signals": "Tín hiệu hoàn thành", "open_items": "Còn bỏ ngỏ"}
STATUS_LABELS = {"clear": "rõ", "partial": "mơ hồ", "missing": "thiếu", "not_applicable": "không áp dụng",
                 "deferred": "để bước sau", "resolved": "đã rõ sau khi hỏi", "outstanding": "còn thiếu"}


def coverage_lines(coverage: list[dict]) -> list[str]:
    if not coverage:
        return []
    lines = ["| Nhóm | Trạng thái | Ghi chú |", "|---|---|---|"]
    for item in coverage:
        note = str(item.get("note", "")).replace("|", "/").replace("\n", " ")
        lines.append(f"| {CATEGORY_LABELS.get(item['category'], item['category'])} | "
                     f"{STATUS_LABELS.get(item['status'], item['status'])} | {note} |")
    return lines


def render_answers(rounds: list[dict], answers: dict[str, str]) -> str:
    """Toàn bộ câu hỏi của các vòng; giữ câu trả lời người dùng đã sửa (`answers`), câu mới điền đáp án khuyến nghị."""
    lines = [HEADER]
    for number, round_ in enumerate(rounds, start=1):
        lines.append(f"## Vòng {number}")
        if round_.get("summary"):
            lines.append(f"> {round_['summary']}")
        if round_.get("coverage"):
            lines += ["", *coverage_lines(round_["coverage"]), ""]
        if not round_["questions"]:
            lines.append("_Không cần hỏi thêm: thông tin đã đủ._")
        for question in round_["questions"]:
            lines.append(f"### {question['id']}: {question['text']}")
            meta = [f"Nhóm: {CATEGORY_LABELS.get(question.get('category'), question.get('category'))}"
                    ] if question.get("category") else []
            if question.get("why"):
                meta.append(f"Vì sao hỏi: {question['why']}")
            if meta:
                lines.append(f"_{' · '.join(meta)}_")
            if question.get("options"):
                lines.append("Lựa chọn: " + " · ".join(
                    f"**{option}** (khuyến nghị)" if option == question.get("default_answer") else option
                    for option in question["options"]))
            lines.append(f"A: {answers.get(question['id'], question.get('default_answer', ''))}".rstrip())
            lines.append("")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_answers(text: str) -> dict[str, str]:
    """{Qn: câu trả lời} đọc từ answers.md; câu trả lời nhiều dòng được giữ nguyên."""
    result = {}
    matches = list(QUESTION.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end():end]
        answer = re.search(r"^A:[ \t]?(.*)", block, re.MULTILINE | re.DOTALL)
        if answer:
            body = re.split(r"^## ", answer.group(1), maxsplit=1, flags=re.MULTILINE)[0]
            result[match.group(1)] = body.strip()
    return result


PRIORITY = {"high": "cao", "medium": "trung bình", "low": "thấp"}


def _cell(text: str) -> str:
    return str(text).replace("|", r"\|").replace("\n", " ")


def render_requirements(data: dict) -> str:
    """requirements.json -> requirements.md để người dùng đọc, góp ý, duyệt (nguồn sự thật vẫn là json)."""
    overview = data["overview"]
    lines = ["# Yêu cầu sản phẩm", "", "## Tổng quan", "", overview["summary"], "",
             f"- **Mục tiêu:** {overview['product_goal']}",
             f"- **Người dùng:** {', '.join(overview['target_users'])}"]
    if overview.get("platforms"):
        lines.append(f"- **Nền tảng:** {', '.join(overview['platforms'])}")
    if overview.get("scope"):
        lines.append(f"- **Phạm vi bản đầu:** {overview['scope']}")
    lines += ["", "## Chức năng cốt lõi"]
    for feature in data["features"]:
        lines += ["", f"### {feature['id']} · {feature['name']} — ưu tiên "
                      f"{PRIORITY.get(feature['priority'], feature['priority'])}", "", feature["description"]]
        if feature.get("why_priority"):
            lines.append(f"- **Vì sao ưu tiên này:** {feature['why_priority']}")
        if feature.get("independent_test"):
            lines.append(f"- **Kiểm độc lập:** {feature['independent_test']}")
        if feature.get("sources"):
            lines.append(f"- **Căn cứ:** {', '.join(feature['sources'])}")
    if data.get("success_criteria"):
        lines += ["", "## Tiêu chí thành công", "", "| ID | Kết quả | Đo bằng |", "|---|---|---|"]
        lines += [f"| {sc['id']} | {_cell(sc['outcome'])} | {_cell(sc['metric'])} |" for sc in data["success_criteria"]]
    if data.get("key_entities"):
        lines += ["", "## Thực thể chính", ""]
        for entity in data["key_entities"]:
            detail = "; ".join(part for part in (", ".join(entity.get("attributes", [])),
                                                 ", ".join(entity.get("relations", []))) if part)
            lines.append(f"- **{entity['name']}**: {entity['description']}" + (f" ({detail})" if detail else ""))
    nfrs = data.get("non_functional_requirements", [])
    if nfrs:
        lines += ["", "## Yêu cầu phi chức năng", "", "| ID | Loại | Yêu cầu | Đo bằng |", "|---|---|---|---|"]
        for nfr in nfrs:
            lines.append(f"| {nfr['id']} | {nfr['category']} | {_cell(nfr['requirement'])} | {_cell(nfr['metric'])} |")
    for title, key in (("Ràng buộc", "constraints"), ("Ngoài phạm vi", "out_of_scope"), ("Câu hỏi mở", "open_questions")):
        if data.get(key):
            lines += ["", f"## {title}", "", *[f"- {item}" for item in data[key]]]
    if data.get("assumptions"):
        lines += ["", "## Giả định", ""]
        lines += [f"- {item['text']}" + (f" _(từ {item['clarification_id']})_" if item.get("clarification_id") else "")
                  for item in data["assumptions"]]
    return "\n".join(lines) + "\n"


def render_profile(data: dict) -> str:
    """requirement_profile.json -> .md: từng chức năng với yêu cầu chi tiết + AC, rồi user flow, quy tắc, NFR."""
    overview = data["overview"]
    lines = ["# Hồ sơ yêu cầu (Requirement Profile)", "",
             "> Tổng quan, tên/mô tả chức năng (F-n), tiêu chí thành công, NFR, ràng buộc và giả định được chép nguyên "
             "từ requirements đã duyệt: muốn đổi những phần đó, góp ý ở bước requirements (dòng lệnh: `revise requirements`, rồi "
             "duyệt lại và chạy lại profile). Góp ý ở bước này chỉ đổi user flow, quy tắc nghiệp vụ, "
             "yêu cầu chi tiết, AC và trường hợp biên.", "", overview["summary"], "",
             f"- **Mục tiêu:** {overview['product_goal']}", f"- **Người dùng:** {', '.join(overview['target_users'])}"]
    rules = data.get("business_rules", [])
    for feature in data["features"]:
        lines += ["", f"## {feature['id']} · {feature['name']} ({PRIORITY.get(feature['priority'], feature['priority'])})",
                  "", feature["description"]]
        for fr in [fr for fr in data["functional_requirements"] if fr["feature_id"] == feature["id"]]:
            lines += ["", f"**{fr['id']}** ({PRIORITY.get(fr['priority'], fr['priority'])}): {fr['description']}"]
            lines += [f"- {ac['id']}: **Cho** {ac['given']}, **khi** {ac['when']}, **thì** {ac['then']}"
                      for ac in fr["acceptance_criteria"]]
        feature_rules = [rule for rule in rules if rule["feature_id"] == feature["id"]]
        if feature_rules:
            lines += ["", "_Quy tắc nghiệp vụ:_", *[f"- {rule['id']}: {rule['rule']}" for rule in feature_rules]]
        cases = [case for case in data.get("edge_cases", []) if case["feature_id"] == feature["id"]]
        if cases:
            lines += ["", "_Trường hợp biên:_", *[f"- {case['id']}: {case['situation']} → {case['expected']}"
                                                  for case in cases]]
    lines += ["", "## User flow"]
    for flow in data["user_flows"]:
        lines += ["", f"**{flow['id']} · {flow['name']}** — {flow['actor']} ({', '.join(flow['feature_ids'])})",
                  *[f"{number}. {step}" for number, step in enumerate(flow["steps"], start=1)]]
    nfrs = data.get("non_functional_requirements", [])
    if nfrs:
        lines += ["", "## Yêu cầu phi chức năng", "", "| ID | Loại | Yêu cầu | Đo bằng |", "|---|---|---|---|"]
        lines += [f"| {n['id']} | {n['category']} | {_cell(n['requirement'])} | {_cell(n['metric'])} |" for n in nfrs]
    for title, key in (("Ràng buộc", "constraints"), ("Ngoài phạm vi", "out_of_scope"), ("Câu hỏi mở", "open_questions")):
        if data.get(key):
            lines += ["", f"## {title}", "", *[f"- {item}" for item in data[key]]]
    if data.get("assumptions"):
        lines += ["", "## Giả định", "", *[f"- {item['text']}" for item in data["assumptions"]]]
    return "\n".join(lines) + "\n"
