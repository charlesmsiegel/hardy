# Hardy general literature sources and reusable mathematical library

Status: **review-ready architectural specification**, 2026-09-10. This file is the
durable design record for general scholarly sources, cross-project mathematical
reuse, and shared formalization. It supersedes the earlier design seed and the
incremental notes that grew out of the delegation/swarm discussion.

Design branch: `general-literature-sources-spec`.

The design is intentionally backend/semantic. Rich UI/UX is a separate follow-on.
The implementation may choose concrete Pydantic class names and storage files, but
it must preserve the identity, provenance, authority, and reuse boundaries below.

Related Hardy architecture:

- `docs/superpowers/specs/2026-09-10-delegation-swarm-design.md` consumes the
  generic retrieval interfaces defined here. Delegation does not own literature
  ingestion or reusable formalization identity.
- Existing `hardy.literature` arXiv handling is a concrete acquisition adapter to
  generalize, not discard.
- Existing ledger, graph, evidence, scope, context, representation, transport,
  retrieval, formal verification, and workspace owners remain authoritative.
- Hardy already has project/shared retrieval and a user-level shared Lean location;
  this design extends those seams rather than inventing parallel memory/truth paths.

---

## 1. Goal

Hardy should accumulate reusable mathematical knowledge across projects.

If one project has already done the expensive work of understanding and formally
verifying a result from Hartshorne, Donagi's *Fibers of the Prym Map*, or another
source, later projects should discover and reuse that work instead of translating
or proving the same mathematics again.

The core architecture is:

```text
BIBLIOGRAPHIC WORK
        │
        ▼
EDITION / VERSION
        │
        ▼
SOURCE ARTIFACT
exact immutable bytes Hardy imported
        │
        ▼
DERIVED REPRESENTATIONS
text / OCR / layout / source assembly / page images / formulas
        │
        ▼
SOURCE TREE + EXACT SOURCE SPANS
what this artifact literally contains and where
        │
        ▼
SHARED MATHEMATICAL CLAIM REGISTRY
what exact mathematics those source units express
        │
        ▼
FORMAL REALIZATIONS
Mathlib declarations + reusable Hardy Lean declarations
```

These are deliberately different identities.

A theorem in Hartshorne is not a Lean declaration. A Lean declaration is not a
bibliographic source. Two source statements with different wording may express the
same mathematical claim. Two results both called “Riemann–Roch” may be materially
different claims. A stronger formal theorem may satisfy a weaker claim only through
an explicit, authenticated specialization/transport.

The reusable semantic unit is therefore **the exact mathematical claim**, not the
paper, book, theorem name, project, or formal declaration that happened to
introduce it. The authoritative textual unit remains the exact source artifact and
source span Hardy actually read.

### 1.1 Primary success cases

The architecture should make these ordinary:

```text
import Hartshorne once
→ navigate/search it in many Hardy projects
→ link individual statements to exact reusable claims
→ discover which are already in Mathlib
→ formalize missing ones only when needed
→ promote reusable project formalizations
→ never re-formalize the same exact result unnecessarily
```

and:

```text
first Prym project needs Donagi 1991 Theorem X
→ formalize Claim C
→ promote verified reusable closure

later genus-6 project needs the same Claim C
→ retrieve shared realization
→ import/reuse it
→ no repeated paper translation/proof
```

### 1.2 Non-goals

This design does not require:

- eagerly formalizing whole books or papers;
- proving mathematical equivalence from text similarity;
- a universal canonical natural-language statement for every theorem;
- redistributing copyrighted source bytes;
- replacing project ledgers with a global database;
- replacing Mathlib with Hardy's own library;
- committing private literature bytes to project repositories;
- a final visual literature-management UI;
- executing arbitrary TeX/source material during ingestion.

---

## 2. Architectural doctrine

The following rules govern the whole subsystem.

1. **Source identity, mathematical identity, and formal identity are distinct.**
   Never collapse a source node, a mathematical claim, and a Lean declaration.

2. **Work, edition/version, exact artifact, and derived representation are distinct.**
   Metadata similarity may propose grouping; authoritative grouping needs evidence.

3. **Exact claim identity controls formal reuse.** Names, embeddings, and text
   similarity generate candidates; they never establish substitutability.

4. **Reuse is cumulative across projects.** A reusable result proved once should be
   discoverable later when policy permits.

5. **Project-local does not imply globally reusable.** Local assumptions,
   temporary declarations, contextual transports, and project-only definitions
   remain local until deliberate promotion/generalization.

6. **Literature provenance survives semantic deduplication.** Many source nodes may
   express one claim; every exact source remains independently addressable.

7. **Formal validity and semantic faithfulness are separate.** Kernel verification
   does not prove that a Lean declaration means the source theorem; a semantic
   interpretation does not prove the Lean theorem.

8. **Indexing does not widen trust.** Finding a theorem in a book, linking it to a
   claim, or storing it in the shared library does not automatically allow a
   project to assume it.

9. **Mathlib is a formal provider, not the claim registry.** Mathlib declaration
   names do not define mathematical identity.

10. **The personal library is a shared source, not hidden LLM memory.** Delivery to
    projects goes through Hardy's authenticated project/shared retrieval boundary.

11. **All indexed source bytes are managed immutable imports.** External paths and
    URLs are acquisition provenance, never mutable backing stores.

12. **Derived indexes are rebuildable.** Durable authority is in exact artifacts,
    structured records, evidence, and formal artifacts, not search indexes.

13. **Private copyrighted bytes remain private/local by default.** Portable records
    may name digests and locators without carrying source bytes.

14. **Progressive enrichment beats eager whole-corpus analysis.** A 500-page book
    becomes useful before every theorem is semantically interpreted.

15. **No locator exists outside a coordinate system.** Page labels, PDF pages,
    source byte ranges, OCR offsets, DOM nodes, and image boxes are distinct.

16. **Improved extraction never silently moves old evidence.** New extraction,
    parsing, OCR, or tree construction creates versioned objects and mappings.

17. **Ingestion preserves plurality.** An importer produces attributable
    representations, not one lossy supposedly canonical text blob.

18. **Expensive extraction is adaptive.** Native/high-fidelity data comes first;
    OCR, formula recognition, and model repair are lazy unless needed/requested.

19. **There is one mathematical semantics system.** The cross-project claim
    registry reuses Hardy ledger concepts/relations/context/evidence wherever they
    fit; it is not a second theorem truth database.

20. **Discovery/proposal is never acceptance.** Models may propose source structure,
    claim matches, equivalences, or formal matches; existing admission/evidence
    owners decide what becomes durable/reusable.

---

## 3. Persistent personal mathematical library

Hardy maintains a user-level reusable mathematical library shared across projects.
Conceptually it contains:

```text
Personal Mathematical Library
├── bibliographic works
├── editions / source versions
├── exact source artifacts
├── derived representations
├── source trees / structural observations
├── source spans / locator mappings / correspondences
├── shared mathematical ledger
│   ├── concepts
│   ├── exact claims
│   ├── contexts/representations
│   ├── claim relations
│   └── evidence/history
├── source ↔ claim interpretation records
├── formal realization registry
├── reusable Hardy Lean source/artifacts
├── bibliography/citation metadata
└── rebuildable indexes/search structures
```

