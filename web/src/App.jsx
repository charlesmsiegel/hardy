// State and the event reducer. Nothing here draws anything: the message list
// is `Chat`, the input is `Composer`, an open gate is `Prompt`.
//
// The page holds no truth of its own. Every field below is either something
// the server said (`status`, `commands`, `projects`, the transcript) or the
// accumulation of events it sent, and `changed` is the server telling the page
// that a panel's underlying artifact moved. The one exception is the user's
// own line, which is echoed locally the moment it is accepted: the stream
// republishes the model's half of a turn and never the half the user typed.

import {useCallback, useEffect, useMemo, useReducer} from 'react';
import {ApiError, events, get, post} from './api.js';
import Chat from './Chat.jsx';
import Composer from './Composer.jsx';

let counter = 0;
const nextId = () => `m${++counter}`;

const EMPTY_STATUS = {slug: '', chat: '', model: '', turn_running: false, command_running: false, prompts: []};

//: What the composer says before the dispatcher has had a chance to say it
//: itself. The host's own wording, so the first refusal a user sees and every
//: one after it read the same.
export const BUSY_REASON = 'A turn is still running. Wait for it to finish.';

const initial = {
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
function fromTranscript(entries) {
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
 *  that follows another of the same style extends it instead. Anything else
 *  arriving between two writes ends the run, which is what keeps a command's
 *  output from swallowing the turn that interrupted it.
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
    case 'turn_end':
      // The one place the turn's message is closed. Everything else that
      // arrives mid-turn goes beside it and leaves `streamId` alone.
      return {...settle(state), runningTool: ''};
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

function reducer(state, action) {
  switch (action.type) {
    case 'loaded':
      // The transcript goes *before* whatever has already arrived live. The
      // stream is subscribed to without waiting for these four fetches, so an
      // event can land first; replacing the list rather than prefixing it
      // would drop that event for good.
      return {
        ...state, loaded: true, status: action.status, commands: action.commands,
        projects: action.projects, messages: [...fromTranscript(action.transcript), ...state.messages],
        // A gate that was already open when this page loaded: its `prompt`
        // event predates the subscription, so the snapshot is the only place
        // the card can come from.
        prompts: mergePrompts(state.prompts, action.status.prompts ?? []),
      };
    case 'projects':
      return {...state, projects: action.projects};
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

export default function App() {
  const [state, dispatch] = useReducer(reducer, initial);
  const revision = state.revision;
  // `dispatch` is stable, so this is too and no effect below re-runs on it.
  const failed = useCallback((error) => dispatch({type: 'failed', text: String(error?.message ?? error)}), []);

  useEffect(() => {
    let live = true;
    Promise.all([get('/api/state'), get('/api/commands'), get('/api/transcript'), get('/api/projects')])
      .then(([status, commands, transcript, projects]) => {
        if (live) dispatch({type: 'loaded', status, commands, transcript, projects});
      })
      .catch((error) => live && failed(error));
    // Subscribed after the fetches are asked for but without waiting on them:
    // an event that lands while the transcript is in flight is still numbered,
    // and losing it would leave the page a turn behind until the next one.
    const stop = events((event) => live && dispatch({type: 'event', event}));
    return () => {
      live = false;
      stop();
    };
  }, []);

  // `changed` is the session saying an artifact moved. The project list is the
  // only panel this task owns; Task 12's panels key their own fetches on it.
  useEffect(() => {
    if (!revision) return undefined;
    let live = true;
    get('/api/projects')
      .then((projects) => live && dispatch({type: 'projects', projects}))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [revision]);

  const send = useCallback(async (text) => {
    const line = text.trim();
    if (!line) return;
    const id = nextId();
    dispatch({type: 'sending', id, text: line});
    try {
      const result = await post('/api/input', {text: line});
      if (result?.kind === 'unknown' && result.message) {
        // A `/nonsense`: nothing ran, so the echo stands and the dispatcher's
        // sentence goes under it.
        dispatch({type: 'failed', text: result.message});
      }
    } catch (error) {
      dispatch({type: 'unsent', id, text: line});
      if (error instanceof ApiError && error.status === 409) {
        // The dispatcher's own sentence, printed rather than paraphrased.
        dispatch({type: 'refused', message: error.message});
      } else {
        failed(error);
      }
    }
  }, [failed]);

  const answer = useCallback((promptId, value) => {
    post('/api/answer', {prompt_id: promptId, value}).catch((error) => {
      if (error instanceof ApiError && error.status === 409) {
        // Nothing is waiting for it: the gate closed between the snapshot
        // this card came from and the answer. Take the card away rather than
        // leave the user pressing a button that can no longer do anything.
        dispatch({type: 'event', event: {type: 'prompt_closed', id: promptId}});
        return;
      }
      failed(error);
    });
  }, [failed]);

  const cancel = useCallback(() => {
    post('/api/cancel')
      // A notice, not a `write`: what cancelling did is the page's own remark
      // and must not be absorbed into the run of lines a command was writing.
      .then((result) => result?.note && dispatch({type: 'event', event: {type: 'notice', text: result.note}}))
      .catch((error) => failed(error));
  }, [failed]);

  const setDraft = useCallback((draft) => dispatch({type: 'draft', draft}), []);
  const blocked = useCallback(() => dispatch({type: 'blocked'}), []);

  const busy = state.status.turn_running || state.status.command_running;
  const title = useMemo(
    () => [state.status.slug, state.status.chat].filter(Boolean).join(' / '),
    [state.status.slug, state.status.chat],
  );

  return (
    <div className="layout">
      <aside className="rail rail--left">
        <div className="rail__title">Projects</div>
        <ul className="rail__list">
          {state.projects.map((project) => (
            <li key={project.slug} className={project.active ? 'rail__item rail__item--active' : 'rail__item'}>
              {project.slug}
            </li>
          ))}
        </ul>
        <p className="rail__note">Switching, chats and uploads arrive with the panels.</p>
      </aside>

      <main className="main">
        <header className="header">
          <span className="header__title">{title || 'Hardy'}</span>
          <span className="header__model">{state.status.model}</span>
          {busy ? <span className="header__busy">working</span> : null}
        </header>
        <Chat
          messages={state.messages}
          prompts={state.prompts}
          loaded={state.loaded}
          onAnswer={answer}
        />
        <Composer
          draft={state.draft}
          commands={state.commands}
          busy={busy}
          refusal={state.refusal}
          runningTool={state.runningTool}
          onDraft={setDraft}
          onSend={send}
          onBlocked={blocked}
          onCancel={cancel}
        />
      </main>

      <aside className="rail rail--right">
        <div className="rail__title">Panels</div>
        <p className="rail__note">Summary, files, the lean tree and the ledger graph arrive with the panels.</p>
      </aside>
    </div>
  );
}
