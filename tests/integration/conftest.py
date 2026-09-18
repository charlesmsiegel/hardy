from pathlib import Path

import pytest


@pytest.fixture
def lean_project() -> Path:
    """The Lake project these tests run Lean in, or a skip.

    Resolved the way Hardy resolves it -- `HARDY_LEAN_PROJECT`, else the user
    config's `lean_project` -- and never a directory inside the checkout. The
    shared Mathlib project is multi-gigabyte installation data that the
    installers and `hardy setup` keep in the per-user data directory; a copy
    under the repository was a second, unpinned environment the tests quietly
    preferred. CI provisions its own project outside the checkout and names
    it with `HARDY_LEAN_PROJECT`.
    """
    from hardy.app import config as configuration

    project = configuration.load().lean_project
    if project is None:
        pytest.skip('no Lean project is configured; run `hardy setup` or set HARDY_LEAN_PROJECT')
    if not (project / 'lake-manifest.json').exists():
        pytest.skip(f'{project} is not a resolved Lake project; run `hardy setup`')
    return project