Projects do **not** copy this library into their own ledgers. A project records its
own uses, citations, obligations, imported/referenced shared claims, scope/trust
choices, and evidence.

### 3.1 Shared semantic ledger

The architectural term `MathematicalClaim` means an exact reusable mathematical
identity. Implementation should normally represent such reusable mathematics with
the same semantic machinery Hardy already uses in projects:

```text
ProjectItem (theorem / lemma / proposition / definition / construction / ...)
MathematicalContext
Relation
Representation / declaration records
EvidenceRef / ArtifactRef
Research/history records where appropriate
```

The shared library ledger is a user-level authoritative source for **shared
mathematical records**, while project ledgers remain authoritative for project
state. It must obey the same principles that graph reachability is not truth,
evidence is exact, scope/context matter, and historical records are not silently
rewritten.

Do not introduce a separate free-form “theorem memory” database whose entries can
bypass ledger/evidence policy.

### 3.2 Shared retrieval boundary

Hardy's existing retrieval layer already distinguishes project and `shared_library`
sources. The mathematical library plugs into that seam.

A shared record existing on the machine does not itself establish that it is:

- relevant to the current problem;
- allowed by current project scope;
- valid in the current mathematical context;
- formally importable in the current Lean environment;
- supported by the exact evidence requested.

Retrieval reauthenticates those conditions at use.

---

## 4. Source identity hierarchy

Hardy uses a deliberately small hierarchy rather than a full library-science
ontology:

```text
BibliographicWork
  ↓
EditionOrVersion
  ↓
SourceArtifact
  ↓
DerivedRepresentation
  ↓
SourceTree / SourceNode / SourceSpan
```

### 4.1 BibliographicWork

Human/intellectual publication identity: “Hartshorne's *Algebraic Geometry*” or
“Donagi's *Fibers of the Prym Map*.”

Representative fields:

```text
stable id
kind: book | paper | thesis | proceedings | notes | web_text | other
title
authors/editors
aliases / alternate titles
work-level identifiers if genuinely work-level
metadata assertions + provenance
```

A work is for discovery/grouping; it is insufficient to say what Hardy read.

### 4.2 EditionOrVersion

Specific published/released state:

```text
Hartshorne, GTM 52, Springer, 1977
specific corrected printing when identifiable
Donagi journal version
arXiv:1302.5946v1
arXiv:1302.5946v2
institutional-repository thesis revision
```

Representative fields:

```text
stable id
work ref
edition/version label
publisher/journal/venue
year/date
volume/issue/pages
ISBN / DOI / exact arXiv version / repository identifiers
language
correction/revision metadata
metadata provenance
```

Different versions stay distinct even when mostly identical. “Same theorem across
versions” is a later source/claim relation, not an assumption from metadata.

### 4.3 SourceArtifact

Exact immutable bytes Hardy imported for one source representation.

```text
SourceArtifact
  id / content sha256
  edition/version ref if established
  byte size
  format/media type
  managed storage ref
  acquisition/import provenance
  imported/fetched timestamp
  privacy/access policy
```

Several artifacts may belong to one edition:

```text
Hartshorne 1977
├── publisher PDF  sha256:A
├── EPUB           sha256:B
└── scan           sha256:C
```

Identical bytes deduplicate. Different bytes remain different artifacts.

### 4.4 Candidate grouping vs authoritative grouping

Hardy may propose that artifacts belong to the same work/edition from metadata,
internal front matter, identifiers, provider data, or strong structural similarity.
That is only a candidate relation.

Authoritative grouping requires reliable identity evidence appropriate to the
source type or explicit human confirmation. Title/year/author similarity alone is
insufficient.

This prevents revised printings, translations, unofficial scans, or nearby paper
versions from silently inheriting source/claim links.

---

## 5. Managed immutable import

Hardy indexes only bytes it owns in its managed user-level library.

```text
C:/Downloads/Hartshorne.pdf
        │ import
        ▼
~/.hardy/.../literature/artifacts/<sha256>/...
```

(The exact directory layout is an implementation detail.)

### 5.1 Import transaction

```text
read bounded/validated input
→ detect/validate format
→ compute digest
→ copy to temporary managed location
→ verify copied bytes/digest
→ atomically admit artifact + provenance record
→ derive/index only from managed copy
```

Interrupted/refused imports cannot leave half-admitted artifacts that later look
valid.

Re-importing the same bytes reuses the artifact and may add provenance aliases.
Changing/deleting the external original cannot change Hardy's copy. New bytes mean
a new artifact identity.

### 5.2 Provenance

Acquisition facts remain distinct from byte identity:

```text
original filename/path or provider handle
source URL/provider identity
retrieved/imported timestamp
MIME/detected format
user/provider metadata
adapter/version
privacy/access classification
```

Original local paths are diagnostic provenance, not durable source identity.

### 5.3 Archive/source security

Directories, EPUBs, TeX bundles, and source archives are hostile input:

- normalize/refuse traversal paths;
- refuse links when they could escape the managed tree;
- bound archive/download/uncompressed bytes and file counts;
- do not execute imported source;
- do not compile imported TeX merely to ingest it;
- extract into temporary managed storage and admit atomically;
- preserve exact archive bytes/digest where provenance depends on them.

This generalizes the current arXiv hostile-archive discipline.

---

## 6. Ingestion and derived representations

Every source uses one outer pipeline:

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
SourceTree construction
        ↓
semantic interpretation later
```

Importers do not mint mathematical claims.

### 6.1 DerivedRepresentation

An attributable reading of one exact artifact:

```text
id / digest
artifact ref
kind: native_text | normalized_text | ocr_text | page_images |
      layout | native_source | dom | formula_layer | other
retained output artifact refs
extractor/parser/model identity + version/configuration
input refs
derivation timestamp
quality/confidence/failure metadata
mapping refs back to artifact/parent representation
```

Improved OCR/parser output creates a new representation. Materially used old
representations retain durable identity even if regenerable.

### 6.2 Born-digital PDF

Typical outputs:

```text
page manifest / geometry
native text + layout blocks
page/bbox ↔ text mappings
rendered page images
printed-page labels when recoverable
optional OCR/formula/layout augmentation
```

Do not OCR an entire clean publisher PDF by default.

### 6.3 Scanned/image-heavy PDF

Typical outputs:

```text
page images
OCR text with word/line/region confidence
image-region ↔ OCR mappings
layout segmentation
formula/diagram recognition where needed
```

OCR is a derived reading; the page image remains source evidence.

### 6.4 EPUB / HTML

Preserve native structure:

```text
manifest/spine/resource graph
DOM/XHTML representation
normalized reading-order text
headings/anchors/links
MathML/media refs
DOM ↔ normalized-text mappings
```

### 6.5 TeX/source trees

Preserve native files and inclusion structure:

```text
immutable admitted file tree/archive
native-source representation
include/input graph
assembled reading-order representation
environment/label/reference observations
file/range ↔ assembled-text mappings
```

Existing Hardy TeX statement inventory should become a high-confidence structure
producer inside this generalized pipeline, not be replaced.

### 6.6 Plain text / Markdown / simple sources

They still receive exact artifact identity and representation-relative spans.
Simple format does not bypass provenance/versioning.

### 6.7 Multi-pass extraction

Multiple useful readings may coexist:

```text
native PDF text R1
OCR text R2
formula layer R3
layout segmentation R4
page images R5
```

A workflow may prefer different representations for prose, formulas, navigation,
or visual inspection. “Preferred for operation X” is policy, not identity merging.

### 6.8 Quality profile

Each pass exposes enough quality/failure data for downstream policy:

```text
coverage
text quality/confidence
layout quality
formula quality
unmapped/unreadable regions
truncation
warnings/errors
native-vs-OCR disagreement where measured
```

Partial extraction failure does not invalidate a valid artifact or successful
representations.

### 6.9 Lazy/adaptive enrichment

Default policy:

```text
cheap/native extraction
→ coarse structure/quality checks
→ mark weak regions
→ run OCR/formula/vision/model work only when:
   - native extraction is missing/poor,
   - SourceTree repair needs it,
   - retrieval reaches the weak region,
   - a formula/diagram needs interpretation,
   - or user/workflow explicitly requests enrichment
