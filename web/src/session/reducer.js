// The event reducer: state built by folding server events, never drawn
// directly. `SessionProvider` is the only thing that dispatches into it; every
// component reads the result through `useSession`.
//
// The session holds no truth of its own. Every field below is either
// something the server said (`status`, `commands`, `projects`, the
// transcript) or the accumulation of events it sent, and `changed` is the
// server telling the page that a panel's underlying artifact moved. The one
// exception is the user's own line, which is echoed locally the moment it is
// accepted: the stream republishes the model's half of a turn and never the
// half the user typed.

let counter = 0;
//: Exported so `SessionProvider` can stamp the same sequence onto the user's
//: echoed line -- one counter, so an id minted outside the reducer never
//: collides with one minted inside it.
export const nextId = () => `m${++counter}`;

const EMPTY_STATUS = {slug: '', chat: '', model: '', turn_running: false, command_running: false, queued: 0, prompts: []};

//: What the composer says before the dispatcher has had a chance to say it
//: itself. The host's own wording, so the first refusal a user sees and every
//: one after it read the same.
export const BUSY_REASON = 'A turn is still running. Wait for it to finish.';

export const initial = {
  status: EMPTY_STATUS,
  commands: [],
  projects: [],
  messages: [],
  prompts: [],
  //: The assistant message this turn is streaming into, held for the life of
  //: the turn. Null between turns.
  streamId: null,
  draft: '',
  refusal: '',
  runningTool: '',
  revision: 0,
  loaded: false,
};

/** The transcript's roles, which are the history's, mapped to message shapes. */
export function fromTranscript(entries) {
  return entries.map((entry) => {
    const id = entry.entry_id || nextId();
    if (entry.role === 'assistant') {
      return {id, kind: 'assistant', text: entry.text ?? '', thought: '', thinking: false,
              done: !entry.partial, partial: Boolean(entry.partial), tools: []};
    }
    if (entry.role === 'tool') {
      return {id, kind: 'tool', name: entry.name ?? '', ok: entry.ok ?? null, text: entry.text ?? ''};
    }
    if (entry.role === 'user') return {id, kind: 'user', text: entry.text ?? ''};
    if (entry.role === 'hardy') return {id, kind: 'hardy', text: entry.text ?? ''};
    if (entry.role === 'turn') return {id, kind: 'turn', text: entry.text ?? ''};
    return {id, kind: 'notice', text: entry.text ?? ''};
  });
}

function blank(id) {
  return {id, kind: 'assistant', text: '', thought: '', thinking: false, done: false, tools: []};
}

/** Apply `update` to the message this turn is streaming into, opening one if none is.
 *
 *  The turn's message is named by `streamId` and never inferred from the end
 *  of the list. A `notice` from a delegation finishing, or a line written by a
 *  safe-in-flight command, lands in the middle of a turn and becomes the last
 *  message; inferring from the tail would then start a second assistant
 *  message, and `reply` -- which carries the turn's whole final text -- would
 *  replace only that one, leaving everything said before the interruption on
 *  screen above a reply that repeats it. A `tool_result` would likewise find
 *  no chip to resolve and open an empty bubble to hold it.
 */
function withStream(state, update) {
  const messages = state.messages.slice();
  let id = state.streamId;
  let index = id === null ? -1 : messages.findLastIndex((message) => message.id === id);
  if (index < 0) {
    id = nextId();
    messages.push(blank(id));
    index = messages.length - 1;
  }
  messages[index] = update(messages[index]);
  return {...state, streamId: id, messages};
}

function push(state, message) {
  return {...state, messages: [...state.messages, {id: nextId(), ...message}]};
}

/** End the turn's message and forget it, so the next turn opens its own. */
function settle(state) {
  if (state.streamId === null) return {...state, streamId: null};
  return {
    ...state,
    streamId: null,
    messages: state.messages.map((message) =>
      message.id === state.streamId ? {...message, done: true, thinking: false} : message),
  };
}

