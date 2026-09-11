# Hardy general literature sources and reusable mathematical library

Status: **living architectural design**, expanded 2026-09-10. This file is the
durable design record for general scholarly sources and cross-project mathematical
reuse. It began as a book-management seed during delegation/swarm design, but the
central requirement is broader: papers, books, theses, proceedings, Mathlib, and
mathematics formalized inside Hardy projects should converge onto one reusable
mathematical library rather than remain separate silos.

Design branch: `general-literature-sources-spec`.

Related architecture:

- `docs/superpowers/specs/2026-09-10-delegation-swarm-design.md` consumes the
  generic retrieval/evidence interfaces designed here. Delegation does not own
  source ingestion or reusable formalization identity.
- Existing `hardy.literature` arXiv support is a concrete acquisition/source
  implementation to generalize, not something to discard.
- Existing project/shared retrieval, ledger/evidence policy, representation,
  context, transport, and acquisition owners remain authoritative.

## 1. Goal

Hardy should accumulate reusable mathematical knowledge across projects.

If one project has already done the expensive work of understanding and formally
verifying a result from Hartshorne, Donagi's *Fibers of the Prym Map*, or another
source, a later project should be able to discover and reuse that work instead of
translating and proving the same mathematics again.

The core architecture is:

```text
BIBLIOGRAPHIC WORK
        │
        ▼
EDITION / VERSION
        │
        ▼
SOURCE ARTIFACT
exact bytes Hardy imported
        │
        ▼
DERIVED REPRESENTATIONS
text / OCR / layout / source assembly / page images
        │
        ▼
SOURCE TREE
what the artifact literally contains and where
        │
        ▼
MATHEMATICAL CLAIM REGISTRY
what mathematical proposition/concept/construction this expresses
        │
        ▼
FORMAL REALIZATIONS
Mathlib declarations and reusable Hardy Lean declarations
```

These are deliberately separate identities.

A theorem in Hartshorne is not a Lean declaration. A Lean declaration is not a
bibliographic source. Two source statements with different wording may express the
same mathematical claim. Two familiar theorem names may refer to materially
different claims. A stronger formal theorem may be reusable for a weaker source
claim through an explicit specialization or transport.

The reusable semantic unit is therefore **the exact mathematical claim**, not the
paper, book, project, theorem name, or formal declaration that first introduced it.
The authoritative textual unit remains the exact source artifact and source span
Hardy actually read.

## 2. Motivating examples

### 2.1 Hartshorne

A user supplies an exact artifact of Hartshorne's *Algebraic Geometry*. Hardy
imports it once into a persistent personal literature library and derives a
navigable source tree:

```text
Hartshorne, Algebraic Geometry [exact artifact digest]
├── Chapter I
│   ├── §1
│   │   ├── Definition ...
│   │   ├── Proposition ...
│   │   ├── Example ...
│   │   └── Exercise ...
│   └── ...
├── Chapter II
└── ...
```

Projects can seed retrieval with this source without putting the whole book into
model context. A worker gets a compact source map and lazily retrieves exact nodes
or spans.

If a Hartshorne theorem is already formalized in Mathlib, Hardy links the source
node to the exact mathematical claim and the claim to the authenticated Mathlib
realization. If the required theorem is formalized later in a Hardy project, that
verified project result may be promoted into the reusable formal library.

### 2.2 Donagi 1991

The first Prym/Schottky project may need several results from Donagi's *Fibers of
the Prym Map*. Hardy may progressively identify source statements, connect them to
exact claims, and formalize the claims actually needed.

A later genus-6 project should not repeat that work:

```text
Donagi source node
       │ expresses
       ▼
Claim C
       │ formalized_by
       ▼
shared verified Lean declaration
       │
       └── reused by later project
```

Formalizing a paper is therefore not fundamentally a one-shot operation. A paper
becomes progressively enriched as projects use it.

### 2.3 Cross-source reuse

The same mathematics may appear in several places:

```text
Donagi 1991, Theorem X ─────┐
                            │
Later survey, Theorem 4.2 ──┼── expresses ──> Claim C
                            │
Textbook, Proposition 12.7 ─┘
                                      │
                                      └── formalized_by --> Lean realization R
```

This is only one claim if semantic equivalence has actually been established.
Similarity, theorem names, or embeddings are not sufficient to merge them.

### 2.4 Multiple artifacts for one edition

The user may possess several representations of the same edition:

```text
Hartshorne 1977 edition
├── publisher PDF
├── EPUB
└── scanned PDF
```

Those are not one artifact. They are distinct exact byte sequences that may later
be authoritatively grouped under one edition. One representation may be best for
text extraction, another for page images, and another for structure. Hardy should
be able to use the cleanest representation for reading while preserving exact
anchors back to the artifact actually supporting a source claim.

## 3. Architectural principles

1. **Source identity, mathematical identity, and formal identity are distinct.**
   Never collapse source nodes, claims, and Lean declarations into one object.

2. **Bibliographic work, edition/version, exact artifact, and derived
   representation are separate identities.** Metadata similarity may propose a
   grouping, but exact grouping requires reliable identity evidence.

3. **Exact claim identity controls formal reuse.** Search may use names, text,
   embeddings, and neighboring concepts, but reuse requires an exact claim match
   or an explicitly justified relation such as equivalence/generalization/
   specialization.

4. **Formalization is cumulative across projects.** A reusable verified theorem
   proved once should become discoverable to later projects when promotion policy
   permits.

5. **Project-local does not automatically mean globally reusable.** Local
   hypotheses, project-specific axioms, temporary declarations, contextual
   transports, or idiosyncratic helper facts remain local unless deliberately
   generalized and promoted.

6. **Literature provenance survives semantic deduplication.** Three source nodes may
   express one claim; all three source identities/spans remain available.

7. **A formal proof is attached to a claim, not a citation.** A Lean realization
   can support every source node genuinely expressing that claim, but it does not
   prove that those source nodes were interpreted correctly. Source-to-claim
   faithfulness remains separately evidenced.

8. **A source statement is not trusted merely because it is indexed.** Source
   extraction and claim interpretation do not widen a project's trust scope.

9. **Mathlib is one formal provider, not the semantic registry.** Mathlib
   declarations participate as formal realizations of claims. Hardy should not
   identify claims by Mathlib names alone.

