# Hardy general literature sources

Status: design seed, intentionally deferred. This came up while designing delegation/swarm literature context and is a separate concern from that architecture.

## Scope boundary

Hardy's literature subsystem should eventually support sources beyond arXiv papers, including user-supplied monographs, textbooks, books, proceedings, and other locally available scholarly works. The delegation/swarm design should depend only on a generic literature-retrieval capability and must not own ingestion, indexing, edition identity, OCR, or storage rules for these source types.

## Requirements captured so far

- A source Hardy cannot legally/technically access and that the user has not supplied remains unavailable.
- A user should be able to seed a project/run with a locally available book or monograph such as Hartshorne.
- For books, durable identity must distinguish the exact edition/artifact Hardy actually read; bibliographic edition metadata should be bound to a content digest rather than treating a title alone as sufficient identity.
- Locally supplied copyrighted source bytes should remain private/local; the system should not assume it may redistribute or independently acquire them.
- A large source should be navigable and retrievable by useful internal structure such as chapter, section, theorem/lemma/definition, page, and other stable locators when those can be recovered.
- "Seed this run with Book X" should make the source prominent and readily discoverable, not paste the entire book into every model context. Workers should receive a compact source map/index and retrieve exact relevant passages lazily.
- Exact extracted passages/results should retain source/edition/artifact provenance so later claims can point back to what Hardy actually read.

## Intentionally unresolved

Acquisition/import interfaces, PDF/ebook/source formats, OCR policy, indexing and statement extraction, bibliography/citation representation, edition/version reconciliation, local search, page-number mapping, copyright/privacy handling, and how this generalizes the current `PaperRecord`/`PaperLibrary` APIs all require a separate design discussion.

This file is only a durable seed for that future design. Do not treat it as an implementation plan or as part of the delegation/swarm subsystem.
