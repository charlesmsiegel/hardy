"""Where the identity a run is frozen under comes from.

It used to live in `cli.py` and take a whole `Config` to read one field off
it, which put it out of reach of anything that is not the command line --
including the interactive session, which needs the same identity to search.

The Lean version and commit used to be literals here, so a manifest written
on any other machine named a compiler nobody had run. They are now asked of
the Lean the configured command actually invokes (issue #81), and a compiler
that cannot be identified is refused rather than partly invented.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest

MANIFEST = {'packages': [{'name': 'mathlib', 'rev': '81a5d257' + '0' * 32}]}
VERSION_LINE = (
    'Lean (version 4.33.1, x86_64-unknown-linux-gnu, '
    'commit 819816b2e0a3bf405af45ae5c7af2491d8f5bee6, Release)\n'
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / 'lake-manifest.json').write_text(json.dumps(MANIFEST), encoding='utf-8')
    return tmp_path


def _lean(stdout: str = VERSION_LINE, *, returncode: int = 0, timed_out: bool = False):
    """A runner standing in for `lake env lean --version`, recording what it was asked."""
    process = importlib.import_module('hardy.process')
    asked = []

    def run(spec):
        asked.append(spec)
        return process.ProcessResult(
            argv=spec.argv,
            cwd=spec.cwd,
            returncode=returncode,
            stdout=stdout,
            stderr='',
            timed_out=timed_out,
            output_overflow=False,
            duration_ms=1,
        )

    return run, asked


def test_the_identity_names_the_mathlib_the_manifest_resolved(tmp_path) -> None:
    lean = importlib.import_module('hardy.lean')
    run, _ = _lean()

    identity = lean.environment_identity(_project(tmp_path), runner=run)

    assert identity.mathlib_revision == MANIFEST['packages'][0]['rev']
    assert identity.imports == ('Mathlib',)


def test_the_identity_names_the_lean_the_command_actually_reports(tmp_path) -> None:
    """Not a constant. Two machines on different Lean releases must freeze
    different identities, or a claim proved on one is reported as verified
    by the other."""
    lean = importlib.import_module('hardy.lean')
    run, asked = _lean(
        'Lean (version 4.29.0-rc2, aarch64-apple-darwin, commit abcdef0123456789, Release)\n'
    )

    identity = lean.environment_identity(
        _project(tmp_path), lean_command=('/opt/pinned/lake', 'env', 'lean'), runner=run
    )

    assert identity.lean_version == '4.29.0-rc2'
    assert identity.lean_commit == 'abcdef0123456789'
    # Asked of the configured command, in the project, so elan reads the
    # project's own `lean-toolchain` pin rather than the default toolchain.
    assert asked[0].argv == ('/opt/pinned/lake', 'env', 'lean', '--version')
    assert asked[0].cwd == tmp_path


def test_the_manifest_digest_is_taken_over_the_bytes_on_disk(tmp_path) -> None:
    """Not over a re-serialisation of the parsed JSON.

    `LeanSearchSource._manifest_matches` compares this number against a fresh
    hash of the same file, so a digest taken over anything but those exact
    bytes would report every pinned environment as unpinned.
    """
    lean = importlib.import_module('hardy.lean')
    project = _project(tmp_path)
    run, _ = _lean()

    identity = lean.environment_identity(project, runner=run)

    expected = hashlib.sha256((project / 'lake-manifest.json').read_bytes()).hexdigest()
    assert identity.lake_manifest_sha256 == expected


def test_no_project_is_an_error_naming_what_is_missing() -> None:
    lean = importlib.import_module('hardy.lean')

    with pytest.raises(ValueError, match='lean_project'):
        lean.environment_identity(None)


def test_a_project_without_a_manifest_names_the_file_it_wanted(tmp_path) -> None:
    lean = importlib.import_module('hardy.lean')

    with pytest.raises(ValueError, match='lake-manifest.json'):
        lean.environment_identity(tmp_path)


def test_a_lean_that_names_no_version_is_refused_rather_than_guessed(tmp_path) -> None:
    """Half an identity is worse than none: a version with an invented commit
    cannot be caught, where a refusal names the command that would not answer."""
    lean = importlib.import_module('hardy.lean')
    run, _ = _lean('Lake version 5.0.0\n')

    with pytest.raises(ValueError, match='named no Lean version and commit'):
        lean.environment_identity(_project(tmp_path), runner=run)


def test_a_lean_that_cannot_be_run_says_so(tmp_path) -> None:
    lean = importlib.import_module('hardy.lean')

    def run(spec):
        raise FileNotFoundError(spec.argv[0])

    with pytest.raises(ValueError, match='could not be run'):
        lean.environment_identity(
            _project(tmp_path), lean_command=('nowhere-lake', 'env', 'lean'), runner=run
        )


def test_a_lean_that_fails_reports_its_exit_code(tmp_path) -> None:
    lean = importlib.import_module('hardy.lean')
    run, _ = _lean('', returncode=1)

    with pytest.raises(ValueError, match='exited 1'):
        lean.environment_identity(_project(tmp_path), runner=run)


def test_a_lean_that_hangs_reports_the_timeout(tmp_path) -> None:
    lean = importlib.import_module('hardy.lean')
    run, _ = _lean('', timed_out=True)

    with pytest.raises(ValueError, match='timed out'):
        lean.environment_identity(_project(tmp_path), runner=run, timeout_seconds=5)
