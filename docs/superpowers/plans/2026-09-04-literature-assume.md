# Literature and Assumed-Paper Libraries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the literature stream (#65–#72). Four of the eight — reference checking (#65), polite fetching with immutable records (#66), the canonical bibliography (#68), and the paper tools (#69) — landed in the change this one builds on. This plan covers the remaining four: hostile archive extraction (#67), the Assume workflow (#70), faithfulness review with quarantine for assumed statements (#71), and verified-modulo grading with an exact axiom manifest (#72).

**Architecture:** Three new modules and one extended one. `archives.py` unpacks a downloaded source bundle defensively and knows nothing about arXiv; `arxiv.py` gains `fetch_source` and a `source/` tree beside each record, admitted atomically. `assume.py` inventories a paper's theorem environments from that source, renders the `Papers.<CiteKey>` module through one write path, and reviews each minted statement on a fresh thread with no tools. `refute.py` runs cheap refutation probes and is shared by the interactive `request_assumption` and the staged `hardy prove`, which learns to accept a declared axiom set and grade the result `verified_modulo`.

**Tech Stack:** Python 3.11, pydantic v2 `FrozenModel`s, `tarfile`/`gzip` from the standard library (never `extractall`), pytest with the fake Lean and fake LaTeX stand-ins.

## Global Constraints

- **Nothing in a source bundle is executed or compiled by Hardy.** Extracted files are text to read and inventory. Hardy still has no process isolation (#84); the defensive unpacking is a bound on what an archive can do to the filesystem, not a sandbox, and the documentation says so.
- **One write path per generated artifact.** `bibliography.json`/`references.tex` (already), the `source/` tree of a record (`PaperLibrary.admit_source`), the Papers module (`assume.mint` via the session's own save path), and `session.json` (already).
- **Fail closed.** A violation rejects the archive with the reason and leaves no partial extraction. A reviewer that cannot be reached is not an agreement. A refuted assumption is refused before any human is asked.
- **Every stored thing is checkable.** The source manifest carries the archive digest and every file's digest; a read verifies before serving. Minted axioms carry the paper's identity and the bibliography key in the docstring and the record.
- **Graded, never silently.** `verified_modulo` is a third grade. The manifest lists exactly the assumptions the proof used, read from `#print axioms`, never from what was declared.
- Tests are hermetic. Run with `uv run --extra test pytest -q`; lint with `uvx ruff check src tests`.

---

### Task 1 (#67): `archives.py` — defensive extraction

**Files:** Create `src/hardy/archives.py`; test `tests/unit/test_archives.py`.

**Interfaces:** `extract(data: bytes, into: Path, *, limits: Limits = Limits()) -> Extraction`. `Extraction(kind, files: tuple[ExtractedFile, ...])`; `ExtractedFile(path, size, sha256, text)`. `ArchiveError(ValueError)`.

- [ ] A tar.gz with nested files extracts, each with size and digest.
- [ ] `..`, absolute, backslash, NUL, drive-letter, over-deep, and duplicate member paths are refused by name.
- [ ] Symlink, hardlink, device, and FIFO members are refused.
- [ ] File-count and total-byte quotas hold on the decompressed stream; a single oversized member is refused.
- [ ] A gzipped single file becomes `main.tex`; a PDF becomes `paper.pdf`; anything else is refused.
- [ ] A refused archive leaves nothing under `into`.

### Task 2 (#67): the library admits a source tree atomically; the client fetches it politely

**Files:** `src/hardy/arxiv.py`, `src/hardy/paper_tools.py`, `src/hardy/prompts/chat.md.j2`; tests in `tests/unit/test_arxiv.py`, `tests/unit/test_paper_tools.py`, `tests/test_chat_papers.py`.

- [ ] `PaperLibrary.admit_source(identifier, archive, *, source_url, fetched_at)` stages under the record directory and lands `source/` with one rename; `source/source.json` carries the archive digest and per-file digests; a refused archive leaves the record exactly as it was.
- [ ] `PaperLibrary.read_source(identifier, path)` verifies the digest before serving; `holds_source` and `source_manifest` read the manifest.
- [ ] `ArxivClient.fetch_source(raw)` requires the record to be held, goes through the same throttle, uses a separate bounded archive transport, and returns `(manifest, already_held)`.
- [ ] `fetch_source` tool; `read_paper(paper_id, file=..., start_line=...)` serves a text file from the source tree, refusing binaries; the prompt and the tool descriptions say the source is unpacked defensively and never compiled.

### Task 3 (#70): `assume.py` — inventory and the Papers module

**Files:** Create `src/hardy/assume.py`; tests `tests/unit/test_assume.py`.

- [ ] `inventory(files)` lists theorem-like environments in reading order (root first, following `\input`/`\include`), with kind, printed word, best-effort number, label, heading, file and line, over executed TeX only (comments and verbatim dropped).
- [ ] The inventory is computed eagerly at `admit_source` and stored beside the source manifest.
- [ ] `namespace_for(cite_key)` yields a Lean-safe `Papers.<Key>`; `render_module(...)` writes the axioms with docstrings tying each to the paper's numbering, label, arXiv identifier and cite key.

### Task 4 (#70, #71): minting through the session

**Files:** `src/hardy/chat.py`, `src/hardy/prompts/assume_review.md.j2`, `src/hardy/tui/handlers.py`; tests `tests/test_chat_assume.py`.

- [ ] `list_statements(paper_id, start)` and `assume_statement(paper_id, statement, formal_name, lean_statement, informal_statement, reason, kind, latex_name)` tools; the latter runs the search-first gate, the shape check, the elaboration probe, the refutation probe, the faithfulness review, and the human confirmation, in that order, and mints into `lean/Papers/<Key>.lean` through the ordinary save path.
- [ ] A disputed review lands in `state["quarantine"]`, is reported by `read_workspace`, and a saved axiom under that name is refused by `_final_gates` naming the quarantine.
- [ ] `kind="constant"` records added trust; the Papers module and `Papers/` paths may not be written or deleted by hand.
- [ ] `/assume <paper_id> <ref> [<ref>...]` composes an explicit-set request the model answers with `assume_statement` calls.

### Task 5 (#72): `refute.py` and verified-modulo grading

**Files:** Create `src/hardy/refute.py`; `src/hardy/domain.py`, `src/hardy/verifier.py`, `src/hardy/workflow.py`, `src/hardy/writeup.py`, `src/hardy/acceptance.py`, `src/hardy/cli.py`, `src/hardy/chat.py`; tests `tests/unit/test_refute.py`, `tests/unit/test_verifier.py`, `tests/unit/test_workflow.py`, `tests/unit/test_prove_cli.py`.

- [ ] `refute.probe_source(statement)` and `refute.judge(result)`: a tactic that closes the negation refutes; an unplaced or stray error is a caveat, never a refutation.
- [ ] `request_assumption` and `assume_statement` refuse a refuted statement before anyone is asked.
- [ ] `DeclaredAssumption`, `ProveRequest.assumptions`, `FormalStatus.VERIFIED_MODULO`, `Grades.assumed`; the verifier renders declared axioms, classifies against their names, and reports exactly what was used; the writeup and the acceptance audit read the new grade; `hardy prove --assume FILE`.

### Task 6: documentation

- [ ] `README.md`, `DESIGN.md`, `FEATURES.md`, `ARCHITECTURE.html` updated together.
