# Streaming, cancellable runtime — design

Issue #32. Deferred deliberately from the TUI rework
(`2026-07-24-tui-improvements-design.md`), which reworked the input layer and
transcript presentation but left the runtime protocol alone.

## The problem

`ChatRuntime.ask(text) -> str` is blocking. A turn produces nothing visible
until the model is completely finished, which in a long proof search is a long
silent wait. Worse, the blocking call is why Esc cannot honestly cancel: the
TUI spec says so outright, and the shell's Esc handler says only that it
"stopped waiting". Streaming and cancellation are one change, not two.

## What this does not change

The transcript. `transcript.jsonl` records whole assistant blocks, tool calls,
and tool results, and it keeps doing exactly that. Streaming changes what the
terminal draws, not what the record holds — a transcript of ten thousand token
deltas would be worse evidence, not better.

The batch path. `runner.Runtime` (`runner.py:30`) stays `ask(text) -> str`, and
`staged.py:203` is untouched. A batch run has nobody watching it stream.

## Architecture

### `models.TurnEvent`

```python
@dataclass(frozen=True)
class TurnEvent:
    kind: str                    # text | thinking | tool_use | tool_result | reply
    text: str = ""               # a delta for `text`; the whole reply for `reply`
    name: str = ""               # tool name for tool_use / tool_result
    ok: bool | None = None       # tool outcome for tool_result
```

### The double-counting hazard

`include_partial_messages=True` makes the SDK emit **both** incremental
`StreamEvent` deltas **and** the completed `TextBlock` that those deltas built.
Consuming both would double every reply.

The rule, applied everywhere: **deltas are for display; `TextBlock`s remain
authoritative.** `ask()` joins blocks exactly as it does today, the closing
`reply` event carries that same joined text, and `observe` records whole
blocks. Nothing derives the reply from deltas.

### Protocol

```python
class ChatRuntime(Protocol):
    model: str
    def stream(self, text: str) -> Iterator[TurnEvent]: ...
    def ask(self, text: str) -> str: ...
    def cancel(self) -> None: ...
```

`ask` is a collector over `stream`, so there is one implementation of the turn
and no second path to drift.

### `claude_runtime` — the sync/async bridge

`stream()` is called from a worker thread; the SDK is async. A background
thread runs `asyncio.run`, pushing `TurnEvent`s into a `queue.Queue`, and
`stream()` yields from that queue until a sentinel. Exceptions raised inside
the loop travel through the queue and re-raise on the consumer's thread, so a
provider error still surfaces where the caller can see it.

`cancel()` is thread-safe and idempotent. It reaches the loop with
`run_coroutine_threadsafe(client.interrupt(), loop)`; the SDK supports
`interrupt()` in the streaming mode `ClaudeSDKClient.query` already uses.

### Cancellation, stated honestly

`interrupt()` stops the model. It does **not** kill a Lean or LaTeX subprocess
already running, and it cannot unwrite a file such a tool has already written.

So `MathematicsSession.cancel()` sets a flag that makes `_dispatch` refuse
*new* tool calls while letting in-flight ones finish — the pattern `runner.py`
already uses with its `closed` Event. The flag is read twice, once before the
tool gate and again after acquiring it: the SDK may launch several calls at
once, and one can pass the first check, block behind a Lean run that takes
minutes, and arrive long after the turn was cancelled.

Starting a turn is what clears that flag, so where "starting" happens decides
whether a cancellation survives. Neither `MathematicsSession.stream` nor
`ClaudeAgentRuntime.stream` is a generator: a generator body runs on whoever
iterates it, which is a worker thread, and Esc resolves in the same input batch
as the Enter that began the turn. A lazy reset would land after the
cancellation and undo it, leaving the model running while the transcript said
it had stopped. The shell therefore calls `session.stream` on the loop and
hands only the iteration to the executor.

Eagerness closes the window before the turn's own thread starts; it does not
close the one inside it. Connecting the SDK client happens on that thread, so a
cancellation landing while `_client` is still unpublished finds nothing to
interrupt and can only set the flag. `_exchange` therefore reads it after
publishing the client and before asking anything: without that, the turn goes on
to consume a whole reply while the terminal and the transcript both call it
stopped. A cancellation arriving after that line has a client and takes the
ordinary path.

