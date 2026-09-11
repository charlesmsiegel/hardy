# Hardy general literature sources and reusable mathematical library

Status: **living architectural design**, expanded 2026-09-10. This file is the
durable design record for general scholarly sources and cross-project mathematical
reuse. It began as a book-management seed during the delegation/swarm design, but
the central requirement is broader: papers, books, theses, proceedings, Mathlib,
and mathematics formalized inside Hardy projects should converge onto one reusable
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
SOURCE ARTIFACT
exact edition/version/bytes
        │
        ▼
SOURCE TREE
what this artifact literally contains and where
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

The reusable unit is therefore **the exact mathematical claim**, not the paper,
book, project, theorem name, or formal declaration that first introduced it.

## 2. Motivating examples

### 2.1 Hartshorne

A user supplies an exact artifact of Hartshorne's *Algebraic Geometry*.
Hardy imports it once into a persistent personal literature library and derives a
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

## 3. Architectural principles

1. **Source identity, mathematical identity, and formal identity are distinct.**
   Never collapse source nodes, claims, and Lean declarations into one object.

2. **Exact claim identity controls reuse.** Search may use names, text, embeddings,
   and neighboring concepts, but reuse requires an exact claim match or an
   explicitly justified relation such as equivalence/generalization/specialization.

3. **Formalization is cumulative across projects.** A reusable verified theorem
   proved once should become discoverable to later projects when promotion policy
   permits.

4. **Project-local does not automatically mean globally reusable.** Local
   hypotheses, project-specific axioms, temporary declarations, contextual
   transports, or idiosyncratic helper facts remain local unless deliberately
   generalized and promoted.

5. **Literature provenance survives semantic deduplication.** Three source nodes may
   express one claim; all three source identities/spans remain available.

6. **A formal proof is attached to a claim, not a citation.** A Lean realization
   can support every source node genuinely expressing that claim, but it does not
   prove that those source nodes were interpreted correctly. Source-to-claim
   faithfulness remains separately evidenced.

7. **A source statement is not trusted merely because it is indexed.** Source
   extraction and claim interpretation do not widen the project's trust scope.

8. **Mathlib is one formal provider, not the semantic registry.** Mathlib
   declarations participate as formal realizations of claims. Hardy should not
   identify claims by Mathlib names alone.

9. **The personal reusable library is a shared source, not a hidden memory system.**
   Projects explicitly retrieve/authorize material from it through Hardy's
   existing project/shared retrieval boundary.

10. **Derived indexes are rebuildable.** Exact source artifacts, structured records,
    evidence, and formal artifacts are durable; semantic/full-text/vector indexes
    are derived acceleration structures.

11. **Private copyrighted bytes remain private/local.** Durable mathematical and
    bibliographic records may reference exact digests/locators without assuming
    Hardy may redistribute the original artifact.

12. **Progressive enrichment beats eager whole-corpus formalization.** Importing a
    500-page book should not require resolving or formalizing every statement
    before the source becomes useful.

13. **Indexed source bytes are Hardy-owned immutable imports.** External files,
    downloads, or mounted paths are acquisition inputs/provenance, not mutable
    backing stores for an indexed source. Once admitted, Hardy's exact managed copy
    is what every source node, extraction, claim link, and citation refers to.

14. **Bibliographic work, edition/version, exact artifact, and derived
    representation are separate identities.** Metadata matching can propose that
    two artifacts belong to the same edition, but authoritative grouping requires
    explicit/reliable identity evidence.

## 4. Persistent personal mathematical library

Hardy should maintain a reusable user-level library shared across projects. The
exact storage location is an implementation detail, but conceptually it contains
several connected stores:

```text
Personal Mathematical Library
├── bibliographic works
├── editions / source versions
├── exact source artifacts
├── derived representations
├── source trees / structural inventories
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
used. The new library broadens the source types and adds the semantic/formal reuse
layers.

The existing retrieval layer already recognizes `project` and `shared_library`
sources. The personal mathematical library should plug into that seam. Shared
material remains subject to explicit source provenance, project authorization,
scope/context checks, and current formal importability rather than being silently
trusted because it exists on the machine.

## 5. Source layer: work, edition/version, artifact, representation, and source tree

Hardy needs enough bibliographic structure to distinguish human-facing publication
identity from the exact bytes and extraction pipeline it actually used, without
adopting a full library-science ontology whose complexity does not serve
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

A work is **not** enough to identify what Hardy read. Mathematical extraction,
source claims, page locators, and citations that depend on exact wording always
continue down to an edition/version and artifact identity.

### 5.2 EditionOrVersion

An `EditionOrVersion` identifies a specific published/released state of a work.
Examples include:

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

The word “edition” is used broadly here to include exact source versions of papers.
The architectural point is that this layer captures bibliographic/version identity
above file-format bytes.

Different editions/versions remain distinct even when most content is identical.
A relation can later state that a theorem is unchanged across them; Hardy never
assumes this from matching titles or page counts.

### 5.3 SourceArtifact

A `SourceArtifact` represents the exact bytes Hardy actually imported/read for one
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

Artifact identity is content identity. Two identical byte sequences deduplicate as
one artifact even if imported through different paths. Different bytes are
separate artifact identities even when believed to represent the same edition.

### 5.4 Candidate grouping versus authoritative grouping

Hardy may automatically propose that two artifacts belong to one edition based on
metadata such as title, authors, ISBN/DOI/arXiv version, publisher information,
internal title pages, or high structural similarity. This is useful for import UX
and deduplication assistance.

But candidate grouping is not authoritative grouping.

```text
artifact A ── candidate_same_edition ── artifact B
```

is weaker than:

```text
artifact A ── belongs_to ── Edition E
artifact B ── belongs_to ── Edition E
```

Authoritative edition membership requires reliable identity evidence appropriate to
the source type. Examples include matching exact ISBN/edition metadata corroborated
by internal front matter, exact arXiv version identity, publisher/provider metadata,
or explicit human confirmation where machine evidence cannot distinguish printings.

Title/year/author similarity alone is insufficient. This prevents a revised
printing, unofficial scan, translation, or nearby version from silently inheriting
source-node/claim links belonging to another artifact.

The same principle applies at the work layer: fuzzy metadata may propose that two
records describe the same work, but merging stable work identity is an explicit
reconciliation operation with provenance.

### 5.5 Managed immutable import

Hardy should not index user-supplied scholarly material in place. Importing a local
PDF, EPUB, TeX tree, scan, downloaded file, or provider result copies the exact
admitted bytes into Hardy's user-level managed literature store. The external path
or URL remains provenance only.

Conceptually:

```text
C:/Downloads/Hartshorne.pdf
        │ import
        ▼
~/.hardy/.../literature/artifacts/<sha256>/original.pdf
        │
        ├── immutable exact bytes
        ├── import/provenance record
        ├── derived text/OCR representations
        └── SourceTree/indexes
```

The literal layout/name above is illustrative; Hardy already has a user-level
`~/.hardy` root, but this spec does not freeze the final directory names.

The import contract is:

```text
read bounded/validated input
→ compute content digest
→ copy to a temporary managed location
→ verify copied bytes/digest
→ atomically admit the artifact record + managed bytes
→ derive/index only from the managed copy
```

A refused or interrupted import must not leave a half-admitted artifact that later
looks valid.

Identical bytes deduplicate by content digest. Importing the same PDF from two
paths may add provenance/aliases, but should not duplicate the immutable artifact.
Different bytes are different artifact identities even when metadata says they are
the same title/edition.

After import, edits, deletion, renaming, cloud-sync changes, or replacement of the
external original do not change Hardy's artifact. To consume changed bytes, the
user/provider imports again, producing either the same digest (no mathematical
change) or a new `SourceArtifact` identity.

### 5.6 Import provenance

The managed artifact retains acquisition facts separately from byte identity, such
as:

```text
original filename/path or provider handle
source URL/provider identity when fetched
time imported/fetched
media/MIME type and detected format
user/provider supplied bibliographic metadata
acquisition adapter/version
privacy/access classification
```

Original local paths can be useful diagnostics but are not durable semantic
identity and should not be required for later reading.

### 5.7 DerivedRepresentation

A `DerivedRepresentation` is a reproducible or attributable reading of one exact
artifact produced by some extractor/parser/OCR/model/configuration. It is a
first-class identity because two extraction pipelines may disagree while both
remain useful historical inputs.

Examples:

```text
PDF native text extraction v2 from artifact A
OCR pass v1 from scanned artifact C
EPUB XHTML normalized text from artifact B
TeX source assembly from arXiv source archive
page-image manifest from PDF artifact A
layout/block analysis from PDF artifact A
SourceTree construction v4 from normalized representation R
```

Representative fields:

```text
DerivedRepresentation
  id / digest
  artifact ref
  kind: native_text | ocr_text | normalized_text | page_images |
        source_tree_input | layout | other
  bytes/artifact refs for derived output when retained
  extractor/parser/model identity
  extractor version/configuration
  derivation timestamp
  input refs
  quality/confidence/failure metadata
  locator mapping back to source artifact
