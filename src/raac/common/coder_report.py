"""The Coder's handoff report: what it did per plan step, and why anything was left undone.

The patch itself comes from the saved workspace files. The report is the Coder's own account, so downstream roles
treat it as a claim about intent, never as evidence of correctness.

Format (plain text, one item per line)::

    STEP 1 | done | what changed and why
    STEP 2 | blocked | the reason the step could not be done
    ISSUE test_foo fails on None | unfixed | the reason it could not be fixed
    NOTES: risks, checks that were actually run
"""

import re

STEP = re.compile(r"^\s*STEP\s+(\d+)\s*\|\s*(done|partial|blocked)\s*\|\s*(.*?)\s*$", re.I)
ISSUE = re.compile(r"^\s*ISSUE\s+(.+?)\s*\|\s*(fixed|unfixed)\s*\|\s*(.*?)\s*$", re.I)
NOTES = re.compile(r"^\s*NOTES?\s*:\s*(.*?)\s*$", re.I)
NOTE_LIMIT, TOTAL_LIMIT = 300, 2000
NEEDS_REASON = {"partial", "blocked", "unfixed"}


def looks_like_diff(text: str) -> bool:
    """True when the text is presented as a patch (so a broken one is an invalid patch, not a report)."""
    head = text.lstrip()[:400]
    return head.startswith(("diff --git", "--- ", "Index: ")) or ("\n+++ " in head and "\n@@" in text)


def parse_coder_report(text: str, step_ids=()) -> dict:
    """Structured report from the Coder's final message; step ids come from the plan it was given."""
    known = [step for step in step_ids if isinstance(step, int)]
    steps, issues, notes = {}, [], []
    for line in text.splitlines():
        if matched := STEP.match(line):
            number, status, note = int(matched.group(1)), matched.group(2).lower(), matched.group(3)
            if number in known or not known:
                steps[number] = {"step": number, "status": status, "note": note[:NOTE_LIMIT]}
        elif matched := ISSUE.match(line):
            issues.append({"issue": matched.group(1)[:120], "status": matched.group(2).lower(),
                           "note": matched.group(3)[:NOTE_LIMIT]})
        elif matched := NOTES.match(line):
            notes.append(matched.group(1))
        elif line.strip() and not steps and not issues:
            notes.append(line.strip())
    if known:
        ordered = [steps.get(number) or {"step": number, "status": "not_reported", "note": ""} for number in known]
    else:
        ordered = [steps[number] for number in sorted(steps)]
    for entry in (*ordered, *issues):
        entry["reason_missing"] = entry["status"] in NEEDS_REASON and not entry["note"]
    return {"steps": ordered, "issues": issues, "notes": " ".join(notes)[:NOTE_LIMIT * 4],
            "structured": bool(steps or issues)}


def render_coder_report(report: dict) -> str:
    """Readable summary for the Tester, the Reviewer and the Coder's next turn."""
    lines = []
    for entry in report["steps"]:
        reason = " (no reason given)" if entry["reason_missing"] else ""
        lines.append(f"Step {entry['step']} {entry['status'].replace('_', ' ')}"
                     + (f": {entry['note']}" if entry["note"] else "") + reason)
    for entry in report["issues"]:
        reason = " (no reason given)" if entry["reason_missing"] else ""
        lines.append(f"Issue '{entry['issue']}' {entry['status']}" + (f": {entry['note']}" if entry["note"] else "") + reason)
    if report["notes"]:
        lines.append("Notes: " + report["notes"])
    return "\n".join(lines)[:TOTAL_LIMIT]