```

Once a lazy representation becomes evidence-bearing, it receives durable identity.

---

## 7. Multi-coordinate locators and mappings

There is no universal “location in a book.”

The same theorem may have:

```text
printed page 128
PDF artifact page index 147 + bounding boxes
native-text blocks 3812–3830
OCR token spans
EPUB spine item + DOM nodes
TeX file + byte ranges
```

### 7.1 SourceAnchor

Typed coordinate, never opaque/naked offset:

```text
SourceAnchor
  artifact/representation ref
  locator kind
  typed payload
  derivation/provenance
  confidence/quality when relevant
```

Representative locator kinds:

```text
RepresentationSpan
ArtifactPageRegion
PrintedPageLocator
NativeSourceSpan
DOMLocator
ImageRegion
```

Compound/noncontiguous ranges are allowed.

### 7.2 Printed pages vs artifact pages

Printed labels and PDF/image indices are separate facts. A second scan can map a
different PDF page to the same printed page. Page-label mappings are themselves
derived/evidenced metadata.

### 7.3 Representation-relative offsets

Offsets are valid only inside the exact representation they name. Never persist:

```text
start_character = 193442
```

without the representation identity.

### 7.4 RepresentationMapping

Explicit partial/many-to-many mappings among coordinates:

```text
PDF page/bbox      ↔ native text blocks
page image/bbox    ↔ OCR words/tokens
EPUB DOM           ↔ normalized text
TeX file/range     ↔ assembled reading text
formula span       ↔ page image region
```

Uncertain mapping remains uncertain; never invent exact alignment.

### 7.5 Exact SourceSpan

Structural nodes are navigation; evidence often needs a narrower span:

```text
SourceSpan
  id
  source node ref if applicable
  exact representation range(s)
  artifact anchor(s) where available
  digest of delivered/extracted content
  representation/mapping provenance
```

Source-backed evidence can therefore identify the exact hypotheses/conclusion it
actually read.

### 7.6 Non-text mathematics

Equations, aligned displays, diagrams, figures, tables, and page-image regions are
first-class source material. A formula may have TeX source, PDF bbox, MathML,
OCR/model interpretation, and normalized text simultaneously. No lossy rendering
erases the original evidence.

---

## 8. SourceTree construction

A `SourceTree` is an artifact-bound, versioned structural interpretation—an AST-like
view of what the artifact contains.

**One SourceTree belongs to one exact SourceArtifact.** It may use several derived
representations of that artifact. PDF and EPUB trees for the same edition remain
separate and connect through explicit correspondence.

### 8.1 Construction pipeline

```text
native structural producers
        ↓
StructuralObservations
        ↓
deterministic reconstruction
        ↓
partial SourceTreeCandidate + ambiguity map
        ↓
optional bounded model/human repair
        ↓
mechanical validation
        ↓
admitted versioned SourceTree
```

### 8.2 StructuralObservation

Evidence about document structure, not yet a durable tree claim:

```text
input artifact/representation
observation kind
anchor/span
payload
producer + version
quality/provenance
```

Examples include explicit TeX `\section`, EPUB headings, PDF outline entries,
visual “Theorem 5.2” headings, `Proof.` markers, TOC entries, labels, refs, equation
numbers, and font/layout transitions.

### 8.3 Native structure first

Use explicit source structure whenever available:

- TeX sectioning, theorem/proof environments, labels, refs, includes;
- EPUB/HTML headings, sections, anchors, links, MathML, spine/TOC;
- PDF outline, tags/page labels, link destinations, layout blocks;
- existing provider metadata.

Hardy's current TeX inventory principles remain: follow actual included/executable
source where safely statically readable; do not infer printed theorem numbers that
only compilation would create.

### 8.4 Deterministic reconstruction second

Combine observations using format-independent rules: heading hierarchy, numbering,
TOC support, typography, proof markers, labels/refs, equation numbering, reading
order, DOM/TeX containment, and repeated layout patterns.

Partial/unknown structure is acceptable:

```text
Theorem 3.2
  statement boundary: high confidence
  proof start: probable
  proof end: unresolved
```

Never fabricate missing theorem nodes simply to make numbering continuous.

### 8.5 Model-assisted repair third

Models operate only on bounded exact source windows plus available structural/
visual metadata. They may classify or propose boundaries/parentage for material they
were actually shown.

They may not generate absent source structure from expectation.

Model output is a structured proposal with anchors, supporting observations,
provenance, and uncertainty; it does not write directly to the durable library.

Model repair is lazy/adaptive. Clean TeX may need zero calls; a difficult scan may
need many local repairs.

### 8.6 Structural validation

Before admission check at least:

```text
all anchors derive from the tree artifact
all representation refs derive from that artifact
parent/child graph acyclic
reading order coherent
span/parent relationships internally consistent where applicable
no source material silently disappears from claimed-complete regions
source numbering marked explicit vs inferred
proof_of targets plausible statement nodes
no fabricated locations outside mapped material
truncation/gaps/ambiguities represented
```

Numbering gaps produce diagnostics, not claims of absence.

### 8.7 Tree/node versioning and structural sharing

Parser improvements do not mutate old nodes used by evidence.

A newer tree may refine an old one. Unchanged nodes should retain/reuse stable
structural identity/version where artifact, anchors, kind, and parentage are
materially unchanged; changed boundaries/kind/parentage create new node versions.

This avoids invalidating thousands of unrelated claim links when one page is fixed.

Trees/nodes may be preferred/superseded for new retrieval without deleting
historical identity.

### 8.8 Node kinds

At minimum support navigable units such as:

```text
part / chapter / section / subsection
paragraph
definition / theorem / lemma / proposition / corollary / claim / conjecture
proof
construction / example / exercise / solution / remark
formula/equation/display / diagram / figure / table
bibliography/index/front matter/appendix
unknown/other structured block
```

Proofs are separate nodes linked `proof_of`; statement/proof retrieval must be
independent.

### 8.9 Source-level reference graph

Alongside containment, record document-level relations:

```text
proof_of
source_refers_to
source_cites
continues_from
uses_numbered_equation
source_defines_or_labels
```

These mean “the document refers to this source unit,” not “mathematical logical
dependency is established.” Mathematical dependencies live in the claim graph.

### 8.10 TOCs and indexes

Tables of contents and indexes are strong navigational evidence, not absolute tree
authority. The anchored body material remains authoritative for what actually
occurs in the artifact.

---

## 9. Cross-artifact correspondence

Even artifacts authoritatively grouped under one edition have independent trees.
Hardy can establish explicit correspondences:

```text
SourceCorrespondence
  left node/span
  right node/span
  relation: same_source_unit | overlapping | variant | translated | other
  evidence/provenance
