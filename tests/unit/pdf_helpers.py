"""Build small, uncompressed PDFs by hand so tests own every byte they read.

`build_pdf` takes pages as lists of `(text, x, y)` fragments and writes a
one-font document with an optional `/PageLabels` tree, an optional outline,
and an optional image-only page. The xref table is computed, not guessed, so
pypdf reads the result without repair.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OutlineEntry:
    title: str
    page: int
    children: tuple[OutlineEntry, ...] = ()


@dataclass(frozen=True)
class Page:
    fragments: tuple[tuple[str, float, float], ...] = ()
    image: bool = False
    width: float = 612.0
    height: float = 792.0
    font_size: float = 12.0


@dataclass
class _Doc:
    objects: list[bytes] = field(default_factory=list)

    def add(self, body: bytes) -> int:
        self.objects.append(body)
        return len(self.objects)


def _escape(text: str) -> bytes:
    return text.encode("latin-1", "replace").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def build_pdf(
    pages: list[Page | list[tuple[str, float, float]]],
    *,
    labels: list[str | None] | None = None,
    outline: tuple[OutlineEntry, ...] = (),
    title: str | None = None,
    author: str | None = None,
) -> bytes:
    doc = _Doc()
    normalized = [p if isinstance(p, Page) else Page(fragments=tuple(p)) for p in pages]
    font = doc.add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    image = None
    if any(p.image for p in normalized):
        pixels = bytes([0x80] * 16)
        image = doc.add(
            b"<< /Type /XObject /Subtype /Image /Width 4 /Height 4 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 16 >>\nstream\n"
            + pixels + b"\nendstream"
        )
    pages_placeholder = doc.add(b"")  # patched below
    page_ids: list[int] = []
    for page in normalized:
        parts = [b"BT"]
        for text, x, y in page.fragments:
            parts.append(f"/F1 {page.font_size:g} Tf 1 0 0 1 {x:g} {y:g} Tm (".encode() + _escape(text) + b") Tj")
        parts.append(b"ET")
        if page.image:
            parts.append(f"q {page.width:g} 0 0 {page.height:g} 0 0 cm /Im1 Do Q".encode())
        stream = b"\n".join(parts)
        contents = doc.add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        resources = b"<< /Font << /F1 " + str(font).encode() + b" 0 R >>"
        if image is not None:
            resources += b" /XObject << /Im1 " + str(image).encode() + b" 0 R >>"
        resources += b" >>"
        page_ids.append(doc.add(
            b"<< /Type /Page /Parent " + str(pages_placeholder).encode() + b" 0 R /MediaBox [0 0 "
            + f"{page.width:g} {page.height:g}".encode() + b"] /Resources " + resources
            + b" /Contents " + str(contents).encode() + b" 0 R >>"
        ))
    kids = b" ".join(f"{i} 0 R".encode() for i in page_ids)
    doc.objects[pages_placeholder - 1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>"

    catalog_extra = b""
    if labels is not None:
        nums: list[bytes] = []
        for index, label in enumerate(labels):
            if label is None:
                nums.append(f"{index} << >>".encode())
            else:
                nums.append(f"{index} << /P (".encode() + _escape(label) + b") >>")
        catalog_extra += b" /PageLabels << /Nums [" + b" ".join(nums) + b"] >>"

    outline_id = None
    if outline:
        outline_id = doc.add(b"")
        first, last, count = _write_outline(doc, outline, outline_id, page_ids)
        doc.objects[outline_id - 1] = (
            b"<< /Type /Outlines /First " + str(first).encode() + b" 0 R /Last " + str(last).encode()
            + b" 0 R /Count " + str(count).encode() + b" >>"
        )
        catalog_extra += b" /Outlines " + str(outline_id).encode() + b" 0 R /PageMode /UseOutlines"

    catalog = doc.add(b"<< /Type /Catalog /Pages " + str(pages_placeholder).encode() + b" 0 R" + catalog_extra + b" >>")
    info = None
    if title is not None or author is not None:
        fields = b""
        if title is not None:
            fields += b" /Title (" + _escape(title) + b")"
        if author is not None:
            fields += b" /Author (" + _escape(author) + b")"
        info = doc.add(b"<<" + fields + b" >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(doc.objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(doc.objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    trailer = b"<< /Size " + str(len(doc.objects) + 1).encode() + b" /Root " + str(catalog).encode() + b" 0 R"
    if info is not None:
        trailer += b" /Info " + str(info).encode() + b" 0 R"
    trailer += b" >>"
    out += b"trailer\n" + trailer + f"\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _write_outline(doc: _Doc, entries: tuple[OutlineEntry, ...], parent: int, page_ids: list[int]) -> tuple[int, int, int]:
    ids = [doc.add(b"") for _ in entries]
    total = len(entries)
    for index, (entry, number) in enumerate(zip(entries, ids, strict=True)):
        body = b"<< /Title (" + _escape(entry.title) + b") /Parent " + str(parent).encode() + b" 0 R"
        body += b" /Dest [" + str(page_ids[entry.page]).encode() + b" 0 R /XYZ 0 792 0]"
        if index > 0:
            body += b" /Prev " + str(ids[index - 1]).encode() + b" 0 R"
        if index + 1 < len(ids):
            body += b" /Next " + str(ids[index + 1]).encode() + b" 0 R"
        if entry.children:
            first, last, count = _write_outline(doc, entry.children, number, page_ids)
            body += b" /First " + str(first).encode() + b" 0 R /Last " + str(last).encode() + b" 0 R /Count " + str(count).encode()
            total += count
        body += b" >>"
        doc.objects[number - 1] = body
    return ids[0], ids[-1], total


def book_pages() -> list[Page]:
    """A tiny two-chapter book with numbered theorems, proofs and a cross reference."""
    return [
        Page((("Chapter 1. Groups", 72, 720), ("1.1 Subgroups", 72, 690), ("Definition 1.1. A subgroup is a subset closed under the operation.", 72, 660),
              ("Theorem 1.2. Every subgroup of a cyclic group is cyclic.", 72, 630), ("Proof. Let H be a subgroup of Z. Take the least positive element.", 72, 600),
              ("Then H is generated by it. ∎", 72, 580))),
        Page((("Proposition 1.3. By Theorem 1.2, every quotient of a cyclic group is cyclic.", 72, 720),
              ("Proof. Immediate. ∎", 72, 690), ("Theorem 1.8. A gap in numbering is not evidence of missing theorems.", 72, 660))),
        Page((("Chapter 2. Rings", 72, 720), ("Lemma 2.1. Every ideal of Z is principal.", 72, 690), ("Proof. As for Theorem 1.2. ∎", 72, 660))),
    ]


def book_outline() -> tuple[OutlineEntry, ...]:
    return (
        OutlineEntry("Chapter 1. Groups", 0, (OutlineEntry("1.1 Subgroups", 0),)),
        OutlineEntry("Chapter 2. Rings", 2),
    )