10. **The personal reusable library is a shared source, not a hidden memory
    system.** Projects explicitly retrieve/authorize material from it through
    Hardy's existing project/shared retrieval boundary.

11. **Indexed source bytes are Hardy-owned immutable imports.** External files,
    downloads, or mounted paths are acquisition inputs/provenance, not mutable
    backing stores for indexed sources.

12. **Derived indexes are rebuildable.** Exact artifacts, structured records,
    evidence, and formal artifacts are durable; semantic/full-text/vector indexes
    are derived acceleration structures.

13. **Private copyrighted bytes remain private/local.** Durable mathematical and
    bibliographic records may reference exact digests/locators without assuming
    Hardy may redistribute original artifacts.

14. **Progressive enrichment beats eager whole-corpus formalization.** Importing a
    500-page book should not require resolving or formalizing every statement
    before the source becomes useful.

15. **No locator exists outside a coordinate system.** Printed page labels,
    artifact page indices, TeX byte ranges, OCR offsets, text blocks, DOM nodes,
    and image bounding boxes are distinct coordinates. Every locator names the
    exact artifact or derived representation in which it is meaningful.

16. **Improved extraction never silently moves old evidence.** New OCR, parsing,
    layout analysis, or source-tree construction creates new versioned derived
    objects and explicit correspondences rather than mutating old source nodes or
    spans underneath claim/evidence links.

17. **Ingestion preserves plurality instead of flattening sources.** Importers
    produce artifact-bound derived representations, mappings, diagnostics, and
    quality information rather than one supposedly canonical text blob.

18. **Expensive extraction is adaptive.** Native structure/text is preferred when
    it is high quality; OCR, formula recognition, and model-assisted reconstruction
    are invoked lazily on weak, missing, or explicitly requested regions.

## 4. Persistent personal mathematical library

Hardy should maintain a reusable user-level library shared across projects. The
exact storage layout remains an implementation choice, but conceptually it contains
connected durable stores:

```text
Personal Mathematical Library
├── bibliographic works
├── editions / source versions
├── exact source artifacts
├── derived representations
├── source trees / structural inventories
├── source spans / locator mappings
├── mathematical claim registry
├── source ↔ claim interpretation links
├── claim ↔ claim semantic relations
├── formal realization registry
├── reusable Hardy Lean source/artifacts
├── indexes/search structures
└── provenance/evidence records
```

A project does **not** copy the entire library into its own ledger. Project state
records its own uses, citations, admitted assumptions, obligations, and exact
references to shared material.

This generalizes the useful property of the current arXiv paper library: third-party
bytes are machine-local while project bibliography records what exact bytes were
used. The new library broadens the source types and adds semantic/formal reuse.

The existing retrieval layer already recognizes `project` and `shared_library`
sources. The personal mathematical library should plug into that seam. Shared
material remains subject to explicit provenance, project authorization,
scope/context checks, and current formal importability rather than being silently
trusted because it exists on the machine.

## 5. Source identity hierarchy

Hardy needs enough bibliographic structure to distinguish human-facing publication
identity from the exact bytes and extraction pipeline it actually used, without
adopting a full FRBR-style library-science ontology whose complexity does not serve
mathematical work.

The recommended hierarchy is:

```text
BibliographicWork
  ↓
EditionOrVersion
  ↓
SourceArtifact
  ↓
DerivedRepresentation
  ↓
SourceTree / SourceNode
```

Each layer answers a different question and has a different lifetime.

### 5.1 BibliographicWork

A `BibliographicWork` is the intellectual publication people usually mean when they
say “Hartshorne's Algebraic Geometry” or “Donagi's Fibers of the Prym Map.” It is
useful for discovery, citation grouping, human navigation, and grouping related
editions/versions.

Representative fields:

```text
BibliographicWork
  stable id
  kind: book | paper | thesis | proceedings | notes | other
  title
  authors/editors
  broad publication identity
  persistent external identifiers when work-level
  aliases / alternate titles
  provenance for metadata assertions
```

A work is not enough to identify what Hardy read. Mathematical extraction, source
claims, page locators, and citations depending on exact wording continue down to
edition/version and artifact identity.

### 5.2 EditionOrVersion

An `EditionOrVersion` identifies a specific published/released state of a work.
Examples:

```text
Hartshorne, GTM 52, Springer, 1977 edition
Hartshorne corrected printing, if materially identifiable as distinct
Donagi journal publication
arXiv:1302.5946v1
arXiv:1302.5946v2
thesis revision / institutional repository version
```

Representative fields:

```text
EditionOrVersion
  stable id
  work ref
  edition/version label
  publisher / journal / venue
  year/date
  volume / issue / pages when bibliographic
  ISBN / DOI / arXiv version / repository identifier / other identifiers
  language
  known correction/revision metadata
  metadata provenance
```

“Edition” is used broadly enough to include exact source versions of papers. The
architectural point is that this layer captures bibliographic/version identity
above file-format bytes.

Different editions/versions remain distinct even when most content is identical. A
relation can later state that a theorem is unchanged across them; Hardy never
assumes this from matching titles or page counts.

### 5.3 SourceArtifact

A `SourceArtifact` is the exact managed bytes Hardy imported/read for one
edition/version. Several artifacts may represent the same edition:

```text
Edition: Hartshorne 1977
├── publisher PDF artifact      sha256:A
├── EPUB artifact               sha256:B
└── scanned PDF artifact        sha256:C
```

Representative fields:

```text
SourceArtifact
  id / content digest
  edition/version ref if established
  byte size
  format / media type
  managed immutable storage ref
  acquisition/import provenance
  imported/fetched timestamp
  privacy/access policy
  original filename/path/provider handle
```

Artifact identity is content identity. Identical byte sequences deduplicate even if
imported through different paths. Different bytes remain separate artifact
identities even when believed to represent the same edition.

### 5.4 Candidate grouping versus authoritative grouping

Hardy may propose that two artifacts belong to one edition based on title, authors,
ISBN/DOI/arXiv version, publisher information, internal front matter, provider
metadata, or strong structural similarity. This is useful for import UX and
reconciliation.

But candidate grouping is weaker than authoritative grouping:

```text
artifact A ── candidate_same_edition ── artifact B
```

