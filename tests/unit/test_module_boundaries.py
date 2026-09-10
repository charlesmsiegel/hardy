"""Architectural regressions are observable even without a live provider."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "hardy"


def test_package_root_contains_only_bootstrap_and_launch_shims():
    assert {path.name for path in SOURCE.glob('*.py')} == {
        '__init__.py', '__main__.py', 'cli.py', 'mcp_server.py', 'cas_driver.py',
    }
    for name in ('cli.py', 'mcp_server.py', 'cas_driver.py'):
        tree = ast.parse((SOURCE / name).read_text(encoding='utf-8'))
        assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                       for node in ast.walk(tree)), name


def _imports(module, tree, modules, *, package=False):
    """Resolve imports at every depth, including local and TYPE_CHECKING code."""
    context = module.split('.') if package else module.split('.')[:-1]
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name in modules)
        elif isinstance(node, ast.ImportFrom):
            prefix = context[:len(context) - node.level + 1] if node.level else []
            base = '.'.join([*prefix, *([node.module] if node.module else [])])
            if base in modules:
                found.add(base)
            found.update(f'{base}.{alias.name}' for alias in node.names
                         if f'{base}.{alias.name}' in modules)
    return found


@pytest.fixture(scope='module')
def import_graph():
    paths = {}
    for path in SOURCE.rglob('*.py'):
        parts = list(path.relative_to(SOURCE.parent).with_suffix('').parts)
        if parts[-1] == '__init__':
            parts.pop()
        paths['.'.join(parts)] = path
    graph = {name: _imports(name, ast.parse(path.read_text(encoding='utf-8')), paths,
                            package=path.name == '__init__.py')
             for name, path in paths.items()}
    return _with_parent_package_imports(graph)


def _reachable(graph, start):
    reached = set()
    todo = list(graph[start])
    while todo:
        module = todo.pop()
        if module not in reached:
            reached.add(module)
            todo.extend(graph[module] - reached)
    return reached


def _with_parent_package_imports(graph):
    """Model package initializers executed before an imported child module."""
    return {
        module: dependencies | {
            '.'.join(module.split('.')[:index])
            for index in range(1, len(module.split('.')))
            if '.'.join(module.split('.')[:index]) in graph
        }
        for module, dependencies in graph.items()
    }


def _boundary_violations(graph, modules, forbidden):
    """Return each module's direct or transitive forbidden dependencies."""
    return {
        module: _reachable(graph, module) & forbidden
        for module in modules
        if _reachable(graph, module) & forbidden
    }


@pytest.mark.parametrize(
    ("graph", "module", "forbidden", "expected"),
    [
        (
            {
                "hardy.workflows.ledger": {"hardy.agents.claude"},
                "hardy.agents.claude": set(),
            },
            "hardy.workflows.ledger",
            {"hardy.agents.claude"},
            {"hardy.workflows.ledger": {"hardy.agents.claude"}},
        ),
        (
            {
                "hardy.workflows.ledger": {"hardy.formal.tools"},
                "hardy.formal.tools": {"hardy.workflows.prove"},
                "hardy.workflows.prove": set(),
            },
            "hardy.workflows.ledger",
            {"hardy.workflows.prove"},
            {"hardy.workflows.ledger": {"hardy.workflows.prove"}},
        ),
        (
            {
                "hardy.workflows.ledger": {"hardy.agents.claude"},
                "hardy.workflows.ledger.contracts": set(),
                "hardy.agents.claude": set(),
            },
            "hardy.workflows.ledger.contracts",
            {"hardy.agents.claude"},
            {"hardy.workflows.ledger.contracts": {"hardy.agents.claude"}},
        ),
    ],
    ids=("direct", "transitive", "package-init"),
)
def test_boundary_violations_detect_direct_transitive_and_package_imports(
    graph, module, forbidden, expected,
):
    assert _boundary_violations(_with_parent_package_imports(graph), {module}, forbidden) == expected


def test_shared_workflow_and_ledger_boundaries(import_graph):
    capabilities = {
        name for name in import_graph
        if name.startswith((
            'hardy.formal.', 'hardy.documents.', 'hardy.algebra.',
            'hardy.literature.', 'hardy.corpus.',
        ))
    }
    permitted_workflow_primitives = {
        'hardy.workflows.contracts', 'hardy.workflows.batch_contracts',
        'hardy.workflows.layout', 'hardy.workflows.storage',
        # formal.search reaches this pure assembler through app.config's
        # compaction defaults; it does not initiate an interactive session.
        'hardy.workflows.interactive', 'hardy.workflows.interactive.summary',
    }
    shared_orchestration = {
        name for name in import_graph
        if name.startswith('hardy.workflows.')
    } - permitted_workflow_primitives
    assert not _boundary_violations(import_graph, capabilities, shared_orchestration)

    ledger = {name for name in import_graph if name.startswith('hardy.workflows.ledger')}
    transports = {
        'hardy.agents.api', 'hardy.agents.claude', 'hardy.agents.codex',
        'hardy.agents.loop', 'hardy.agents.staged',
    }
    application_assembly = {
        'hardy.cli', 'hardy.app.cli', 'hardy.mcp_server', 'hardy.app.mcp',
        'hardy.app.wiring',
    }
    execution_controllers = {
        'hardy.workflows.acceptance', 'hardy.workflows.batch',
        'hardy.workflows.interactive.session', 'hardy.workflows.prove',
        'hardy.evals.runner',
    }
    forbidden = transports | application_assembly | execution_controllers
    assert not _boundary_violations(import_graph, ledger, forbidden)


