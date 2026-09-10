"""Adapt a frozen publication plan to the existing mechanical document gate.

Theory: assembly copies recorded text and declares gaps; compilation can only
judge the resulting document, never upgrade mathematical evidence. The adapter
has no ledger/store access and never generates or refreshes human exposition.
Reused: documents.writeup escaping, LatexTools compilation/publication, and
WriteGuard artifact writes. Each output directory is created exclusively so a
second publication cannot silently overwrite a human-edited manuscript.
Assumes: callers supply a plan from PublicationPlanner and a trusted configured
compiler in a disposable development environment. This is not process isolation.
Watch: artifact-only exposition is identified but not dereferenced; this first
adapter renders recorded plain text and the planner's exact container structure,
not arbitrary linked TeX. Containers never introduce ambient hypotheses. Heading
depth follows exact ancestry; beyond LaTeX's levels, explicit paths retain it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hardy.documents.latex import LatexTools
from hardy.documents.writeup import escape_tex_text
from hardy.foundation.files import WriteGuard
from hardy.foundation.values import FrozenModel, ToolResult, json_digest
from hardy.workflows.ledger.contracts import ProjectItemKind
from hardy.workflows.publication import PublicationPlan


class PublicationDraft(FrozenModel):
    plan_digest: str
    title: str
    source: str
    gaps: tuple[str, ...]
    ready: bool


@dataclass(frozen=True)
class PublicationResult:
    draft: PublicationDraft
    output: Path
    compilation: ToolResult


def assemble_publication(plan: PublicationPlan, *, title: str = "Selected results") -> PublicationDraft:
    """Copy exact statements/exposition into escaped text with attributed gaps."""
    if not title.strip():
        raise ValueError("publication title must not be blank")
    identity = json_digest({"schema": "hardy.publication-plan/v1", "plan": plan.model_dump(mode="json")})
    gaps = [f"Unestablished mathematics: {r.id}@{r.digest}" for r in plan.unestablished]
    gaps.extend(f"Open obligation {o.id}@{o.digest}: {o.reason or o.kind.value}" for o in plan.obligations)
    gaps.extend(f"Unchecked citation contract: {r.id}@{r.digest}" for r in plan.citations_open)
    gaps.extend(f"Missing exposition: {r.id}@{r.digest}" for r in plan.missing_exposition)
    gaps.extend(f"Stale exposition {p.prose.id}: documents {p.documented.id}@{p.documented.digest}; "
                f"selected {p.target.id}@{p.target.digest}" for p in plan.stale_exposition)
    for passage in plan.exposition:
        if passage.prose.statement is None:
            artifacts = "; ".join(f"{a.uri}@{a.digest}" for a in passage.prose.artifacts)
            gaps.append(f"Exposition text unavailable for {passage.prose.id}: {artifacts or 'no text artifact'}")
    if not plan.ready and not gaps:
        gaps.append("Selected material is not eligible for publication readiness.")

    kinds = {item.kind for item in plan.items}
    document_class = "book" if ProjectItemKind.BOOK in kinds else "report" if ProjectItemKind.CHAPTER in kinds else "article"
    lines = [r"\documentclass{" + document_class + "}", r"\usepackage[T1]{fontenc}", r"\usepackage[utf8]{inputenc}",
             r"\usepackage{hyperref}", r"\begin{document}",
             r"\title{" + escape_tex_text(title) + "}", r"\author{}", r"\date{}", r"\maketitle"]

    def paragraph(text: str) -> None:
        lines.extend((escape_tex_text(text), ""))

    def heading(text: str, command: str = "section") -> None:
        lines.append("\\" + command + "*{" + escape_tex_text(text) + "}")

    def local_heading(text: str) -> None:
        lines.append(r"\par\medskip\noindent\textbf{" + escape_tex_text(text) + r"}\par")

    paragraph(f"Publication plan {identity}; ledger revision {plan.revision}.")
    paragraph("Draft assembled from recorded mathematics and author exposition. "
              "Document compilation is separate from kernel verification and independent prose review.")
    heading("Status and remaining work")
    if gaps:
        for gap in gaps:
            paragraph(gap)
    else:
        paragraph("The frozen plan reported no outstanding publication gaps when it was assembled.")

    contexts = {context.item: context for context in plan.contexts}
    placements = {p.item: p for p in plan.structure}
    by_ref = {item.ref: item for item in plan.items}
    ordered_items = tuple(by_ref[p.item] for p in plan.structure) if plan.structure else plan.items
    heading_commands = ("part", "chapter", "section", "subsection", "subsubsection", "paragraph", "subparagraph")
    container_levels = {ProjectItemKind.BOOK: 0, ProjectItemKind.CHAPTER: 1, ProjectItemKind.SECTION: 2}
    for item in ordered_items:
        label = f"{item.kind.value.replace('_', ' ').title()}: {item.name}"
        ancestors = placements[item.ref].containers if item.ref in placements else ()
        level = -1
        for ref in (*ancestors, item.ref):
            level = max(level + 1, container_levels.get(by_ref[ref].kind, 2))
        if level < len(heading_commands):
            heading(label, heading_commands[level])
        else:
            local_heading(label)
        paragraph("Document containers: " + (" / ".join(
            f"{by_ref[ref].name} [{ref.id}@{ref.digest}]" for ref in ancestors) or "top level"))
        paragraph(f"Source identity: {item.id}@{item.digest}")
        if item.publication_role is not None:
            paragraph(f"Publication role: {item.publication_role.value}")
        context = contexts.get(item.ref)
        if context is not None:
            local_heading("Required mathematical context")
            paragraph(f"Context: {context.context.id}@{context.context.digest}")
            for declaration in context.parameters:
                detail = declaration.declaration
                paragraph(f"{detail.role.value} {detail.symbol}: {detail.semantic_type} "
                          f"[{declaration.id}@{declaration.digest}]")
            for declaration in context.local_hypotheses:
                detail = declaration.declaration
                paragraph(f"Local hypothesis {detail.symbol}: {detail.semantic_type} "
                          f"[{declaration.id}@{declaration.digest}]")
            for binding in context.bindings:
                paragraph(f"{binding.kind.value} {binding.symbol}: {binding.meaning} "
                          f"[{binding.id}@{binding.digest}]")
                if binding.target is not None:
                    paragraph(f"Referent: {binding.target.id}@{binding.target.digest}")
        if item.statement is not None:
            paragraph(item.statement)
        elif item.kind not in container_levels:
            paragraph("No statement text recorded.")
        for name, value in item.semantics:
            paragraph(f"{name}: {value}")
        for artifact in item.artifacts:
            paragraph(f"Source artifact: {artifact.uri}@{artifact.digest}" +
                      (f" ({artifact.locator})" if artifact.locator else ""))
        for passage in plan.exposition:
            if passage.target == item.ref and passage.prose.statement is not None:
                local_heading("Exposition")
                paragraph(f"{passage.prose.origin.value}: {passage.prose.id}@{passage.prose.digest}; "
                          f"documents {passage.documented.id}@{passage.documented.digest}")
                paragraph(passage.prose.statement)

    if plan.citations:
        heading("Citation contracts")
        for citation in plan.citations:
            paragraph(f"{citation.paper_id}, version {citation.paper_version}; contract {citation.id}@{citation.digest}")
            paragraph(f"Used at {citation.use_site.id}@{citation.use_site.digest}; "
                      f"required claim {citation.required_claim.id}@{citation.required_claim.digest}")
            source = citation.source_statement
            paragraph(f"Source: {source.uri}@{source.digest}" + (f" ({source.locator})" if source.locator else ""))
            paragraph(f"Conclusion: {citation.conclusion}")
            for hypothesis in citation.source_hypotheses:
                paragraph(f"Source hypothesis: {hypothesis}")
            for mapping in citation.hypothesis_mapping:
                if mapping.obligation is not None:
                    paragraph(f"Hypothesis {mapping.hypothesis}: obligation "
                              f"{mapping.obligation.id}@{mapping.obligation.digest}")
    lines.append(r"\end{document}")
    return PublicationDraft(plan_digest=identity, title=title, source="\n".join(lines) + "\n",
                            gaps=tuple(gaps), ready=plan.ready and not gaps)


class PublishWorkflow:
    def __init__(self, latex: LatexTools):
        self.latex = latex

    def publish(self, plan: PublicationPlan, *, output: Path,
                title: str = "Selected results") -> PublicationResult:
        """Save an immutable draft bundle, then compile it through LatexTools.

        Failure leaves the exact draft and diagnostic available. Existing output
        directories, including previous generated bundles, must be given a new
        destination so human edits cannot be mistaken for replaceable output.
        """
        draft = assemble_publication(plan, title=title)
        output = Path(output).absolute()
        parent = WriteGuard(output.parent, create=True)
        parent.confirm()
        parent.path(output.name).mkdir()  # Exclusive creation also rejects links.
        guard = WriteGuard(output)
        guard.write_json("publication.json", {
            "schema": "hardy.publication-bundle/v1",
            "plan": plan.model_dump(mode="json"),
            "draft": draft.model_dump(mode="json"),
        })
        guard.write_bytes("writeup.tex", draft.source.encode("utf-8"))
        result = self.latex.check(draft.source, output_dir=output, aux_dir=output / "aux")
        guard.write_bytes("compile.log", result.output.encode("utf-8"))
        return PublicationResult(draft, output, result)
