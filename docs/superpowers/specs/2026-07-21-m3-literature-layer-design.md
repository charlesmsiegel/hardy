# M3 — Literature Layer — Design Spec

**Milestone goal (DESIGN.md):** arXiv search/fetch/read tools, paper store,
machine-maintained `references.bib`, citations wired into writeups.

**Exit criterion:** a writeup that cites fetched papers with a valid bibliography.

## Context: what M3 builds on

- M1's `ToolRegistry` — the new tools are `ToolDef`s like every other.
- M1's `write_latex` pipeline and M0's template/compile layer, which M3 extends
  with bibliography support.
- M1's Prove workflow, which gains an optional literature phase.

## Requirements (from DESIGN.md Component 5)

- Version-keyed paper store: `papers/<arxiv-id>v<N>/` with a content digest in the
  manifest; an unversioned fetch resolves to the latest revision and is stored
  under its resolved version; cached entries are **immutable**; later revisions
  are distinct entries.
- Archive extraction treats downloads as untrusted: path-normalized, symlink-safe
  unpacking, byte and file-count quotas, isolated temp dir, atomic admission.
- Polite API usage: rate limiting, query caching, never re-fetch a stored version.
- One canonical `references.bib`, machine-maintained: entries from arXiv metadata,
  dedup by arXiv id + version (or DOI), stable cite keys (`author2023short`;
  version-qualified on collision, e.g. `author2023shortV3`), validation in CI.
- The `cite` tool is the **only** write path to the `.bib` — `fetch_paper`
  registers papers by delegating to it.
- Writeup pipeline gains `\cite` + bibliography; compile-check covers the
  bibliography (missing keys are errors, not warnings to ignore).

## Architecture

```
hardy/literature/
  arxiv.py       — API client: search, metadata, download (rate-limited, cached)
  store.py       — version-keyed immutable paper store + safe extraction
  bibliography.py— references.bib read/model/write; cite-key minting; validation
  reading.py     — section-chunked serving of stored papers
hardy/tools/literature_tools.py — arxiv_search, fetch_paper, read_paper, cite
papers/          — the store (gitignored except manifests? see decision below)
references.bib   — canonical bibliography (committed)
scripts/validate_bib.py — CI check: parses, no duplicate keys, entries well-formed
```

### `arxiv.py`

- Thin client over the arXiv Atom API (the `arxiv` PyPI package per DESIGN's
  stack, wrapped behind our own interface so it stays swappable).
- `search(query: ArxivQuery) -> list[PaperMeta]` — `ArxivQuery` supports category,
  author, title/abstract text, date range, max results. `PaperMeta` carries id,
  version, title, authors, abstract, categories, DOI/journal when present.
- `resolve_version(arxiv_id) -> str` and `download(arxiv_id_v) ->
  DownloadedPaper(pdf_path: Path, source_tar_path: Path | None)` — source
  (e-print tarball) fetched whenever available; its absence is recorded, not an
  error. Downloads **stream to temporary storage under explicit byte limits**
  (per-payload caps, checked on the compressed bytes as they arrive; the
  response is aborted the moment a cap is exceeded) — materializing whole
  responses as `bytes` would let one oversized or hostile payload exhaust host
  memory before the store's extraction quotas or the PDF parser's bounds ever
  run.
- **Rate limiting:** a limiter honoring arXiv's published guidance
  (1 request / 3 s, single connection), coordinated **across processes** — the
  interval and connection lease live behind an interprocess lock with a shared
  last-request timestamp (same lock discipline as the bibliography), because
  concurrent Hardy runs are a supported scenario and per-process limiters
  would multiply the real request rate and invite throttling or bans. Every
  call path goes through it.
- **Query cache:** search responses cached on disk keyed by canonicalized query,
  with a TTL (default 24 h) — searches are cheap to redo but the agent may loop.

### `store.py`

- Layout: `papers/<id>v<N>/` containing `paper.pdf`, `source/` (extracted LaTeX
  when available), `meta.json` (PaperMeta + fetch timestamp + SHA-256 digests +
  extraction report).
