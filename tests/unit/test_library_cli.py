"""`hardy library` imports, inspects, confirms and seeds without inferring anything."""

from __future__ import annotations

from pdf_helpers import book_pages, build_pdf

from hardy.app import config as config_module
from hardy.app.cli import build_parser
from hardy.app.library import main
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.seeds import SeedStore


def run(argv, config, library):
    args = build_parser().parse_args(argv)
    return main(args, config, library=library)


def make_config(tmp_path):
    root = tmp_path / "root"
    (root / "main").mkdir(parents=True)
    return config_module.load(tmp_path / "config.toml", root=root, project="main")


def test_import_list_show_tree_map_and_seed(tmp_path, capsys):
    library = ManagedLibrary(tmp_path / "library")
    config = make_config(tmp_path)
    pdf = tmp_path / "tiny.pdf"
    pdf.write_bytes(build_pdf(book_pages(), title="Tiny Algebra", author="A. Author"))
    assert run(["library", "import", str(pdf)], config, library) == 0
    out = capsys.readouterr().out
    assert "artifact " in out and "extraction ok" in out and "proposed edition" in out and "not authoritative" in out
    sha = library.artifacts.stored()[0]
    assert run(["library", "list"], config, library) == 0
    assert "(edition not confirmed)" in capsys.readouterr().out
    assert run(["library", "show", sha[:10]], config, library) == 0
    shown = capsys.readouterr().out
    assert "candidate edition" in shown and "not authoritatively grouped" in shown
    edition = library.catalog.candidates_for(sha)[0].edition
    assert run(["library", "confirm", sha[:10], edition, "--reason", "I own it"], config, library) == 0
    assert library.catalog.edition_of(sha).id == edition
    assert run(["library", "tree", sha[:10]], config, library) == 0
    assert "nodes" in capsys.readouterr().out
    assert run(["library", "map", sha[:10], "--depth", "3"], config, library) == 0
    mapped = capsys.readouterr().out
    assert "Tiny Algebra" in mapped and "theorem 1.2" in mapped
    assert run(["library", "seed", sha[:10], "--priority", "3", "--intent", "background"], config, library) == 0
    seeds = SeedStore(config.layout.problem).seeds()
    assert len(seeds) == 1 and seeds[0].artifact_sha256 == sha and seeds[0].priority == 3 and seeds[0].edition == edition
    assert run(["library", "seeds"], config, library) == 0
    assert seeds[0].id in capsys.readouterr().out
    assert run(["library", "unseed", seeds[0].id], config, library) == 0
    assert SeedStore(config.layout.problem).seeds() == ()


def test_refusals_exit_nonzero(tmp_path, capsys):
    library = ManagedLibrary(tmp_path / "library")
    config = make_config(tmp_path)
    assert run(["library", "import", str(tmp_path / "missing.pdf")], config, library) == 1
    assert "import refused" in capsys.readouterr().out
    assert run(["library", "show", "zzz"], config, library) == 1
    assert run(["library", "unseed", "seed-nope"], config, library) == 1
    assert run(["library", "list"], config, library) == 0
    assert "no artifacts" in capsys.readouterr().out


def test_confirm_refuses_unknown_edition(tmp_path, capsys):
    library = ManagedLibrary(tmp_path / "library")
    config = make_config(tmp_path)
    pdf = tmp_path / "tiny.pdf"
    pdf.write_bytes(build_pdf(book_pages()))
    run(["library", "import", str(pdf), "--no-extract"], config, library)
    sha = library.artifacts.stored()[0]
    assert run(["library", "confirm", sha[:10], "edition-missing"], config, library) == 1
    assert "not in the catalog" in capsys.readouterr().out


def test_report_prints_counts_per_dimension(tmp_path, capsys):
    import json as _json

    library = ManagedLibrary(tmp_path / "library")
    config = make_config(tmp_path)
    pdf = tmp_path / "tiny.pdf"
    pdf.write_bytes(build_pdf(book_pages(), title="Tiny Algebra"))
    run(["library", "import", str(pdf)], config, library)
    capsys.readouterr()
    assert run(["library", "report"], config, library) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["sources"]["artifacts"] == 1 and payload["semantics"]["claims"] == 0
    assert "extraction_quality" in payload["sources"] and "links_by_status" in payload["semantics"]


def test_seeding_refuses_a_symlinked_problem_directory(tmp_path, capsys):
    import pytest

    library = ManagedLibrary(tmp_path / "library")
    config = make_config(tmp_path)
    pdf = tmp_path / "tiny.pdf"
    pdf.write_bytes(build_pdf(book_pages()))
    run(["library", "import", str(pdf), "--no-extract"], config, library)
    sha = library.artifacts.stored()[0]
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = config.root / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    hostile = config_module.load(tmp_path / "config.toml", root=config.root, project="linked")
    capsys.readouterr()
    assert run(["library", "seed", sha[:10]], hostile, library) == 1
    assert "refusing to touch seeds" in capsys.readouterr().out
    assert not (outside / "sources").exists()
