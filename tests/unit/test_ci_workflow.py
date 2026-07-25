import re
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).parents[2]

# Every file that registers or dispatches CAS tools. A regression in any one
# of them can remove tools or route calls incorrectly, but only `cas*.py`
# and the CAS tests were in the original path filter.
REQUIRED_BINDING_FILES = (
    'src/hardy/chat.py',
    'src/hardy/cli.py',
    'src/hardy/staged.py',
    'src/hardy/mcp_server.py',
)


def _pull_request_paths(text: str) -> list[str]:
    """The `pull_request: paths:` list, without pulling in a YAML dependency."""
    start = text.index('pull_request:')
    end = text.index('\njobs:', start)
    block = text[start:end]
    paths = re.findall(r'-\s*"([^"]+)"', block)
    assert paths, 'could not find any pull_request/paths entries in the workflow'
    return paths


def test_cas_ci_runs_when_a_cas_binding_file_changes() -> None:
    """The repository has no workflow besides this one. A PR that changes CAS
    registration or dispatch in chat.py, cli.py, staged.py, or mcp_server.py
    must not skip the hermetic suite.
    """
    text = (ROOT / '.github' / 'workflows' / 'cas-backends.yml').read_text(encoding='utf-8')
    paths = _pull_request_paths(text)

    for required in REQUIRED_BINDING_FILES:
        assert any(fnmatch(required, pattern) for pattern in paths), (
            f'{required} is not covered by any pull_request path pattern: {paths}'
        )