- `PaperStore.get(id_v) -> StoredPaper | None`; `PaperStore.admit(downloaded) ->
  StoredPaper` — extraction and digesting happen in an isolated temp directory;
  the completed entry is `rename()`d into place (atomic admission); a partially
  admitted entry can never be observed. Admission and digest publication are
  **jointly crash-safe**, and the ledger itself is physically so: it is an
  **event journal** — complete, individually fsynced JSONL appends (a
  *pending* event, then a *committed* event), never an in-place rewrite,
  which a crash could truncate into an unparsable file that loses the durable
  history for every stored version (the lock serializes writers but protects
  nothing against a crash mid-write). Holding the ledger lock, `admit` first
  appends the pending event (id, version, digest), then **fsyncs the staged
  entry tree, renames it into place, and fsyncs the parent directory — all
  before** appending the committed event (a visible directory entry whose
  file contents never reached durable storage would otherwise be blessed by
  recovery as `pending + entry present → committed`, and the no-refetch rule
  would preserve the incomplete entry forever) — recovery under the same lock
  reconciles journal state against the store (pending + entry present →
  append committed; pending + no entry → superseded). Rename-then-publish without the journal would let a crash strand an
  admitted entry whose digest never becomes durable, and the no-refetch rule
  would preserve that hole forever; publish-then-rename would bless digests
  for entries that don't exist.
- **Safe extraction** (untrusted tarball): members are rejected unless a regular
  file or directory; paths normalized and confined under the target (no `..`, no
  absolute paths); symlinks/hardlinks/devices skipped and logged in the extraction
  report; quotas — max total bytes (default 512 MB) and max file count (default
  10 000) — abort extraction over-quota, and the entry is admitted PDF-only with
  the abort recorded.
- Immutability: `admit` refuses to overwrite an existing version directory; a
  digest mismatch on re-download of the same version is an error surfaced to the
  user (upstream mutation of a published version is anomalous). For that check to
  survive a fresh clone (where no old store entry exists to compare against),
  digests are also recorded **durably in a committed ledger** —
  `papers/DIGESTS.json`, tiny, written only through `admit` — and this is
  **the same fsynced JSONL event journal specified above** (pending/committed
  appends under the `DIGESTS.json.lock`, never a read-modify-write rewrite,
  which a crash could truncate into losing the durable history; concurrent
  fetches serialize on the lock) — and
  every refetch verifies against it; per-result manifests additionally record the
  digests of the papers they used, so a writeup's exact inputs are auditable even
  without the store.

### `bibliography.py`

- Model: `BibEntry` (pydantic) covering the fields we emit (`@article`/`@misc`
  with `eprint`, `archivePrefix`, `primaryClass`, DOI/journal when known, and a
  `note = {arXiv <id>v<N>}` recording the exact version). Field values are
  **confined before persistence**: arXiv metadata is network-controlled text
  that the writeup pipeline will later execute as TeX when the bibliography
  renders, so titles/authors/abstract-derived fields pass through the same
  allowlisted representation as M1's model-authored fields (math-mode and
  benign formatting preserved; control sequences that define, redirect, or
  terminate structure rejected or escaped) — successful BibTeX *parsing* is
  not sanitization.
- `Bibliography.load(path)` / `.save(path)` via `bibtexparser`; save is atomic
  (temp file + rename) and normalizes formatting so diffs stay reviewable. The
  whole load → dedup/mint → save transaction runs under an **interprocess file
  lock** (`references.bib.lock`): atomic rename protects readers from partial
  files but does not serialize concurrent writers — two parallel runs calling
  `cite`/`fetch_paper` would otherwise each load, mint, and rename, and the
  last rename would silently discard the other's entry.
- `mint_key(meta) -> str`: `<first-author-surname><year><first-content-word>`
  (ASCII-folded, lowercased) **plus a short deterministic fragment of the
  paper's arXiv id, always** (e.g. `smith2023modular-2301.12345`). Embedding
  the identity in *every* key eliminates collision handling entirely — no
  ordinals, no first-inserted-wins bare key, no re-keying — so a paper's cite
  key (and the `Papers.*` namespace derived from it) is a pure function of
  the paper, identical across any fetch order, lock interleaving, or fresh
  rebuild. The readability cost of the fragment is the accepted price of full
  order-independence (two prior schemes here fell to ordering dependence). A
  different *version of the same paper* gets the version-qualified key
  (`…V3`) per DESIGN.
- `add_or_get(meta) -> str`: dedup by (arXiv id, version); the DOI fallback
  applies **only when the entry has no arXiv identity** — revisions of one arXiv
  paper share a DOI, and letting v3 resolve to v1's entry through it would
  silently point citations (and M4 namespaces keyed from them) at the wrong
  version. Distinct versions of an arXiv record always get distinct entries and
  version-qualified keys. Returns the existing key when already present. This is
  the single write path.
- `validate(path) -> list[str]`: parse errors, duplicate keys, entries missing
  required fields — wired into CI via `scripts/validate_bib.py` (runs in the
  default unit-test tier; no network).

### `reading.py`