```

Derived identity includes the exact artifact plus enough pipeline identity to know
what generated it. Improved OCR or a parser update creates a new representation,
not a mutation of the old one.

Derived caches that are purely reconstructable may be discarded/rebuilt. Derived
representations that are cited by source nodes, interpretation evidence, or claim
links must retain durable identity/provenance even if their bytes can later be
regenerated.

### 5.8 Artifact and representation plurality

Hardy should not force one canonical text representation when multiple readings are
useful. For the same edition/artifact, native PDF text may preserve searchable text
well while page-image OCR recovers formulas or headers better. TeX source may offer
superior theorem boundaries while the published PDF supplies authoritative printed
pagination.

The architecture should therefore permit several parallel representations and
explicit mappings among them rather than overwriting one extraction with another.

A later “preferred representation” may be selected for ordinary retrieval, but
preference is a policy/view and does not erase provenance or silently retarget
source nodes already built from another representation.

### 5.9 SourceTree

A `SourceTree` is analogous to an AST for the scholarly artifact: it records the
artifact's structural organization and exact locations without initially claiming
that two mathematical statements are equivalent.

A SourceTree is itself a derived structured representation. Its identity must name
its input artifact/representations and parser/model/configuration. A newer parser can
produce a new tree while historical source-node links continue to resolve against
the old tree they used.

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
│   │   └── paragraph
│   └── ...
└── bibliography/index/etc.
```

A `SourceNode` should retain at least:

```text
id
SourceTree ref
artifact/representation refs
parent / children
kind
title / heading / label / source numbering when known
exact representation span(s)
locator(s) back to artifact
printed page or artifact page when known
reading order
parser/extractor provenance
confidence/quality metadata for structural extraction
```

Source numbering must distinguish what the source actually provides from numbering
Hardy inferred. Page identity should distinguish printed page labels from PDF/image
page indices.

### 5.10 Source graph beyond the tree

The literal containment tree is not enough. Source nodes may also carry derived
structural/reference edges such as:

```text
proof_of
source_cites
source_refers_to
uses_notation_from
continues_from
```

These are claims about the source document's structure/reference behavior, not yet
semantic mathematical dependency edges.

### 5.11 Progressive source enrichment

Import should be useful before semantic understanding is complete.

A newly imported book can begin as:

```text
✓ exact work/edition/artifact identity where known
✓ managed immutable bytes
✓ chapter/section/page map
✓ extracted theorem-like nodes where recoverable
? some edition grouping still only candidate
? claim identities unresolved
? formal realizations unknown
```

Later projects can enrich individual nodes without rebuilding or reinterpreting the
entire artifact.

## 6. Mathematical claim layer

### 6.1 Why a claim registry is required

The source tree cannot itself provide cross-project formalization reuse because a
source statement is tied to wording, notation, edition, and document context.
Likewise a Lean declaration is tied to a formal representation and environment.

Hardy therefore needs a persistent **mathematical claim registry** representing the
mathematics between source and formalization.

Conceptually:

```text
SourceNode ──expresses──> MathematicalClaim <──realizes── FormalRealization
```

### 6.2 MathematicalClaim

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

The claim registry must not pretend that natural-language canonicalization solves
mathematical identity. A claim's stable identity is minted through an explicit
semantic admission process, and later revisions that change mathematical meaning
become new claims linked by explicit relations.

The claim object should reuse Hardy's existing project mathematical semantics where
possible rather than invent a contradictory theorem ontology. Exact integration
with `ProjectItem`/context/representation records is an implementation-design
question, but the architectural rule is that one cross-project exact mathematical
identity must exist independently of a particular source or Lean declaration.

### 6.3 Claim families/concepts

Names such as "Riemann–Roch" are useful for discovery but too coarse for exact
reuse. Hardy should support a looser family/concept layer:

```text
ClaimFamily / Concept:
  Riemann–Roch

Exact claims:
  divisor RR for smooth projective curves under hypotheses H1
  line-bundle RR under hypotheses H2
  stronger field-general version
  scheme-theoretic generalization
```