```

Candidate correspondence may use numbering, headings, exact/controlled-normalized
text, structural position, identifiers, or model suggestions. Authoritative
correspondence requires appropriate evidence/review.

This permits, for example, reading clean EPUB text while showing/citing publisher
PDF pages without pretending the two artifacts are one source tree.

Cross-edition correspondence is allowed but should default to `variant`/candidate
until semantic comparison shows whether the mathematics is unchanged.

---

## 10. Mathematical claim registry

The source tree alone cannot provide formalization reuse because it is tied to one
document's wording/notation. A Lean declaration is tied to one formal
representation/environment. Hardy therefore needs reusable exact mathematical
identity between them.

### 10.1 MathematicalClaim is an architectural role

Conceptually:

```text
SourceSpan ──expresses──> MathematicalClaim <──realizes── FormalRealization
```

Implementation should map the claim role onto Hardy's existing ledger semantics
where possible—typically a shared-library `ProjectItem` of the appropriate kind plus
exact mathematical context/representations/relations/evidence—not create a parallel
free-form theorem store.

Representative semantic content:

```text
stable shared ref
kind
human/navigational name
informal statement/summary
semantic context
parameters/declarations
hypotheses
conclusion/body
representation choices where identity-relevant
known dependencies
aliases/search terms
concept/family membership
creation/interpretation provenance
```

Natural-language normalization is not mathematical identity. Meaning-changing
revision creates a new item/version with explicit relations.

### 10.2 Claim families/concepts

“Riemann–Roch” is a useful concept/family, not an exact reusable theorem.

```text
Concept: Riemann–Roch
├── divisor formulation on smooth projective curves
├── line-bundle formulation
├── stronger field-general version
└── scheme-theoretic generalization
```

Use existing Hardy concept/representation semantics where appropriate. Family
membership aids retrieval; it does not establish equivalence.

### 10.3 Claim relations

Use existing `Relation` kinds when they match; extend only when the semantic
relation is genuinely missing. Important relations include:

```text
equivalent_to
generalizes
specializes
implies
refines
interprets / reformulates
transported_from
```

A relation that enables proof reuse requires sufficient evidence/transport. Similar
text or independent model agreement is not proof of equivalence.

### 10.4 Duplicate policy

Candidate duplicate detection may use exact text, normalized structure, embeddings,
formal types, aliases, and source correspondences.

Outcomes:

```text
exact established identity       → reuse existing shared claim
near duplicate / uncertain       → keep separate + cluster/propose relation
proved/evidenced equivalence     → explicit relation; merge identity only if policy truly warrants it
stronger/weaker                  → separate claims + relation
```

When uncertain, duplicate mathematical claims are preferable to an incorrect merge.
They can be reconciled later; a false merge corrupts reuse.

---

## 11. Source-to-claim interpretation and admission

Structural extraction identifies what a source says. Semantic interpretation asks
what mathematics it means.

### 11.1 SourceClaimLink

```text
SourceClaimLink
  exact source node/span refs
  shared claim ref
  relation: expresses | specializes | generalizes | reformulates | other
  notation/object/representation mapping
  semantic assumptions/context mapping
  faithfulness/interpretation evidence
  interpreter/verifier provenance
  status/history
```

A theorem extractor saying “Theorem 3.2” is not sufficient evidence that it
expresses any particular shared claim.

### 11.2 Interpretation workflow

```text
exact SourceNode/SourceSpan
→ construct bounded semantic interpretation proposal
→ search existing shared claims + Mathlib candidates
→ compare hypotheses/conclusion/context/representations
→ one of:
    link to existing exact claim
    link via stronger/weaker/reformulation relation
    create a new shared claim candidate
    preserve ambiguity / request review
→ produce faithfulness evidence
→ admit link/claim through shared-library policy
```

Models can perform proposal/comparison work. Reusable semantic identity is admitted
only under the configured faithfulness/evidence policy.

### 11.3 Default automation boundary

Hardy should scale beyond mandatory human review of every source theorem, but it
must not lower identity standards to do so.

Default policy:

- structural nodes may be generated automatically with provenance/quality;
- candidate claim interpretations and matches may be generated automatically;
- exact reusable `SourceClaimLink`s require the same kind of authenticated
  faithfulness evidence Hardy uses for informal↔formal interpretation or explicit
  authorized human approval;
- low-confidence/ambiguous interpretations remain proposals;
- project trust/admission remains separate even after a reusable source↔claim link
  exists.

The concrete verifier may evolve; the semantic distinction must not.

### 11.4 Many sources, one claim

Expected:

```text
Donagi 1991 theorem ─┐
Survey theorem ──────┼──> shared Claim C
Textbook proposition ┘
```

Every exact source span remains independently citable/auditable.

### 11.5 One source, ambiguous meanings

Competing interpretations remain separate proposals/links until adjudicated. Hardy
must not choose the interpretation that conveniently matches an existing
formalization merely because it saves work.

### 11.6 Definitions and constructions

The registry is not only theorems. Source definitions/constructions can link to
shared concept/representation/declaration records. A source's definition may be:

```text
exactly the shared concept
one representation of the shared concept
a stricter/weaker variant
a source-local convention
```

These distinctions are essential to later theorem matching.

---

## 12. Formal realizations

A `FormalRealization` records an exact formal declaration realizing an exact shared
claim.

Representative content:

```text
id
shared claim ref
system: lean
origin: mathlib | hardy_shared | project | external_formal_library
declaration/module
exact elaborated formal statement / declaration identity
source/module artifact digest
environment/toolchain identity
required imports
formal verification/audit evidence
semantic faithfulness evidence
used assumptions / trust boundary
formal context/representation mapping
provenance/history
```

Kernel verification without claim faithfulness is insufficient for semantic reuse;
faithfulness without formal verification is insufficient for proof reuse.

### 12.1 Mathlib

Mathlib is continuously searchable as a formal provider. Candidate discovery may
use names, docs, types, embeddings, nearby declarations, or theorem search.

Before attaching a declaration to a claim, check exact formal statement,
quantification, implicit typeclasses, ambient categories, hypotheses,
representations, and conventions.

Record exact environment/Mathlib revision. A later environment may make the
realization unavailable/import-incompatible without changing the mathematical
claim.

### 12.2 Project-local realizations

A project theorem can realize a shared claim while remaining project-local. This is
useful for that project and for promotion analysis, but other projects cannot simply
import it unless its artifact/context is intentionally made available.

Context, scope, assumptions, and exact source artifact remain explicit.

### 12.3 Revalidation

Retrieval of a formal realization reauthenticates present importability against the
requesting Lean environment and required imports. Historical proof evidence is not
silently upgraded to current-environment validity.

---

## 13. Promotion to the shared Hardy formal library

Hardy already has a user-level shared Lean seam. Promotion turns selected verified
project mathematics into durable reusable formal infrastructure.

### 13.1 Promotion is explicit

The shared formal library is curated reusable mathematics, not every theorem ever
proved. Promotion can be user-requested, workflow-proposed, or policy-triggered, but
it is a distinct operation with its own admission record.

Good candidates include generally useful literature theorems such as results from
Hartshorne/Donagi that several projects may need. Highly local helper lemmas may
remain project-local.

### 13.2 Promotion criteria

A realization is promotable when:

- Lean declaration is freshly authenticated/verified;
- semantic faithfulness to an exact shared claim is established;
- trust boundary/used assumptions are explicit and acceptable;
- no hidden project-only hypotheses/declarations remain;
- required context/representations can be reconstructed elsewhere;
- dependency closure is reusable or promoted with it;
- promoted code builds independently of originating project state;
- exact shared artifact/module/declaration identity can be recorded.

### 13.3 Reusable dependency closure

Compute the minimal formal dependency closure:

```text
Target theorem
├── Mathlib dependency       → external import
├── existing shared theorem  → reuse
├── reusable project lemma   → promote too
└── project-specific fact    → promotion blocker
```

A theorem proved from a project-local axiom cannot silently become a globally
verified theorem.

### 13.4 Generalization/transport during promotion

Promotion may create obligations to:

```text
remove a project-specific hypothesis
generalize a chosen object to a parameter
replace local definitions with shared representations
prove transport/equivalence to a reusable formulation
factor reusable dependencies out of project modules
```

The promoted result may be a new claim/realization related to the project theorem;
historical project identity remains unchanged.

### 13.5 Packaging

The shared Lean library should be an ordinary buildable source tree under Hardy's
user-level formal-library ownership, not generated opaque proof blobs.

Guidelines:

- human-readable domain/module structure where practical;
- stable declaration names once published to projects;
- exact source/module digests and environment identity recorded;
- append/new-version behavior preferred over silently changing theorem meaning;
- current build artifacts are derived and rebuildable;
- imports from the shared library participate in normal Hardy workspace/build
  assembly;
- promoted modules may import Mathlib and other shared modules, not project roots.

Concrete module naming is implementation policy and need not encode source citation
names. The declaration realizes a mathematical claim; source provenance lives in
records/docstrings/evidence, not necessarily the Lean namespace.

### 13.6 Update/staleness policy

Bug fixes that preserve a theorem's exact formal type may update implementation
artifacts with new digests/evidence. Meaning/type changes create a new realization
or declaration version and explicit supersession/relation; do not silently retarget
historical imports/evidence.

### 13.7 Promotion provenance

Record:

```text
originating project/result
shared claim
formal dependency closure
rewrites/generalizations/transports
verification + faithfulness evidence
trust/assumption audit
shared module/declaration
source/module digest + environment
actor/reason/time
```

---

## 14. Reuse/acquisition resolver

When a project needs mathematics, Hardy checks reusable knowledge before new proof
work.

Conceptual preference:

```text
1. exact established result already in current project
2. exact compatible Mathlib realization
3. exact compatible Hardy shared realization
4. equivalent/stronger realization with authenticated transport
5. exact source-backed literature result usable under project scope
6. new formalization/proof work
```

This is not required to be six serial expensive searches; indexes can search layers
jointly.

### 14.1 Result classes

The resolver must distinguish:

```text
EXACT CLAIM + FORMAL REALIZATION
  reusable after environment/scope checks

