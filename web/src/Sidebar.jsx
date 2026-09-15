// The projects rail: every problem in the root, its chats under it, and the
// live one marked.
//
// Nothing here decides what is open. Clicking a chat asks the server to open
// it and then says nothing; the switch arrives as a `state` event and a
// `changed`, and the highlight follows `status.slug`/`status.chat` rather than
// a local guess. That matters because an open can be refused -- a turn owns
// the session -- and a rail that had already moved its highlight would be
// claiming a switch that never happened. The refusal is the server's own
// sentence, printed beside the chat that was clicked and taken away again a
// few seconds later.

import {useCallback, useEffect, useRef, useState} from 'react';
import {patch, post} from './api.js';

//: How long a refusal stays beside the item it belongs to. Long enough to
//: read, short enough that it does not become part of the rail.
const NOTICE_MS = 6000;

/** `main` first, then whatever order the server listed. */
function ordered(chats) {
  const rest = chats.filter((chat) => chat.id !== 'main');
  const main = chats.filter((chat) => chat.id === 'main');
  return [...main, ...rest];
}

export default function Sidebar({projects, slug, chat, onProjects, onRefresh}) {
  //: Slugs the user has folded shut. Absent means open: a rail that starts
  //: collapsed hides the chats of the project the user is in.
  const [shut, setShut] = useState(() => new Set());
  const [notice, setNotice] = useState(null);
  //: The slug whose "+ chat" input is open, and what has been typed into it.
  const [adding, setAdding] = useState('');
  const [title, setTitle] = useState('');
  //: The chat being renamed, as `{slug, id}`, and the replacement title.
  const [renaming, setRenaming] = useState(null);
  const [renamed, setRenamed] = useState('');
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState('');
  const timer = useRef(null);

  const say = useCallback((key, text) => {
    setNotice({key, text});
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setNotice(null), NOTICE_MS);
  }, []);

  useEffect(() => () => timer.current && clearTimeout(timer.current), []);

  const open = useCallback(
    (projectSlug, chatId) =>
      post('/api/open', {slug: projectSlug, chat: chatId})
        .then(() => {
          setNotice(null);
          onRefresh();
        })
        .catch((error) => say(`${projectSlug}/${chatId}`, String(error?.message ?? error))),
    [onRefresh, say],
  );

  const toggle = (projectSlug) =>
    setShut((current) => {
      const next = new Set(current);
      if (next.has(projectSlug)) next.delete(projectSlug);
      else next.add(projectSlug);
      return next;
    });

  const createChat = async (projectSlug) => {
    const wanted = title.trim();
    if (!wanted) return;
    try {
      const created = await post(`/api/projects/${encodeURIComponent(projectSlug)}/chats`, {title: wanted});
      setAdding('');
      setTitle('');
      // The rail is redrawn *before* the open is attempted, and waited for.
      // The two halves of "+ chat" are not refused together: making the chat
      // is file I/O with no busy check, while `/api/open` raises `Busy` and
      // answers 409 whenever a turn owns the session. So "made but not
      // opened" is an ordinary outcome, and the user has to see both halves
      // of it -- the chat in the rail and the reason it is not open. The
      // refusal is keyed on the new chat's own row, which does not exist
      // until this refetch lands: without the wait the sentence would render
      // nowhere and the chat would appear, unopened and unexplained,
      // whenever the next `changed` happened to arrive.
      await onRefresh();
      await open(projectSlug, created.id);
    } catch (error) {
      say(`${projectSlug}/+chat`, String(error?.message ?? error));
    }
  };

  const rename = (projectSlug, chatId) => {
    const wanted = renamed.trim();
    if (!wanted) return;
    patch(`/api/projects/${encodeURIComponent(projectSlug)}/chats/${encodeURIComponent(chatId)}`, {title: wanted})
      .then(() => {
        setRenaming(null);
        setRenamed('');
        onRefresh();
      })
      .catch((error) => say(`${projectSlug}/${chatId}`, String(error?.message ?? error)));
  };

  const createProject = () => {
    const wanted = name.trim();
    if (!wanted) return;
    // The server answers with the whole list, already including the project it
    // just opened, so there is nothing to refetch.
    post('/api/projects', {name: wanted})
      .then((list) => {
        setNaming(false);
        setName('');
        onProjects(list);
      })
      .catch((error) => say('+project', String(error?.message ?? error)));
  };

  const beside = (key) => (notice && notice.key === key ? <div className="rail__error">{notice.text}</div> : null);

  return (
    <aside className="rail rail--left">
      <div className="rail__title">Projects</div>
      <ul className="rail__list">
        {projects.map((project) => {
          const folded = shut.has(project.slug);
          return (
            <li key={project.slug} className="rail__project">
              <div className="rail__row">
                <button
                  type="button"
                  className="rail__twist"
                  aria-expanded={!folded}
                  aria-label={folded ? `Show ${project.slug}'s chats` : `Hide ${project.slug}'s chats`}
                  onClick={() => toggle(project.slug)}
                >
                  {folded ? '>' : 'v'}
                </button>
                <span className={project.active ? 'rail__slug rail__slug--active' : 'rail__slug'}>{project.slug}</span>
              </div>
              {folded ? null : (
                <ul className="rail__chats">
                  {ordered(project.chats).map((entry) => {
                    const live = project.slug === slug && entry.id === chat;
                    const editing = renaming && renaming.slug === project.slug && renaming.id === entry.id;
                    return (
                      <li key={entry.id}>
                        {editing ? (
                          <input
                            className="rail__input"
                            autoFocus
                            value={renamed}
                            aria-label={`Rename ${entry.title}`}
                            onChange={(event) => setRenamed(event.target.value)}
                            onBlur={() => setRenaming(null)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') rename(project.slug, entry.id);
                              if (event.key === 'Escape') setRenaming(null);
                            }}
                          />
                        ) : (
                          <button
                            type="button"
                            className={live ? 'rail__chat rail__chat--live' : 'rail__chat'}
                            title="Double-click to rename"
                            onClick={() => open(project.slug, entry.id)}
                            onDoubleClick={() => {
                              setRenaming({slug: project.slug, id: entry.id});
                              setRenamed(entry.title);
                            }}
                          >
                            {entry.title || entry.id}
                          </button>
                        )}
                        {beside(`${project.slug}/${entry.id}`)}
                      </li>
                    );
                  })}
                  <li>
                    {adding === project.slug ? (
                      <input
                        className="rail__input"
                        autoFocus
                        placeholder="chat title"
                        value={title}
                        aria-label={`Title for a new chat in ${project.slug}`}
                        onChange={(event) => setTitle(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') createChat(project.slug);
                          if (event.key === 'Escape') setAdding('');
                        }}
                      />
                    ) : (
                      <button
                        type="button"
                        className="rail__add"
                        onClick={() => {
                          setAdding(project.slug);
                          setTitle('');
                        }}
                      >
                        + chat
                      </button>
                    )}
                    {beside(`${project.slug}/+chat`)}
                  </li>
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      {naming ? (
        <input
          className="rail__input"
          autoFocus
          placeholder="project name"
          value={name}
          aria-label="Name for a new project"
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') createProject();
            if (event.key === 'Escape') setNaming(false);
          }}
        />
      ) : (
        <button type="button" className="rail__add" onClick={() => setNaming(true)}>
          + project
        </button>
      )}
      {beside('+project')}
    </aside>
  );
}
