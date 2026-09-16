// The workbench Chat page's transcript: turn separators, the four message
// rail colours, collapsible thoughts, tool pills, Lean blocks and system
// lines. A sibling of `chat/Chat.jsx` (the old three-column client's message
// list), not a replacement for it -- `App.jsx` still renders that one
// unchanged, so this file owns its own markup and its own `wb-msg*` classes
// rather than reusing `.message`, even though the shapes are close cousins.
//
// Two things the old component didn't have to do, because the old client
// drew every reply as one undifferentiated block: turn separators, and
// picking Lean fences out of a reply's markdown so each one renders through
// `<Lean/>` (clickable names) instead of the generic `<pre><code>` the
// markdown renderer would otherwise give it.
//
// What is *not* drawn here, and why: `panels.session.transcript()` maps only
// `user`/`assistant`/`tool`/`turn` and a JSON-stringified notice for
// `conversation_branch`/`imported`/`model`/`obligations` -- a `report` event
// (the source of the prototype's post-save "§ record saved X → path · entry
// r-0412" / "⊢ kernel ..." / "§ ledger ..." lines) is not one of the types it
// maps, so no line the browser can honestly draw exists for it yet. A tool
// chip carries no duration and a thought carries no token count -- neither
// field exists on the transcript payload -- so neither is printed as a
// number; inventing one would be exactly the kind of rounding the design
// forbids.

import {memo, useMemo} from 'react';
import Absent from '../components/Absent.jsx';
import Lean from '../components/Lean.jsx';
import {render} from '../markdown.js';
import {splitLean} from './leanBlocks.js';
import Prompt from './Prompt.jsx';

function ToolPills({tools}) {
  if (!tools.length) return null;
  return (
    <div className="tools">
      {tools.map((tool, index) => (
        <span
          key={`${tool.call_id || tool.name}-${index}`}
          className={`chip chip--${tool.ok === null ? '' : tool.ok ? 'ok' : 'bad'}`}
        >
          {tool.name || 'tool'}
          {tool.ok === null ? ' …' : tool.ok ? ' ok' : ' failed'}
        </span>
      ))}
    </div>
  );
}

/** An assistant reply's markdown, with any ```lean fence drawn through
 *  `<Lean/>` instead of the generic code block the rest of the prose gets. */
function Prose({text, known, onLeanName}) {
  const segments = useMemo(() => splitLean(text), [text]);
  return (
    <>
      {segments.map((segment, index) =>
        segment.kind === 'lean' ? (
          <div key={index} className="wb-lean">
            <Lean src={segment.text} known={known} onName={onLeanName} />
            {known?.length ? (
              <div className="wb-lean__note">dotted names open their definition and informal reading</div>
            ) : null}
          </div>
        ) : segment.text ? (
          // eslint-disable-next-line react/no-danger
          <div key={index} className="prose" dangerouslySetInnerHTML={{__html: render(segment.text)}} />
        ) : null,
      )}
    </>
  );
}

/** A `notice` message's text, which is either a live plain-text note (from
 *  `on_notice`) or a JSON-stringified transcript event (`conversation_branch`
 *  /`model`/`imported`/`obligations`) -- `panels.session.transcript` builds
 *  the second kind with `json.dumps`, so parsing tells the two apart without
 *  guessing from shape. Two forms get the prototype's own glyphs, because the
 *  design names them specifically; everything else prints its exact type and
 *  fields rather than invented prose, the same "the label prints the exact
 *  kind" rule the ledger graph uses. */
function describeNotice(raw) {
  try {
    const payload = JSON.parse(raw);
    return payload && typeof payload === 'object' ? payload : null;
  } catch {
    return null;
  }
}

