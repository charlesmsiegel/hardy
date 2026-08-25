"""Where the identity a run is frozen under comes from.

It used to live in `cli.py` and take a whole `Config` to read one field off
it, which put it out of reach of anything that is not the command line --
including the interactive session, which needs the same identity to search.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest

MANIFEST = {'packages': [{'name': 'mathlib', 'rev': '81a5d257' + '0' * 32}]}


def _project(tmp_path: Path) -> Path:
    (tmp_path / 'lake-manifest.json').write_text(json.dumps(MANIFEST), encoding='utf-8')
    return tmp_path


def test_the_identity_names_the_mathlib_the_manifest_resolved(tmp_path) -> None:
    lean = importlib.import_module('hardy.lean')

    identity = lean.environment_identity(_project(tmp_path))

    assert identity.mathlib_revision == MANIFEST['packages'][0]['rev']
    assert identity.imports == ('Mathlib',)


def test_the_manifest_digest_is_taken_over_the_bytes_on_disk(tmp_path) -> None:
    """Not over a re-serialisation of the parsed JSON.

    `LeanSearchSource._manifest_matches` compares this number against a fresh
    hash of the same file, so a digest taken over anything but those exact
    bytes would report every pinned environment as unpinned.
    """
    lean = importlib.import_module('hardy.lean')
    project = _project(tmp_path)

    identity = lean.environment_identity(project)

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
