# The MCP server

This page describes Hardy's Model Context Protocol server: what it serves,
how to run it standalone, and how the Codex backend launches it, for anyone
wiring an MCP-capable client to Hardy or debugging a staged Codex run.

## What it is

`hardy.app.mcp` builds a FastMCP server named `Hardy Lean Tools`
(`json_response=True`) and serves it over stdio. It is the same Lean
service the in-process workflow uses, so a client that cannot host
in-process tools (the Codex SDK, an editor, another agent) still goes
through Hardy's checks rather than around them. `hardy.mcp_server` is a
thin compatibility entry point that re-exports the public names from
`hardy.app.mcp`.

## Running it

```sh
python -m hardy.mcp_server
```

The process reads its configuration from the environment rather than from
flags:

- `HARDY_RUN_DIR` and `HARDY_CONFIG` are required. The server refuses to
  start without both.
- `HARDY_CLAIM_SHA256` is optional. Its presence is what decides whether
  the Lean tools are served at all; see below.

These variables, and what happens when one is missing, are also
documented in
[configuration.md](../reference/configuration.md#environment-variables-without-a-setting).

## The tools

Five Lean tools are registered at import:

| Tool | What it does |
| --- | --- |
| `lean_check_proof` | Check one proof body against the exact Frozen Claim. |
| `lean_check_scratch` | Check bounded exploratory source under Hardy's fixed imports. |
| `lean_inspect_declarations` | Resolve a bounded list of exact Lean declaration names. |
| `lean_search_declarations` | Search declaration names read from the pinned package sources. |
| `rank_premises` | Rank the declarations most likely to help with one goal. |

Four more are registered only once a computer algebra kernel has actually
answered a probe, a broken or absent backend leaves the server without
them rather than with tools that could only fail:

| Tool | What it does |
| --- | --- |
| `cas_run` | Execute one cell in the persistent computer algebra session. |
| `cas_state` | List the accepted cells that built the current session state. |
| `cas_reset` | Discard the session state and start a clean kernel. |
| `cas_export` | Export the session, replaying it in a fresh kernel to check it reproduces. |

## Without a claim, only CAS is served

`HARDY_CLAIM_SHA256` is what says a Frozen Claim exists for this run.
Without it, the Lean tools are withdrawn rather than registered and left to
fail, and the server serves the computer algebra session and nothing else.
That is what a formalization-stage server needs, and all it can honestly
offer: deciding what to formalize is exactly when examples get computed,
before any claim is frozen to check a proof against.

## With a claim, the claim must match this run

When `HARDY_CLAIM_SHA256` is set, the server reads `formalization.json`
from `HARDY_RUN_DIR` and refuses to start unless all of the following
hold:

- The claim re-hashes to itself: recomputing its content hash from its own
  fields must reproduce the hash stored on it.
- That hash must equal `HARDY_CLAIM_SHA256`, so a tool call cannot be
  answered against a different run's claim.
- The claim's recorded imports must match its environment's imports.

A Lean project must also be configured (`lean_project` in the config file
named by `HARDY_CONFIG`); the server refuses to start for a claim without
one.

## Two bounds enforced here, and nowhere else

- **The official proof-check budget.** `lean_check_proof` spends from the
  run's official-check budget, so a client cannot buy extra attempts by
  asking again.
- **The model-observation byte cap.** Every result is measured before it
  is returned. Anything larger than the observation budget is written to
  the run store whole, and the tool answers with a bounded summary that
  names the artifact holding the rest.

## How the Codex backend uses it

A staged `--backend codex` run starts this server as a subprocess and
serves Hardy's tools to the Codex SDK's agent over stdio MCP, rather than
in process. `agents/codex.py` launches it as:

```json
{
  "mcp_servers": {
    "hardy": {
      "command": "<sys.executable>",
      "args": ["-m", "hardy.mcp_server"],
      "cwd": "<the run directory>",
      "env": {
        "HARDY_RUN_DIR": "...",
        "HARDY_CONFIG": "...",
        "HARDY_CLAIM_SHA256": "..."
      },
      "startup_timeout_sec": 20,
      "required": true
    }
  }
}
```

`HARDY_CLAIM_SHA256` is set only once a claim exists; before that, the
server is still started and serves the computer algebra session alone.
`required: true` means the agent thread does not start if the server
fails to come up within its startup timeout.

## This runs on the same unconfined host

The MCP subprocess is a process seam, not a security boundary: it runs on
the same unconfined host as everything else Hardy starts, and the Codex
SDK's own agent keeps its own file and shell tools alongside it. See
[running Hardy safely](running-safely.md) for what that means in
practice and how to put a real boundary around the whole of it.
