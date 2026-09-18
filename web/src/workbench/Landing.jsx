// What the body shows while no project is open: the registered projects,
// each with an Open button, and the two ways to get one -- New (a name, and
// optionally a location; the default root is the placeholder) and Add (a
// path to a Hardy problem, or to a folder holding several).
//
// This is a real state, not a loading screen. The server started with
// nothing to open, or the user closed what was open, and until something is
// opened there is no transcript, no record and no files to draw. Every
// project-scoped request answers 409 with one sentence in that state; the
// shell mounts this in place of the pages rather than letting each one
// print that sentence in its own words.
//
// The actions here are the switcher's, and deliberately so: `/api/open`,
// `/api/projects`, `/api/projects/add` and `/api/projects/forget` are the
// same four requests, refused the same way, and the list both draw is the
// same `projects` from the session. Two surfaces, one contract.

import {useCallback, useState} from 'react';
import {post} from '../api.js';
import Label from '../components/Label.jsx';
import Table from '../components/Table.jsx';
import {when} from './ProjectSwitcher.jsx';

export default function Landing({status, projects, setProjects, refreshProjects}) {
  const [name, setName] = useState('');
  const [location, setLocation] = useState('');
  const [path, setPath] = useState('');
  const [error, setError] = useState('');

  const failed = useCallback((problem) => setError(String(problem?.message ?? problem)), []);

  const open = useCallback(
    (projectPath) => {
      setError('');
      post('/api/open', {path: projectPath, chat: 'main'})
        .then(() => refreshProjects())
        .catch(failed);
    },
    [refreshProjects, failed],
  );

  const create = useCallback(() => {
    const wanted = name.trim();
    if (!wanted) return;
    setError('');
    const body = {name: wanted};
    if (location.trim()) body.location = location.trim();
    post('/api/projects', body)
      .then((list) => {
        setName('');
        setLocation('');
        setProjects(list);
      })
      .catch(failed);
  }, [name, location, setProjects, failed]);

  const add = useCallback(() => {
    const wanted = path.trim();
    if (!wanted) return;
    setError('');
    post('/api/projects/add', {path: wanted})
      .then((list) => {
        setPath('');
        setProjects(list);
      })
      .catch(failed);
  }, [path, setProjects, failed]);

  const forget = useCallback(
    (projectPath) => {
      setError('');
      post('/api/projects/forget', {path: projectPath})
        .then((list) => setProjects(list))
        .catch(failed);
    },
    [setProjects, failed],
  );

  const enter = (submit) => (event) => {
    if (event.key === 'Enter') submit();
  };

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">No project is open</span>
        <span className="wb-page-subtitle">
          {projects.length
            ? `${projects.length} registered · open one, or make one`
            : 'nothing registered yet · make one, or add a folder'}
        </span>
      </div>
      {error ? <p className="panel__error">{error}</p> : null}

      <div className="wb-section">
        <Label>{`Projects · ${projects.length}`}</Label>
        {projects.length ? (
          <Table
            head={['project', 'location', 'chats', 'last opened', '']}
            rows={projects.map((project) => ({
              key: project.path,
              cells: [
                <a
                  key="open"
                  href="#"
                  onClick={(event) => {
                    event.preventDefault();
                    open(project.path);
                  }}
                >
                  {project.slug}
                </a>,
                <span key="root" className="panel__note" title={project.path}>{project.root}</span>,
                project.chats.length,
                when(project.last_opened) || <span className="panel__note">never</span>,
                <a
                  key="forget"
                  href="#"
                  className="panel__note"
                  onClick={(event) => {
                    event.preventDefault();
                    forget(project.path);
                  }}
                >
                  forget
                </a>,
              ],
            }))}
          />
        ) : (
          <div className="panel__note">
            The browser lists only projects registered in your Hardy directory; the folder the
            server was started in is not one. Create a project below, or add one that exists.
          </div>
        )}
      </div>

      <div className="wb-cols3">
        <div className="wb-card">
          <Label>New project</Label>
          <input
            className="wb-switcher__input"
            placeholder="project name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={enter(create)}
          />
          <input
            className="wb-switcher__input"
            placeholder={`location (default ${status.default_root || ''})`}
            title="the directory the project is created under; leave empty for the default"
            value={location}
            onChange={(event) => setLocation(event.target.value)}
            onKeyDown={enter(create)}
          />
          <button type="button" className="wb-switcher__button" onClick={create}>Create and open</button>
        </div>
        <div className="wb-card">
          <Label>Add existing</Label>
          <input
            className="wb-switcher__input"
            placeholder="path to a Hardy project, or a folder holding several"
            value={path}
            onChange={(event) => setPath(event.target.value)}
            onKeyDown={enter(add)}
          />
          <button type="button" className="wb-switcher__button" onClick={add}>Add</button>
          <div className="panel__note">
            A project is a folder Hardy has written a record into. A folder holding several
            registers each of them.
          </div>
        </div>
        <div className="wb-card">
          <Label>At the terminal</Label>
          <div className="panel__note">
            A project made here is opened at the terminal with
            {' '}<code>hardy chat --root &lt;location&gt; --project &lt;name&gt;</code>.
            Forgetting a project here never deletes its folder.
          </div>
        </div>
      </div>
    </div>
  );
}
