// The project's own files: the two trees served as text, and the PDFs.
//
// A PDF goes into an `<object>` rather than an `<iframe>` or a download link.
// The CSP allows `object-src 'self'`, the browser's own viewer is better than
// anything this page could draw, and the bytes never leave the machine.
// A text file goes into a `<pre>`: `/api/file` truncates past a megabyte and
// says so, and the notice is printed rather than dropped, because a reader
// scrolling to the bottom of a file that silently stopped would believe they
// had reached the end of it.

import {useEffect, useState} from 'react';
import {get} from '../api.js';
import Cells from './Cells.jsx';
import Uploads from './Uploads.jsx';
import usePanel from './usePanel.js';

//: The journal is a file like any other in `cas/`, and the one that is drawn
//: as cells rather than as text when it is picked.
const JOURNAL = 'cas/cells.jsonl';

/** `lean/Sylow/Basic.lean` shown as `Sylow/Basic.lean`, indented by depth. */
function shown(path, tree) {
  const tail = path.startsWith(`${tree}/`) ? path.slice(tree.length + 1) : path;
  return {tail, depth: tail.split('/').length - 1};
}

function Tree({title, tree, paths, picked, onPick}) {
  return (
    <section>
      <h3 className="panel__subheading">{title}</h3>
      {paths.length ? (
        <ul className="files">
          {paths.map((path) => {
            const {tail, depth} = shown(path, tree);
            return (
              <li key={path} style={{paddingLeft: `${depth * 12}px`}}>
                <button
                  type="button"
                  className={picked === path ? 'files__item files__item--on' : 'files__item'}
                  onClick={() => onPick(path)}
                >
                  {tail}
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="panel__note">nothing in {tree}/ yet</p>
      )}
    </section>
  );
}

export default function Files({revision, onSend}) {
  const {data, error} = usePanel('/api/files', revision);
  //: `{path, kind}` for whatever is open in the viewer below, or null.
  const [pick, setPick] = useState(null);
  const [text, setText] = useState(null);
  const [failure, setFailure] = useState('');

  useEffect(() => {
    if (!pick || pick.kind !== 'text') {
      setText(null);
      setFailure('');
      return undefined;
    }
    let live = true;
    setText(null);
    setFailure('');
    // Keyed on `revision` as well as on the pick: a file the session has
    // rewritten since it was opened is a panel showing yesterday's proof.
    get(`/api/file?path=${encodeURIComponent(pick.path)}`)
      .then((value) => live && setText(value))
      .catch((problem) => live && setFailure(String(problem?.message ?? problem)));
    return () => {
      live = false;
    };
  }, [pick, revision]);

  return (
    <div className="panel__body">
      <Uploads revision={revision} onSend={onSend} />

      <h2 className="panel__heading">Files</h2>
      {error ? <p className="panel__error">{error}</p> : null}
      {!data && !error ? <p className="panel__note">Reading the project...</p> : null}

      {data ? (
        <>
          <Tree
            title="Lean"
            tree="lean"
            paths={data.lean}
            picked={pick && pick.kind === 'text' ? pick.path : ''}
            onPick={(path) => setPick({path, kind: 'text'})}
          />
          <Tree
            title="TeX"
            tree="tex"
            paths={data.tex}
            picked={pick && pick.kind === 'text' ? pick.path : ''}
            onPick={(path) => setPick({path, kind: 'text'})}
          />
          <Tree
            title="Computer algebra"
            tree="cas"
            paths={data.cas ?? []}
            picked={pick && pick.kind !== 'pdf' ? pick.path : ''}
            onPick={(path) => setPick({path, kind: path === JOURNAL ? 'cells' : 'text'})}
          />
          <section>
            <h3 className="panel__subheading">PDF</h3>
            {data.pdf.length ? (
              <ul className="files">
                {data.pdf.map((path) => (
                  <li key={path}>
                    <button
                      type="button"
                      className={pick && pick.path === path ? 'files__item files__item--on' : 'files__item'}
                      onClick={() => setPick({path, kind: 'pdf'})}
                    >
                      {path}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="panel__note">nothing compiled yet</p>
            )}
          </section>
        </>
      ) : null}

      {pick ? (
        <section className="viewer">
          <div className="viewer__path">{pick.path}</div>
          {failure ? <p className="panel__error">{failure}</p> : null}
          {pick.kind === 'pdf' ? (
            <object className="viewer__pdf" data={`/api/pdf?path=${encodeURIComponent(pick.path)}`} type="application/pdf">
              <p className="panel__note">This browser will not display the PDF inline.</p>
            </object>
          ) : null}
          {pick.kind === 'text' && text ? (
            <>
              {text.truncated ? (
                <p className="panel__note">
                  Truncated: only the first megabyte is shown. Open the file on disk to read the rest.
                </p>
              ) : null}
              <pre className="viewer__text">{text.text}</pre>
            </>
          ) : null}
          {pick.kind === 'text' && !text && !failure ? <p className="panel__note">Reading...</p> : null}
          {pick.kind === 'cells' ? <Cells revision={revision} /> : null}
        </section>
      ) : null}
    </div>
  );
}