is not yet:

```text
artifact A ── belongs_to ── Edition E
artifact B ── belongs_to ── Edition E
```

Authoritative membership requires reliable evidence appropriate to the source type:
matching exact identifiers corroborated by internal metadata, exact arXiv version,
publisher/provider identity, or explicit human confirmation where machine evidence
cannot distinguish printings.

Title/year/author similarity alone is insufficient. The same principle applies to
merging work identities.

### 5.5 Managed immutable import

Hardy should not index user-supplied scholarly material in place. Importing a local
PDF, EPUB, TeX tree, scan, downloaded file, or provider result copies the admitted
bytes into Hardy's user-level managed literature store. The external path or URL
remains provenance only.

Conceptually:

```text
C:/Downloads/Hartshorne.pdf
        │ import
        ▼
~/.hardy/.../literature/artifacts/<sha256>/original.pdf
        │
        ├── immutable exact bytes
        ├── import/provenance record
        ├── derived representations
        └── SourceTree/indexes
```

The literal layout is illustrative; the final directory names are not frozen.

The import contract is:

```text
read bounded/validated input
→ compute content digest
→ copy to temporary managed location
→ verify copied bytes/digest
→ atomically admit artifact record + managed bytes
→ derive/index only from managed copy
```

A refused/interrupted import must not leave a half-admitted artifact that later
looks valid.

After import, edits, deletion, renaming, cloud-sync changes, or replacement of the
external original do not change Hardy's artifact. Consuming changed bytes requires
a new import and therefore either the same digest or a new `SourceArtifact`.

### 5.6 Import provenance

Managed artifacts retain acquisition facts separately from byte identity:

```text
original filename/path or provider handle
source URL/provider identity when fetched
time imported/fetched
media/MIME type and detected format
user/provider supplied bibliographic metadata
acquisition adapter/version
privacy/access classification
```

Original local paths are useful diagnostics, not durable semantic identity.

### 5.7 DerivedRepresentation

A `DerivedRepresentation` is an attributable reading of one exact artifact produced
by an extractor/parser/OCR/model/configuration. It is first-class because different
pipelines may disagree while both remain historically relevant.

Examples:

```text
PDF native text extraction v2 from artifact A
OCR pass v1 from scanned artifact C
EPUB XHTML normalized text from artifact B
TeX source assembly from arXiv source archive
page-image manifest from PDF artifact A
layout/block analysis from PDF artifact A
formula/diagram extraction from artifact A
```

Representative fields:

```text
DerivedRepresentation
  id / digest
  artifact ref
  kind: native_text | ocr_text | normalized_text | page_images |
        layout | native_source | dom | formula_layer | other
  retained output artifact refs
  extractor/parser/model identity
  extractor version/configuration
  derivation timestamp
  input refs
  quality/confidence/failure metadata
  locator mapping back to artifact/parent representation
```

Derived identity includes the exact artifact plus enough pipeline identity to know
what generated it. Improved OCR/parser output creates a new representation, not a
mutation of the old one.

Purely reconstructable caches may be discarded. Representations referenced by
source nodes, interpretation evidence, or claim links retain durable
identity/provenance even if bytes can later be regenerated.

### 5.8 Artifact and representation plurality

Hardy should not force one canonical text representation when several readings are
useful. Native PDF text may be best for prose; OCR/page images may recover formulas
or headers; TeX source may expose theorem boundaries; published PDF pages may carry
the authoritative printed pagination.

The system can prefer one representation for a particular operation while
preserving the others and their explicit correspondences.

## 6. Multi-coordinate locators and alignment

### 6.1 No universal locator

The same theorem may have several legitimate locations:

```text
Hartshorne II.5.8

printed edition:
  printed page 128

publisher PDF artifact A:
  PDF page index 147
  bounding boxes across pages 147–148

native PDF text representation R1:
  blocks 3812–3830

OCR representation R2:
  token/character spans ...

EPUB artifact B:
  spine item 17 / DOM nodes ...

TeX source artifact C:
  chapter2.tex byte ranges ...
```

These are different coordinate systems. None is the universal location of the
source unit.

### 6.2 SourceAnchor

Source nodes and evidence spans carry typed anchors rather than one opaque location
string.

Conceptually:

```text
SourceAnchor
  artifact or representation ref
  locator kind
  exact typed locator payload
  derivation/provenance
  quality/confidence where relevant
```

Representative locator shapes include:

```text
RepresentationSpan
  representation ref
  block/token/character range

ArtifactPageRegion
  artifact ref
  artifact page index
  one or more bounding boxes/polygons

PrintedPageLocator
  edition/version ref
  printed page label/range

NativeSourceSpan
  artifact/representation ref
  file/path ref + file digest
  byte/character range

DOMLocator
  EPUB/HTML representation ref
  spine/resource id + structural selector/range

ImageRegion
  page/image representation ref
  bounding region
```

Compound/noncontiguous ranges are allowed. A theorem, proof, or formula can cross
pages, source files, columns, or layout blocks.

### 6.3 Printed page labels versus artifact page indices

Printed pagination and PDF/image page positions are explicitly different facts.
For example:

```text
artifact page index 0   = cover
artifact page index 7   = printed page vii
artifact page index 20  = printed page 1
artifact page index 147 = printed page 128
```

A second scan of the same edition may map a different artifact index to printed
page 128. The edition-level printed locator can therefore serve as a human
bibliographic coordinate while each artifact retains its own physical page index.

Hardy should not infer equality merely because two artifacts both contain a string
“128”; printed-page mapping is itself derived/evidenced metadata.

### 6.4 Representation-relative offsets

Character/token/block offsets are meaningful **only inside the exact
`DerivedRepresentation` they name**.

Never persist a naked offset such as:

```text
start_character = 193442
```

without the exact representation identity. Whitespace normalization, ligature
handling, OCR improvements, or parser changes can all move offsets.

Old offsets remain valid for the old representation; they are not silently migrated
to a new one.

### 6.5 RepresentationMapping

Extraction pipelines should emit explicit alignment mappings when they can map one
coordinate system to another.

Conceptually:

```text
RepresentationMapping
  from representation/artifact
  to representation/artifact
  ordered mapping segments
  mapper/extractor identity + configuration
  quality/confidence/failure metadata
```

Examples:

```text
PDF page/bbox      ↔ native text blocks
page image/bbox    ↔ OCR words/lines/tokens
EPUB DOM element   ↔ normalized text range
TeX file/range     ↔ assembled reading-order text
layout block       ↔ page region
formula token span ↔ page image region
```

Mappings may be partial or many-to-many. A mapping that cannot confidently align a
region should represent that uncertainty rather than invent an exact coordinate.

This allows Hardy to read from a clean normalized representation while still
showing the exact PDF/image region from which that text came.

### 6.6 SourceTree and SourceNode

A `SourceTree` is analogous to an AST for the scholarly artifact: it records the
artifact's structural organization and exact locations without initially claiming
semantic equivalence to other mathematical statements.

Representative hierarchy:

```text
book / paper
├── part
├── chapter
│   ├── section
│   │   ├── subsection
│   │   ├── definition
│   │   ├── theorem
│   │   ├── lemma
│   │   ├── proposition
│   │   ├── corollary
│   │   ├── proof
│   │   ├── construction
│   │   ├── example
│   │   ├── exercise
│   │   ├── remark
│   │   ├── equation/display
│   │   ├── diagram
│   │   ├── figure/table
│   │   └── paragraph
│   └── ...
└── bibliography/index/etc.
```

A `SourceNode` should retain at least:

```text
id
source-tree identity
artifact/representation inputs
parent / children
kind
title / heading / source label / source numbering
reading order
one or more typed source anchors
text/formula/image representation refs as appropriate
parser/extractor provenance
quality/confidence metadata
```

Source numbering distinguishes what the source literally provides from numbering
Hardy inferred.

### 6.7 Versioned structural identity

A new parser or extraction pass may find better boundaries. It must not mutate an
old `SourceNode` that existing source-to-claim links already cite.

Example:

```text
SourceTree T1 / node N1
  statement = blocks 3812–3820
  proof     = 3821–3870

SourceTree T2 / node N2
  statement = blocks 3812–3823
  proof     = 3824–3870
```

T2 may be preferred for new work, and `N2` may be recorded as a refinement or
correspondence of `N1`, but historical evidence tied to `N1` remains auditable.

### 6.8 Cross-artifact source correspondence

Even after two artifacts are authoritatively grouped under the same edition, their
source nodes are not automatically identical. Hardy may establish explicit
correspondences:

```text
PDF SourceNode P52
EPUB SourceNode E47

SourceCorrespondence
  P52 ↔ E47
  relation: same_source_unit / overlapping_source_unit / variant
  evidence/provenance
```

Candidate correspondence can be generated from headings, exact or controlled-
normalized text, numbering, and structural position. Authoritative correspondence
requires stronger evidence/review appropriate to how it will be used.

This allows Hardy to read from a clean EPUB/TeX representation while citing or
showing the publisher PDF location.

### 6.9 Exact SourceSpan for evidence

A structural `SourceNode` is navigation. Exact literature evidence should be able
to point to a narrower durable span.

Conceptually:

```text
SourceSpan
  id
  source node ref when applicable
  exact representation range(s)
  exact artifact anchor(s) where available
  digest of extracted/normalized content used
  representation/mapping provenance
```

A source-backed claim can therefore say not merely “Donagi Theorem 2.1” but “this
exact statement/hypothesis span in this exact representation of this exact artifact.”

### 6.10 Formulas, diagrams, figures, and non-text material

Mathematics must not be irreversibly flattened to prose text. Source trees and
representations should preserve first-class nodes/regions for:

```text
equations / aligned equations
commutative diagrams
figures
tables
displayed constructions
page-image regions
```

A formula may simultaneously have:

```text
PDF/image bounding box
TeX source span
MathML/LaTeX extraction
OCR or model interpretation
normalized textual rendering
```

All remain distinct representations linked by mappings. A model-readable formula
interpretation does not erase the underlying image/source evidence.

### 6.11 OCR is a derived reading, not replacement source bytes

For scans or image-heavy PDFs:

```text
exact page image
      ↓
OCR DerivedRepresentation
```

OCR output should carry region-level or segment-level confidence/failure metadata
when available, especially for mathematical formulas and symbols. If an old
source-to-claim interpretation used OCR representation R1 and improved OCR R2 later
disagrees materially, Hardy can flag the old interpretation for review without
rewriting historical provenance.

### 6.12 Retrieval returns provenance-bearing source material

A source read should always carry machine-visible identity for what was returned:

```text
source artifact / edition
source-tree/node when applicable
derived representation
exact span/anchor refs
mapping provenance where relevant
```

The model need not receive a verbose human-readable provenance header every time,
but Hardy must preserve exact refs so a source-backed finding/citation can carry
provenance forward rather than reconstruct it after the fact.

Representative retrieval operations become natural:

```text
show Hartshorne II.5
list theorem-like nodes in Hartshorne II.5
read statement of II.5.8
read proof of II.5.8
show exact PDF pages/regions for II.5.8
show original image region for this formula
read two paragraphs before this theorem
show corresponding EPUB/TeX representation
give the exact source span supporting Claim C
```

## 7. Progressive source enrichment

Import should be useful before semantic understanding is complete.

A newly imported book may begin as:

```text
✓ exact artifact identity
✓ derived page/text representations
✓ chapter/section/page map
✓ extracted theorem-like nodes where recoverable
? claim identities unresolved
? formal realizations unknown
```

Later projects enrich individual nodes without rebuilding or reinterpreting the
whole artifact. New parsers may create improved SourceTrees while old interpretation
links remain tied to the exact trees/spans they used.

## 8. Mathematical claim layer

### 8.1 Why a claim registry is required

The source tree cannot itself provide cross-project formalization reuse because a
source statement is tied to wording, notation, edition, and document context.
Likewise a Lean declaration is tied to a formal representation and environment.

Hardy therefore needs a persistent **mathematical claim registry** representing the
mathematics between source and formalization:

```text
SourceNode ──expresses──> MathematicalClaim <──realizes── FormalRealization
```

### 8.2 MathematicalClaim

A claim is an exact mathematical proposition/definition/construction suitable for
cross-project identity and dependency tracking.

Representative fields:

```text
MathematicalClaim
  stable id
  kind
  canonical/navigational name
  normalized informal statement
  semantic context:
    concepts/objects
    declarations/parameters
    hypotheses
    conclusion/body
    representation choices where identity-relevant
  exact dependencies when established
  aliases / search terms
  family/concept tags
  provenance of claim creation/interpretation
  status/evidence of semantic review
```

Natural-language canonicalization does not solve mathematical identity. A stable
claim identity is minted through an explicit semantic admission process; later
meaning-changing revisions become new claims linked by explicit relations.

The claim layer should reuse Hardy's existing mathematical project semantics where
possible rather than invent a contradictory theorem ontology. The architectural
requirement is one reusable exact mathematical identity independent of a particular
source or Lean declaration.

### 8.3 Claim families/concepts

Names such as “Riemann–Roch” are useful for discovery but too coarse for exact
reuse.

```text
ClaimFamily / Concept: Riemann–Roch

Exact claims:
  divisor RR for smooth projective curves under H1
  line-bundle RR under H2
  stronger field-general version
  scheme-theoretic generalization
```

Family membership assists retrieval/UI and never establishes formal
substitutability.

### 8.4 Claim relations

Exact claims can carry semantic relations:

```text
equivalent_to
generalizes
specializes
implies
refines
reformulates
```

Relations permitting proof reuse carry enough evidence/transport to justify the
mapping. Similarity can propose relations but cannot certify them.

## 9. Source-to-claim interpretation

### 9.1 SourceClaimLink

A source node and mathematical claim are connected by an explicit interpretation
record:

```text
SourceClaimLink
  source node / exact source span
  claim
  relation:
    expresses
    specializes
    generalizes
    reformulates
  notation/object mapping
  interpretation/faithfulness evidence
  interpreter/model/human provenance
```

A theorem extractor saying “this looks like a theorem” is not enough to produce a
trusted `expresses` link.

### 9.2 Many sources, one claim

Cross-source deduplication is expected:

```text
Source A node ─┐
Source B node ─┼──> Claim C
Source C node ─┘
```

Every source keeps its own artifact/span provenance.

### 9.3 Ambiguity

If Hardy has competing interpretations of a source statement, preserve them as
proposals/assessments until adjudicated instead of silently choosing the one that
matches an existing theorem.

## 10. Formal realization layer

### 10.1 FormalRealization

A `FormalRealization` records one formal declaration representing an exact
mathematical claim.

```text
FormalRealization
  id
  claim ref
  system: lean
  origin:
    mathlib
    hardy_shared
    project
    external_formal_library
  declaration/module
  exact formal statement
  source artifact/module digest
  environment/toolchain identity
  required imports
  formal proof/elaboration evidence
  semantic faithfulness evidence
  used assumptions / trust boundary
  formal context/representation mapping
  provenance
```

A declaration becomes reusable only when Hardy authenticates both formal validity
and semantic correspondence to the exact claim.

### 10.2 Mathlib participation

Mathlib is continuously searchable as a provider of candidate realizations. A
candidate must be checked for exact statement, implicit hypotheses/typeclasses,
representations, ambient categories, and relevant conventions before attachment to
a claim. Similar names/embeddings are leads only.

Formal realizations record Mathlib/environment revision so later retrieval can
recheck availability/importability.

### 10.3 Project-local realizations

A Project A theorem may initially remain local:

```text
Claim C
  formalized_by -> Project A / Foo.lean / theorem foo
```

It may be indexed for inspection without becoming globally reusable. Context and
trust boundary remain explicit.

### 10.4 Shared Hardy formal library

Hardy supports deliberate promotion into a user-level shared Lean library:

```text
Project A verified theorem
        │ reusable promotion
        ▼
Hardy shared formal library
        │
        └── reusable FormalRealization of Claim C
```

The shared library is curated reusable mathematics, not every theorem ever proved.

## 11. Promotion of project formalizations

### 11.1 Promotion criteria

A project theorem is a shared-promotion candidate when:

- its Lean declaration is authenticated/verified;
- semantic faithfulness to an exact reusable claim is established;
- its trust boundary is explicit and acceptable;
- it does not silently rely on project-only hypotheses/declarations;
- required representations/context are reconstructable elsewhere;
- dependencies are Mathlib/shared reusable material or promotable with it;
- the resulting shared artifact can be imported/revalidated independently of the
  originating workspace.

Formal correctness alone does not imply project-worthiness.

### 11.2 Reusable dependency closure

Promotion computes a minimal reusable formal dependency closure:

```text
RiemannRoch
├── Mathlib dependency       → external import
├── already shared lemma     → reuse
├── reusable project lemma   → promote too
└── project-specific fact    → block/generalize/replace
```

A proof using a project-local axiom does not become globally verified because Lean
accepted the axiom locally.

### 11.3 Generalization during promotion

Promotion may generate work to remove project-specific hypotheses, generalize
chosen objects to parameters, replace local definitions with shared
representations, or prove transport/equivalence to a reusable formulation. The
result can be a new claim/formal realization rather than mutation of historical
project work.

### 11.4 Promotion provenance

Promotion records originating project/result, claim identity, dependency closure,
transports/generalizations, formal/faithfulness evidence, trust audit, shared
module/declaration identity, environment/digest, actor/reason/time.

## 12. Reuse and acquisition behavior

When a project needs mathematics, Hardy should prefer reuse before new proof work:

```text
1. exact established result already in current project
2. exact compatible Mathlib realization
3. exact compatible Hardy shared realization
4. equivalent/stronger reusable realization with authenticated transport
5. exact source-backed literature result usable under project policy
6. new formalization/proof work
```

This is a preference hierarchy, not necessarily six expensive serial searches.

Search distinguishes:

```text
EXACT CLAIM MATCH
RELATED CLAIM / family member
STRONGER/WEAKER CLAIM with relation
SOURCE ONLY
FORMAL CANDIDATE not yet semantically linked
```

Hardy should not ask an LLM to rediscover/reprove a theorem before checking known
reusable formal mathematics.

## 13. Project relationship to the shared library

The personal library is reusable infrastructure; projects retain their own
mathematical/trust decisions.

A project can retrieve shared claims/realizations, import verified shared Lean,
cite a source node/span, admit a literature result according to scope policy,
create project-specific obligations/relations, and contribute promoted reusable
formalizations.