Family membership assists retrieval and UI. It never establishes equivalence or
formal substitutability.

### 6.4 Claim relations

Exact claims can be connected by explicit semantic relations:

```text
equivalent_to
generalizes
specializes
implies
refines
reformulates
```

A relation that permits proof reuse should carry sufficient evidence/transport to
justify the mapping. Semantic similarity can propose candidate relations but cannot
certify them.

This lets a stronger existing theorem discharge a need for a weaker one when the
specialization is authenticated instead of forcing a duplicate proof.

## 7. Source-to-claim interpretation

### 7.1 SourceClaimLink

A source node and mathematical claim are connected by an explicit interpretation
record, conceptually:

```text
SourceClaimLink
  source node
  claim
  relation:
    expresses
    specializes
    generalizes
    reformulates
  notation/object mapping
  interpretation/faithfulness evidence
  interpreter/model/human provenance
  exact source span/version
```

A theorem extractor saying "this looks like a theorem" is not enough to produce a
trusted `expresses` link. The semantic link represents the interpretation that the
source statement has the claim's exact meaning.

### 7.2 Many source nodes, one claim

Cross-source deduplication is expected:

```text
Source A node ─┐
Source B node ─┼──> Claim C
Source C node ─┘
```

Every source keeps its own artifact/span provenance. Claim reuse does not erase
bibliographic differences.

### 7.3 One source node, multiple semantic records

Ambiguity must be representable. If Hardy has two competing interpretations of a
source statement, preserve them as proposals/assessments until adjudicated rather
than silently choosing one because it matches an existing theorem.

## 8. Formal realization layer

### 8.1 FormalRealization

A `FormalRealization` records one formal declaration that represents an exact
mathematical claim.

Representative fields:

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

A declaration becomes reusable because the realization authenticates both formal
validity and semantic correspondence to the exact claim. Kernel proof without
faithful claim mapping is not enough; semantic mapping without formal verification
is not enough.

### 8.2 Mathlib participation

Mathlib should be continuously searchable as a provider of candidate formal
realizations.

When Hardy identifies a candidate Mathlib declaration it must check the exact
statement, implicit hypotheses/typeclasses, representations, ambient categories,
and relevant notation/conventions before attaching it to a claim.

A similar theorem name or embedding match is a lead only.

The realization also records the Mathlib/environment revision so later retrieval
can tell whether the declaration remains available/importable and whether a changed
library requires revalidation.

### 8.3 Project-local realizations

A theorem proved in Project A may initially remain a project-local formal
realization:

```text
Claim C
  formalized_by -> Project A / Foo.lean / theorem foo
```

That is already useful to Project A and may be indexed for inspection, but it does
not automatically become globally reusable. Context identity and trust boundary
must be preserved.

### 8.4 Shared Hardy formal library

Hardy should support deliberate promotion of reusable project formalizations into
a persistent user-level shared Lean library.

Conceptually:

```text
Project A verified theorem
        │
        │ reusable promotion
        ▼
Hardy shared formal library
        │
        └── registered as reusable FormalRealization of Claim C
```

Later projects can import/reuse this declaration through the normal authenticated
shared-library retrieval boundary.

The shared formal library is not a dump of everything Hardy has ever proved. It is
curated reusable mathematics.

## 9. Promotion of project formalizations

### 9.1 Promotion criteria

A project theorem is a candidate for shared promotion when:

- the Lean declaration is authenticated/verified;
- semantic faithfulness to an exact reusable claim is established;
- its trust boundary is explicit and acceptable for reuse;
- it does not silently rely on project-only hypotheses or declarations;
- its required representations/context can be reconstructed in another project;
- its dependency closure can be satisfied from Mathlib/shared reusable material or
  promoted with it;
- the resulting shared artifact can be imported and revalidated independently of
  the originating project workspace.

Not every helper lemma should be promoted. Project-worthiness is distinct from
formal correctness.

### 9.2 Reusable dependency closure

Promotion computes the minimal formal dependency closure needed for the theorem:

```text
RiemannRoch
├── Mathlib dependency       → keep as external import
├── already shared lemma     → reuse
├── reusable project lemma   → promote too
└── project-specific fact    → block/generalize/replace before promotion
```

A theorem whose proof depends on a project-local axiom does not become a globally
verified theorem merely because Lean accepted the axiom inside that project.

The promotion workflow should expose blockers explicitly and allow the user/agent
to generalize or separately prove them.

