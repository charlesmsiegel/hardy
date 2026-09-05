"""`hardy prove --assume` declares what a run may stand on, from a file."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path

import pytest

ONE = {
    "assumptions": [
        {
            "name": "Papers.perelman.no_local_collapsing",
            "statement": "True",
            "source": "arXiv:math.DG/0211159v1 (thm:collapse)",
            "justification": "Mathlib has no Ricci flow theory.",
        }
    ]
}


def _args(**overrides):
    fields = {
        "claim": "Two equals two.",
        "backend": "claude",
        "assume": None,
        "config": None,
        "model": "test-model",
        "faithfulness_model": None,
    }
    fields.update(overrides)
    return argparse.Namespace(**fields)


def _run(cli, args, requests):
    class Workflow:
        def run(self, request, terminal):
            requests.append(request)
            return importlib.import_module("hardy.domain").RunManifest(
                run_id=importlib.import_module("uuid").uuid4(),
                created_at=importlib.import_module("datetime").datetime.now(
                    importlib.import_module("datetime").UTC
                ),
                phase=importlib.import_module("hardy.domain").RunPhase.COMPLETED,
                model="test-model",
                prompt_set_sha256="a" * 64,
            )

    return cli.run_prove(args, workflow_factory=lambda *a, **k: Workflow())


def test_a_declared_file_reaches_the_request(tmp_path: Path) -> None:
    cli = importlib.import_module("hardy.cli")
    path = tmp_path / "assume.json"
    path.write_text(json.dumps(ONE), encoding="utf-8")
    requests: list = []

    _run(cli, _args(assume=path), requests)

    declared = requests[0].assumptions
    assert [item.name for item in declared] == ["Papers.perelman.no_local_collapsing"]
    assert declared[0].source.startswith("arXiv:")


def test_a_run_with_no_flag_declares_nothing(tmp_path: Path) -> None:
    cli = importlib.import_module("hardy.cli")
    requests: list = []

    _run(cli, _args(), requests)

    assert requests[0].assumptions == ()


def test_a_missing_file_is_refused_before_the_run(tmp_path: Path, capsys) -> None:
    cli = importlib.import_module("hardy.cli")
    requests: list = []

    code = _run(cli, _args(assume=tmp_path / "absent.json"), requests)

    assert code == 2
    assert requests == []
    assert "absent.json" in capsys.readouterr().out


@pytest.mark.parametrize(
    "payload",
    [
        {"assumptions": [{"statement": "True", "source": "s"}]},
        {"assumptions": [{"name": "n", "source": "s"}]},
        {"assumptions": [{"name": "n", "statement": "True"}]},
        {"assumptions": "not a list"},
        {},
    ],
)
def test_a_malformed_declaration_is_refused_before_the_run(
    tmp_path: Path, payload, capsys
) -> None:
    """Every field is load-bearing: a declaration with no source is an axiom
    whose provenance nobody wrote down."""
    cli = importlib.import_module("hardy.cli")
    path = tmp_path / "assume.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    requests: list = []

    code = _run(cli, _args(assume=path), requests)

    assert code == 2
    assert requests == []
