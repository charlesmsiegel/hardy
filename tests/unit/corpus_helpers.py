"""Build a corpus *directory* for tests that used to write one problems file.

`load_corpus` reads `problems/<NN>.json` plus the id registry and the
changelog, so a test fixture that wants three entries has to lay out the same
shape the shipped corpus has -- otherwise it is exercising a loader nobody
runs. Entries are filed by `entry.shard`, which is where they must be found.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from hardy.evals.problems import Entry

VERSION = "0.1.0"


def write_corpus(root: Path, entries: tuple[Entry, ...], *, version: str = VERSION) -> Path:
    """Write `entries` as a loadable corpus under `root`; return `root`."""
    shards: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        shards[entry.shard].append(entry.model_dump(mode="json"))
    (root / "problems").mkdir(parents=True, exist_ok=True)
    for shard, rows in shards.items():
        (root / "problems" / f"{shard}.json").write_text(
            json.dumps({"schema_version": 2, "corpus_version": version, "entries": rows}),
            encoding="utf-8",
        )
    (root / "tombstones.json").write_text(
        json.dumps({"schema_version": 1, "issued": {e.id: "2026-09-03" for e in entries}}),
        encoding="utf-8",
    )
    _changelog(root, version)
    return root


def _changelog(root: Path, version: str) -> None:
    """Written last: the head binds the manifest digest of the content above."""
    from hardy.evals.corpus import manifest_digest

    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## {version} - 2026-09-03 - manifest {manifest_digest(root)}\n"
        "\n- test corpus\n",
        encoding="utf-8")