A project does **not** automatically trust every theorem stored in the shared
library. Existing scope/evidence policy remains authoritative.

## 14. Progressive formalization of sources

Formalization coverage is a derived view:

```text
Donagi 1991
§1
  ✓ Lemma 1.1        linked claim + shared Lean realization
  ✓ Proposition 1.3 linked claim + Mathlib realization
  ○ Lemma 1.4        claim identified, no formal realization
  · Remark 1.5       structural source node only
§2
  ✓ Theorem 2.1      shared Lean realization
  ◐ Proposition 2.4 project-local realization only
  ? Theorem 2.6      semantic identity unresolved
```

Symbols are illustrative UI only.

Hardy should also answer inverse queries: sources expressing Claim C, projects
using it, and all formal realizations.

## 15. Source seeding for projects and delegations

“Seed this project/run with Hartshorne” means:

- select an exact admitted work/edition/artifact;
- expose compact SourceTree/index identity prominently;
- increase retrieval priority for its nodes;
- allow lazy exact span/proof/formula retrieval;
- expose known claim/formalization links where permitted;
- do **not** inject the whole source into every context.

Delegation workers may receive different source subtrees/retrieval intents while
sharing the exact source identity.

## 16. Citation versus formal reuse

Literature citation and formal reuse remain distinct:

```text
SourceSpan S --expresses--> Claim C <--realizes-- Lean R
```

A project may cite S, use R, do both, use R without citing S where publication
policy permits, or cite S without R when admitting literature background.

Formal proof does not retroactively certify that S expresses C. Source faithfulness
is independent evidence.

## 17. Versioning, revisions, and staleness

### 17.1 Source changes

New arXiv versions, new book editions, corrected scans, improved OCR, or improved
SourceTrees create new identities. Old source-to-claim links remain tied to their
exact artifact/representation/span.

New artifacts may later be assessed as same mathematical statement, equivalent
with notation changes, strengthened/weakened, meaningfully changed, or unknown.

### 17.2 Claim changes

A corrected/strengthened mathematical statement receives a new claim identity with
explicit relation to the prior claim. Claim identity is not mutable prose.

### 17.3 Formal environment changes

Mathlib/toolchain changes may make a realization stale/unimportable without
changing the claim. Formal validity/importability is environment-specific and can
be rechecked independently from source interpretation.

## 18. Search and indexing

Useful rebuildable indexes include:

```text
source full-text index
SourceTree structural index
page/locator index
claim text/alias/concept index
claim-relation graph
source-node/span ↔ claim index
claim ↔ formal-realization index
formal declaration/name index
vector/semantic retrieval index where useful
project usage/citation index
```

Exact IDs/aliases/structural relationships outrank fuzzy similarity when available.
Embeddings are candidate generators, not identity proofs.

## 19. Privacy, copyright, and storage boundary

User-supplied copyrighted books/papers may be stored/indexed locally for the user's
own Hardy workflows. The architecture does not assume Hardy may independently
acquire unavailable material, redistribute bytes, commit private artifacts into
project repos, or send entire copyrighted sources to unrelated external services.

Access policy should remain explicit enough for later executors/plugins/UI to
respect local/private restrictions.

Derived claim/formalization records can be more portable than source bytes, but
retain enough provenance to identify what was interpreted.

## 20. Relationship to current arXiv paper handling

Current arXiv support already has valuable properties to retain:

- search results are leads, not citations;
- exact versioned identity is pinned before use;
- metadata/content/source bytes carry digests;
- library bytes are machine-local;
- source bundles are treated as hostile archives;
- bounded reads prevent uncontrolled context injection;
- project bibliography is the controlled citation writer;
- extracted statements are inventoried separately from approval/trust.

General literature should **generalize** these properties. `PaperRecord`/
`PaperLibrary` becomes one acquisition compatibility layer beneath general source
primitives rather than the downstream universal abstraction.

## 21. Relationship to Hardy's project ledger and retrieval

Do not create a second theorem truth system.

The shared claim/formalization registry supplies reusable cross-project identities
and artifacts. Concrete projects continue recording goals, contexts, dependencies,
representations, transports, obligations, scope/trust, evidence, acceptance,
citations, and publication relations in the existing ledger.

When shared material enters a project, Hardy links/imports/references it through
existing project operations rather than bypassing them.

The existing project/shared retrieval index remains the delivery boundary. The
personal mathematical library appears as an authenticated shared source. Discovery
establishes relevance; existing owners establish whether material can actually be
used in the requesting scope/context/environment.

## 22. Key conceptual contracts

Names are provisional, but implementation should preserve these roles:

```text
BibliographicWork
  human/intellectual publication identity

EditionOrVersion
  specific published/released/versioned state

SourceArtifact
  exact managed imported bytes

DerivedRepresentation
  attributable text/OCR/layout/source/image reading of one artifact

RepresentationMapping
  explicit mapping among representation/artifact coordinate systems

SourceTree / SourceNode
  versioned document structure and source-unit identity

SourceAnchor / SourceSpan
  exact typed location/evidence ranges within named coordinate systems

SourceCorrespondence
  evidenced source-unit relation across trees/artifacts

MathematicalClaim
  exact reusable semantic proposition/definition/construction

ClaimFamily / Concept
  looser discovery grouping such as “Riemann–Roch”

SourceClaimLink
  evidenced interpretation from exact source material to exact claim

ClaimRelation
  exact semantic relation between claims

FormalRealization
  exact Lean declaration + environment + formal/faithfulness/trust evidence

PromotionRecord
  project theorem → reusable shared formal artifact, including dependency closure

SourceUsage / ProjectLink
  project-specific citation/use/authorization of shared material
```

Do not merge these simply because an early implementation could use fewer classes.
Their separation carries correctness semantics.

## 23. Core invariants for source provenance and formal reuse