### 9.3 Local context and generalization

Sometimes a useful project theorem is stated in an unnecessarily local context. A
promotion attempt may therefore generate work such as:

```text
remove project-specific hypothesis
generalize a chosen object to a parameter
replace local definition with shared representation
prove transport/equivalence to a reusable formulation
```

The resulting reusable theorem may be a new claim/formal realization rather than a
mutation of the original project result. Historical project identity remains intact.

### 9.4 Promotion provenance

Promotion records at least:

```text
originating project/result
claim identity
formal dependency closure
rewrites/transports/generalizations performed
formal verification evidence
faithfulness evidence
trust/assumption audit
shared module/declaration identity
shared artifact digest/environment
promotion actor/reason/time
```

## 10. Reuse and acquisition behavior

When a project needs mathematics, Hardy should prefer reuse before new proof work.
A conceptual search order is:

```text
1. exact established result already in current project
2. exact compatible Mathlib realization
3. exact compatible Hardy shared realization
4. equivalent/stronger reusable realization with authenticated transport
5. exact source-backed literature result usable under project policy
6. new formalization/proof work
```

This need not be implemented as six expensive sequential searches. Cheap indexes
may search several layers together. The invariant is that Hardy should not ask an
LLM to rediscover or reprove a theorem before checking known reusable formal
mathematics.

Search results must distinguish:

```text
EXACT CLAIM MATCH
  reusable directly if formal/trust/context checks succeed

RELATED CLAIM
  family member / semantic similarity only; navigation

STRONGER/WEAKER CLAIM
  reusable only through established relation/transport

SOURCE ONLY
  literature statement known, no reusable formal realization

FORMAL CANDIDATE
  possible Mathlib/shared declaration not yet semantically linked
```

## 11. Project relationship to the shared library

The personal library is reusable infrastructure, but each project retains its own
mathematical/trust decisions.

A project can:

- retrieve shared claims/formal realizations;
- import a verified shared Lean theorem;
- cite a source artifact/node;
- admit a source result as background according to existing scope policy;
- create project-specific relations/obligations around a shared claim;
- contribute newly promoted reusable formalizations back to the shared library.

A project does **not** automatically trust every theorem stored in the shared
library. Existing scope/evidence policy still decides whether a result is usable in
that project's proof context.

This matches Hardy's existing retrieval architecture, where a shared source must be
authenticated/authorized for the requesting project's exact scope/context rather
than becoming valid merely because it appears in a shared index.

## 12. Progressive formalization of sources

A source's formalization state should be queryable without requiring all nodes to
be resolved.

For example:

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

These display symbols are illustrative UI, not stored truth states. The underlying
records should derive the view.

Hardy should also answer the inverse question:

```text
Claim C / shared theorem R
Sources expressing it:
  Donagi 1991, Theorem 2.1
  Survey X, Theorem 5.4
Projects using it:
  genus-5 Schottky
  genus-6 Schottky
Formal realizations:
  Hardy shared Lean module ...
```

## 13. Source seeding for projects and delegations

"Seed this project/run with Hartshorne" means:

- select an exact admitted source edition/version and artifact;
- expose its work/edition identity and compact SourceTree/index prominently;
- increase retrieval priority for its nodes;
- allow lazy retrieval of exact relevant spans/statements/proofs;
- expose known claim/formalization links where policy permits;
- do **not** inject the whole source into every context.

Delegation workers may be given different subtrees or retrieval intents while
sharing the same exact artifact identity.

## 14. Citation versus formal reuse

Literature citation and formal reuse remain distinct even when they meet at one
claim.

Example:

```text
SourceNode S --expresses--> Claim C <--realizes-- Lean R
```

A project may:

- cite S because the paper/book is the historical/source authority;
- use R because it is the verified formal theorem;
- do both;
- use R without citing S if the theorem is being used through Mathlib/shared
  formal infrastructure and publication policy does not require that source;
- cite S without having R if the project is deliberately accepting the literature
  result as background under scope policy.

Formal proof does not retroactively certify that S expresses C. Source faithfulness
is independently evidenced.

Citation identity normally targets the bibliographic work/edition appropriate to
publication conventions, while Hardy's provenance additionally records the exact
artifact/derived span it actually read. Thus a human-readable citation need not
expose a content digest in prose, but the project can still audit which bytes stood
behind the claim.

## 15. Versioning, revisions, and staleness

### 15.1 Source changes