/** Append one `write` line, joining the run of lines it belongs to.
 *
 *  A handler writes a line at a time -- `/help` is forty of them -- and each
 *  arrives as its own event because that is how the terminal consumes them.
 *  Drawn as forty blocks they would read as forty unrelated remarks, so a line
 *  extends the message at the end of the list whenever that message is a
 *  system line of the same style.
 *
 *  The test is the tail and nothing else, which cuts two ways. A message
 *  *pushed* between two writes -- a notice, an error, the user's own echoed
 *  line -- becomes the tail, so the next write starts a fresh run under it. A
 *  turn streaming meanwhile does not: `withStream` edits the message
 *  `streamId` names, wherever it sits in the list, so a command's run of lines
 *  stays open above it and the turn's words never land inside the command's
 *  output.
 */
function write(state, style, text) {
  const last = state.messages[state.messages.length - 1];
  if (last && last.kind === 'system' && last.style === style) {
    const messages = state.messages.slice(0, -1);
    messages.push({...last, text: `${last.text}\n${text}`});
    return {...state, messages};
  }
  return push(state, {kind: 'system', style, text});
}

/** Add prompts that are not open already, keeping the order they opened in.
 *
 *  Two sources say a gate is open and neither is complete on its own: the
 *  `prompt` events, which a page that loaded after the gate opened never saw,
 *  and `state.prompts`, which is a snapshot. Merging by id is what lets a
 *  reload draw a card that has been waiting since before the page existed
 *  without drawing the ones it did see twice. Nothing is removed here --
 *  `prompt_closed` is the only thing that closes a card, so a snapshot taken
 *  before a gate closed cannot reopen it.
 */
function mergePrompts(open, arriving) {
  const known = new Set(open.map((prompt) => prompt.id));
  const added = arriving.filter((prompt) => prompt?.id && !known.has(prompt.id));
  return added.length ? [...open, ...added] : open;
}

function turnEvent(state, event) {
  switch (event.kind) {
    case 'text':
      return withStream(state, (message) => ({...message, text: message.text + (event.text ?? ''), thinking: false}));
    case 'thinking':
      return withStream(state, (message) => ({
        ...message, thinking: true, thought: message.thought + (event.text ?? ''),
      }));
    case 'tool_use':
      return {
        ...withStream(state, (message) => ({
          ...message,
          tools: [...message.tools, {call_id: event.call_id ?? '', name: event.name ?? '', ok: null}],
        })),
        runningTool: event.name ?? '',
      };
    case 'tool_result':
      // No open turn is nothing to resolve: opening a message to hold an
      // orphan result would draw an empty bubble.
      if (state.streamId === null) return state;
      return withStream(state, (message) => ({
        ...message,
        tools: message.tools.map((tool) =>
          tool.call_id && tool.call_id === event.call_id ? {...tool, ok: event.ok ?? null} : tool),
      }));
    case 'reply':
      // Replace, never append: `reply` is the turn's whole final text, and the
      // `text` events before it were the same words arriving a piece at a time.
      return withStream(state, (message) => ({
        ...message, text: event.text ?? '', thinking: false, done: true,
      }));
    case 'notice':
      // Beside the turn, not instead of it: the turn keeps streaming into its
      // own message, which `streamId` still names.
      return push(state, {kind: 'notice', text: event.text ?? ''});
    default:
      return state;
  }
}

function applyEvent(state, event) {
  switch (event.type) {
    case 'turn':
      return turnEvent(state, event);
    case 'turn_end': {
      // The one place the turn's message is closed. Everything else that
      // arrives mid-turn goes beside it and leaves `streamId` alone.
      //
      // `leaf` names the transcript entry the turn ended on. A page that
      // loaded its transcript while this turn was ending already holds that
      // entry, so the message streamed here is a replay of one drawn, and
      // is dropped rather than shown under it a second time. Otherwise the
      // name is kept on the message, for a transcript that loads after it.
      const leaf = event.leaf || null;
      const replayed = leaf !== null && state.messages.some(
        (message) => message.id === leaf && message.id !== state.streamId);
      if (replayed) {
        return {
          ...state,
          streamId: null,
          runningTool: '',
          messages: state.messages.filter((message) => message.id !== state.streamId),
        };
      }
      const settled = settle(state);
      return {
        ...settled,
        runningTool: '',
        messages: leaf === null ? settled.messages : settled.messages.map((message) =>
          message.id === state.streamId ? {...message, leaf} : message),
      };
    }
    case 'error':
      return push(state, {kind: 'system', style: 'error', text: event.text ?? ''});
    case 'write':
      return write(state, event.style || 'system', event.text ?? '');
    case 'notice':
      // The session's own out-of-band note -- a delegation finishing, say.
      // It arrives mid-turn and goes beside the turn, not through it.
      return push(state, {kind: 'notice', text: event.text ?? ''});
    case 'prompt':
      return {...state, prompts: mergePrompts(state.prompts, [event])};
    case 'prompt_closed':
      return {...state, prompts: state.prompts.filter((prompt) => prompt.id !== event.id)};
    case 'state': {
      const {seq, type, prompts, ...status} = event;
      // A turn that has ended cannot still be refusing the composer.
      const refusal = status.turn_running || status.command_running ? state.refusal : '';
      // Prompts only ever *arrive* from a snapshot. A gate this page has
      // already been told closed must not be reopened by a status read taken
      // before it closed, and `prompt_closed` is the only thing that removes.
      return {
        ...state,
        status: {...state.status, ...status, prompts: prompts ?? []},
        prompts: mergePrompts(state.prompts, prompts ?? []),
        refusal,
      };
    }
    case 'changed':
      return {...state, revision: state.revision + 1};
    default:
      return state;
  }
}

