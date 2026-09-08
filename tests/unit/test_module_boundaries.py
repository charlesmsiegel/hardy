"""Architectural regressions are observable even without a live provider."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "hardy"


@pytest.mark.parametrize("module, forbidden", [
    ("completion", ("workspace", "latex")),
    ("workflows.recorded", ("workflow", "runner", "staged", "claude_runtime")),
    ("evals.scoreboard", ("evals.runner", "evals.commands", "evals.staged", "workflow")),
    ("evals.pool", ("evals.runner", "evals.commands", "evals.staged", "workflow")),
])
def test_evidence_reader_imports_no_execution(module, forbidden):
    script = f"import hardy.{module}; import sys; assert not ({{'hardy.' + x for x in {forbidden!r}}} & sys.modules.keys())"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module", ["claude_runtime", "api_runtime", "codex_runtime", "staged"])
def test_agent_import_does_not_load_interactive_workflow(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import hardy.{module}; import sys; assert 'hardy.chat' not in sys.modules"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_in_process_formal_tools_do_not_load_transport():
    result = subprocess.run(
        [sys.executable, "-c", "import hardy.formal.tools; import sys; assert 'hardy.mcp_server' not in sys.modules; assert 'mcp.server.fastmcp' not in sys.modules"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_corpus_import_does_not_load_measurement_or_model_code():
    script = "import hardy.corpus.catalog; import sys; assert not any(name.startswith(('hardy.evals', 'hardy.claude_runtime', 'hardy.workflow')) for name in sys.modules)"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module", ["claude_runtime", "api_runtime", "codex_runtime", "staged", "loop"])
def test_agent_import_fence_includes_local_imports(module):
    tree = ast.parse((SOURCE / f"{module}.py").read_text(encoding="utf-8"))
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"chat", "cli", "hardy.chat", "hardy.cli"}:
            violations.append((node.lineno, node.module))
    assert not violations, violations
