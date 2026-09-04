#!/usr/bin/env python3
"""Regenerate `corpus/taxonomy/` from the official MSC2020 release.

    python3 scripts/vendor_msc2020.py [path/to/MSC_2020.csv]

The MSC table is a faithful copy of what msc2020.org publishes: every code, in
the form it publishes them, with its own name. The arXiv and reporting-group
tables beside it are *editorial* — arXiv has no official MSC crosswalk — so
they are hand-maintained here, versioned with the corpus, and written by this
script so a re-vendoring cannot silently drop them.

The CSV is tab-separated despite its name, latin-1, CRLF, with quoted fields.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

SOURCE = "https://msc2020.org/MSC_2020.csv"
TAXONOMY = Path(__file__).resolve().parents[1] / "corpus" / "taxonomy"

# arXiv's math archive has no official MSC crosswalk, so every line here is a
# judgement against https://arxiv.org/archive/math. Where a class genuinely
# splits, a section-level key below overrides it; lookup is most-specific
# first, so `12Lxx` finds `12L` before it would find `12`.
ARXIV_BY_CLASS = {
    "00": "math.GM", "01": "math.HO", "03": "math.LO", "05": "math.CO",
    "06": "math.RA", "08": "math.RA", "11": "math.NT",
    # 12 defaults to math.NT because arXiv's own math.NT description names
    # "Galois theory", and 12E/12F (general field theory, extensions) are the
    # bulk of the class. The sections that genuinely sit elsewhere override.
    "12": "math.NT",
    "13": "math.AC", "14": "math.AG", "15": "math.RA", "16": "math.RA",
    "17": "math.RA", "18": "math.CT", "19": "math.KT", "20": "math.GR",
    "22": "math.GR", "26": "math.CA",
    # math.FA names "measure theory" explicitly; math.CA does not.
    "28": "math.FA",
    "30": "math.CV", "31": "math.AP", "32": "math.CV", "33": "math.CA",
    "34": "math.CA", "35": "math.AP", "37": "math.DS", "39": "math.CA",
    "40": "math.CA", "41": "math.CA", "42": "math.CA", "43": "math.FA",
    "44": "math.CA", "45": "math.CA", "46": "math.FA", "47": "math.FA",
    "49": "math.OC", "51": "math.MG", "52": "math.MG", "53": "math.DG",
    "54": "math.GN", "55": "math.AT", "57": "math.GT", "58": "math.DG",
    "60": "math.PR", "62": "math.ST", "65": "math.NA",
    # No math.* class covers theory of computing; cs.DM is the honest target.
    "68": "cs.DM",
    "70": "math.MP", "74": "math.AP", "76": "math.AP", "78": "math.AP",
    "80": "math.AP", "81": "math.MP", "82": "math.MP", "83": "math.DG",
    "85": "math.MP", "86": "math.AP", "90": "math.OC", "91": "math.OC",
    "92": "q-bio.QM", "93": "math.OC", "94": "math.IT", "97": "math.HO",
}

ARXIV_BY_SECTION = {
    # MSC 12 is the most split class in the table.
    "12D": "math.AC",   # real and complex fields, sums of squares
    "12H": "math.AC",   # differential and difference algebra
    "12J": "math.AC",   # valued, normed and ordered fields: valuation theory
    "12K": "math.RA",   # near-fields and semifields, which are not commutative
    "12L": "math.LO",   # decidability, ultraproducts, model theory of fields
    "12E15": "math.RA",  # skew fields and division rings, inside a math.NT class
    "20C": "math.RT",   # representation theory of groups
    "22E": "math.RT",   # Lie groups: arXiv files Lie theory under math.RT
    "46L": "math.OA",   # C*-algebras and von Neumann algebras
    "47L": "math.OA",   # linear spaces and algebras of operators
}

# Reporting groups: deliberately coarser than the classes, because a ranking
# per 2-digit class would be dozens of underpowered comparisons. The four the
# corpus actually targets -- commutative algebra, real analysis, group theory,
# linear algebra -- are kept distinct; the rest are merged where a reader would
# not want them ranked apart.
GROUPS = {
    "general": ["00"], "history": ["01"], "logic": ["03"],
    "combinatorics": ["05"], "order-and-lattices": ["06"],
    "algebra": ["08", "16", "17"], "number-theory": ["11"],
    "field-theory": ["12"], "commutative-algebra": ["13"],
    "algebraic-geometry": ["14"], "linear-algebra": ["15"],
    "category-theory": ["18"], "k-theory": ["19"], "group-theory": ["20"],
    "lie-theory": ["22"],
    # The spec's own example: MSC 26 *and* 28 are one reporting field.
    "analysis": ["26", "28", "40"],
    "complex-analysis": ["30", "32"], "potential-theory": ["31"],
    "special-functions": ["33"], "differential-equations": ["34", "35", "39", "45"],
    "dynamical-systems": ["37"], "approximation": ["41"],
    "harmonic-analysis": ["42", "43", "44"],
    "functional-analysis": ["46", "47"], "optimization": ["49", "90", "93"],
    "geometry": ["51", "52"], "differential-geometry": ["53", "58"],
    "topology": ["54", "55", "57"], "probability": ["60"], "statistics": ["62"],
    "numerical-analysis": ["65"], "computer-science": ["68"],
    "mechanics-and-physics": ["70", "74", "76", "78", "80", "81", "82", "83", "85", "86"],
    "social-sciences": ["91"], "biology": ["92"], "information-theory": ["94"],
    "education": ["97"],
}


def read(path: Path) -> tuple[list[tuple[str, str]], str]:
    """The rows, and the digest of the bytes they were read from.

    The digest is what makes the recorded provenance checkable. This script
    takes a path, so the bytes may be a stale download, a hand-edited copy, or
    the wrong file entirely -- and the taxonomy it writes is manifest-bound
    into a published corpus whose entries are classified by these codes.
    Naming only the official URL would let any of those be published as the
    official release; naming the digest lets anyone verify which it was.
    """
    raw = path.read_bytes()
    rows = list(csv.reader(raw.decode("latin-1").splitlines(), delimiter="\t"))
    assert rows[0] == ["code", "text", "description"], rows[0]
    return [(code, text) for code, text, _ in rows[1:]], hashlib.sha256(raw).hexdigest()


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else Path("MSC_2020.csv")
    rows, digest = read(source)
    provenance = {"url": SOURCE, "sha256": digest}
    codes = dict(rows)
    fields = {code[:2]: text for code, text in rows if code.endswith("-XX")}

    groups = {klass: group for group, classes in GROUPS.items() for klass in classes}
    missing = sorted(set(fields) - set(groups))
    assert not missing, f"classes with no reporting group: {missing}"
    assert not sorted(set(fields) - set(ARXIV_BY_CLASS)), "classes with no arXiv class"

    (TAXONOMY / "msc2020.json").write_text(
        json.dumps({"schema_version": 2, "source": provenance, "codes": codes},
                   indent=1, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    (TAXONOMY / "msc-to-arxiv.json").write_text(
        json.dumps({"schema_version": 2,
                    # `arxiv` and `groups` are editorial and belong to this
                    # script; only `fields` comes from the CSV, so only it has
                    # a source to name.
                    "fields_source": provenance,
                    "arxiv": {**ARXIV_BY_CLASS, **ARXIV_BY_SECTION},
                    "fields": fields, "groups": groups},
                   indent=1, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"{len(codes)} codes, {len(fields)} classes, {len(set(groups.values()))} groups")
    print(f"from {source} sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