- `read(stored: StoredPaper, section: str | None, offset: int, limit: int) ->
  ReadResult` — when LaTeX source exists, serve it section-chunked (split on
  `\section`/`\subsection`, math source intact); otherwise extracted PDF text, page-chunked.
  PDF parsing treats the file as untrusted input (it came from the network,
  chosen by the agent): extraction runs in a **resource-limited subprocess**
  (rss/cpu rlimits, wall-clock kill, bounded input size and page count) — a
  malformed or decompression-bomb PDF can burn unbounded CPU/memory *inside*
  the parser, long before any output cap applies, so the cap alone cannot
  protect the harness process. The extractor's *output* is bounded too — streamed through a
  hard byte quota before the cache admits it, aborting over-quota — because a
  bounded-size PDF can still expand into an enormous text stream that would
  exhaust disk/memory after the parser's own rlimits and before any per-call
  response cap applies. Extracted text is cached in the derived-data layer so
  the parse happens once per paper. Output capped per call (compact, high-signal —
  Component 2 rules), with a table of contents served on the first call so the
  agent can navigate.

### Tools (`literature_tools.py`)

| Tool | Backing | Notes |
|------|---------|-------|
| `arxiv_search` | `arxiv.search` | Returns id, version, title, authors, truncated abstract per hit. |
| `fetch_paper` | `download` + `store.admit` + `bibliography.add_or_get` | Returns the store path, cite key, and whether LaTeX source is available. Idempotent on stored versions (no re-fetch). |
| `read_paper` | `reading.read` | Sectioned reading with navigation. |
| `cite` | `bibliography.add_or_get` / lookup | Look up or add; returns the cite key. The only `.bib` writer (fetch_paper delegates here). |

### Writeup pipeline changes

- `render_writeup` gains `bibliography: bool` behavior: when the document body
  contains `\cite`, the template emits `\bibliographystyle`+`\bibliography`
  pointing at a staged copy of the **needed fragment** of `references.bib` (the
  compile sandbox sees only the staging directory — DESIGN Component 5 — so the
  full project `.bib` is filtered to the cited keys and staged).
- The compile step treats unresolved citations as failures: after the run, the log
  is scanned for `Citation ... undefined`; that error feeds back to the agent like
  any TeX error. (Tectonic runs the bib pass automatically.)
- The Prove workflow's writeup phase gains the literature tools in its registry;
  M1's "no citations" rule is retired.

## Key decisions and rationale

- **Store is data, not git content.** `papers/` is gitignored (multi-hundred-MB
  PDFs don't belong in the repo); `references.bib` *is* committed — it is the
  durable, reviewable artifact and writeups must build from a fresh clone plus
  fetches. Considered committing `meta.json` manifests; rejected — the `.bib`
  entry records the version, and the committed `papers/DIGESTS.json` ledger
  (above) carries the content digests, the one piece of manifest data that must
  survive a fresh clone. (`papers/` stays gitignored except that ledger.)
- **Wrap the arXiv client library.** Alternative: call the Atom API directly.
  The library saves parsing work, but everything routes through our façade so
  rate-limit policy and caching are ours and the dependency is swappable.
- **Filter the `.bib` into staging rather than mount it whole.** The sandboxed
  compiler must not see project state beyond the document's needs (disclosure
  rule, Component 5); filtering to cited keys is cheap and keeps the invariant.
- **PDF-only admission on source problems.** A paper whose e-print is missing,
  over-quota, or hostile still enters the store (the PDF is independently useful);
  the extraction report keeps the failure visible instead of failing the fetch.

## Testing strategy

- **Unit:** query canonicalization + cache TTL; safe extraction against crafted
  tarballs (traversal via `..` and absolute paths, symlink escape, hardlink,
  device node, byte-quota and count-quota breaches) — fixture tarballs built in
  the test, no network; store atomicity (crash-mid-admit leaves nothing);
  immutability refusals; key minting incl. collisions and version qualification;
  dedup; bib round-trip + validator; section chunking on fixture LaTeX; each tool
  handler against fakes.
- **`tex`:** a writeup with `\cite` compiles against a staged bib fragment; an
  undefined citation is reported as a failure.
- **`network` (new marker):** one live arXiv search + fetch of a small canonical
  paper, exercising rate limiting and idempotent re-fetch. Excluded from CI.
- **CI:** `scripts/validate_bib.py` joins the unit job.

## Out of scope for M3

- `assume_paper`/axiomatization (M4 — `list_assumptions` too); semantic retrieval
  over papers (M8); citation-graph chasing; non-arXiv sources; OCR for scanned
  PDFs; bibliography styles beyond the template default.
