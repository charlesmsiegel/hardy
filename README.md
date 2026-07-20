# Hardy

An agentic harness for automated theorem proving — plug in any LLM and make it good
at proving mathematical theorems.

## The name

In 1913, G.H. Hardy received a letter from Srinivasa Ramanujan: pages of
extraordinary mathematical claims, stated without proof. Hardy's response defined
the collaboration — recognize the brilliance, then *demand the proof*. That is
precisely this harness's relationship to the model plugged into it: the LLM supplies
the flashes of insight; Hardy supplies the rigor, the verification, and the
insistence that nothing ships until it's proved. Hardy also wrote
*A Mathematician's Apology* — the writeup matters as much as the proof, which is why
every result here comes with one.

## What it does

Ask it to prove that the square root of 2 is irrational and it produces **both** a
kernel-checked Lean 4 proof and a compile-checked LaTeX writeup with citations into
the project bibliography. LaTeX always; Lean wherever formalization is within reach.

Three composable workflows, which hand a problem back and forth iteratively:

- **Prove** — *"find a proof of X"*: search for a proof, produce the artifact pair.
- **Critique** — *"find holes in this proof"*: take any proof — yours, the
  literature's, or Hardy's own draft — and produce a structured ledger of gaps:
  unjustified steps, missing cases, quantifier slips, misapplied citations.
- **Repair** — *"this proof has a hole; propose a fix"*: patch one hole locally and
  verify the patch.

Prove drafts, Critique finds the holes, Repair closes them one at a time, Critique
re-checks — around the loop until the ledger is empty or the remaining holes are
honestly reported.

## How

The idea: Claude Code and Codex showed that the *harness* around a model (tight
feedback loops, well-designed tools, context management) is as important as the model
itself. Theorem proving is the ideal domain for this approach because the Lean kernel
provides perfect, free verification of every attempt. Built first on the Claude Agent
SDK, behind an abstraction layer that also admits Strands, Ollama, and other
runtimes; with arXiv search/download and a machine-maintained BibTeX bibliography as
first-class tools.

Beyond Mathlib's frontier, "assume this paper" turns a paper's results into an
axiomatized Lean library (`Papers.*` namespaces of `axiom` declarations), so new
theorems can be proved *modulo* the literature — with every result carrying an
explicit axiom manifest of exactly which paper results it relied on.

See [DESIGN.md](DESIGN.md) for the full project outline: architecture, components,
milestones, and open questions — or open the interactive design map at
[docs/architecture.html](docs/architecture.html) for a browsable, diagrammed
version of the whole plan.

## Status

Design phase. No code yet.