New arXiv versions and new book editions are new `EditionOrVersion` identities.
Corrected scans, alternate file formats, or publisher/EPUB representations may be
new `SourceArtifact`s within one edition when identity is established. Improved OCR
or extraction is a new `DerivedRepresentation`.

Do not silently retarget old source-to-claim links at any layer.

A new edition/artifact/representation may be assessed as:

```text
same mathematical statement
equivalent with notation changes
strengthened/weakened statement
meaningfully changed
unknown
```

Historical links remain tied to the exact artifact/representation/span they
interpreted.

### 15.2 Claim changes

A corrected or strengthened mathematical statement receives a new claim identity
with an explicit relation to the prior claim. Claim identity is not mutable prose.

### 15.3 Formal environment changes

Mathlib/toolchain changes can make a formal realization stale/unimportable without
changing the mathematical claim. Formal realization validity and importability are
therefore version/environment-specific and can be rechecked independently of source
interpretation.

## 16. Search and indexing

The system will need several derived indexes, but no index is authority:

```text
work/edition identifier index
artifact digest/provenance index
source full-text index
SourceTree structural index
claim text/alias/concept index
claim-relation graph
source-node ↔ claim index
claim ↔ formal-realization index
formal declaration/name index
vector/semantic retrieval index where useful
project usage/citation index
```

Exact IDs/aliases/structural relationships should outrank fuzzy similarity when
available. Embeddings are candidate generators, not identity proofs.

Indexes must be rebuildable from durable records and immutable artifacts.

## 17. Privacy, copyright, and storage boundary

User-supplied copyrighted books/papers may be stored and indexed locally for the
user's own Hardy workflows. The architecture must not assume Hardy can:

- independently acquire a source the user has not supplied or that an enabled
  provider cannot legally/technically fetch;
- redistribute source bytes;
- commit private source artifacts into project repositories;
- send an entire copyrighted source to unrelated external services merely because
  it exists locally.

Source access policy should be explicit enough that later executor/plugin/UI work
can respect local/private restrictions.

Derived claim/formalization records may be much more portable than the source bytes,
but must retain enough provenance to know what source was interpreted.

## 18. Relationship to current arXiv paper handling

Current arXiv support already has several valuable properties that should survive:

- search results are leads, not citations;
- exact versioned paper identity is pinned before use;
- metadata/content/source bytes carry digests;
- library bytes are machine-local;
- source bundles are treated as hostile archives;
- bounded reads prevent uncontrolled context injection;
- project bibliography is the one controlled citation writer;
- extracted statements are inventoried separately from human approval/trust.

The general architecture should **generalize** these properties.

What becomes less central is the assumption that `PaperRecord`/`PaperLibrary` is
the downstream abstraction. An arXiv paper should become one `BibliographicWork`
with versioned `EditionOrVersion` records and one or more exact artifacts/source
bundles underneath. Its statement inventory feeds the same
SourceTree/claim/formalization system used by books and other sources.

Existing APIs may remain as compatibility/convenience layers while ownership moves
toward general source primitives.

## 19. Relationship to Hardy's project ledger and retrieval

Do not create a second theorem truth system.

The shared claim/formalization registry supplies reusable mathematical identities
and artifacts across projects. A concrete project still uses its existing ledger
to record:

- active goals/claims/approaches;
- mathematical context and declarations;
- dependencies/representations/transports;
- obligations;
- project scope/trust;
- exact evidence and acceptance;
- citations and publication relations.

When a shared claim enters a project, Hardy should link/import/reference it through
existing project operations rather than silently bypassing them.

Likewise, the existing project/shared retrieval index should remain the delivery
boundary. The personal mathematical library appears as an authenticated shared
source. Discovery establishes relevance; existing owners establish whether the
material can actually be delivered/used in the requesting scope/context/environment.

## 20. Key conceptual contracts

Names are provisional, but implementation should preserve these distinct roles:

```text
BibliographicWork
  human-facing intellectual publication identity

EditionOrVersion
  exact bibliographic/released version of a work

SourceArtifact
  exact managed bytes and acquisition provenance

DerivedRepresentation
  artifact-bound extraction/OCR/layout/text representation with pipeline identity

SourceTree / SourceNode
  document structure and exact locators/spans

MathematicalClaim
  exact reusable semantic proposition/definition/construction

ClaimFamily / Concept
  looser discovery grouping such as "Riemann–Roch"

SourceClaimLink
  evidenced interpretation from exact source node to exact claim

ClaimRelation
  exact semantic relation between claims

FormalRealization
  exact Lean declaration + environment + formal/faithfulness/trust evidence

PromotionRecord
  project theorem → reusable shared formal artifact, including dependency closure

SourceUsage / ProjectLink
  project-specific citation/use/authorization of shared source/claim/formal result
```

