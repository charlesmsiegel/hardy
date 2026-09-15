// Files dropped on the page: staged, listed, and promoted from here.
//
// Staging is the only thing this panel does on its own. Promotion goes back
// through the two gates that already own it -- `/import` for Lean and TeX,
// `POST /api/library` for a document -- because admitting a file into the
// audited tree or into the personal library is a decision with a record
// attached, and a second path into either would be a second set of rules.
//
// `/import` is submitted as a line of input rather than called: the command
// writes what it did into the transcript, where the user is already reading,
// and a panel that reported the outcome itself would be a second account of
// one event. A path is quoted with double quotes because the handler splits
// its argument with `shlex`, so a space in a project directory's name would
// otherwise arrive as two arguments; a name that contains a double quote
// cannot be quoted that way at all and is refused here with a note rather than
// submitted as a line that would mean something else.

import {useCallback, useState} from 'react';
import {del, post, upload} from '../api.js';
import usePanel from './usePanel.js';

const KINDS = {
  lean: [
    ['Authored Lean', 'lean'],
    ['Reference Lean', 'reference'],
  ],
  tex: [['TeX', 'tex']],
};

function size(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Whether `value` can be carried inside double quotes without changing meaning. */
const quotable = (value) => !value.includes('"');

function Card({file, onSend, onReload}) {
  const [dest, setDest] = useState('');
  const [title, setTitle] = useState('');
  const [author, setAuthor] = useState('');
  const [intent, setIntent] = useState('');
  const [result, setResult] = useState(null);
  const [failure, setFailure] = useState('');
  const [working, setWorking] = useState(false);

  const safe = quotable(file.path) && quotable(file.name);

  const promote = (verb) => {
    const tail = dest.trim() ? ` "${dest.trim()}"` : '';
    onSend(`/import ${verb} "${file.path}"${tail}`);
  };

  const toLibrary = () => {
    setWorking(true);
    setFailure('');
    post('/api/library', {name: file.name, title: title.trim(), author: author.trim(), intent: intent.trim()})
      .then((answer) => setResult(answer))
      .catch((error) => setFailure(String(error?.message ?? error)))
      .finally(() => setWorking(false));
  };

  const discard = () => {
    setFailure('');
    del(`/api/uploads/${encodeURIComponent(file.name)}`)
      .then(onReload)
      .catch((error) => setFailure(String(error?.message ?? error)));
  };

  const isDocument = file.kind === 'source' || file.kind === 'other';

  return (
    <li className="card">
      <div className="card__name">{file.name}</div>
      <div className="card__meta">
        {file.kind} · {size(file.size)}
      </div>

      {!safe ? (
        <p className="panel__note">
          This name contains a double quote, which cannot be carried through a quoted path. Rename the file and upload
          it again before promoting it.
        </p>
      ) : (
        <>
          {KINDS[file.kind] ? (
            <>
              <input
                className="card__input"
                placeholder="destination inside the project (optional)"
                aria-label={`Destination for ${file.name}`}
                value={dest}
                onChange={(event) => setDest(event.target.value)}
              />
              <div className="card__actions">
                {KINDS[file.kind].map(([label, verb]) => (
                  <button key={verb} type="button" className="button" onClick={() => promote(verb)}>
                    {label}
                  </button>
                ))}
              </div>
            </>
          ) : null}

          {isDocument ? (
            <>
              <input
                className="card__input"
                placeholder="title (optional)"
                aria-label={`Title for ${file.name}`}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
              <input
                className="card__input"
                placeholder="author (optional)"
                aria-label={`Author for ${file.name}`}
                value={author}
                onChange={(event) => setAuthor(event.target.value)}
              />
              <input
                className="card__input"
                placeholder="intent (optional)"
                aria-label={`Intent for ${file.name}`}
                value={intent}
                onChange={(event) => setIntent(event.target.value)}
              />
              <div className="card__actions">
                <button type="button" className="button" disabled={working} onClick={toLibrary}>
                  {working ? 'Importing...' : 'Import to library and seed'}
                </button>
              </div>
            </>
          ) : null}
        </>
      )}

      <div className="card__actions">
        <button type="button" className="button" onClick={discard}>
          Discard
        </button>
      </div>

      {/* The five fields the import answered with, as it answered them: the
          artifact digest is what everything downstream names the document by,
          and `reused` says whether the library already held it. */}
      {result ? (
        <dl className="card__result">
          <dt>artifact</dt>
          <dd>{result.artifact}</dd>
          <dt>format</dt>
          <dd>{result.format}</dd>
          <dt>reused</dt>
          <dd>{String(result.reused)}</dd>
          <dt>extraction</dt>
          <dd>{result.extraction === null ? 'not extracted' : String(result.extraction)}</dd>
          <dt>seed</dt>
          <dd>{result.seed}</dd>
        </dl>
      ) : null}
      {failure ? <p className="panel__error">{failure}</p> : null}
    </li>
  );
}

export default function Uploads({revision, onSend}) {
  const {data, error, reload} = usePanel('/api/uploads', revision);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState('');
  const [failure, setFailure] = useState('');

  const take = useCallback(
    async (files) => {
      setFailure('');
      // One at a time: `stage` suffixes a collision by looking at what is
      // already on disk, so two uploads of `A.lean` in flight together could
      // both find the name free.
      for (const file of files) {
        setBusy(file.name);
        try {
          await upload(file);
        } catch (problem) {
          setFailure(String(problem?.message ?? problem));
          break;
        }
      }
      setBusy('');
      reload();
    },
    [reload],
  );

  return (
    <section className="uploads">
      <h2 className="panel__heading">Uploads</h2>
      <div
        className={over ? 'drop drop--over' : 'drop'}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          take([...event.dataTransfer.files]);
        }}
      >
        <p className="panel__note">Drop files here, or</p>
        <input
          type="file"
          multiple
          aria-label="Files to stage"
          onChange={(event) => {
            take([...event.target.files]);
            event.target.value = '';
          }}
        />
        {busy ? <p className="panel__note">staging {busy}...</p> : null}
      </div>
      {failure ? <p className="panel__error">{failure}</p> : null}
      {error ? <p className="panel__error">{error}</p> : null}
      {data && !data.length ? <p className="panel__note">Nothing staged.</p> : null}
      {data ? (
        <ul className="cards">
          {data.map((file) => (
            <Card key={file.name} file={file} onSend={onSend} onReload={reload} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}