def test_import_resolver_catches_absolute_relative_and_local_aliases():
    tree = ast.parse('''
import hardy.workflows.interactive.session as session
def deferred():
    from ..workflows import batch
    from ..app import cli as commands
    from hardy import mcp_server
''')
    modules = {'hardy.workflows.interactive.session', 'hardy.workflows.batch', 'hardy.app.cli', 'hardy.mcp_server'}
    assert _imports('hardy.agents.example', tree, modules) == modules


def test_full_tree_dependency_directions(import_graph):
    providers = {'hardy.agents.claude', 'hardy.agents.api', 'hardy.agents.codex',
                 'hardy.agents.staged', 'hardy.agents.loop'}
    launchers = {'hardy.cli', 'hardy.app.cli', 'hardy.mcp_server', 'hardy.app.mcp'}
    controllers = {'hardy.workflows.interactive.session', 'hardy.workflows.prove', 'hardy.workflows.batch', 'hardy.evals.runner'}
    readers = {'hardy.workflows.recorded', 'hardy.evals.scoreboard', 'hardy.evals.pool'}
    capabilities = {name for name in import_graph if name.startswith(
        ('hardy.formal.', 'hardy.documents.', 'hardy.algebra.', 'hardy.literature.', 'hardy.corpus.')
    )} | {'hardy.algebra.tools', 'hardy.algebra.export', 'hardy.literature.tools'}
    for module in providers | readers | capabilities:
        forbidden = launchers | controllers
        if module in readers:
            forbidden |= providers | {'hardy.app.evals', 'hardy.evals.staged'}
        if module.startswith('hardy.corpus.'):
            forbidden |= {name for name in import_graph if name.startswith('hardy.evals.')}
        assert not (_reachable(import_graph, module) & forbidden), module
    for module in import_graph:
        if module.startswith('hardy.app.tui.') or module in {'hardy.app.projects', 'hardy.app.terminal'}:
            assert not (_reachable(import_graph, module) & {'hardy.cli', 'hardy.app.cli'}), module


def test_evaluation_and_cli_cycles_are_removed(import_graph):
    for module in import_graph:
        if module.startswith('hardy.evals.') or module in {'hardy.cli', 'hardy.app.cli'}:
            assert module not in _reachable(import_graph, module), module


def test_run_identity_keeps_relocated_owners_and_excludes_unreachable_cli(import_graph):
    from hardy.evals.identity import RUN_SOURCE_ROOT, run_source_paths

    included = {path.relative_to(RUN_SOURCE_ROOT).as_posix() for path in run_source_paths()}
    for directory in ('foundation', 'agents', 'formal', 'documents', 'algebra', 'literature', 'corpus', 'workflows'):
        for path in (SOURCE / directory).rglob('*.py'):
            assert path.relative_to(SOURCE).as_posix() in included
    assert 'cas_driver.py' in included
    assert 'app/cli.py' not in included
    assert not (_reachable(import_graph, 'hardy.evals.runner') & {'hardy.app.cli', 'hardy.cli'})


def test_known_dynamic_launch_modules_still_exist():
    launches = set()
    for path in SOURCE.rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, (ast.List, ast.Tuple)):
                for index, value in enumerate(node.elts[:-1]):
                    following = node.elts[index + 1]
                    if (isinstance(value, ast.Constant) and value.value == '-m'
                            and isinstance(following, ast.Constant)
                            and isinstance(following.value, str)
                            and following.value.startswith('hardy.')):
                        launches.add(following.value)
    assert launches == {'hardy.mcp_server', 'hardy.cas_driver'}
    for module in launches:
        assert (SOURCE.parent / Path(*module.split('.'))).with_suffix('.py').is_file()


@pytest.mark.parametrize("module, forbidden", [
    ("documents.completion", ("formal.workspace", "documents.latex")),
    ("workflows.recorded", ("workflows.prove", "workflows.batch", "agents.staged", "agents.claude")),
    ("evals.scoreboard", ("evals.runner", "evals.commands", "evals.staged", "workflows.prove")),
    ("evals.pool", ("evals.runner", "evals.commands", "evals.staged", "workflows.prove")),
])
def test_evidence_reader_imports_no_execution(module, forbidden):
    script = f"import hardy.{module}; import sys; assert not ({{'hardy.' + x for x in {forbidden!r}}} & sys.modules.keys())"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module", ["agents.claude", "agents.api", "agents.codex", "agents.staged"])
def test_agent_import_does_not_load_interactive_workflow(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import hardy.{module}; import sys; assert 'hardy.workflows.interactive.session' not in sys.modules"],
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
    script = "import hardy.corpus.catalog; import sys; assert not any(name.startswith(('hardy.evals', 'hardy.agents.claude', 'hardy.workflows.prove')) for name in sys.modules)"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module", ["agents.claude", "agents.api", "agents.codex", "agents.staged", "agents.loop"])
def test_agent_import_fence_includes_local_imports(module):
    tree = ast.parse((SOURCE / Path(*module.split(".")).with_suffix(".py")).read_text(encoding="utf-8"))
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"chat", "cli", "hardy.workflows.interactive.session", "hardy.cli"}:
            violations.append((node.lineno, node.module))
    assert not violations, violations


def test_terminal_adapters_do_not_import_command_entry_point():
    violations = []
    for path in (SOURCE / "app" / "tui").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (
                node.module in {"cli", "hardy.cli"}
                or node.module is None and any(n.name == "cli" for n in node.names)
            ):
                violations.append((path.name, node.lineno))
    assert not violations, violations