Do not merge these merely because an early implementation could use fewer classes.
Their separations carry correctness semantics.

## 21. Core invariants for cross-project formal reuse

The following should be treated as architectural invariants:

```text
work identity != edition/version identity != artifact identity != derived identity

source node identity != mathematical claim identity != Lean declaration identity

formal reuse is keyed by exact claim or authenticated claim relation,
not theorem name or textual similarity

metadata similarity can propose grouping but cannot authoritatively merge editions
or works without reliable identity evidence

one edition may have multiple artifacts; one artifact may have multiple derived
representations; historical links stay attached to the representation actually used

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
managed imported bytes, not external mutable paths, define indexed source identity
```

## 22. Evaluation questions

This architecture should eventually let Hardy measure:

- how often a project need is satisfied by existing Mathlib/shared formalization
  rather than new proof work;
- how often two sources are correctly linked to one reusable claim;
- false-positive rates in proposed source/claim and claim/formal matches;
- false-positive rates in proposed same-work/same-edition artifact grouping;
- how much formalization effort is saved across projects;
- which project-local results are actually worth promoting;
- how often promotion is blocked by project-local dependencies;
- how often stronger/generalizing claims can satisfy weaker needs through transport;
- staleness rates after Mathlib/toolchain/source revisions;
- cumulative formal coverage of important books/papers;
- whether source seeding improves retrieval without excessive context anchoring.

## 23. Design decisions settled in this section

The following are considered agreed unless later discussion revises them:

```text
general literature and existing paper handling converge on one source architecture
user has a persistent personal mathematical library shared across projects
bibliographic work, edition/version, exact artifact, and derived representation are
  distinct identities
one edition may be represented by multiple exact file artifacts
same-work/same-edition detection may be proposed automatically but authoritative
  grouping requires reliable identity evidence
exact source artifacts are content-identified and remain distinct
Hardy copies admitted source bytes into its own managed user-level library
external paths/URLs are provenance/acquisition inputs, not live backing stores
managed imports are immutable, digest-verified, atomic, and deduplicate identical bytes
derived representations remain explicitly bound to exact managed artifact identity
multiple derived representations may coexist; newer extraction never silently
  replaces provenance of older SourceNodes/claim links
large sources are represented by navigable SourceTrees with exact locators
source structure is useful before semantic/formal enrichment is complete
there is a cross-project exact mathematical claim registry
claim families/concepts assist discovery but do not control exact reuse
source nodes connect to claims through evidenced interpretation links
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

## 24. Next design areas

The semantic/formal reuse and source-identity architecture above should be treated as
foundational. The next sections to design are source-management mechanics rather
than a reconsideration of these layers:

1. **Derived representation and locator model:** exact page/image/byte/text spans,
   printed-page labels, cross-representation mappings, formulas/figures, and how a
   SourceNode can cite robust locators even when PDF text/OCR/TeX disagree.
2. **Import/acquisition interfaces and formats:** PDF, EPUB, TeX/source trees,
   HTML, plaintext, scans, directories, URLs/provider fetches, and user-supplied
   files.
3. **Text extraction/OCR and normalization:** what representations are produced,
   confidence/failure handling, and preserving exact page/span mappings.
4. **SourceTree construction:** parsing native structure vs model-assisted
   reconstruction; tables of contents; theorem/proof/example/exercise extraction;
   page/section/label locators.
5. **Library portability:** backup/export, multiple machines, privacy, and shared
   metadata without redistributing private source bytes.
6. **Bibliography/citation generalization:** extend current arXiv-centric
   bibliography identity while preserving exact artifact provenance and stable
   citation keys.
7. **Claim interpretation/admission workflow:** when source nodes get claim IDs,
   how candidate matches are reviewed, and how much can be automated.
8. **Shared formal-library packaging:** module layout, dependency promotion,
   environment/version compatibility, and how promoted Lean is built/imported.
9. **Source/claim/formal search API:** operations used by Explore, Research,
   acquisition, and delegation.

This document should be updated in place as each section is settled so that the
architecture does not depend on conversation memory.