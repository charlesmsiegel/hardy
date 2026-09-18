// The project switcher: the 560px dropdown behind the top bar's project
// chip. `/api/projects` lists every REGISTERED problem -- from any number of
// roots, since the browser opens what `~/.hardy/projects.json` names rather
// than whatever directory the server was started in -- each with its path,
// its chats and whether it is the one open. `/api/chats` adds turn counts
// and a last-activity stamp, but only for whichever project is open: it
// reads the live session, not an arbitrary path.
//
// Between the two, a row can honestly show a real chat count for every
// project and a real last-activity stamp for the open one. A goal, a state
// word for a project that is not open, per-project theorem counts -- none of
// those has a source, so those cells render `Absent` rather than a number
// nothing asked for.
//
// Picking a row calls `/api/open` with the project's PATH, not a `/project
// switch` line through the composer: it is the direct action, refusable the
// same way (a turn still running answers 409), and the highlight follows the
// `state` event it produces rather than an optimistic guess here. The footer
// holds the four registry actions: New (a name, and optionally a location;
// the default root is what the placeholder shows), Add (a path to a problem
// or to a root holding several), Close (back to nothing open), and a Forget
// link per row that is not the open one. Forgetting drops the entry and
// never touches the directory; the server says so if asked to forget the
// open project.

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
  return 'open';
}

/** The latest `last_activity` across `/api/chats`' rows, or `null` if none report one. */
function lastActivity(overview) {
  if (!overview) return null;
  const stamps = overview.map((chat) => chat.last_activity).filter((value) => typeof value === 'number');
  return stamps.length ? Math.max(...stamps) : null;
}

