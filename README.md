# llm-math

An agentic harness for automated theorem proving — plug in any LLM and make it good
at proving mathematical theorems.

Ask it to prove that the square root of 2 is irrational and it produces **both** a
kernel-checked Lean 4 proof and a compile-checked LaTeX writeup with citations into
the project bibliography. LaTeX always; Lean wherever formalization is within reach.

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
milestones, and open questions.

## Status

Design phase. No code yet.