EXACT CLAIM, SOURCE ONLY
  literature statement known, no reusable formal realization

STRONGER/WEAKER/EQUIVALENT CLAIM
  potentially reusable through recorded relation/transport

RELATED/FAMILY MATCH
  navigational only

FORMAL CANDIDATE
  Mathlib/shared declaration not yet semantically authenticated

SOURCE LEAD
  search result / likely source not yet admitted/interpreted
```

No “found theorem with similar name, therefore solved.”

### 14.2 Acquisition obligations

If no reusable realization exists, Hardy can create ordinary project obligations:

```text
locate exact source statement
interpret/link claim
find Mathlib realization
formalize claim
prove missing reusable dependency
resolve representation mismatch
promote reusable result
```

These reuse existing project/obligation semantics.

---

## 15. Bibliography and citation generalization

The current arXiv bibliography has strong properties worth preserving: one controlled
writer, stable keys, no model-authored metadata, and exact content digests.
General literature broadens identity without weakening those guarantees.

### 15.1 Citation identity

A project bibliography entry cites an `EditionOrVersion`, not merely a title and not
an arbitrary artifact path. It also records the exact artifact digest(s)/source
spans Hardy actually used.

Conceptually:

```text
CitationEntry
  stable cite key
  work ref
  edition/version ref
  rendered bibliographic metadata
  canonical external identifiers
  exact read artifact digests
  optional exact cited SourceSpan refs
  cited_at / provenance
