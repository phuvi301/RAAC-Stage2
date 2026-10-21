"""Chain-owned input selection. Runtime adapters and Layer 2 never choose role visibility."""

from copy import deepcopy

from raac.common.report_context import compact_environment_report, compact_test_report


def _select_task_input(request, *, role=None):
    """Send raw chat only to Environment/Analyst/Reviewer, as a Chain handoff rule."""
    result = deepcopy(request)
    if role not in {"Planner", "Coder", "Tester"}:
        return result
    requirement = result.pop("requirement", None)
    incoming = result.get("incoming_message")
    artifacts = result.get("artifacts")
    has_spec = isinstance(artifacts, dict) and isinstance(artifacts.get("spec"), dict)
    if isinstance(incoming, dict):
        for key, value in list(incoming.items()):
            # Remove known handoff copies and keep references resolvable.
            if key == "requirement" or (key == "content" and requirement is not None and value == requirement):
                del incoming[key]
                if has_spec:
                    incoming[key + "_ref"] = "artifacts.spec"
            elif key.endswith("_ref") and value == "requirement":
                if has_spec:
                    incoming[key] = "artifacts.spec"
                else:
                    del incoming[key]
    return result


def prepare_role_request(request: dict, *, role: str) -> dict:
    """Select the Chain handoff before the runner receives it, independent of compaction."""
    result = _select_task_input(request, role=role)
    incoming = result.get("incoming_message")
    artifacts = result.get("artifacts", {})
    if isinstance(incoming, dict):
        for key, value in list(incoming.items()):
            if key == "from":
                continue
            reference = None
            if key in result and key != "incoming_message" and value == result[key]:
                reference = key
            elif isinstance(artifacts, dict):
                reference = next(("artifacts." + name for name, item in artifacts.items()
                                  if value == item), None)
            if reference is not None:
                del incoming[key]
                incoming[key + "_ref"] = reference
    if isinstance(artifacts, dict):
        if isinstance(artifacts.get("test_report"), dict):
            artifacts["test_report"] = compact_test_report(artifacts["test_report"])
        # Runtime is shared once at the top level, not copied in artifacts.
        if artifacts.get("environment_report") == result.get("environment_report"):
            artifacts.pop("environment_report", None)
    if isinstance(result.get("environment_report"), dict):
        result["environment_report"] = compact_environment_report(result["environment_report"])
    if isinstance(incoming, dict):
        for key, value in incoming.items():
            if key.endswith("_ref") and value == "artifacts.environment_report" and "environment_report" not in artifacts:
                incoming[key] = "environment_report"
    # Resolve duplicate incoming payloads first, so they cannot reintroduce a
    # full plan after filtering. The generated references still resolve here.
    plan = artifacts.get("plan") if isinstance(artifacts, dict) else None
    if isinstance(plan, dict) and role == "Tester":
        # Independent oracles come from the Analyst's spec/AC, not implementation.
        artifacts["plan"] = {"interface_contracts": deepcopy(plan.get("interface_contracts", {}))}
    elif isinstance(plan, dict) and role == "Reviewer":
        steps = plan.get("steps")
        if isinstance(steps, list) and steps and all(isinstance(step, dict) for step in steps):
            artifacts["plan"] = {
                "decisions": [{key: deepcopy(step[key]) for key in
                               (("id", "description", "files", "depends_on", "ac_ids") if "description" in step
                                else ("ac_ids", "target", "approach", "complexity", "risks")) if key in step}
                              for step in steps],
                "interface_contracts": deepcopy(plan.get("interface_contracts", {})),
            }
        # Legacy recorded plans lack structured decisions; retain their prose
        # for offline inspection rather than guessing which text is safe to drop.
    # Tester needs current implementation, not a full diff repeated every turn.
    # Coder retries can likewise inspect saved files. Reviewer keeps the full diff.
    # Only substitute when Chain state contains observed changed paths from the Coder.
    patch_key = "patch" if role == "Tester" else "previous_patch" if role == "Coder" else None
    changes = result.get("repository_changes")
    if (patch_key and changes and isinstance(artifacts, dict)
            and isinstance(artifacts.get(patch_key), str) and len(artifacts[patch_key]) > 2000):
        patch = artifacts.pop(patch_key)
        context_key = patch_key + "_context"
        artifacts[context_key] = {"chars": len(patch), "changed_files_ref": "repository_changes",
            "note": "Read the indicated current files to inspect implementation; deleted files are marked. The full diff remains in the run artifact."}
        if isinstance(incoming, dict):
            for key, value in incoming.items():
                if key.endswith("_ref") and value == "artifacts." + patch_key:
                    incoming[key] = "artifacts." + context_key
    return result