export function when(ts) {
  if (!ts) return null;
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
  //: Which footer form is out: `''`, `'new'` or `'add'`.
  const [form, setForm] = useState('');
  const [name, setName] = useState('');
  const [location, setLocation] = useState('');
  const [path, setPath] = useState('');
  const boxRef = useRef(null);
  const timer = useRef(null);

  // The open project's chat overview, fetched only while the dropdown is
  // open -- the same "a mount is a fetch" rule every panel in this client
  // follows -- and refetched if the session's own revision moves while it
  // is open, so a turn that ends mid-browse updates the row under the user.
  useEffect(() => {
    if (!open || !status.open) return undefined;
    let live = true;
    get('/api/chats')
      .then((rows) => live && setOverview(rows))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [open, revision, status.open]);

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

  const afterSwitch = useCallback(() => {
    setNotice('');
    setOpen(false);
    refreshProjects();
    // The switch opens `main`, so the route has to say `main`. While the
    // hash still read `#/chat/<some-other-id>`, the Chat page's route effect
    // saw that id differ from the new `status.chat` and called `/api/open`
    // again for it in the project just opened -- landing on an unintended
    // same-named chat, or erroring if that project has no such chat. Naming
    // the chat that was actually opened is what stops the page arguing with
    // the switch.
    if (route.page === 'chat' && route.arg) go({page: 'chat', arg: ''});
  }, [refreshProjects, route.page, route.arg, go]);

  const pick = useCallback(
    (projectPath) => {
      if (status.open && projectPath === status.path) {
        setOpen(false);
        return;
      }
      post('/api/open', {path: projectPath, chat: 'main'})
        .then(afterSwitch)
        .catch((error) => say(String(error?.message ?? error)));
    },
    [status.open, status.path, afterSwitch, say],
  );

  const close = useCallback(() => {
    post('/api/close')
      .then(() => {
        setNotice('');
        setOpen(false);
        refreshProjects();
        go('home');
      })
      .catch((error) => say(String(error?.message ?? error)));
  }, [refreshProjects, go, say]);

  const createProject = useCallback(() => {
    const wanted = name.trim();
    if (!wanted) return;
    const body = {name: wanted};
    if (location.trim()) body.location = location.trim();
    post('/api/projects', body)
      .then((list) => {
        setForm('');
        setName('');
        setLocation('');
        setProjects(list);
        afterSwitch();
      })
      .catch((error) => say(String(error?.message ?? error)));
  }, [name, location, setProjects, afterSwitch, say]);

  const addProject = useCallback(() => {
    const wanted = path.trim();
    if (!wanted) return;
    post('/api/projects/add', {path: wanted})
      .then((list) => {
        setForm('');
        setPath('');
        setProjects(list);
        say(`added ${wanted}`);
      })
      .catch((error) => say(String(error?.message ?? error)));
  }, [path, setProjects, say]);

  const forget = useCallback(
    (projectPath) => {
      post('/api/projects/forget', {path: projectPath})
        .then((list) => setProjects(list))
        .catch((error) => say(String(error?.message ?? error)));
    },
    [setProjects, say],
  );

  const onFormKey = (submit) => (event) => {
    if (event.key === 'Enter') submit();
    if (event.key === 'Escape') setForm('');
  };

  return (
    <span className="wb-switcher" ref={boxRef}>
      <button type="button" className="wb-switcher__button" onClick={() => setOpen((value) => !value)}>
        {status.open ? status.slug : 'no project'} ▾
      </button>
      {open ? (
        <div className="wb-switcher__panel">
          <div className="wb-switcher__header">
            <span>Project</span>
            <span>Location</span>
          </div>
          {projects.length === 0 ? (
            <div className="wb-switcher__none">
              No projects registered. New project… creates one; Add existing… registers a folder.
            </div>
          ) : null}
          {projects.map((project) => {
            const active = project.active;
            const count = project.chats.length;
            const activity = active ? lastActivity(overview) : null;
            return (
              <a
                key={project.path}
                href="#"
                className="wb-switcher__row"
                style={{background: active ? 'color-mix(in srgb, var(--accent) 10%, transparent)' : 'transparent'}}
                onClick={(event) => {
                  event.preventDefault();
                  pick(project.path);
                }}
              >
                <span className="wb-switcher__col">
                  <span className="wb-switcher__name" style={{color: active ? 'var(--accent)' : 'var(--fg)'}}>
                    {project.slug}
                  </span>
                  <span className="wb-switcher__state" style={{color: active ? 'var(--accent)' : 'var(--muted)'}}>
                    {active ? stateWord(status) : project.registered ? 'registered' : <Absent kind="unreported" />}
                  </span>
                </span>
                <span className="wb-switcher__col">
                  <span className="wb-switcher__goal" title={project.path}>
                    {project.root}
                  </span>
                  <span className="wb-switcher__facts">
                    {count} chat{count === 1 ? '' : 's'}
                    {activity ? ` · last activity ${when(activity)}` : project.last_opened ? ` · opened ${when(project.last_opened)}` : ''}
                    {active || !project.registered ? null : (
                      <>
                        {' · '}
                        <a
                          href="#"
                          className="wb-switcher__forget"
                          onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            forget(project.path);
                          }}
                        >
                          forget
                        </a>
                      </>
                    )}
                  </span>
                </span>
              </a>
            );
          })}
          <div className="wb-switcher__divider" />
          {form === 'new' ? (
            <div className="wb-switcher__form">
              <input
                className="wb-switcher__input"
                autoFocus
                placeholder="project name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                onKeyDown={onFormKey(createProject)}
              />
              <input
                className="wb-switcher__input wb-switcher__input--wide"
                placeholder={`location (default ${status.default_root || ''})`}
                title="the directory the project is created under; leave empty for the default"
                value={location}
                onChange={(event) => setLocation(event.target.value)}
                onKeyDown={onFormKey(createProject)}
              />
              <button type="button" className="wb-switcher__button" onClick={createProject}>create</button>
            </div>
          ) : null}
          {form === 'add' ? (
            <div className="wb-switcher__form">
              <input
                className="wb-switcher__input wb-switcher__input--wide"
                autoFocus
                placeholder="path to a Hardy project, or a folder holding several"
                value={path}
                onChange={(event) => setPath(event.target.value)}
                onKeyDown={onFormKey(addProject)}
              />
              <button type="button" className="wb-switcher__button" onClick={addProject}>add</button>
            </div>
          ) : null}
          <div className="wb-switcher__footer">
            <a
              href="#"
              onClick={(event) => {
                event.preventDefault();
                setForm(form === 'new' ? '' : 'new');
              }}
            >
              New project…
            </a>
            <a
              href="#"
              onClick={(event) => {
                event.preventDefault();
                setForm(form === 'add' ? '' : 'add');
              }}
            >
              Add existing…
            </a>
            {status.open ? (
              <a
                href="#"
                onClick={(event) => {
                  event.preventDefault();
                  close();
                }}
              >
                Close project
              </a>
            ) : null}
            <span className={notice ? 'wb-switcher__note wb-switcher__note--error' : 'wb-switcher__note'}>
              {notice || 'switching leaves the running turn alone · also /project <name>'}
            </span>
          </div>
        </div>
      ) : null}
    </span>
  );
}
