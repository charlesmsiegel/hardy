// The one place `dispatch` is held. Everything that used to live in `App`
// besides drawing -- the initial fetches, the event subscription, and the
// actions a component can take against the session -- lives here now, and is
// handed down through context so any component under `SessionProvider` can
// read the session or act on it without threading props through the tree.

import {createContext, useCallback, useEffect, useMemo, useReducer, useRef} from 'react';
import {ApiError, events, get, post} from '../api.js';
import {fromTranscript, initial, nextId, reducer} from './reducer.js';

export const SessionContext = createContext(null);

export default function SessionProvider({children}) {
  const [state, dispatch] = useReducer(reducer, initial);
  const revision = state.revision;
  //: Which chat the messages on screen belong to. Empty until the first load
  //: has said, so the load's own transcript is not immediately refetched.
  const opened = useRef('');
  // `dispatch` is stable, so this is too and no effect below re-runs on it.
  const failed = useCallback((error) => dispatch({type: 'failed', text: String(error?.message ?? error)}), []);

  // The transcript is fetched only once the state says a project is open.
  // With nothing open `/api/transcript` answers 409, and a `Promise.all`
  // that included it rejected as a whole -- `loaded` never flipped, no
  // `state` event replays for a fresh subscription, and the page sat on
  // the full shell with an empty status instead of the project menu.
  const transcriptFor = (status) => (status.open ? get('/api/transcript') : Promise.resolve([]));

  useEffect(() => {
    let live = true;
    Promise.all([get('/api/state'), get('/api/commands'), get('/api/projects')])
      .then(([status, commands, projects]) =>
        transcriptFor(status).then((transcript) => {
          if (live) dispatch({type: 'loaded', status, commands, transcript, projects});
        }))
      .catch((error) => live && failed(error));
    // Subscribed after the fetches are asked for but without waiting on them:
    // an event that lands while the transcript is in flight is still numbered,
    // and losing it would leave the page a turn behind until the next one.
    // `resync` is the stream saying a reconnect asked for more than the ring
    // holds: the transcript is fetched again rather than drawn from a suffix.
    const resync = () =>
      get('/api/state')
        .then((status) => transcriptFor(status).then((transcript) => {
          if (live) dispatch({type: 'resynced', status, transcript});
        }))
        .catch((error) => live && failed(error));
    const stop = events((event) => {
      if (!live) return;
      if (event.type === 'resync') resync();
      else dispatch({type: 'event', event});
    });
    return () => {
      live = false;
      stop();
    };
  }, []);

  // `changed` is the session saying an artifact moved. The project list is
  // the session's own; every panel keys its fetch on the same number, through
  // `usePanel`.
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

  // A chat switch replaces the conversation. `changed` alone cannot say so --
  // it fires at the end of every turn as well -- so this keys on the pair the
  // status reports, which only an open moves. Without it the rail would open a
  // chat and leave the previous one's messages on screen under its name.
  const where = `${state.status.slug}/${state.status.chat}`;
  const loaded = state.loaded;
  useEffect(() => {
    if (!loaded) return undefined;
    if (opened.current === '') {
      // The first load already fetched this chat's transcript beside its state.
      opened.current = where;
      return undefined;
    }
    if (opened.current === where) return undefined;
    opened.current = where;
    // A close moves `where` to `null/null`; there is no transcript to fetch
    // and asking would only file a 409 into the messages.
    if (!state.status.open) return undefined;
    let live = true;
    get('/api/transcript')
      .then((transcript) => live && dispatch({type: 'switched', transcript}))
      .catch((error) => live && failed(error));
    return () => {
      live = false;
    };
  }, [where, loaded, failed]);

  // What the sidebar calls after a chat was made or renamed. Neither writes
  // through the session, so neither emits a `changed` the effect above would
  // see, and a rail still showing the old title would be wrong about the one
  // thing it exists to say.
  //
  // The promise is returned, and it always resolves: "+ chat" waits for the
  // redraw before it tries to open what it made, so the new chat has a row for
  // a refusal to be printed beside. A failure here is reported in the
  // transcript by `failed` and must not also reject the caller, which would
  // turn a stale rail into a chat that is never opened.
  const refreshProjects = useCallback(
    () =>
      get('/api/projects')
        .then((projects) => dispatch({type: 'projects', projects}))
        .catch((error) => failed(error)),
    [failed],
  );

  // Also for the sidebar: it computes its own optimistic project list (a
  // rename, say) and hands it straight to state rather than round-tripping
  // through `refreshProjects`.
  const setProjects = useCallback((projects) => dispatch({type: 'projects', projects}), []);

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
      // A `queued` answer needs nothing here: the echo stands where it was
      // typed, the `state` event carries how many wait, and the line is sent
      // by the server the moment the session is free.
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
  // Enter on a line the session will not take, from the composer.
  const blocked = useCallback(() => dispatch({type: 'blocked'}), []);

  const value = useMemo(
    () => ({...state, send, answer, cancel, setDraft, blocked, refreshProjects, setProjects}),
    [state, send, answer, cancel, setDraft, blocked, refreshProjects, setProjects],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
