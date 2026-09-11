"""TeX source trees: exact admitted files, their inclusion structure, and an assembled reading.

A TeX source is admitted as one artifact whose bytes are a tar or gzip
archive (an arXiv e-print, or a directory packed canonically by
`pack_directory`), unpacked under `archives.py`'s hostile-input rules and
never executed or compiled. The native representation keeps every text file
by path with its digest; the assembled representation walks the root and
its `\\input`s in the order the statement inventory already uses and maps
each file's range back to its `NativeSourceSpan`. Structural observations
come from the source's own environments, sectioning, labels and references,
so a tree built from them needs no model. Theorem numbers only compilation
would print are never inferred: a `theorem` environment has a label when it
carries one and no number otherwise.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from hardy.foundation.values import json_digest
from hardy.literature import archives
from hardy.literature.statements import AssumeError, _pages, root_of

from .adapters import ExtractionBudget, ExtractionRefused, ExtractionResult
from .contracts import (
    DerivedRepresentation,
    Diagnostic,
    NativeSourceSpan,
    ObservationKind,
    QualityProfile,
    RepresentationKind,
    RepresentationMapping,
    RepresentationSpan,
    SourceAnchor,
    SourceArtifact,
    SourceFormat,
    StructuralObservation,
)
from .representations import TEXT_PAYLOAD, payload_digest

NAME = "hardy.tex.native"
VERSION = "1"
SOURCES_PAYLOAD = "sources.json"
MANIFEST_PAYLOAD = "files.json"
FILE_SEPARATOR = "\f"
STATEMENT_ENVS = {"theorem", "lemma", "proposition", "corollary", "definition", "claim", "conjecture", "example", "exercise", "remark",
                  "thm", "lem", "prop", "cor", "defn", "defi", "conj", "construction", "solution"}
ALIASES = {"thm": "theorem", "lem": "lemma", "prop": "proposition", "cor": "corollary", "defn": "definition", "defi": "definition", "conj": "conjecture"}
DISPLAY_ENVS = {"equation", "equation*", "align", "align*", "gather", "gather*", "multline", "multline*", "figure", "figure*", "table", "table*", "tikzcd"}
BEGIN = re.compile(r"\\begin\{(?P<env>[A-Za-z*]+)\}(?:\[(?P<name>[^\]]*)\])?")
END = re.compile(r"\\end\{(?P<env>[A-Za-z*]+)\}")
SECTION = re.compile(r"\\(?P<level>part|chapter|section|subsection|subsubsection)\*?\{(?P<title>[^}]*)\}")
LABEL = re.compile(r"\\label\{(?P<label>[^}]*)\}")
REF = re.compile(r"\\(?:eq|c|C|auto|name)?ref\{(?P<label>[^}]*)\}")
CITE = re.compile(r"\\cite[a-zA-Z]*(?:\[[^\]]*\])?\{(?P<keys>[^}]*)\}")
LEVELS = {"part": "part", "chapter": "chapter", "section": "section", "subsection": "subsection", "subsubsection": "subsection"}


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def pack_directory(directory: Path, *, limits: archives.Limits = archives.DEFAULT_LIMITS) -> bytes:
    """A canonical tar of a directory's regular files: identical trees pack to identical bytes.

    Symlinks, special files and hidden version-control directories are
    refused rather than followed, and every member path goes through the
    same path rule the unpacker enforces.
    """
    root = Path(directory)
    if not root.is_dir() or root.is_symlink():
        raise ExtractionRefused(f"{root} is not a directory")
    members: list[tuple[str, Path]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if any(part in {".git", ".hg", ".svn", "__pycache__"} for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise ExtractionRefused(f"{relative} is a symlink; a source tree is packed from regular files only")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ExtractionRefused(f"{relative} is not a regular file")
        members.append((archives.member_path(relative, limits), path))
    if not members:
        raise ExtractionRefused(f"{root} holds no files")
    if len(members) > limits.max_files:
        raise ExtractionRefused(f"{root} holds more than {limits.max_files} files")
    buffer = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, path in members:
            data = path.read_bytes()
            total += len(data)
            if len(data) > limits.max_file_bytes or total > limits.max_total_bytes:
                raise ExtractionRefused(f"{name} exceeds the source tree byte bounds")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class TexAdapter:
    name = NAME
    version = VERSION

    def __init__(self, *, limits: archives.Limits = archives.DEFAULT_LIMITS) -> None:
        self.limits = limits

    def handles(self, artifact: SourceArtifact) -> bool:
        return artifact.format is SourceFormat.TEX_TREE

    def extract(self, artifact: SourceArtifact, data: bytes, *, budget: ExtractionBudget) -> ExtractionResult:
        diagnostics: list[Diagnostic] = []
        files, manifest = self._unpack(data)
        texts = {path: text for path, text in files.items()}
        try:
            root = root_of(texts)
            pages = _pages(texts, root)
        except AssumeError as error:
            diagnostics.append(Diagnostic(code="no_document_root", detail=str(error), severity="warning"))
            root = None
            pages = [(path, texts[path]) for path in sorted(texts)]
        if not pages:
            raise ExtractionRefused("the source tree holds no readable TeX")
        native_payload = {
            SOURCES_PAYLOAD: json.dumps(texts, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            MANIFEST_PAYLOAD: json.dumps(manifest, sort_keys=True).encode("utf-8"),
        }
        native_id = f"native_source-{json_digest([self.name, self.version])[:12]}"
        assembled_id = f"normalized_text-{json_digest([self.name, self.version, 'assembled'])[:12]}"
        stamp = _stamp()
        native = DerivedRepresentation(
            id=native_id, artifact_sha256=artifact.sha256, kind=RepresentationKind.NATIVE_SOURCE, extractor=self.name, extractor_version=self.version,
            output_sha256=payload_digest(native_payload), derived_at=stamp, quality=QualityProfile(status="ok", coverage=1.0),
            access=artifact.access, payload_files=tuple(native_payload),
        )
        assembled_parts: list[str] = []
        pairs: list[tuple[NativeSourceSpan, RepresentationSpan]] = []
        offset = 0
        for index, (path, text) in enumerate(pages):
            start = offset
            assembled_parts.append(text)
            offset += len(text)
            pairs.append((NativeSourceSpan(representation=native_id, path=path, start=0, end=len(text)),
                          RepresentationSpan(representation=assembled_id, start=start, end=offset)))
            if index + 1 < len(pages):
                assembled_parts.append(FILE_SEPARATOR)
                offset += 1
        assembled_text = "".join(assembled_parts)
        if len(assembled_text.encode("utf-8")) > budget.max_text_bytes:
            raise ExtractionRefused(f"assembled source exceeds the {budget.max_text_bytes} byte bound")
        assembled_payload = {TEXT_PAYLOAD: assembled_text.encode("utf-8")}
        unread = sorted(set(texts) - {path for path, _ in pages})
        if unread:
            diagnostics.append(Diagnostic(code="files_not_reached", detail=f"{len(unread)} text file(s) are not reached from the root: {', '.join(unread[:8])}", severity="info"))
        assembled = DerivedRepresentation(
            id=assembled_id, artifact_sha256=artifact.sha256, kind=RepresentationKind.NORMALIZED_TEXT, extractor=self.name, extractor_version=self.version,
            configuration=(("assembly", "root-and-inputs"), ("root", root or "")), inputs=(native_id,), output_sha256=payload_digest(assembled_payload),
            derived_at=stamp, quality=QualityProfile(status="ok" if not unread else "partial", coverage=len(pages) / max(1, len(texts)), diagnostics=tuple(diagnostics)),
            access=artifact.access, payload_files=tuple(assembled_payload),
        )
        mapping = RepresentationMapping(id=f"map-{native_id}-{assembled_id}", artifact_sha256=artifact.sha256, left=native_id, right=assembled_id,
                                        pairs=tuple(pairs), partial=bool(unread), producer=f"{self.name}/{self.version}")
        observations = observe_tex(assembled, assembled_text, artifact.sha256)
        metadata = _metadata(assembled_text)
        return ExtractionResult(representations=((native, native_payload), (assembled, assembled_payload)), mappings=(mapping,),
                                observations=observations, metadata=metadata, diagnostics=tuple(diagnostics))

    def _unpack(self, data: bytes) -> tuple[dict[str, str], list[dict]]:
        """Text files by path and a manifest, from an archive or a single TeX file. Nothing runs."""
        try:
            kind = archives.kind_of(data)
        except archives.ArchiveError:
            kind = None
        if kind is None:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ExtractionRefused("the source is neither an archive nor UTF-8 text") from error
            return {"main.tex": text}, [{"path": "main.tex", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "text": True}]
        if kind == "pdf":
            raise ExtractionRefused("a PDF is not a TeX source tree")
        with tempfile.TemporaryDirectory(prefix="hardy-tex-") as temporary:
            into = Path(temporary)
            try:
                extraction = archives.extract(data, into, limits=self.limits)
            except archives.ArchiveError as error:
                raise ExtractionRefused(f"the source archive was refused: {error}") from error
            texts: dict[str, str] = {}
            manifest = []
            for member in extraction.files:
                manifest.append({"path": member.path, "size": member.size, "sha256": member.sha256, "text": member.text})
                if member.text:
                    texts[member.path] = (into / member.path).read_bytes().decode("utf-8", errors="replace")
        if not texts:
            raise ExtractionRefused("the source archive holds no text files")
        return texts, manifest


def observe_tex(representation: DerivedRepresentation, text: str, artifact_sha256: str) -> tuple[StructuralObservation, ...]:
    """Observations from TeX's own structure over the assembled reading."""
    rep = representation.id
    found: list[StructuralObservation] = []

    def add(kind: ObservationKind, start: int, end: int, payload: tuple[tuple[str, str], ...], derivation: str, confidence: float = 1.0) -> None:
        found.append(StructuralObservation(
            id="obs-" + hashlib.sha256(f"{artifact_sha256}|tex|{kind.value}|{start}|{end}".encode()).hexdigest()[:16], artifact_sha256=artifact_sha256,
            kind=kind, anchor=SourceAnchor(artifact_sha256=artifact_sha256, locator=RepresentationSpan(representation=rep, start=start, end=end), derivation=derivation),
            payload=payload, producer=NAME, producer_version=VERSION, confidence=confidence,
        ))

    for match in SECTION.finditer(text):
        add(ObservationKind.HEADING, match.start(), match.end(), (("level", LEVELS[match.group("level")]), ("number", ""), ("title", match.group("title").strip())), "sectioning command")
    open_envs: list[tuple[str, int, int, str | None]] = []
    for match in re.finditer(r"\\begin\{(?P<env>[A-Za-z*]+)\}(?:\[(?P<name>[^\]]*)\])?|\\end\{(?P<env2>[A-Za-z*]+)\}", text):
        if match.group("env") is not None:
            env = match.group("env")
            base = ALIASES.get(env.rstrip("*"), env.rstrip("*"))
            if base in STATEMENT_ENVS:
                label = LABEL.search(text, match.end(), min(len(text), match.end() + 400))
                add(ObservationKind.STATEMENT_START, match.start(), match.end(),
                    (("kind", ALIASES.get(base, base)), ("number", ""), ("number_origin", "none"), ("name", match.group("name") or ""),
                     ("label", label.group("label") if label else ""), ("environment", env)), "theorem-like environment")
            elif base == "proof":
                add(ObservationKind.PROOF_START, match.start(), match.end(), (("label", "Proof"), ("environment", env)), "proof environment")
            elif env in DISPLAY_ENVS:
                open_envs.append((env, match.start(), match.end(), None))
        else:
            env = match.group("env2")
            if env == "proof":
                add(ObservationKind.PROOF_END, match.start(), match.end(), (("marker", "\\end{proof}"),), "proof environment end")
            elif env in DISPLAY_ENVS and open_envs and open_envs[-1][0] == env:
                _, start, _, _ = open_envs.pop()
                kind = "figure" if env.startswith("figure") else "table" if env.startswith("table") else "diagram" if env == "tikzcd" else "equation"
                add(ObservationKind.ENVIRONMENT, start, match.end(), (("kind", kind), ("environment", env)), "display environment")
    for match in LABEL.finditer(text):
        add(ObservationKind.LABEL, match.start(), match.end(), (("label", match.group("label")),), "label command")
    for match in REF.finditer(text):
        add(ObservationKind.REFERENCE, match.start(), match.end(), (("kind", "label"), ("number", match.group("label"))), "reference command", 0.9)
    for match in CITE.finditer(text):
        add(ObservationKind.REFERENCE, match.start(), match.end(), (("kind", "citation"), ("number", match.group("keys"))), "citation command", 0.9)
    for index, _ in enumerate(text.split(FILE_SEPARATOR)):
        if index > 0:
            position = _nth_separator(text, index)
            add(ObservationKind.PAGE_BREAK, position, position + 1, (("file_boundary", str(index)),), "file boundary in the assembled reading")
    return tuple(sorted(found, key=lambda o: (o.anchor.locator.start, o.kind.value)))  # type: ignore[union-attr]


def _nth_separator(text: str, n: int) -> int:
    position = -1
    for _ in range(n):
        position = text.find(FILE_SEPARATOR, position + 1)
    return position


def _metadata(text: str) -> tuple[tuple[str, str], ...]:
    found = []
    title = re.search(r"\\title\{([^}]*)\}", text)
    author = re.search(r"\\author\{([^}]*)\}", text)
    if title and title.group(1).strip():
        found.append(("title", re.sub(r"\s+", " ", title.group(1)).strip()))
    if author and author.group(1).strip():
        found.append(("author", re.sub(r"\\and", ";", author.group(1)).strip()))
    return tuple(found)
