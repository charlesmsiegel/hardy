# llm-math

An agentic harness for automated theorem proving in Lean 4 — plug in any LLM and make
it good at proving mathematical theorems.

The idea: Claude Code and Codex showed that the *harness* around a model (tight
feedback loops, well-designed tools, context management) is as important as the model
itself. Theorem proving is the ideal domain for this approach because the Lean kernel
provides perfect, free verification of every attempt.

See [DESIGN.md](DESIGN.md) for the full project outline: architecture, components,
milestones, and open questions.

## Status

Design phase. No code yet.
