# Documentation

This page maps every page under `docs/` and says where to start; it is for anyone opening Hardy's documentation for the first time.

The documentation is organized in four layers: a tutorial and guides that walk through doing something, reference pages that name every command, setting, and file exactly, and design pages that explain why the system is shaped the way it is. Status lives in one place, [the roadmap](roadmap.md); no other page marks what is planned, in progress, or done.

## Start here

- [Getting started](getting-started.md): walks a fresh clone through installing Hardy, checking the machine, and looking at a first proof.
- [Install](install.md): gets a working `hardy` command onto your machine and explains what each installer does and where things go.

## Guides

- [Interactive session](guides/interactive-session.md): walks through the things you do inside a running `hardy chat` session.
- [Proving](guides/proving.md): walks one claim from plain language to a verified, written-up result with `hardy prove`.
- [Evaluation](guides/evaluation.md): walks through `hardy evals`, from checking the corpus to reading what a scoreboard says.
- [Running safely](guides/running-safely.md): states what Hardy's trust boundary actually is today and how to put a real boundary around it.
- [MCP server](guides/mcp-server.md): describes Hardy's Model Context Protocol server and how to run it standalone or under the Codex backend.

## Reference

- [CLI](reference/cli.md): names every `hardy` command and every option each one takes.
- [Session commands](reference/session-commands.md): names every `/` command the prompt inside `hardy chat` accepts.
- [Configuration](reference/configuration.md): names every setting Hardy reads and where each one comes from.
- [On-disk layout](reference/on-disk-layout.md): names every directory and file Hardy reads or writes on a real filesystem.
- [Artifacts](reference/artifacts.md): names every record Hardy writes to disk and what its fields mean.

## Design

- [Overview](design/overview.md): explains the shape of Hardy and the reasoning behind it.
- [Module boundaries](design/module-boundaries.md): says who owns what inside `src/hardy/` and which imports are allowed in which direction.
- [Trust boundary](design/trust-boundary.md): states what Hardy controls, what it does not, and why.
- [Output contract](design/output-contract.md): says what Hardy's artifacts are allowed to claim and what stops them claiming more.
- [Computer algebra](design/computer-algebra.md): says how Hardy's persistent computer algebra session works and what its results are worth.
- [Interactive session](design/interactive-session.md): explains how `hardy chat` keeps a workspace that survives the process and stays honest about what happened in it.
- [Corpus](design/corpus.md): explains what the Hardy corpus holds and why it holds only that.
- [Evaluation](design/evaluation.md): explains how Hardy measures a model against the corpus, and how it measures itself.
- [Decisions](design/decisions.md): records the choices behind Hardy that no other page states as a choice.

## Planning and research

- [Roadmap](roadmap.md): tracks the work that remains and is the one place status lives.
- [Research architecture](research-architecture.md): sets out the direction for research, auditing, and publication features built on shared primitives.
- [Isolation](isolation.md): specifies the confinement boundary a sandboxed run must implement.
- [Delegation and research-swarm design](superpowers/specs/2026-09-10-delegation-swarm-design.md): the architectural specification for background workers and research swarms over the project ledger.
- [General literature sources design](superpowers/specs/2026-09-10-general-literature-sources-design.md): the architecture for managed scholarly sources, shared mathematical claims and reusable formalizations.

## Archive

- [Ideas](archive/ideas/README.md): archived planning material; start with [the roadmap](roadmap.md) and [research architecture](research-architecture.md) instead.