```text
work != edition/version != artifact != derived representation
source node identity != mathematical claim identity != Lean declaration identity

all indexed source bytes are Hardy-managed immutable imports
external paths/URLs are acquisition provenance, not live backing stores

printed page labels != PDF/image page indices
representation offsets have meaning only relative to exact representation identity
source evidence never stores naked offsets without coordinate-system identity

extraction pipelines preserve mappings back to exact artifact/source coordinates
new extraction/parser versions create new identities rather than relocating old evidence
cross-artifact source correspondence is explicit and evidenced
formulas/diagrams/images remain first-class source material rather than lossy text only

ingestion never collapses an artifact immediately to one canonical text blob
native/high-fidelity extraction precedes expensive OCR/model assistance
OCR/model assistance is lazy/adaptive unless explicitly requested or native extraction is inadequate
partial extraction failure does not invalidate an otherwise valid imported artifact

formal reuse is keyed by exact claim or authenticated claim relation,
not theorem name or textual similarity

one claim may have many source nodes and many formal realizations
one source may contain many independently reusable claims
one project formalization can become shared only through explicit promotion
promotion carries reusable dependency closure and trust/context audit
project-local assumptions cannot silently become shared theorem assumptions
Mathlib declarations are candidate/reusable realizations, not claim identities
shared existence does not automatically widen a project's trust scope
citation provenance and formal proof provenance remain independent
source/claim/formal histories are versioned; no silent retargeting
indexes accelerate discovery but never establish identity/truth
```

## 24. Evaluation questions

This architecture should eventually let Hardy measure:

- how often project needs are satisfied by existing Mathlib/shared formalization;
- how often two sources are correctly linked to one reusable claim;
- false-positive rates in source/claim and claim/formal candidate matching;
- formalization effort saved across projects;
- which project-local results are worth promoting;
- promotion blockers due to project-local dependencies;
- stronger/generalizing claims satisfying weaker needs through transport;
- source-tree/extraction disagreement rates across representations;
- OCR/formula extraction error rates that affect mathematical interpretation;
- source-span remapping quality across PDF/EPUB/TeX artifacts;
- staleness after Mathlib/toolchain/source revisions;
- cumulative formal coverage of important books/papers;
- whether source seeding improves retrieval without excessive anchoring;
- cost/quality tradeoffs of eager versus lazy OCR/model-assisted extraction;
- how often native extraction quality was sufficient without expensive fallback.

## 25. Design decisions settled so far

The following are considered agreed unless later discussion revises them:

```text
general literature and existing paper handling converge on one source architecture
user has a persistent personal mathematical library shared across projects

BibliographicWork → EditionOrVersion → SourceArtifact → DerivedRepresentation
is the core source identity hierarchy
candidate metadata grouping is weaker than authoritative work/edition grouping
exact source artifacts are content-identified and distinct
Hardy copies admitted source bytes into its managed user-level library
external paths/URLs are provenance/acquisition inputs, not live backing stores
managed imports are immutable, digest-verified, atomic, and deduplicate identical bytes

multiple derived representations may coexist for one artifact
all representation-relative offsets name the exact representation
printed pagination and artifact pagination are distinct coordinate systems
source nodes carry typed multi-coordinate anchors, not one universal location
extraction pipelines emit explicit alignment mappings where possible
new extraction/parser versions create new representations/trees rather than moving old evidence
cross-artifact source-unit correspondence is explicit/evidenced
exact literature evidence can point to narrower durable SourceSpans than structural nodes
formulas, diagrams, page regions, and other non-text material remain first-class
OCR is a derived reading with provenance/quality metadata, not replacement source bytes
source retrieval always preserves machine-visible provenance refs

all source types use one common ingestion envelope
importers produce one or more artifact-bound DerivedRepresentations plus mappings/diagnostics
byte acquisition, extraction, SourceTree construction, and semantic interpretation remain separate stages
native source/text/layout structure is preferred when high quality
OCR, formula recognition, and model-assisted extraction are lazy/adaptive fallbacks or augmentations
multi-pass extraction is normal; no premature canonical-text collapse
per-representation quality profiles expose coverage/confidence/warnings/unmapped regions
partial extractor failure is recorded granularly instead of failing the whole import

large sources are represented by navigable versioned SourceTrees
source structure is useful before semantic/formal enrichment is complete
there is a cross-project exact mathematical claim registry
claim families/concepts assist discovery but do not control exact reuse
source nodes/spans connect to claims through evidenced interpretation links
multiple sources can express one claim without losing source provenance
claims can have explicit equivalence/generalization/specialization/etc. relations
formal realizations attach to claims rather than directly to source documents
Mathlib participates as a provider of formal realizations
project formalizations may remain local or be deliberately promoted
reusable promotion carries the minimal reusable dependency closure
project-local assumptions/context block automatic global promotion
shared Hardy Lean results form a reusable user-level formal library
later projects search reusable formal mathematics before reproving/reformalizing
project scope/evidence policy remains authoritative despite shared availability
citation and formal reuse remain distinct
source, claim, and formal versions never silently retarget one another
existing project/shared retrieval is reused rather than adding a hidden memory path
```

## 26. Remaining design areas

With the ingestion contract now settled below, remaining source-management design
areas are:

1. **SourceTree construction policy:** native structure versus deterministic parser
   versus model-assisted reconstruction; TOCs; theorem/proof/example/exercise
   extraction; review/refinement workflow.
2. **Library portability:** backup/export, multiple machines, privacy, and shared
   metadata without redistributing private source bytes.
3. **Bibliography/citation generalization:** extend current arXiv-centric identity
   while preserving exact artifact/source-span provenance and stable cite keys.
4. **Claim interpretation/admission workflow:** when source nodes get claim IDs,
   candidate matching/review, and acceptable automation.
5. **Shared formal-library packaging:** module layout, dependency promotion,
   environment/version compatibility, and how promoted Lean is built/imported.
6. **Source/claim/formal search API:** operations used by Explore, Research,
   acquisition, and delegation.

## 27. Ingestion and extraction pipeline contract

### 27.1 Common outer pipeline

Every source type uses one common ingestion envelope:

```text
external input/provider result
        ↓
managed immutable SourceArtifact
        ↓
format-specific extractor(s)
        ↓
one or more DerivedRepresentations
        ↓
RepresentationMappings + diagnostics + quality profiles
        ↓
SourceTree builder(s)
        ↓
later semantic claim interpretation
```

An importer therefore does not return “the text of the book.” It returns exact
artifact identity plus attributable representations and enough mapping/provenance
for later structural and semantic work.

Acquisition/import, extraction, SourceTree construction, and mathematical
interpretation are separate stages with separate failure modes and owners.

### 27.2 Common ingestion result

