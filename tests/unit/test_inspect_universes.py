"""Lean prints a universe-polymorphic declaration with its universes attached.

`inspect_declarations` tested `message.startswith(f"{name} ")` and so reported
almost every Mathlib declaration as unavailable: Lean writes
`IsCyclic.{u} (G : Type u) [Pow G Int] : Prop`, and the space never comes where
that test wanted it. Nearly everything in Mathlib about types is
universe-polymorphic, so the one declaration search that does not hang was
answering "no such declaration" about declarations that exist -- a live session
was told `IsSimpleGroup` was unavailable and went on to assume classical
theorems Mathlib proves.
"""

from __future__ import annotations

import importlib

lean = importlib.import_module("hardy.lean")


def _service(messages: list[str]):
    """A `LeanService` whose scratch check returns exactly these diagnostics."""
    service = lean.LeanService.__new__(lean.LeanService)

    def check_scratch(source: str):
        return lean.LeanCheckResult(
            success=True,
            diagnostics=tuple(
                lean.LeanDiagnostic(severity="information", message=text, line=index + 3)
                for index, text in enumerate(messages)
            ),
            open_goals=(),
            process=lean.ProcessResult(
                returncode=0,
                stdout="",
                stderr="",
                duration_ms=1,
                timed_out=False,
                output_overflow=False,
                argv=("lean",),
                cwd=".",
            ),
            source_sha256="",
            toolchain=lean.EnvironmentIdentity(
                lean_version="x",
                lean_commit="c" * 40,
                mathlib_revision="y",
                lake_manifest_sha256="z" * 64,
            ),
        )

    service.check_scratch = check_scratch  # type: ignore[method-assign]
    return service


def test_a_universe_polymorphic_declaration_resolves() -> None:
    service = _service(["IsSimpleGroup.{u_1} (G : Type u_1) [Group G] : Prop"])

    found = service.inspect_declarations(("IsSimpleGroup",))

    assert [record.name for record in found.resolved] == ["IsSimpleGroup"]
    assert found.unavailable == ()


def test_a_monomorphic_declaration_still_resolves() -> None:
    service = _service(["Nat.succ (n : Nat) : Nat"])

    found = service.inspect_declarations(("Nat.succ",))

    assert [record.name for record in found.resolved] == ["Nat.succ"]


def test_a_declaration_whose_signature_starts_with_a_colon_resolves() -> None:
    service = _service(["mul_comm.{u_1} : forall {G : Type u_1}, True"])

    found = service.inspect_declarations(("mul_comm",))

    assert [record.name for record in found.resolved] == ["mul_comm"]


def test_a_name_that_really_is_absent_is_still_unavailable() -> None:
    """The fix must not turn every question into a yes. `Group.IsSimple` does
    not exist; a live session guessed it and concluded Mathlib lacked simple
    groups entirely."""
    service = _service(["IsSimpleGroup.{u_1} (G : Type u_1) [Group G] : Prop"])

    found = service.inspect_declarations(("Group.IsSimple",))

    assert found.resolved == ()
    assert found.unavailable == ("Group.IsSimple",)


def test_a_longer_name_does_not_answer_for_a_shorter_one() -> None:
    """`IsSimpleGroup` must not be satisfied by `IsSimpleGroupFoo`'s line."""
    service = _service(["IsSimpleGroupFoo.{u_1} (G : Type u_1) : Prop"])

    found = service.inspect_declarations(("IsSimpleGroup",))

    assert found.unavailable == ("IsSimpleGroup",)
