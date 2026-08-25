"""Search on the interactive surface, and what it says when it cannot run.

The session's own Lean access is a `LeanTools` built around a placeholder
`Request`, and its `_environment` is a cache-invalidation string rather than
an `EnvironmentIdentity`. Neither can be handed to a `LeanService`, so the
runtime is assembled from the `Config` instead.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import pathlib
from pathlib import Path

MANIFEST = {'packages': [{'name': 'mathlib', 'rev': '81a5d257' + '0' * 32}]}


def _project(tmp_path: Path) -> Path:
    project = tmp_path / 'lean'
    project.mkdir()
    (project / 'lake-manifest.json').write_text(json.dumps(MANIFEST), encoding='utf-8')
    (project / 'lean-toolchain').write_text('leanprover/lean4:v4.32.0\n', encoding='utf-8')
    return project


def _lake(tmp_path: Path) -> Path:
    """A real executable named `lake`, on disk rather than on `PATH`.

    `_same_toolchain` resolves through `shutil.which` and compares inodes, so
    a config naming a `lake` that exists nowhere makes every test return
    `None` for the same reason regardless of what it meant to exercise. An
    absolute path sidesteps `PATH` entirely -- `shutil.which` on a path
    containing a separator checks that exact file rather than searching --
    so this test suite exercises the real resolve-and-compare code path
    without depending on whether this machine happens to have a Lean
    toolchain installed.
    """
    lake = tmp_path / 'bin' / 'lake'
    lake.parent.mkdir(parents=True, exist_ok=True)
    lake.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    lake.chmod(0o755)
    return lake


def _config(tmp_path: Path, project: Path | None, **overrides):
    """`Config` has no defaults for `model`, `lean_command`, `lean_timeout`, or
    `latex_command`, so every test supplies them here rather than the brief's
    two-keyword form -- the fields a given test cares about are still passed
    as overrides. `lean_command` and `lake` both default to the same on-disk
    stub, so a test that does not care about the toolchain match still gets
    one that resolves and agrees.
    """
    configuration = importlib.import_module('hardy.config')
    lake = _lake(tmp_path)
    fields = dict(
        model=None,
        lean_command=(str(lake), 'env', 'lean'),
        lean_project=project,
        lean_timeout=30.0,
        latex_command=('pdflatex',),
        workspace=tmp_path / 'workspace',
        lake=lake,
    )
    fields.update(overrides)
    return configuration.Config(**fields)


def test_a_configured_project_yields_a_runtime_that_can_search(tmp_path) -> None:
    search_tools = importlib.import_module('hardy.search_tools')

    runtime, detail = search_tools.build_runtime(_config(tmp_path, _project(tmp_path)))

    assert runtime is not None
    assert runtime.service.environment.mathlib_revision == MANIFEST['packages'][0]['rev']
    assert 'Mathlib' in detail


def test_no_lake_project_yields_no_runtime_and_the_reason_why(tmp_path) -> None:
    """The reason travels, because it is what the tools will refuse with.

    A model told the tool does not exist concludes Hardy cannot search. A
    model told no Lake project is configured can say so to the user, which is
    the outcome that gets it fixed.
    """
    search_tools = importlib.import_module('hardy.search_tools')

    runtime, detail = search_tools.build_runtime(_config(tmp_path, None))

    assert runtime is None
    assert 'lean_project' in detail


def test_a_project_without_a_manifest_is_a_reason_and_not_a_crash(tmp_path) -> None:
    bare = tmp_path / 'bare'
    bare.mkdir()
    search_tools = importlib.import_module('hardy.search_tools')

    runtime, detail = search_tools.build_runtime(_config(tmp_path, bare))

    assert runtime is None
    assert 'lake-manifest.json' in detail


def test_a_ranking_comes_back_as_json_a_model_can_read(tmp_path, monkeypatch) -> None:
    search_tools = importlib.import_module('hardy.search_tools')
    retrieval = importlib.import_module('hardy.retrieval')
    runtime, _ = search_tools.build_runtime(_config(tmp_path, _project(tmp_path)))

    goal = '⊢ _ + _ = _ + _'
    query = '_ + _ = _ + _'
    provenance = retrieval.RetrievalProvenance(
        premises_sha256=retrieval.premises_digest(()),
        budget_seconds=600,
        prior_seconds_spent=0.0,
        # `PremiseRanking` validates these against `hashlib.sha256(goal)` and
        # `hashlib.sha256(query)`, so the placeholders have to be the real
        # digests rather than filler.
        goal_sha256=hashlib.sha256(goal.encode('utf-8')).hexdigest(),
        query_sha256=hashlib.sha256(query.encode('utf-8')).hexdigest(),
        ranker=retrieval.RANKER,
        sources=(),
    )
    ranking = retrieval.PremiseRanking(
        goal=goal,
        query=query,
        premises=(),
        provenance=provenance,
        provenance_sha256=provenance.digest,
        complete=True,
        # No source answered, so `_reproducible` -- `bool(answered) and
        # all(pinned)` -- is False for an empty `answered`, not vacuously True.
        reproducible=False,
        seconds_spent=0.0,
        run_seconds_remaining=600.0,
        budget_exhausted=False,
    )
    monkeypatch.setattr(runtime.retriever, 'rank', lambda goal, limit=10: ranking)

    result = runtime.rank_premises('⊢ _ + _ = _ + _', limit=5)

    assert result.ok
    assert json.loads(result.output)['query'] == '_ + _ = _ + _'


def test_a_lean_command_that_is_not_the_configured_lake_yields_no_runtime(tmp_path) -> None:
    """Otherwise the model searches one environment and checks in another.

    Chat elaborates through `lean_command`; search would run `config.lake`.
    Under the default they are one program, and a custom wrapper is exactly
    the configuration where a name found in one Lean does not elaborate in
    the other.
    """
    search_tools = importlib.import_module('hardy.search_tools')
    config = _config(
        tmp_path,
        _project(tmp_path),
        lean_command=('/opt/wrapper/lean-shim',),
    )

    runtime, detail = search_tools.build_runtime(config)

    assert runtime is None
    assert 'lean-shim' in detail


def test_a_lake_elsewhere_on_disk_is_caught_even_though_the_names_agree(tmp_path) -> None:
    """`HARDY_LAKE=/opt/pinned/lake` against the default `lake env lean`.

    Both basenames are `lake`, so a name comparison calls them equivalent.
    Here `config.lake` is pinned to a *different*, genuinely existing `lake`
    than the one `lean_command` resolves to -- both real files, so this
    exercises `os.path.samefile` finding them unequal rather than merely
    `shutil.which` finding nothing to compare.
    """
    search_tools = importlib.import_module('hardy.search_tools')
    elsewhere = tmp_path / 'pinned' / 'lake'
    elsewhere.parent.mkdir()
    elsewhere.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    elsewhere.chmod(0o755)
    config = _config(tmp_path, _project(tmp_path), lake=elsewhere)

    runtime, detail = search_tools.build_runtime(config)

    assert runtime is None
    assert 'lake' in detail


def test_a_relative_lake_resolves_where_the_child_will_run_it(tmp_path) -> None:
    """Both Lean facades run the child with `lean_project` as its working
    directory, so `./bin/lake` on both sides is one program. Resolving it
    against Hardy's own process directory instead refused search over a
    difference that does not exist -- whenever Hardy was started anywhere but
    inside the project."""
    configuration = importlib.import_module('hardy.config')
    search_tools = importlib.import_module('hardy.search_tools')
    project = _project(tmp_path)
    lake = project / 'bin' / 'lake'
    lake.parent.mkdir(parents=True, exist_ok=True)
    lake.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    lake.chmod(0o755)
    config = configuration.Config(
        workspace=tmp_path / 'workspace',
        lean_project=project,
        lake=pathlib.Path('bin/lake'),
        lean_command=('bin/lake', 'env', 'lean'),
        model='claude-opus-5',
        lean_timeout=180.0,
        latex_command=('tectonic',),
    )

    runtime, detail = search_tools.build_runtime(config)

    assert runtime is not None, detail


def test_a_bad_goal_is_refused_as_an_answer_rather_than_an_exception(tmp_path) -> None:
    """The dispatchers catch `ValueError`, but a refusal the model can read
    beats a generic `invalid tool call`."""
    search_tools = importlib.import_module('hardy.search_tools')
    runtime, _ = search_tools.build_runtime(_config(tmp_path, _project(tmp_path)))

    result = runtime.rank_premises('', limit=5)

    assert not result.ok
    assert 'characters' in result.output