A format adapter should conceptually produce:

```text
IngestionResult
  source_artifact
  representations
  representation_mappings
  diagnostics
  quality_summary
  suggested SourceTree inputs
```

The exact class split is an implementation detail, but consumers must not need to
know whether a source entered as PDF, EPUB, TeX, or scan merely to ask for a
representation or source-tree input.

### 27.3 Born-digital PDF

A typical PDF pipeline may produce:

```text
PDF SourceArtifact
├── page manifest / page geometry
├── native text + layout representation
│   ├── blocks/lines/glyph or word ranges as available
│   └── page/bbox alignment
├── rendered page-image representation
├── printed-page-label mapping when recoverable
└── optional OCR/formula/layout augmentation
```

Native embedded text/layout should be preferred when quality is high. A 500-page
publisher PDF with clean text should not be OCRed in full simply because OCR exists.

### 27.4 Scanned or image-heavy PDF

A scan may instead use page images as the primary artifact-facing representation:

```text
scanned PDF SourceArtifact
├── page images
├── OCR text representation
│   ├── words/lines/tokens
│   ├── region confidence
│   └── image-region alignment
├── layout segmentation
└── formula/diagram recognition where needed
```

OCR remains a derived reading. The page image is the evidence-bearing source
region. Mathematical-symbol uncertainty should remain visible in quality metadata.

### 27.5 EPUB and HTML

EPUB/HTML adapters preserve document-native structure rather than flattening it
immediately:

```text
EPUB/HTML SourceArtifact
├── manifest/spine/resource graph
├── DOM/XHTML representation
├── normalized reading-order text
├── headings/anchors/links
└── DOM ↔ normalized-text mappings
```

Native structural IDs and hyperlinks are useful locators even when normalized text
is the representation sent to a model.

### 27.6 TeX/source trees

TeX/source archives or directories preserve native files and inclusion structure:

```text
TeX/source SourceArtifact
├── immutable admitted file tree/archive identity
├── native-source representation
├── include/input graph
├── assembled reading-order representation
├── theorem/environment/label hints
└── file/range ↔ assembled-text mappings
```

Nothing in an imported source tree is executed merely to ingest/read it. Existing
hostile-archive and source-admission principles from arXiv handling should be
reused/generalized.

Native TeX structure is often the highest-quality input to later `SourceTree`
construction, but published PDF artifacts may still supply authoritative pagination
and visual/formula evidence.

### 27.7 Plaintext and other simple text formats

Plaintext/Markdown-like scholarly material can produce a native text
representation directly, but still receives exact artifact identity and
representation-relative locators. Simplicity of format does not bypass provenance.

### 27.8 Multi-pass extraction is normal

One artifact may support several useful readings:

```text
native PDF text R1
OCR text R2
formula-aware layer R3
layout segmentation R4
page images R5
```

No representation is automatically “the canonical text.” A workflow may prefer R1
for prose, R3 for formula-heavy regions, and R5 when visual inspection is needed.

If Hardy later decides one representation is preferred for a particular operation,
that preference is a policy/view, not destruction or identity-merging of the others.

### 27.9 Quality profiles and granular failure

Each representation/extraction pass should expose quality and failure information
sufficient for downstream policy, such as:

```text
coverage
text confidence/quality
layout confidence
formula confidence
unmapped or unreadable regions
truncation
extractor warnings/errors
native-vs-OCR disagreement where measured
```

A source import can therefore succeed with partial derived quality:

```text
artifact admitted          ✓
page map                    ✓
native prose text           ✓
formula extraction          poor
TOC reconstruction          partial
OCR                         not needed / not run
```

Failure of one expensive or optional extractor does not invalidate the immutable
source artifact or successful representations. Diagnostics remain durable enough to
avoid treating missing work as a negative mathematical finding.

### 27.10 Lazy/adaptive expensive extraction

OCR, formula recognition, vision/model-assisted layout recovery, and other expensive
passes should be lazy/adaptive by default.

A typical policy is:

```text
import exact artifact
→ run cheap/native extraction and basic quality checks
→ build coarse navigational structure where possible
→ mark weak/unmapped regions
→ invoke expensive extraction only when:
     native extraction is missing or poor,
     SourceTree construction needs repair,
     a retrieval request reaches a weak region,
     a formula/diagram needs interpretation,
     or user/workflow explicitly requests enrichment
```

This reduces cost and avoids processing large books unnecessarily while preserving
the ability to enrich any region later.

“Lazy” does not mean ephemeral: once a materially used expensive representation is
created, it receives normal durable identity/provenance if later source nodes or
evidence depend on it.

### 27.11 Extraction planning is policy, not truth

A model may help decide that page 217 needs OCR or formula reconstruction, but the
model's choice to run an extractor establishes nothing about source content. The
resulting representation remains ordinary derived evidence with its own quality and
provenance.

Likewise, heuristics may automatically trigger OCR when a page has almost no native
text or obvious extraction corruption. These are execution policies, not semantic
claims.

### 27.12 Security/resource boundaries

All adapters should obey explicit byte/page/file/resource limits appropriate to the
format. Archives/directories remain hostile input; embedded objects do not gain
execution authority; extraction tools operate on managed copies rather than source
paths.

Resource exhaustion or unsupported features produce diagnostics and partial
results where safe. They do not authorize silently dropping pages/chapters and
presenting the remaining representation as complete.

### 27.13 Provenance-bearing delivery

Any representation text/image/formula returned to a later model or workflow carries
machine-visible refs to:

```text
SourceArtifact
DerivedRepresentation
SourceAnchor/SourceSpan or mapping segments
extractor/model/version provenance when relevant
quality/truncation status
```

The user-facing rendering can stay compact; provenance must survive internally so
claims/citations can reference exact source material without reconstructing where it
came from.

### 27.14 Format adapters do not own semantic interpretation

A PDF extractor may identify a visual block headed “Theorem 3.2”; a TeX parser may
identify a `theorem` environment. Those facts are candidates/structural evidence for
`SourceTree` construction.

They do not by themselves mint `MathematicalClaim`s, decide source-to-claim
faithfulness, admit external results into a project, or create formal realizations.
Those remain downstream semantic/trust operations.

This document should be updated in place as each remaining section is settled so
that the architecture does not depend on conversation memory.
