import hashlib
import importlib
import json
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import UUID

import pytest

NOW = datetime(2026, 7, 24, 12, 30, tzinfo=timezone.utc)
RUN_ID = UUID('12345678-1234-5678-1234-567812345678')


def test_write_text_returns_content_addressed_artifact(tmp_path) -> None:
    storage = importlib.import_module('hardy.storage')
    store = storage.RunStore.create(tmp_path, 'odd-sum', now=NOW, run_id=RUN_ID)

    artifact = store.write_text(PurePosixPath('request.md'), 'hello\n')

    assert artifact.relative_path == 'request.md'
    assert artifact.byte_count == 6
    assert artifact.sha256 == hashlib.sha256(b'hello\n').hexdigest()
    assert (store.path / 'request.md').read_text(encoding='utf-8') == 'hello\n'


@pytest.mark.parametrize(
    'relative_path',
    [PurePosixPath('../escape.txt'), PurePosixPath('nested\\escape.txt')],
)
def test_artifact_paths_cannot_escape_the_run_directory(tmp_path, relative_path) -> None:
    storage = importlib.import_module('hardy.storage')
    store = storage.RunStore.create(tmp_path, 'demo', now=NOW, run_id=RUN_ID)

    with pytest.raises(ValueError, match='relative artifact path'):
        store.write_text(relative_path, 'nope')


def test_trajectory_events_are_ordered_and_redacted(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    storage = importlib.import_module('hardy.storage')
    store = storage.RunStore.create(tmp_path, 'demo', now=NOW, run_id=RUN_ID)

    event = store.append(
        'agent.item',
        {
            'authorization': 'Bearer nope',
            'nested': {'api_key': 'also nope'},
            'text': 'keep',
        },
        phase=domain.RunPhase.PROVING,
    )

    saved = json.loads(store.trajectory_path.read_text(encoding='utf-8'))
    assert event.sequence == 0
    assert saved['sequence'] == 0
    assert saved['payload'] == {
        'authorization': '[REDACTED]',
        'nested': {'api_key': '[REDACTED]'},
        'text': 'keep',
    }


def test_reopened_store_continues_the_trajectory_sequence(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    storage = importlib.import_module('hardy.storage')
    store = storage.RunStore.create(tmp_path, 'demo', now=NOW, run_id=RUN_ID)
    store.append('first', {}, phase=domain.RunPhase.SETUP)
    store.append('second', {}, phase=domain.RunPhase.FORMALIZING)

    reopened = storage.RunStore.open(store.path, run_id=RUN_ID)
    event = reopened.append('third', {}, phase=domain.RunPhase.AWAITING_APPROVAL)

    assert event.sequence == 2
    assert len(reopened.trajectory_path.read_text(encoding='utf-8').splitlines()) == 3


def test_reopen_rejects_a_noncontiguous_trajectory(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    storage = importlib.import_module('hardy.storage')
    store = storage.RunStore.create(tmp_path, 'demo', now=NOW, run_id=RUN_ID)
    store.append('first', {}, phase=domain.RunPhase.SETUP)
    event = json.loads(store.trajectory_path.read_text(encoding='utf-8'))
    event['sequence'] = 4
    store.trajectory_path.write_text(json.dumps(event) + '\n', encoding='utf-8')

    with pytest.raises(ValueError, match='sequence'):
        storage.RunStore.open(store.path, run_id=RUN_ID)


def test_finalize_writes_a_parseable_incomplete_manifest(tmp_path) -> None:
    domain = importlib.import_module('hardy.domain')
    storage = importlib.import_module('hardy.storage')
    store = storage.RunStore.create(tmp_path, 'demo', now=NOW, run_id=RUN_ID)
    manifest = domain.RunManifest(
        run_id=RUN_ID,
        created_at=NOW,
        phase=domain.RunPhase.WRITEUP,
        model='gpt-5.6-codex',
        prompt_set_sha256='c' * 64,
        terminal_reason=domain.TerminalReason.PROOF_INCOMPLETE,
        grades=domain.Grades(
            formal=domain.FormalStatus.PARTIAL,
            known_gaps=('no checked proof',),
        ),
    )

    store.finalize(manifest)

    saved = domain.RunManifest.model_validate_json(
        (store.path / 'manifest.json').read_text(encoding='utf-8')
    )
    assert saved.terminal_reason is domain.TerminalReason.PROOF_INCOMPLETE
    assert saved.grades.known_gaps == ('no checked proof',)