```

Two artifacts of the same authoritatively established edition may produce one
bibliographic entry while retaining several `read_artifacts`. Two distinct
versions/editions do not silently collapse just because DOI/title/author overlap.

### 15.2 Stable cite keys

Keys are deterministic functions of bibliographic identity plus a collision-proof
identity suffix, not citation order. Same edition/version gets the same key across
projects/runs. Distinct versions can receive distinct keys when both appear.

Existing human-readable author/year/title stem behavior may remain, generalized
from `PaperRecord` to edition/version metadata.

### 15.3 Exact source provenance vs rendered citation

The reader sees a normal bibliography entry. Hardy internally retains exact artifact
and span provenance supporting the citation/claim.

This permits:

```text
\cite{donagi1991...}
```

while Hardy still knows the theorem was read from artifact SHA A, pages X–Y, span S.

### 15.4 Citation and formal reuse are independent

```text
SourceSpan S --expresses--> Claim C <--realizes-- Lean R
```

A project may cite S, use R, do both, or—when publication policy allows—use R
without citing the historical source. Formal verification does not retroactively
prove that S expresses C.

### 15.5 No hand-written unvouched bibliography path

The existing principle remains: project publication should not accept model-invented
bibliographic metadata through a side channel. Citations originate from admitted
library records; manual/user metadata correction is a controlled metadata
reconciliation operation with provenance.

---

## 16. Search and retrieval API

The backend should expose composable operations; UI syntax is deferred.

### 16.1 Source discovery/navigation

Conceptual operations:

```text
search_sources(query, filters)
find_work / find_edition / list_artifacts
source_map(source)
list_children(node)
list_statements(section/source)
resolve_source_alias("Hartshorne II.5.8")
read_source(node/span, representation preference)
read_statement(node)
read_proof(node)
read_context(node, before/after)
show_original_region(span/formula)
corresponding_source_units(node, artifact/edition filters)
```

All delivery carries machine-visible provenance refs and bounded output.

### 16.2 Semantic/reuse retrieval

```text
search_claims(query/context)
claims_for_source(node/span)
sources_for_claim(claim)
relations_for_claim(claim)
formal_realizations(claim, environment)
resolve_reusable_claim(target, project scope/context/environment)
projects_using_claim(claim)       # where accessible
formal_coverage(source)
```

### 16.3 Import/enrichment operations

```text
import_source(file/provider/url result)
reconcile_work_or_edition(candidate)
enrich_region(source/page/node, requested pass)
build_or_refine_source_tree(artifact)
propose_source_claim_link(node/span)
admit/review interpretation
promote_formal_realization(project result)
```

UI may combine operations; backend authority boundaries remain distinct.

### 16.4 Search ranking

Prefer:

1. exact IDs/aliases/source numbering;
2. structural relations and exact concepts;
3. authenticated claim/formal links;
4. lexical/full-text matches;
5. semantic/vector similarity.

Embeddings are candidate generators. A fuzzy result is never rendered as an exact
identity match.

### 16.5 Bounded/lazy retrieval

A 500-page seeded book contributes a compact source map/index to context, not its
text. Retrieval expands exact subtrees/spans lazily. Models should be able to ask
for proofs, surrounding definitions, original pages, or related claims without
receiving unrelated chapters.

---

## 17. Project and delegation integration

### 17.1 Seeding

“Seed this project/run with Hartshorne” means:

- choose an exact admitted edition/artifact (or explicit edition view over known
  artifacts);
- make its compact SourceTree/index prominent in retrieval;
- increase its retrieval priority;
- permit lazy exact span/proof/formula retrieval;
- expose known claim/formalization links when policy permits;
- do not inject the entire source into every prompt.

### 17.2 Delegation

Delegation workers receive source references/retrieval permissions through their
context manifests. Different workers may be seeded with different sources/subtrees
or literature intents while sharing exact source identity.

Cross-pollination shares exact findings/source handles, not whole books. Existing
visibility/isolation rules apply to source retrieval too.

### 17.3 Project ledger use

When shared mathematics enters a project, the project records ordinary links/
relations/obligations rather than copying global truth. Project-specific context,
representation choices, trust scope, dependencies, and acceptance remain project
state.

Shared existence does not mutate project scope automatically.

---

## 18. Versioning and staleness

### 18.1 Source artifacts/editions

New arXiv version, new edition, corrected scan, or different bytes → new identity.
No old source link silently retargets.

### 18.2 Derived representations/trees

Improved OCR/parser/tree → new representation/tree/node versions. Historical links
stay attached to the exact inputs they interpreted. New preferred versions can
trigger review of affected links when outputs materially differ.

### 18.3 Claims

A corrected/strengthened statement that changes mathematical meaning receives a new
shared claim/version with explicit relation to the old one.

### 18.4 Formal realizations

Mathlib/toolchain/library changes can make a realization unavailable or stale while
the claim remains unchanged. Revalidate environment/importability independently.

### 18.5 Dependency-driven stale review

If a source span materially changes under a newly preferred extraction, or a
faithfulness mapping is invalidated, dependent `SourceClaimLink`s should become
review-needed—not silently deleted and not automatically declared false.

If a shared formal realization changes proof artifact but retains exact theorem type
and passes current verification, downstream mathematical claim identity need not
change.

---

## 19. Privacy, copyright, portability, and multi-machine behavior

### 19.1 Private by default

User-supplied copyrighted artifacts remain in the user-level library. Hardy does
not assume permission to redistribute, upload wholesale to unrelated services, or
commit them to repositories.

Per-artifact/access policy should distinguish at least:

```text
private_local
redistributable
public_provider_retrievable
unknown/restricted
```

Adapters/executors must respect it.

### 19.2 Derived-data privacy

OCR/full extracted text from a copyrighted private book can itself contain the book
and should inherit appropriately restrictive handling. A claim statement,
bibliographic metadata, digest, source locator, or user-authored Lean theorem can
have a different portability policy.

Privacy is not simply “original bytes private, everything derived public.”

### 19.3 Export/backup layers

Support logically separate export classes:

```text
METADATA/SEMANTICS
  work/edition metadata, digests, source identities, claim graph, formal registry

USER-AUTHORED FORMAL LIBRARY
  promoted Lean source/artifacts/evidence subject to user's chosen portability

PRIVATE SOURCE CACHE
  original literature bytes + copyright-sensitive derived representations
```

Default portable project/repo state should not include private source cache bytes.

### 19.4 Missing artifacts on another machine

A transferred semantic registry can retain a source ref/digest even if bytes are
absent. Retrieval reports `source unavailable locally` rather than pretending the
citation/claim never existed.

If the same exact artifact digest is later imported/fetched legally, the historical
refs become readable again without reminting identity.

### 19.5 Synchronization

Multi-machine sync may be added later, but synchronization must be content-addressed
and conflict-safe. A cloud folder must not become a mutable backing-store identity.

Shared ledgers/formal source need revision/conflict handling analogous to Hardy's
other append/transaction stores; private artifacts deduplicate by digest.

---

## 20. Crash safety, concurrency, and authority

### 20.1 Immutable artifact admission

Artifact imports are atomic and content-addressed. Parallel imports of identical
bytes coalesce safely rather than racing two mutable records.

### 20.2 Derived work

Extraction/index/tree building may run concurrently because outputs are immutable/
versioned. Final registration uses serialized/optimistic admission against current
library state.

### 20.3 Shared semantic ledger

Shared mathematical claim/context/relation/evidence mutations use the same kind of
revision-aware append/transaction policy as project ledgers. Two projects cannot
silently overwrite one another's reusable claim interpretation.

### 20.4 Formal promotion

Promotion follows current-head/staged verification principles:

```text
prepare reusable closure
→ reconcile against current shared formal/library head
→ stage shared Lean changes
→ build/audit in exact current environment
→ commit source/artifacts
→ publish realization/evidence/ledger records
```

No shared success claim before durable semantic/evidence admission succeeds.

### 20.5 Failed work remains distinguishable

The system must distinguish:

```text
artifact import failed
artifact admitted; extractor failed
representation partial
SourceTree partial/ambiguous
claim interpretation proposed but unadmitted
formal candidate unmatched
promotion blocked
formal realization stale/unimportable
```

These are not all “source unavailable.”

---

## 21. Failure semantics and diagnostics

Hardy should fail in ways that preserve the user's mathematical state.

### 21.1 Absence is not failure and failure is not absence

Examples:

- search found no match ≠ theorem does not exist;
- parser missed Theorems 4.3–4.7 ≠ source omits them;
- OCR could not read a formula ≠ formula is blank;
- no known formal realization ≠ no Mathlib theorem exists;
- failed exact-claim match ≠ claims are inequivalent.

Return diagnostics that let later work continue.

### 21.2 Quality-aware delivery

When a requested region is low-confidence, retrieval can provide the best text plus
quality warnings and offer/trigger original-image inspection or targeted enrichment.
It must not silently present uncertain OCR as exact source transcription.

### 21.3 Contradictory representations

If native text, OCR, and formula recognition disagree materially, preserve the
conflict and source anchors. Do not majority-vote the text into one synthetic truth.

### 21.4 Broken metadata

Bibliographic reconciliation can remain unresolved while artifact reading works.
A user should still be able to read/index an unknown PDF; lack of ISBN/DOI does not
block source utility.

---

## 22. Relationship to current arXiv subsystem

Keep these existing strengths:

- search results are leads, not citations;
- exact versioned identity is pinned before use;
- metadata/source bytes carry digests;
- machine-local library bytes;
- hostile source archive handling;
- bounded reads;
- one controlled bibliography writer;
- extracted statements separate from trust/assumption minting.

Generalize them into source adapters and common records.

`PaperRecord`/`PaperLibrary` can remain compatibility/convenience layers during
migration, but downstream workflows should increasingly consume generic
work/edition/artifact/tree/span interfaces.

Current TeX source inventory becomes a native structural producer for source trees.
Current `cite_paper` behavior becomes the arXiv-specific façade over generic
citation admission.

---

## 23. Rebuildable indexes

Useful indexes include:

```text
work/edition/artifact metadata index
source full-text index
SourceTree structural/alias index
page/locator index
cross-artifact correspondence index
claim text/alias/concept index
claim relation graph
source span ↔ claim index
claim ↔ formal realization index
Lean declaration/name/type index
vector/semantic indexes where useful
project usage/citation index
```

Indexes establish relevance/navigation, never mathematical truth or identity.

Every authoritative result returned from an index must resolve back to durable
records/artifacts before use.

---

## 24. Evaluation

Evaluation should measure whether the library actually saves mathematicians work
without corrupting identity/provenance.

### 24.1 Source ingestion/structure

Measure:

- artifact dedup/reconciliation accuracy;
- native extraction coverage/quality by source type;
- OCR/formula error rates, especially mathematically material symbols;
- SourceTree node precision/recall on labeled books/papers;
- theorem/proof boundary accuracy;
- printed-page mapping accuracy;
- cross-artifact correspondence accuracy;
- cost saved by lazy vs eager OCR/model reconstruction;
- rate at which model repair creates false structure;
- stability of unchanged node identity across parser versions.

### 24.2 Semantic claim matching

Measure:

- exact source↔claim match precision/recall;
- false merges vs duplicate-but-separate claims;
- stronger/weaker/equivalent relation accuracy;
- formal declaration↔claim faithfulness;
- human/verifier correction rate;
- source-version changes that invalidate prior interpretations.

False merges should be treated as much more costly than temporary duplicate claims.

### 24.3 Reuse value

Measure:

- fraction of project theorem needs satisfied by existing Mathlib/shared
  realizations;
- formalization effort saved across projects;
- how often Donagi/Hartshorne-like results are reused rather than redone;
- project results proposed/promoted vs actually reused later;
- promotion blockers caused by local assumptions/dependencies;
- stronger reusable theorems satisfying weaker requests through transport;
- time/cost from literature need to usable formal theorem.

### 24.4 Retrieval/context

Measure:

- seeded-source retrieval success;
- context tokens used per useful source finding;
- lazy subtree/span retrieval vs whole-document context;
- source-map usefulness;
- error rate caused by low-quality representation selection;
- provenance completeness of source-backed findings.

Do not collapse all metrics into one scalar prematurely.

---

## 25. Conceptual contracts

Exact class names may change. These semantic boundaries are fixed.

```text
BibliographicWork
  human/intellectual publication identity