export function reducer(state, action) {
  switch (action.type) {
    case 'loaded': {
      // The transcript goes *before* whatever has already arrived live. The
      // stream is subscribed to without waiting for these four fetches, so an
      // event can land first; replacing the list rather than prefixing it
      // would drop that event for good. A turn that both ended live and is in
      // the transcript is drawn once: its message carries the entry it ended
      // on, and the transcript holds that entry.
      const loaded = fromTranscript(action.transcript);
      const known = new Set(loaded.map((message) => message.id));
      const live = state.messages.filter((message) => !(message.leaf && known.has(message.leaf)));
      return {
        ...state, loaded: true, status: action.status, commands: action.commands,
        projects: action.projects, messages: [...loaded, ...live],
        // A gate that was already open when this page loaded: its `prompt`
        // event predates the subscription, so the snapshot is the only place
        // the card can come from.
        prompts: mergePrompts(state.prompts, action.status.prompts ?? []),
      };
    }
    case 'resynced':
      // The stream told this page it had been away for longer than the ring
      // holds, so what it drew since is not to be trusted as complete: the
      // transcript is drawn again from the server's copy and the status
      // taken fresh. Prompts are merged, not replaced, for the reason
      // `state` gives: only `prompt_closed` closes a card.
      return {
        ...state,
        status: {...state.status, ...action.status},
        prompts: mergePrompts(state.prompts, action.status.prompts ?? []),
        messages: fromTranscript(action.transcript),
        streamId: null,
        runningTool: '',
      };
    case 'projects':
      return {...state, projects: action.projects};
    case 'switched':
      // A different chat is a different conversation, so the list is replaced
      // rather than added to. Nothing is carried across: `streamId` named a
      // message that is no longer on screen, and `open_chat` cancels the
      // prompts of the session it replaced, so a card left here would be a
      // gate nothing is waiting behind.
      return {
        ...state,
        messages: fromTranscript(action.transcript),
        streamId: null,
        prompts: [],
        refusal: '',
        runningTool: '',
      };
    case 'event':
      return applyEvent(state, action.event);
    case 'draft':
      return {...state, draft: action.draft};
    case 'sending':
      // Echoed before the POST is answered, not after. The turn it starts
      // streams back on a connection of its own, and on a fast session the
      // first `text` event has arrived before `fetch` resolves -- echoing the
      // line then would file the question underneath its own answer.
      return {...push(state, {id: action.id, kind: 'user', text: action.text}), draft: '', refusal: ''};
    case 'unsent':
      // The server would not take it. Take the echo back and hand the user
      // their line, rather than leaving a message on screen that was never sent.
      return {
        ...state,
        messages: state.messages.filter((message) => message.id !== action.id),
        draft: state.draft || action.text,
      };
    case 'refused':
      return {...state, refusal: action.message};
    case 'blocked':
      // Enter on a line the session will not take. Nothing is posted; the
      // reason is the dispatcher's own from the last refusal, or the host's
      // wording until one has been seen.
      return {...state, refusal: state.refusal || BUSY_REASON};
    case 'failed':
      return push(state, {kind: 'system', style: 'error', text: action.text});
    default:
      return state;
  }
}
