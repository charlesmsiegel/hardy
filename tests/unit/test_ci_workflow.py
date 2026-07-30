import re
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / '.github' / 'workflows'

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
    """A PR that changes CAS registration or dispatch in chat.py, cli.py,
    staged.py, or mcp_server.py must not skip the real-backend tests, which are
    the only thing that runs Singular and Macaulay2 for real.
    """
    text = (WORKFLOWS / 'cas-backends.yml').read_text(encoding='utf-8')
    paths = _pull_request_paths(text)

    for required in REQUIRED_BINDING_FILES:
        assert any(fnmatch(required, pattern) for pattern in paths), (
            f'{required} is not covered by any pull_request path pattern: {paths}'
        )


def test_the_hermetic_suite_runs_on_every_pull_request() -> None:
    """Unfiltered, or a change to the verifier or the runner reaches main with
    nothing having run it -- which is how the suite came to be unmeasured."""
    text = (WORKFLOWS / 'tests.yml').read_text(encoding='utf-8')
    header = text[text.index('on:'):text.index('\njobs:')]
    assert 'pull_request:' in header
    assert 'paths:' not in header, 'the unconditional suite must not acquire a path filter'
    assert 'pytest' in text and 'ruff check' in text


def test_ci_measures_coverage_and_keeps_the_report() -> None:
    """A number nobody can read afterwards is not evidence."""
    text = (WORKFLOWS / 'tests.yml').read_text(encoding='utf-8')
    assert '--cov' in text
    assert 'upload-artifact' in text
    assert 'coverage.xml' in text


def test_the_coverage_floor_is_configured() -> None:
    """Measuring without a floor lets coverage fall quietly."""
    text = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    assert '[tool.coverage.report]' in text
    assert re.search(r'(?m)^fail_under\s*=\s*\d+', text)