EditionOrVersion
  specific published/released/versioned state

SourceArtifact
  exact managed immutable bytes

ImportProvenance
  where/how the bytes were acquired

DerivedRepresentation
  attributable text/OCR/layout/source/image reading

RepresentationMapping
  explicit coordinate alignment among representations/artifact

QualityProfile / ExtractionDiagnostic
  coverage/confidence/failure without semantic interpretation

StructuralObservation
  local evidence about document structure

SourceTree / SourceNode
  versioned artifact-bound document structure

SourceAnchor / SourceSpan
  exact typed location/evidence within named coordinates

SourceCorrespondence
  evidenced relation among source units across trees/artifacts

MathematicalClaim
  architectural role: exact reusable semantic item in shared Hardy ledger

ClaimFamily / Concept
  looser discovery grouping

SourceClaimLink
  evidenced interpretation from exact source material to shared claim

ClaimRelation
  semantic relation represented through shared ledger relations

FormalRealization
  exact Lean declaration + environment + formal/faithfulness/trust evidence

PromotionRecord
  project realization → shared formal realization with reusable closure

CitationEntry
  project citation of exact edition/version with read-artifact/span provenance

SourceUsage / ProjectLink
  project-specific use/authorization of shared material

LibraryRevision / admission journal records
  crash-safe authoritative shared mutations
```

---

## 26. Core invariants

```text
work != edition/version != artifact != derived representation
source node != mathematical claim != Lean declaration

indexed source bytes are Hardy-managed immutable imports
external paths/URLs are acquisition provenance, never live backing stores
identical bytes deduplicate; different bytes remain distinct artifacts
candidate bibliographic grouping != authoritative grouping

one SourceTree belongs to one exact SourceArtifact
PDF/EPUB/scan trees never silently fuse
printed page label != PDF/image page index
representation offsets always name exact representation identity
source evidence never stores naked offsets
mappings may be partial/uncertain; uncertainty is not fabricated precision

new OCR/parser/tree versions do not relocate historical evidence
unchanged source nodes should structurally share stable identity where safe
cross-artifact source correspondence is explicit and evidenced
formulas/diagrams/images remain first-class source material

native/high-fidelity extraction precedes expensive OCR/model assistance
OCR/model extraction is lazy/adaptive by default
partial extraction failure does not invalidate valid artifact/representations
models can propose anchored structure but cannot invent absent source content

shared exact mathematical claims reuse Hardy ledger semantics
claim families/names do not control exact reuse
source↔claim faithfulness is evidence, not text similarity
uncertain near-duplicate claims remain separate rather than falsely merged

formal reuse is keyed by exact claim or authenticated claim relation
Mathlib declarations are realizations/candidates, not claim identities
formal validity != semantic faithfulness
project-local assumptions cannot silently become shared theorem assumptions
promotion includes reusable dependency closure + current verification

