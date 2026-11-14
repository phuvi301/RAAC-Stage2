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


def render_answers(rounds: list[dict], answers: dict[str, str]) -> str:
    """Toàn bộ câu hỏi của các vòng; giữ câu trả lời người dùng đã sửa (`answers`), câu mới điền đáp án gợi ý."""
    lines = [HEADER]
    for number, round_ in enumerate(rounds, start=1):
        lines.append(f"## Vòng {number}")
        if round_.get("summary"):
            lines.append(f"> {round_['summary']}")
        if not round_["questions"]:
            lines.append("_Không cần hỏi thêm: thông tin đã đủ._")
        for question in round_["questions"]:
            lines.append(f"### {question['id']}: {question['text']}")
            if question.get("why"):
                lines.append(f"_Vì sao hỏi: {question['why']}_")
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
    lines += ["", "## Chức năng cốt lõi", "", "| ID | Chức năng | Mô tả | Ưu tiên | Căn cứ |", "|---|---|---|---|---|"]
    for feature in data["features"]:
        lines.append(f"| {feature['id']} | {_cell(feature['name'])} | {_cell(feature['description'])} | "
                     f"{PRIORITY.get(feature['priority'], feature['priority'])} | {', '.join(feature.get('sources', []))} |")
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
    lines = ["# Hồ sơ yêu cầu (Requirement Profile)", "", overview["summary"], "",
             f"- **Mục tiêu:** {overview['product_goal']}", f"- **Người dùng:** {', '.join(overview['target_users'])}"]
    rules = data.get("business_rules", [])
    for feature in data["features"]:
        lines += ["", f"## {feature['id']} · {feature['name']} ({PRIORITY.get(feature['priority'], feature['priority'])})",
                  "", feature["description"]]
        for fr in [fr for fr in data["functional_requirements"] if fr["feature_id"] == feature["id"]]:
            lines += ["", f"**{fr['id']}** ({PRIORITY.get(fr['priority'], fr['priority'])}): {fr['description']}"]
            lines += [f"- {ac['id']}: {ac['text']}" for ac in fr["acceptance_criteria"]]
        feature_rules = [rule for rule in rules if rule["feature_id"] == feature["id"]]
        if feature_rules:
            lines += ["", "_Quy tắc nghiệp vụ:_", *[f"- {rule['id']}: {rule['rule']}" for rule in feature_rules]]
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
