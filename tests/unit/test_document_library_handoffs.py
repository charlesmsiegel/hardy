"""Document track's shared library selection regressions."""

from hardy.literature.sources.artifacts import ImportRequest
from hardy.literature.sources.contracts import NodeKind, RepresentationKind
from hardy.literature.sources.library import ManagedLibrary
from hardy.literature.sources.observations import observe_text


def test_tex_library_uses_native_file_boundaries_without_generic_formfeeds(tmp_path, monkeypatch):
    from hardy.literature.sources import library as owner
    from hardy.literature.sources.contracts import ObservationKind

    library = ManagedLibrary(tmp_path)
    report = library.import_source(ImportRequest(
        data=b"\\documentclass{article}\n\\begin{document}\nA\fB\n\\end{document}\n",
        original_name="main.tex",
    ))
    seen = []
    original = owner.build_tree

    def capture(artifact, representation, text, observations, **kwargs):
        seen.extend(observations)
        return original(artifact, representation, text, observations, **kwargs)

    monkeypatch.setattr(owner, "build_tree", capture)
    library.build_tree(report.outcome.artifact.sha256)
    assert not [o for o in seen if o.kind is ObservationKind.PAGE_BREAK]


def test_historical_text_observer_is_retained_but_not_replayed(tmp_path):
    library = ManagedLibrary(tmp_path)
    report = library.import_source(ImportRequest(
        data=b"Theorem 1. True.\nProof. Done.\n", original_name="paper.txt",
    ))
    sha = report.outcome.artifact.sha256
    representation = library.representations.list(sha, RepresentationKind.NORMALIZED_TEXT)[-1]
    before = library.build_tree(sha, representation=representation.id)
    old = observe_text(representation, library.representations.text(sha, representation.id), producer_version="historical")
    library.observations.admit(sha, "old-observer", old)
    after = library.build_tree(sha, representation=representation.id)
    assert [(n.kind, n.span) for n in after.nodes] == [(n.kind, n.span) for n in before.nodes]
    assert library.observations.get(sha, "old-observer") == old
    assert len([n for n in after.nodes if n.kind is NodeKind.THEOREM]) == 1
