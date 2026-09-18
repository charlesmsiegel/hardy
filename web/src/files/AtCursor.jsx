// The rail beside the editor: what the name under the caret is, what the
// record says about the declaration this file makes, and a scratch `#check`.
//
// Three panels, three different sources, and the whole point is that they are
// three. `at cursor` is the Mathlib index (`/api/declaration`). `§ record
// says` is the project ledger (`/api/results`' `record` lane). `#check` is a
// Lean run that saves nothing (`/api/check`). None of them is derived from
// another: a declaration's informal statement is what the ledger recorded,
// never the Lean signature reworded, because the reason the two are drawn
// side by side is so a reader can notice when they disagree.
//
// The index is cold on a fresh server. `/api/declarations` starts the scan in
// the background and answers `indexed: false` until it finishes, because a
// cold read walks every `.lean` file Mathlib ships. The panel says the index
// has not been read yet -- which is true -- rather than drawing a spinner
// that promises an answer is coming.

import {useEffect, useState} from 'react';

import {get, post} from '../api.js';
import Absent from '../components/Absent.jsx';
import Label from '../components/Label.jsx';

function Row({label, children}) {
  return (
    <>
      <span className="wb-rail__key">{label}</span>
      <span className="wb-rail__value">{children}</span>
    </>
  );
}

/** `at cursor`: the Mathlib index's answer for the name under the caret. */
export function AtCursor({name, onTrace}) {
  const [answer, setAnswer] = useState(null);
  const [failed, setFailed] = useState('');

  useEffect(() => {
    if (!name) {
      setAnswer(null);
      return undefined;
    }
    let live = true;
    let timer = null;
    setFailed('');

    // The first lookup starts the background scan and comes back
    // `indexed: false`. Nothing announces its completion -- no `changed`
    // event, no dependency of this effect moves -- so without this the panel
    // sat on "will answer once it finishes" forever unless the caret moved to
    // another name and back. Asking again is the only signal available.
    //
    // Every two seconds, and only while the answer is still uncounted: once
    // the index reports itself read, this stops. A cold Mathlib scan is
    // seconds to minutes, so a handful of cheap requests is the cost.
    const ask = () => {
      get(`/api/declaration?name=${encodeURIComponent(name)}`)
        .then((data) => {
          if (!live) return;
          setAnswer(data);
          if (data.indexed === false) timer = setTimeout(ask, 2000);
        })
        .catch((error) => live && setFailed(error.message));
    };
    ask();

    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [name]);

  return (
    <div className="wb-rail__panel">
      <Label>at cursor {'·'} click a name in the editor</Label>
      {!name ? (
        <div className="panel__note">Put the caret on a Lean name to look it up.</div>
      ) : failed ? (
        <p className="panel__error">{failed}</p>
      ) : !answer ? (
        <div className="panel__note">Looking up {name}...</div>
      ) : answer.indexed === false ? (
        <div className="panel__note">
          <Absent kind="unreported" /> {'·'} {answer.reason}. The scan walks every source file the installed
          packages ship; it has been started and this panel will answer once it finishes.
        </div>
      ) : !answer.found ? (
        <div className="panel__note">
          <span className="wb-rail__name">{name}</span> is not a declaration the installed packages ship. The index
          holds exact names only, so a prefix of a real name is not a match.
        </div>
      ) : (
        <>
          <div className="wb-rail__name">{answer.name}</div>
          <pre className="wb-rail__sig">{answer.signature}</pre>
          <div className="wb-rail__grid">
            <Row label="module">{answer.module}</Row>
            <Row label="line">{answer.line}</Row>
            <Row label="source">
              {answer.source_path ? (
                <button type="button" className="wb-rail__link" onClick={() => onTrace(answer)}>
                  {answer.source_path.split('/').slice(-3).join('/')}:{answer.line}
                </button>
              ) : (
                <>
                  <Absent kind="unreported" />{' '}
                  <span className="panel__note">the index holds the name but the file was not found on disk</span>
                </>
              )}
            </Row>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * `§ this declaration ↔ informal`: what the ledger recorded, and nothing else.
 *
 * `results` is a row per declaration the Lean tree makes, and its `record`
 * lane is the matching `ProjectItem` or null. Null means the ledger has no
 * entry for this theorem -- which is a fact worth showing, not a gap to fill
 * from the kernel's lane or from the Lean source.
 */
export function RecordLane({declares, results, texNames}) {
  if (!declares || declares.length === 0) {
    return (
      <div className="wb-rail__panel">
        <Label>§ this declaration ↔ informal</Label>
        <div className="panel__note">
          <Absent kind="na" /> {'·'} this file declares no theorem or lemma, so there is nothing for the ledger to
          have an entry about.
        </div>
      </div>
    );
  }
  const rows = declares.map((name) => ({
    name,
    row: (results || []).find((theorem) => theorem.name === name) || null,
    tex: (texNames || []).find((entry) => entry.formal_name === name) || null,
  }));
  return (
    <div className="wb-rail__panel">
      <Label>§ this declaration ↔ informal</Label>
      {rows.map(({name, row, tex}) => (
        <div key={name} className="wb-rail__decl">
          <div className="wb-rail__name">{name}</div>
          <div className="wb-rail__grid">
            <Row label="§ says">
              {row && row.record ? (
                row.record.statement || <Absent kind="unreported" />
              ) : (
                <>
                  <Absent kind="na" />{' '}
                  <span className="panel__note">no ledger entry names this theorem</span>
                </>
              )}
            </Row>
            <Row label="§ ref">
              {row && row.record ? row.record.id : <Absent kind="na" />}
            </Row>
            <Row label="in tex/">
              {tex ? (
                tex.latex_name
              ) : (
                <>
                  <Absent kind="na" />{' '}
                  <span className="panel__note">no recorded correspondence</span>
                </>
              )}
            </Row>
          </div>
        </div>
      ))}
    </div>
  );
}

/** `#check`: one line of Lean, run and thrown away. */
export function Scratch({path, imports}) {
  const [line, setLine] = useState('');
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState(null);

  const run = async () => {
    if (!line.trim()) return;
    setBusy(true);
    setOut(null);
    try {
      // The caption says "runs against the current module import set", so
      // what is sent has to be that: the file's own import block, then the
      // typed line. Sending the line alone would run it under no imports and
      // report `unknown identifier` for most of Mathlib.
      const answer = await post('/api/check', {path, source: `${imports}\n${line}\n`});
      setOut(answer.output);
    } catch (error) {
      setOut(error.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="wb-rail__panel">
      <Label>#check {'·'} scratch, not saved</Label>
      <div className="wb-rail__scratch">
        <input
          className="wb-rail__input"
          placeholder="#check Sylow.card_modEq_one"
          value={line}
          spellCheck={false}
          onChange={(event) => setLine(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') run();
          }}
        />
        <button type="button" className="wb-editor__button" disabled={busy} onClick={run}>
          {busy ? 'Running...' : 'Run'}
        </button>
      </div>
      <div className="panel__note">runs against this file's import set {'·'} writes nothing</div>
      {out === null ? null : <pre className="wb-editor__output">{out}</pre>}
    </div>
  );
}
