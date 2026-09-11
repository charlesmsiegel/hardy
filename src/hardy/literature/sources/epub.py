"""EPUB and HTML: native DOM structure kept, normalized text mapped back to it.

An EPUB is a zip unpacked under the hostile-archive rules; its container and
package documents give the spine, which is the reading order the source
itself declares. Each spine item's XHTML is scanned with the tolerant
standard-library parser into block nodes with DOM paths and ids, and the
normalized text is built from those blocks so every block's range maps to a
`DOMLocator`. Headings, ids and same-document links become observations
from the document's own structure, so a tree needs no model. A single HTML
file is the same pipeline with one spine item. XML with a DOCTYPE is
refused rather than parsed, the same rule the arXiv feed reader applies.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from hardy.foundation.values import json_digest
from hardy.literature import archives

from .adapters import ExtractionBudget, ExtractionRefused, ExtractionResult
from .contracts import (
    DerivedRepresentation,
    Diagnostic,
    DOMLocator,
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

NAME = "hardy.epub.native"
VERSION = "1"
DOM_PAYLOAD = "dom.json"
ITEM_SEPARATOR = "\f"
BLOCKS = {"p", "div", "section", "article", "li", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "figure", "figcaption", "table", "dd", "dt", "aside", "header", "footer", "main"}
SKIP = {"script", "style", "head", "title", "svg", "math"}
HEADINGS = {"h1": "chapter", "h2": "section", "h3": "subsection", "h4": "subsection", "h5": "subsection", "h6": "subsection"}
NUMBERED = re.compile(r"^(?:(?:Chapter|Part)\s+)?(?P<number>[0-9IVXLC]+(?:\.[0-9]+)*)\.?\s+(?P<title>.+)$")
NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container", "opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse_xml(data: bytes, what: str) -> ET.Element:
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise ExtractionRefused(f"{what} declares a DOCTYPE or entities; refusing to parse it")
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise ExtractionRefused(f"{what} is not well-formed XML: {error}") from error


class _Blocks(HTMLParser):
    """Block-level text with DOM paths, from tolerant parsing of one XHTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.counts: list[dict[str, int]] = [{}]
        self.nodes: list[dict] = []
        self.text: list[str] = []
        self.length = 0
        self.open_blocks: list[dict] = []
        self.skipping = 0
        self.links: list[tuple[str, int]] = []

    def _path(self) -> str:
        return "/".join(f"{tag}[{index}]" for tag, index in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        counts = self.counts[-1]
        counts[tag] = counts.get(tag, 0) + 1
        self.stack.append((tag, counts[tag]))
        self.counts.append({})
        if tag in SKIP:
            self.skipping += 1
            return
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href", "").startswith("#"):
            self.links.append((attributes["href"][1:], self.length))
        if tag in BLOCKS or attributes.get("id"):
            if self.length and not "".join(self.text).endswith("\n"):
                self.text.append("\n")
                self.length += 1
            node = {"path": self._path(), "tag": tag, "id": attributes.get("id"), "start": self.length, "end": None, "depth": len(self.stack)}
            self.nodes.append(node)
            self.open_blocks.append(node)
        elif tag == "br":
            self.text.append("\n")
            self.length += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP and self.skipping:
            self.skipping -= 1
        while self.stack and self.stack[-1][0] != tag:
            self._close_block()
            self.stack.pop()
            self.counts.pop()
        if self.stack and self.stack[-1][0] == tag:
            self._close_block()
            self.stack.pop()
            self.counts.pop()

    def _close_block(self) -> None:
        depth = len(self.stack)
        while self.open_blocks and self.open_blocks[-1]["depth"] >= depth:
            node = self.open_blocks.pop()
            node["end"] = self.length
            if node["tag"] in BLOCKS and self.length and not "".join(self.text[-1:]).endswith("\n"):
                self.text.append("\n")
                self.length += 1

    def handle_data(self, data: str) -> None:
        if self.skipping:
            return
        cleaned = re.sub(r"\s+", " ", data)
        if not cleaned.strip():
            if self.text and not self.text[-1].endswith((" ", "\n")) and cleaned:
                self.text.append(" ")
                self.length += 1
            return
        if self.text and self.text[-1].endswith("\n"):
            cleaned = cleaned.lstrip()
        self.text.append(cleaned)
        self.length += len(cleaned)

    def finish(self) -> None:
        while self.stack:
            self._close_block()
            self.stack.pop()
            self.counts.pop()
        for node in self.nodes:
            if node["end"] is None:
                node["end"] = self.length


def scan_html(source: str) -> tuple[str, list[dict], list[tuple[str, int]]]:
    parser = _Blocks()
    parser.feed(source)
    parser.close()
    parser.finish()
    return "".join(parser.text), parser.nodes, parser.links


class EpubAdapter:
    name = NAME
    version = VERSION

    def __init__(self, *, limits: archives.Limits = archives.DEFAULT_LIMITS) -> None:
        self.limits = limits

    def handles(self, artifact: SourceArtifact) -> bool:
        return artifact.format in {SourceFormat.EPUB, SourceFormat.HTML}

    def extract(self, artifact: SourceArtifact, data: bytes, *, budget: ExtractionBudget) -> ExtractionResult:
        diagnostics: list[Diagnostic] = []
        if artifact.format is SourceFormat.HTML:
            items = [("document", data.decode("utf-8", errors="replace"))]
            metadata = _html_metadata(items[0][1])
        else:
            items, metadata, diagnostics = self._spine(data)
        if not items:
            raise ExtractionRefused("no readable spine items")
        dom_id = f"dom-{json_digest([self.name, self.version])[:12]}"
        text_id = f"normalized_text-{json_digest([self.name, self.version, 'dom'])[:12]}"
        stamp = _stamp()
        dom_items = []
        parts: list[str] = []
        pairs = []
        observations: list[StructuralObservation] = []
        offset = 0
        for index, (href, source) in enumerate(items):
            text, nodes, links = scan_html(source)
            if len(text) + offset > budget.max_text_bytes:
                raise ExtractionRefused(f"normalized text exceeds the {budget.max_text_bytes} byte bound")
            base = offset
            parts.append(text)
            offset += len(text)
            ids: dict[str, int] = {}
            for node in nodes:
                locator = DOMLocator(representation=dom_id, spine_item=href, node_path=node["path"])
                pairs.append((locator, RepresentationSpan(representation=text_id, start=base + node["start"], end=base + node["end"])))
                if node["id"]:
                    ids[node["id"]] = base + node["start"]
                    observations.append(_observation(artifact.sha256, ObservationKind.LABEL, text_id, base + node["start"], base + node["start"],
                                                     (("label", node["id"]), ("path", node["path"])), "element id"))
                if node["tag"] in HEADINGS:
                    title = text[node["start"]:node["end"]].strip()
                    match = NUMBERED.match(title)
                    observations.append(_observation(artifact.sha256, ObservationKind.HEADING, text_id, base + node["start"], base + node["end"],
                                                     (("level", HEADINGS[node["tag"]]), ("number", match.group("number") if match else ""),
                                                      ("title", match.group("title") if match else title)), f"{node['tag']} heading"))
            for target, position in links:
                observations.append(_observation(artifact.sha256, ObservationKind.REFERENCE, text_id, base + position, base + position,
                                                 (("kind", "label"), ("number", target)), "same-document link", 0.9))
            dom_items.append({"item": href, "source": source, "nodes": nodes})
            if index + 1 < len(items):
                parts.append(ITEM_SEPARATOR)
                observations.append(_observation(artifact.sha256, ObservationKind.PAGE_BREAK, text_id, offset, offset + 1, (("spine_boundary", str(index + 1)),), "spine item boundary"))
                offset += 1
        normalized_text = "".join(parts)
        dom_payload = {DOM_PAYLOAD: json.dumps(dom_items, ensure_ascii=False, sort_keys=True).encode("utf-8")}
        text_payload = {TEXT_PAYLOAD: normalized_text.encode("utf-8")}
        dom = DerivedRepresentation(id=dom_id, artifact_sha256=artifact.sha256, kind=RepresentationKind.DOM, extractor=self.name, extractor_version=self.version,
                                    output_sha256=payload_digest(dom_payload), derived_at=stamp, quality=QualityProfile(status="ok", coverage=1.0, diagnostics=tuple(diagnostics)),
                                    access=artifact.access, payload_files=tuple(dom_payload))
        normalized = DerivedRepresentation(id=text_id, artifact_sha256=artifact.sha256, kind=RepresentationKind.NORMALIZED_TEXT, extractor=self.name,
                                           extractor_version=self.version, inputs=(dom_id,), output_sha256=payload_digest(text_payload), derived_at=stamp,
                                           quality=QualityProfile(status="ok", coverage=1.0), access=artifact.access, payload_files=tuple(text_payload))
        mapping = RepresentationMapping(id=f"map-{dom_id}-{text_id}", artifact_sha256=artifact.sha256, left=dom_id, right=text_id, pairs=tuple(pairs),
                                        partial=True, producer=f"{self.name}/{self.version}")
        observations.sort(key=lambda o: (o.anchor.locator.start, o.kind.value))  # type: ignore[union-attr]
        return ExtractionResult(representations=((dom, dom_payload), (normalized, text_payload)), mappings=(mapping,),
                                observations=tuple(observations), metadata=metadata, diagnostics=tuple(diagnostics))

    def _spine(self, data: bytes) -> tuple[list[tuple[str, str]], tuple[tuple[str, str], ...], list[Diagnostic]]:
        diagnostics: list[Diagnostic] = []
        with tempfile.TemporaryDirectory(prefix="hardy-epub-") as temporary:
            into = Path(temporary)
            try:
                extraction = archives.extract(data, into, limits=self.limits)
            except archives.ArchiveError as error:
                raise ExtractionRefused(f"the EPUB was refused: {error}") from error
            members = {f.path: f for f in extraction.files}
            if "META-INF/container.xml" not in members:
                raise ExtractionRefused("the EPUB has no META-INF/container.xml")
            container = _parse_xml((into / "META-INF/container.xml").read_bytes(), "container.xml")
            rootfile = container.find(".//c:rootfile", NS)
            if rootfile is None or not rootfile.get("full-path"):
                raise ExtractionRefused("the EPUB container names no package document")
            opf_path = PurePosixPath(rootfile.get("full-path"))
            if opf_path.as_posix() not in members:
                raise ExtractionRefused(f"the package document {opf_path} is not in the archive")
            package = _parse_xml((into / opf_path).read_bytes(), "the package document")
            manifest = {item.get("id"): (item.get("href"), item.get("media-type", "")) for item in package.findall(".//opf:manifest/opf:item", NS)}
            spine = [ref.get("idref") for ref in package.findall(".//opf:spine/opf:itemref", NS)]
            items: list[tuple[str, str]] = []
            for idref in spine:
                href, media = manifest.get(idref, (None, ""))
                if href is None:
                    diagnostics.append(Diagnostic(code="spine_item_missing", detail=f"spine names {idref!r}, absent from the manifest", severity="warning"))
                    continue
                relative = (opf_path.parent / href).as_posix() if opf_path.parent != PurePosixPath(".") else href
                relative = PurePosixPath(relative).as_posix()
                if relative not in members or not members[relative].text:
                    diagnostics.append(Diagnostic(code="spine_item_unreadable", detail=f"{relative} is missing or not text", severity="warning"))
                    continue
                items.append((relative, (into / relative).read_bytes().decode("utf-8", errors="replace")))
            metadata = []
            for tag, key in (("title", "title"), ("creator", "author"), ("identifier", "identifier"), ("publisher", "venue"), ("date", "year")):
                element = package.find(f".//opf:metadata/dc:{tag}", NS)
                if element is not None and element.text and element.text.strip():
                    value = element.text.strip()
                    if key == "identifier" and re.search(r"isbn", value, re.I):
                        metadata.append(("isbn", re.sub(r"(?i)^urn:isbn:", "", value)))
                    elif key == "identifier":
                        continue
                    elif key == "year":
                        metadata.append(("year", value[:4]))
                    else:
                        metadata.append((key, value))
        return items, tuple(metadata), diagnostics


def _observation(sha: str, kind: ObservationKind, rep: str, start: int, end: int, payload: tuple[tuple[str, str], ...], derivation: str, confidence: float = 1.0) -> StructuralObservation:
    return StructuralObservation(
        id="obs-" + hashlib.sha256(f"{sha}|epub|{kind.value}|{start}|{end}|{payload}".encode()).hexdigest()[:16], artifact_sha256=sha, kind=kind,
        anchor=SourceAnchor(artifact_sha256=sha, locator=RepresentationSpan(representation=rep, start=start, end=end), derivation=derivation),
        payload=payload, producer=NAME, producer_version=VERSION, confidence=confidence,
    )


def _html_metadata(source: str) -> tuple[tuple[str, str], ...]:
    title = re.search(r"<title[^>]*>(.*?)</title>", source, re.I | re.S)
    return (("title", re.sub(r"\s+", " ", title.group(1)).strip()),) if title and title.group(1).strip() else ()
