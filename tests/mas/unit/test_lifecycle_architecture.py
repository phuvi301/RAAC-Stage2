import ast
from pathlib import Path
from raac.common.agent_contracts import RoleHandoff, parse_role_output
from raac.collaboration.levels.l3_collaboration import chain_state


def test_harness_contracts_keep_collaboration_import_compatibility():
    assert chain_state.RoleHandoff is RoleHandoff
    assert chain_state.parse_role_output is parse_role_output


def test_layer1_has_no_collaboration_dependency():
    root = Path(__file__).resolve().parents[3] / "src/raac/harness/layer1_lifecycle"
    for file in root.glob("*.py"):
        for node in ast.walk(ast.parse(file.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert "collaboration" not in (node.module or ""), str(file)
            if isinstance(node, ast.Import):
                assert all("collaboration" not in item.name for item in node.names)