function NoticeLine({text}) {
  const payload = describeNotice(text);
  if (!payload) return <>{text}</>;
  if (payload.type === 'conversation_branch') {
    const from = payload.parent_id ?? payload.from_leaf ?? <Absent kind="unreported" />;
    if (payload.action === 'abandon') {
      return (
        <>
          × abandoned · branch from {from}
          {payload.summary?.text ? <> · lesson: &ldquo;{payload.summary.text}&rdquo;</> : null}
        </>
      );
    }
    return <>⑂ forked from {from}</>;
  }
  if (payload.type === 'model' && payload.reason === 'switched') {
    const from = payload.previous?.model ?? <Absent kind="unreported" />;
    const to = payload.model ?? <Absent kind="unreported" />;
    return (
      <>
        ⇄ model switched {from} → {to}
      </>
    );
  }
  const rest = Object.entries(payload).filter(([key]) => key !== 'type');
  return (
    <>
      {payload.type || 'notice'}
      {rest.length
        ? ` · ${rest.map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : value}`).join(' · ')}`
        : ''}
    </>
  );
}

const Message = memo(function Message({message, known, onLeanName}) {
  switch (message.kind) {
    case 'user':
      return <article className="wb-msg wb-msg--user">{message.text}</article>;
    case 'hardy':
      // A turn Hardy started on the model's behalf, drawn as Hardy's line,
      // never as one the person typed.
      return <article className="wb-msg wb-msg--hardy">hardy · {message.text}</article>;
    case 'assistant':
      return (
        <article className={message.partial ? 'wb-msg wb-msg--model wb-msg--partial' : 'wb-msg wb-msg--model'}>
          {message.thinking ? <div className="wb-msg__thinking">thinking…</div> : null}
          {message.thought ? (
            <details className="wb-msg__thought">
              <summary>thought</summary>
              <pre>{message.thought}</pre>
            </details>
          ) : null}
          <ToolPills tools={message.tools} />
          <Prose text={message.text} known={known} onLeanName={onLeanName} />
          {message.partial ? <div className="wb-msg__flag">interrupted</div> : null}
        </article>
      );
    case 'tool':
      return (
        <article className={`wb-msg wb-msg--tool ${message.ok === false ? 'wb-msg--refusal' : ''}`}>
          <div className="wb-msg__label">{message.name || 'tool'}</div>
          <pre>{message.text}</pre>
        </article>
      );
    case 'system':
      return (
        <article className={`wb-msg wb-msg--system${message.style === 'error' ? ' wb-msg--refusal' : ''}${message.style === 'warning' ? ' wb-msg--warning' : ''}`}>
          {message.text}
        </article>
      );
    case 'notice':
      return (
        <article className="wb-msg wb-msg--system">
          <NoticeLine text={message.text} />
        </article>
      );
    case 'turn':
      return (
        <article className="wb-msg wb-msg--system">
          <NoticeLine text={message.text} />
        </article>
      );
    default:
      return null;
  }
});

/** Every turn separator this data can honestly draw: an ordinal count of the
 *  messages that opened a turn (`user` or Hardy-initiated), since nothing the
 *  browser reads carries a turn index or a timestamp -- `panels.session
 *  .transcript()` gives no `timestamp` field at all, on any role, and only
 *  `turn` (cancelled/abandoned) entries mark a boundary explicitly. Counting
 *  is not inventing: every `user`/`hardy` message really did start a turn,
 *  which is the actual rule `turns.py` runs on -- so "turn 3" here is exact,
 *  and the date beside it is `Absent` rather than a fabricated one. */
function withSeparators(messages) {
  let turn = 0;
  const rows = [];
  for (const message of messages) {
    if (message.kind === 'user' || message.kind === 'hardy') {
      turn += 1;
      rows.push({sep: true, key: `sep-${message.id}`, turn});
    }
    rows.push({sep: false, key: message.id, message});
  }
  return rows;
}

export default function Transcript({messages, prompts, loaded, known, onAnswer, onLeanName}) {
  const rows = useMemo(() => withSeparators(messages), [messages]);

  return (
    <div className="wb-transcript">
      {!loaded && !messages.length ? <div className="panel__note">Loading the session…</div> : null}
      {loaded && !messages.length && !prompts.length ? (
        <div className="panel__note">Nothing yet. Say something, or run /help.</div>
      ) : null}
      {rows.map((row) =>
        row.sep ? (
          <div className="wb-turn-sep" key={row.key}>
            <span className="wb-turn-sep__line" />
            turn {row.turn} · <Absent kind="unreported" />
            <span className="wb-turn-sep__line" />
          </div>
        ) : (
          <Message key={row.key} message={row.message} known={known} onLeanName={onLeanName} />
        ),
      )}
      {prompts.map((prompt) => (
        <Prompt key={prompt.id} prompt={prompt} onAnswer={onAnswer} />
      ))}
    </div>
  );
}
