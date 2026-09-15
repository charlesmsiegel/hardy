// Every branch of the conversation, and the sources the project is reading.
//
// The history is a tree and the terminal's `/fork` and `/abandon` are the only
// two things that move it, so this panel submits those rather than editing
// anything: a branch taken from here and a branch taken from the prompt leave
// the same record. `/abandon` asks for a lesson first because the command
// requires one -- a branch left behind with no reason for leaving it is a dead
// end the next session will walk into again.

import {useState} from 'react';
import usePanel from './usePanel.js';

/** Entries as `{entry, depth}`, depth-first from the roots, parents before children. */
function laid(entries) {
  const known = new Set(entries.map((entry) => entry.entry_id));
  const children = new Map();
  for (const entry of entries) {
    const parent = entry.parent_id && known.has(entry.parent_id) ? entry.parent_id : null;
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(entry);
  }
  const rows = [];
  // `seen` guards against a parent chain that loops, which a hand-edited
  // record can carry: a walk without it would not return.
  const seen = new Set();
  const walk = (parent, depth) => {
    for (const entry of children.get(parent) ?? []) {
      if (seen.has(entry.entry_id)) continue;
      seen.add(entry.entry_id);
      rows.push({entry, depth});
      walk(entry.entry_id, depth + 1);
    }
  };
  walk(null, 0);
  return rows;
}

function Row({entry, depth, active, onSend}) {
  const [asking, setAsking] = useState(false);
  const [lesson, setLesson] = useState('');

  const abandon = () => {
    const said = lesson.trim();
    if (!said) return;
    onSend(`/abandon ${entry.entry_id} ${said}`);
    setAsking(false);
    setLesson('');
  };

  return (
    <li className="branch" style={{paddingLeft: `${depth * 12}px`}}>
      <div className="branch__head">
        <span className="branch__type">{entry.type || 'entry'}</span>
        <span className="branch__id">{entry.entry_id}</span>
        {active ? <span className="chip chip--ok">active leaf</span> : null}
      </div>
      {entry.lesson ? <div className="branch__lesson">{entry.lesson}</div> : null}
      <div className="branch__actions">
        <button type="button" className="button" onClick={() => onSend(`/fork ${entry.entry_id}`)}>
          Fork here
        </button>
        <button type="button" className="button" onClick={() => setAsking((open) => !open)}>
          Abandon here
        </button>
      </div>
      {asking ? (
        <div className="branch__ask">
          <input
            className="card__input"
            autoFocus
            placeholder="what this branch taught you"
            aria-label={`Lesson for abandoning ${entry.entry_id}`}
            value={lesson}
            onChange={(event) => setLesson(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') abandon();
              if (event.key === 'Escape') setAsking(false);
            }}
          />
          <button type="button" className="button" disabled={!lesson.trim()} onClick={abandon}>
            Abandon
          </button>
        </div>
      ) : null}
    </li>
  );
}

/** One bibliography entry, drawn from whatever fields it happens to carry. */
function Reference({entry}) {
  return (
    <li className="branch">
      <dl className="kv">
        {Object.entries(entry).map(([key, shown]) => (
          <div className="kv__row" key={key}>
            <dt>{key}</dt>
            <dd>{shown === null || shown === undefined ? '' : typeof shown === 'object' ? JSON.stringify(shown) : String(shown)}</dd>
          </div>
        ))}
      </dl>
    </li>
  );
}

export default function Tree({revision, onSend}) {
  const history = usePanel('/api/tree', revision);
  const sources = usePanel('/api/sources', revision);

  return (
    <div className="panel__body">
      <h2 className="panel__heading">Branches</h2>
      {history.error ? <p className="panel__error">{history.error}</p> : null}
      {!history.data && !history.error ? <p className="panel__note">Reading the history...</p> : null}
      {history.data ? (
        <ul className="branches">
          <li className="branch">
            <div className="branch__head">
              <span className="branch__type">root</span>
            </div>
            <div className="branch__actions">
              <button type="button" className="button" onClick={() => onSend('/fork root')}>
                Fork here
              </button>
            </div>
          </li>
          {laid(history.data.entries).map(({entry, depth}) => (
            <Row
              key={entry.entry_id}
              entry={entry}
              depth={depth + 1}
              active={entry.entry_id === history.data.active_leaf}
              onSend={onSend}
            />
          ))}
        </ul>
      ) : null}
      {history.data && !history.data.entries.length ? <p className="panel__note">Nothing said yet.</p> : null}

      <h2 className="panel__heading">Sources</h2>
      {sources.error ? <p className="panel__error">{sources.error}</p> : null}
      {sources.data ? (
        <>
          <h3 className="panel__subheading">Bibliography</h3>
          {sources.data.bibliography.length ? (
            <ul className="branches">
              {sources.data.bibliography.map((entry, index) => (
                <Reference key={entry.id ?? entry.key ?? index} entry={entry} />
              ))}
            </ul>
          ) : (
            <p className="panel__note">nothing cited yet</p>
          )}

          <h3 className="panel__subheading">Seeds</h3>
          {sources.data.seeds.length ? (
            <ul className="branches">
              {sources.data.seeds.map((seed) => (
                <li className="branch" key={seed.id}>
                  <div className="branch__head">
                    <span className="branch__id">{seed.id}</span>
                    <span className="chip">priority {seed.priority}</span>
                  </div>
                  <div className="branch__lesson">{seed.artifact}</div>
                  {seed.intent ? <div className="panel__note">{seed.intent}</div> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="panel__note">nothing seeded yet</p>
          )}
        </>
      ) : null}
    </div>
  );
}