shared library existence does not widen project trust scope
citation provenance != formal proof provenance
source/claim/formal histories are versioned; no silent retargeting
indexes accelerate discovery but never establish identity/truth
private source material stays private unless policy explicitly permits otherwise
```

---

## 27. Implementation acceptance criteria

An initial full implementation of this architecture should satisfy these behavioral
criteria. Exact UI is not part of acceptance.

### Source identity and import

1. Importing a local PDF copies exact bytes into managed user-level storage and no
   later read depends on the original path.
2. Re-importing identical bytes reuses one `SourceArtifact` while retaining new
   acquisition provenance.
3. Different bytes never collapse merely because title/ISBN/DOI match.
4. Two artifacts can be proposed as the same edition without becoming
   authoritatively grouped until reconciliation evidence/approval exists.
5. Interrupted import leaves no readable half-admitted artifact.
6. Private artifact bytes are absent from ordinary project repository state.

### Extraction and locators

7. A born-digital PDF can expose native text plus exact page/bbox mappings.
8. A scan can expose OCR plus image-region mappings and confidence/diagnostics.
9. EPUB/HTML preserves DOM/spine structure and maps normalized text back to it.
10. TeX/source import preserves exact admitted file identity/include structure and
    does not execute source.
11. More than one representation can coexist for one artifact without a forced
    canonical-text merge.
12. Representation offsets cannot be resolved without their exact representation.
13. Printed page labels and artifact page indices remain separately queryable.
14. Lazy OCR/model enrichment can be invoked on one weak region without processing
    the whole book.
15. Failure of optional formula/OCR extraction leaves successful artifact/text
    representations usable and records diagnostics.
16. Retrieval of any excerpt returns machine-visible artifact/representation/span
    provenance and truncation/quality state.

### Source trees

17. A SourceTree is bound to one artifact; PDF and EPUB produce separate trees.
18. Native TeX/EPUB structure can construct a tree without an LLM.
19. Deterministic reconstruction can admit partial/unknown regions instead of
    inventing classifications.
20. Model-assisted repair is bounded to exact provided source material and produces
    proposals with anchors/provenance.
21. Mechanical validation rejects fabricated/unmapped node spans and cyclic trees.
22. A parser improvement can create a refined tree without invalidating unrelated
    unchanged node identities.
23. Historical claim links continue resolving to old tree/node/span versions after
    a new preferred tree is created.
24. Cross-artifact corresponding theorem nodes require explicit correspondence
    records; edition membership alone does not imply node identity.
25. Statement and proof are independently retrievable nodes when structure permits.
26. Source references such as “by Proposition 4.7” create source-level reference
    edges without automatically asserting mathematical dependency.

### Shared claims and interpretation

27. A source theorem can produce a candidate interpretation without creating a
    reusable shared claim/link automatically.
28. Exact reusable source↔claim admission records faithfulness evidence and notation/
    context mapping.
29. Two source nodes can link to the same shared claim while retaining separate
    citations/spans.
30. Ambiguous interpretations can coexist pending adjudication.
31. Near-duplicate claims can remain distinct and clustered; fuzzy matching cannot
    silently merge them.
32. A stronger/weaker theorem pair can be represented as separate claims with an
    explicit relation/transport.
33. Shared claims/relations use Hardy's existing ledger/evidence semantics rather
    than an unauthenticated memory store.

### Formal reuse and promotion

34. A shared claim can have both a Mathlib realization and a Hardy shared
    realization as distinct formal records.
35. Mathlib candidate matching checks exact declaration/context before reusable
    attachment.
36. Retrieval rejects or flags a realization that is not importable in the current
    Lean environment.
37. A verified theorem can remain project-local without being globally importable.
38. Promotion computes its reusable dependency closure.
39. Promotion is blocked or generates work when a dependency is project-local/
    assumption-based rather than silently exporting it.
40. Promotion stages and freshly verifies the shared Lean module against current
    shared/environment state before admission.
41. Later projects can retrieve/import a promoted realization without importing the
    originating project workspace.
42. Meaning-changing shared formal updates produce new realization/version identity
    rather than silently retargeting historical evidence.
43. A later project needing an exact already-formalized Donagi/Hartshorne claim can
    reuse it without a new translation/proof attempt.

### Bibliography, retrieval, and project policy

44. A non-arXiv book edition can be cited through the same controlled bibliography
    path as a paper without model-authored bibliographic metadata.
45. Same authoritative edition read through PDF and EPUB can share a bibliographic
    entry while preserving both artifact digests.
46. Distinct versions/editions do not silently deduplicate because of title/DOI
    similarity.
47. Stable cite keys are order-independent.
48. Seeding a 500-page source exposes a compact map/index and lazy retrieval, not the
    entire text in context.
49. Source/claim/formal search clearly distinguishes exact matches, related matches,
    and unverified candidates.
50. Shared source/formal existence does not automatically change project trust scope.
51. Source-backed project evidence retains exact artifact/span provenance even when
    the same claim also has a verified Lean realization.

### Privacy, portability, and restart

52. Exporting ordinary project/shared metadata can omit private source bytes while
    preserving source digests/refs.
53. On another machine with missing source bytes, source refs remain identifiable
    but reads report unavailable rather than disappearing.
54. Importing the exact missing digest later restores readability without reminting
    semantic identity.
55. Private OCR/full-text derivatives inherit appropriately restrictive handling.
56. Restart preserves admitted artifacts, trees, source↔claim links, formal
    realizations, and promotion provenance; rebuildable indexes can be regenerated.
57. Concurrent imports/extractions/claim proposals cannot last-writer-wins corrupt
    authoritative shared state.
58. A failed shared promotion does not leave the library claiming a reusable formal
    theorem was successfully admitted.

---

## 28. Implementation boundaries and dependency order

This is not the implementation plan, but the architecture implies a natural set of
subsystems and prevents one giant literature manager.

```text
A. generic source contracts + managed artifact store
B. work/edition metadata + reconciliation
C. format adapters + DerivedRepresentation/Mapping
D. locator/source-span primitives
E. SourceTree observations/build/validation/versioning
F. generic bounded source retrieval/search
G. shared semantic ledger source + claim interpretation/admission
H. Mathlib/shared formal realization registry/resolver
I. promotion into shared Lean library
J. generalized bibliography/citation
K. portability/privacy/export + migration of arXiv compatibility paths
```

Dependencies broadly flow A→C→D→E→F and A/B→J; semantic reuse builds on F plus
existing ledger/evidence; formal promotion builds on the shared semantic layer plus
existing Lean verification/workspace support.

Do not couple initial PDF parsing to claim admission or shared Lean promotion. A
book must be useful as a navigable source before the rest exists.

Do not require all source formats in the first vertical slice. The architecture is
successful if adapters can be added behind the same contracts.

---

## 29. Recommended first implementation vertical slice

After this spec is approved and an implementation plan is written, the first
end-to-end slice should prove the architecture with a user-supplied born-digital PDF
without trying to solve every source type at once:

```text
import exact PDF into managed library
→ establish work/edition metadata (manual/proposed reconciliation allowed)
→ native page/text extraction with page mappings
→ deterministic coarse SourceTree (sections + theorem-like nodes where recoverable)
→ bounded source-map/search/read operations with exact provenance
→ seed one Hardy project with the source
→ create one reviewed SourceClaimLink to shared mathematical ledger
→ discover an existing Mathlib/shared realization if available
→ project references the shared claim/source without copying the PDF
```

A second vertical slice should prove cross-project reuse by formalizing/promoting
one missing claim and consuming it from another project.

Scanned PDFs/OCR, EPUB, and TeX then exercise the same contracts rather than create
new semantic systems.

---

## 30. Deferred UI and policy tuning

Rich UI/UX is intentionally separate. The backend should make possible later views
such as:

```text
Hartshorne
  Chapter II
    §5
      ✓ Theorem 5.8  claim-linked; Mathlib/shared realization
      ○ Proposition 5.9 claim-linked; no realization
      ? Lemma 5.10 semantic identity unresolved
```

and inverse views such as sources/uses/formalizations of one claim, without storing
those display statuses as primary truth.

Also deferred to empirical evaluation rather than architecture:

- OCR confidence thresholds;
- when model structural repair triggers automatically;
- embedding models/index implementation;
- promotion-worthiness heuristics;
- automatic claim-match thresholds;
- default source-map/context budgets;
- exact module naming conventions;
- sync provider/UI details.

These policies can change without altering identities/provenance.

---

## 31. Final design checkpoint

The architecture is considered complete enough for an implementation plan once this
spec is reviewed.

The central invariants are:

```text
immutable exact source artifacts
+ versioned attributable representations
+ typed multi-coordinate source provenance
+ artifact-bound versioned SourceTrees
+ shared exact mathematical identities using Hardy ledger semantics
+ separately evidenced source↔claim interpretation
+ separately verified claim↔Lean realization
+ explicit reusable promotion with dependency/trust closure
+ one controlled citation path
+ project scope remains authoritative
```

The purpose is not merely “better PDF search.” It is to let Hardy accumulate a
personal, provenance-preserving mathematical library in which literature and formal
mathematics progressively reinforce one another: Hartshorne, Donagi, Mathlib, and
results proved in previous Hardy projects all become discoverable components of the
same reusable research infrastructure without conflating source text, mathematical
meaning, or formal proof.
