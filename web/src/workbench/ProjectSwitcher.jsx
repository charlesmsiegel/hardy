// The project switcher: the 520px dropdown behind the top bar's project
// chip. `/api/projects` already lists every problem in the root, each with
// its chats and which one is live; `/api/chats` (new this shipment) adds
// turn counts and a last-activity stamp, but only for whichever project is
// currently open -- it reads the live session, not an arbitrary slug.
//
// Between the two, a row can honestly show a real chat count for every
// project and a real last-activity stamp for the live one. Everything else
// the prototype's own mock rows carry -- a goal, per-project theorem and
// obligation counts, a state word for a project that is not the live one --
// has no source anywhere in this shipment, so those cells render `Absent`
// rather than a number nothing asked for.
//
// Picking a row calls the same `/api/open` the old client's `Sidebar` uses
// to switch chats, not a `/project switch` line through the composer: it is
// the direct action, refusable the same way (a turn still running answers
// 409), and the highlight follows the `state` event it produces rather than
// an optimistic guess here.

import {useCallback, useEffect, useRef, useState} from 'react';
import {get, post} from '../api.js';
import Absent from '../components/Absent.jsx';
import useHash from '../session/useHash.js';

//: How long a refusal or an "unavailable" note stays in the footer before
//: the switching-note reclaims the line, matching `Sidebar`'s own timing.
const NOTICE_MS = 6000;

function stateWord(status) {
  if (status.turn_running) return 'turn running';
  if (status.command_running) return 'running';
  return 'idle';
}

/** The latest `last_activity` across `/api/chats`' rows, or `null` if none report one. */
function lastActivity(overview) {
  if (!overview) return null;
  const stamps = overview.map((chat) => chat.last_activity).filter((value) => typeof value === 'number');
  return stamps.length ? Math.max(...stamps) : null;
}

function when(ts) {
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

export default function ProjectSwitcher({status, projects, setProjects, refreshProjects, revision}) {
  // Read here rather than threaded down from `TopBar`: the route is only
  // needed to undo a stale chat id after a switch, and passing it through a
  // component that has no other use for it would make TopBar re-render on
  // every navigation.
  const [route, go] = useHash();
  const [open, setOpen] = useState(false);
  const [overview, setOverview] = useState(null);
  const [notice, setNotice] = useState('');
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const boxRef = useRef(null);
  const timer = useRef(null);

  // The live project's chat overview, fetched only while the dropdown is
  // open -- the same "a mount is a fetch" rule every panel in this client
  // follows -- and refetched if the session's own revision moves while it
  // is open, so a turn that ends mid-browse updates the row under the user.
  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    get('/api/chats')
      .then((rows) => live && setOverview(rows))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [open, revision]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event) => {
      if (boxRef.current && !boxRef.current.contains(event.target)) setOpen(false);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  useEffect(() => () => timer.current && clearTimeout(timer.current), []);

  const say = useCallback((text) => {
    setNotice(text);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setNotice(''), NOTICE_MS);
  }, []);

  const pick = useCallback(
    (slug) => {
      if (slug === status.slug) {
        setOpen(false);
        return;
      }
      post('/api/open', {slug, chat: 'main'})
        .then(() => {
          setNotice('');
          setOpen(false);
          refreshProjects();
          // The switch opens `main`, so the route has to say `main`. While
          // the hash still read `#/chat/<some-other-id>`, the Chat page's
          // route effect saw that id differ from the new `status.chat` and
          // called `/api/open` again for it in the project just opened --
          // landing on an unintended same-named chat, or erroring if that
          // project has no such chat. Naming the chat that was actually
          // opened is what stops the page arguing with the switch.
          if (route.page === 'chat' && route.arg) go({page: 'chat', arg: ''});
        })
        .catch((error) => say(String(error?.message ?? error)));
    },
    [status.slug, refreshProjects, say, route.page, route.arg, go],
  );

  const createProject = useCallback(() => {
    const wanted = name.trim();
    if (!wanted) return;
    post('/api/projects', {name: wanted})
      .then((list) => {
        setAdding(false);
        setName('');
        setOpen(false);
        setProjects(list);
      })
      .catch((error) => say(String(error?.message ?? error)));
  }, [name, setProjects, say]);

  return (
    <span className="wb-switcher" ref={boxRef}>
      <button type="button" className="wb-switcher__button" onClick={() => setOpen((value) => !value)}>
        {status.slug || <Absent kind="unreported" />} ▾
      </button>
      {open ? (
        <div className="wb-switcher__panel">
          <div className="wb-switcher__header">
            <span>Project · ~/math</span>
            <span>Goal</span>
          </div>
          {projects.map((project) => {
            const active = project.slug === status.slug;
            const count = project.chats.length;
            const activity = active ? lastActivity(overview) : null;
            return (
              <a
                key={project.slug}
                href="#"
                className="wb-switcher__row"
                style={{background: active ? 'color-mix(in srgb, var(--accent) 10%, transparent)' : 'transparent'}}
                onClick={(event) => {
                  event.preventDefault();
                  pick(project.slug);
                }}
              >
                <span className="wb-switcher__col">
                  <span className="wb-switcher__name" style={{color: active ? 'var(--accent)' : 'var(--fg)'}}>
                    {project.slug}
                  </span>
                  <span className="wb-switcher__state" style={{color: active ? 'var(--accent)' : 'var(--muted)'}}>
                    {active ? stateWord(status) : <Absent kind="unreported" />}
                  </span>
                </span>
                <span className="wb-switcher__col">
                  <span className="wb-switcher__goal">
                    <Absent kind="unreported" />
                  </span>
                  <span className="wb-switcher__facts">
                    {count} chat{count === 1 ? '' : 's'}
                    {activity ? ` · last activity ${when(activity)}` : ''}
                  </span>
                </span>
              </a>
            );
          })}
          <div className="wb-switcher__divider" />
          <div className="wb-switcher__footer">
            {adding ? (
              <input
                className="wb-switcher__input"
                autoFocus
                placeholder="project name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') createProject();
                  if (event.key === 'Escape') setAdding(false);
                }}
              />
            ) : (
              <a
                href="#"
                onClick={(event) => {
                  event.preventDefault();
                  setAdding(true);
                }}
              >
                New project…
              </a>
            )}
            <a
              href="#"
              onClick={(event) => {
                event.preventDefault();
                say('not available in the browser client');
              }}
            >
              Open folder…
            </a>
            <span className={notice ? 'wb-switcher__note wb-switcher__note--error' : 'wb-switcher__note'}>
              {notice || 'switching leaves the running turn alone · also /project <name>'}
            </span>
          </div>
        </div>
      ) : null}
    </span>
  );
}