The staged (`hardy prove`) path needs the same gate, for a sharper reason.
`ProveWorkflow` calls `runtime.cancel(thread)` and then finalizes: it writes the
terminal event and hashes the run directory. A tool call still running would
write artifacts and trajectory events *after* they were recorded, leaving a
manifest that does not describe the directory it names — and the runtime's
teardown join is bounded at five seconds while a Lean check is not.
`ClaudeStagedRuntime` therefore gates its dispatcher exactly as
`MathematicsSession` does, and `cancel` takes the gate before returning: holding
it is how the finalizing thread learns no tool is running. It then waits on the
provider's own thread through `ClaudeAgentRuntime.settle`, which is what reports
a finished call onward into the trajectory. The wait is bounded by the tools'
own timeouts rather than by a guess, because interrupting a Lean or CAS
subprocess is precisely what this section says Hardy will not do.

Teardown has an ordering requirement of its own. A consumer that unwinds —
Ctrl+C under `--plain` — closes the generator, and with `yield from` that
teardown reaches the runtime first: it interrupts the model and then waits on
its worker, all while the session's tool gate is still open. `_stream` yields
explicitly rather than delegating, so the gate shuts before any of that, and
`plain.run` holds its own reference to the generator so the close happens after
`session.cancel()` rather than the instant the loop is left. The transcript gains
`{"type": "turn", "status": "cancelled"}`, distinct from `"abandoned"`, which
stays for the `/exit`, forced-exit, and app-exited paths where nothing was
actually stopped.

Esc's message changes from "stopped waiting; the call is still running" to a
claim of cancellation, because it is now true. It still says that tool work
already begun may have completed.

### Rendering

`tui/transcript.py` is documented as never reflowing scrollback, because
rewriting scrollback breaks selection and copy. Streaming must not break that
rule, so a new `tui/stream.py` holds an incremental wrapper: it buffers deltas
and emits only *complete* wrapped lines. A line is printed once and never
rewritten; the tail is flushed when the turn ends.

Tool boundaries print as their own notice lines between prose, so output that
came from Lean or LaTeX stays visually distinct from the model's own words.

### Silence during tool work

Token streaming alone does not fix the worst wait. A `check_lean` call can run
for minutes and emits no prose at all, so a purely textual stream would still
show nothing while the slowest part of a turn happens.

Both ends of every tool call already cross the wire and Hardy currently drops
one: `_note` reads `TextBlock`, `ToolUseBlock`, and `ThinkingBlock`, but
ignores the `ToolResultBlock`s the SDK delivers inside `UserMessage`s. Emitting
both gives, with no new plumbing:

- `tool_use` rendered **when the call starts** rather than after it returns;
- `tool_result` rendered on completion, its duration timed by the shell from
  the gap between the two events;
- a spinner hint naming what is actually running — `⠹ check_lean · 45s` —
  instead of a generic `working`.

Out of scope, because nothing produces the information yet: partial output from
a running Lean or LaTeX subprocess, which needs plumbing into `lean.py` and
`latex.py`, and token or cost meters. Those are a separate issue.

`shell._run_turn` drains the stream on the worker thread and marshals each
event to the loop. `plain.run` iterates it directly through the same wrapper,
keeping `hardy < script.txt` working.

## Risk

`_run_turn`'s comments record real ordering bugs already paid for: work is
submitted to the executor synchronously inside `_submit_key` because a task
cancelled before its first scheduled step never runs any of its own body, and
`turn_running` flips there for the same reason. Moving from awaiting one future
to draining a queue must re-establish those invariants rather than assume they
survive. The existing tests in `tests/tui/test_turns.py` and
`test_marshalling.py` encode them and will need updating, not just extending.

## Testing

- `test_chat.py` — `send()` still returns joined block text with a streaming
  fake; `cancel()` records `cancelled`; `_dispatch` refuses new tools after it.
- `test_claude_runtime.py` — a fake SDK emitting deltas *and* blocks yields one
  copy of the reply, not two; `cancel()` reaches `interrupt()`.
- `tests/tui/test_stream.py` — wrapping across delta boundaries, no line
  rewritten, tail flushed.
- `tests/tui/` — Esc cancels a turn; plain mode streams, keeps the words that
  had already arrived when Ctrl+C lands mid-sentence, and still records an
  abandoned turn.
- `test_cancel_races.py` — one test per race, each confirmed to fail against the
  implementation it was written against: the reset that undid a cancellation,
  the tool waiting on the gate, plain-mode teardown ordering, the startup window
  before there is a client to interrupt, and the staged run that finalized while
  a tool was still working.
