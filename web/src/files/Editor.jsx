// The editor: a buffer, `Save & check`, and the verdict that does or does not
// describe what is on screen.
//
// No editor library. The bundle is `react`, `react-dom`, `katex`, `marked`
// and `dagre`, and CodeMirror or Monaco would be a bigger dependency than the
// rest of the client put together for a textarea over a highlighter. So this
// is a plain `<textarea>`: the read-only highlighting `components/Lean.jsx`
// and `components/Tex.jsx` do is shown when the buffer is clean and the
// textarea is not focused, and the raw text while it is being edited.
// Overlaying a transparent textarea on a highlighted `<pre>` is the usual
// trick and it drifts out of alignment on any line the highlighter wraps
// differently, which is worse than no highlighting while typing.
//
// The verdict is the part that matters. A file row's verdict
// (`panels/workspace.py`'s `files`, via `panels/record.py`'s `file_verdicts`)
// describes *what is on disk and audited*. The moment the buffer differs from
// what was loaded, that verdict no longer describes what the reader is
// looking at -- so it is not shown. Not the old verdict, which would be a
// claim about text nobody checked, and not `unreported`, which would say the
// backend was asked and gave no figure. The backend was not asked about this
// text at all: `na` -- "does not apply to this thing" -- with the reason
// spelled out beside it.
//
// Every refusal is printed verbatim. `save_authored` can refuse for a dozen
// reasons -- a hole in completed work, a shadow build that broke an importer,
// an unapproved axiom, the documentation gate -- and each is a sentence
// written for a reader. The `<pre>` below prints `output` untouched.

import {useEffect, useRef, useState} from 'react';

//: What `server.py`'s `MAX_EDIT_BODY` allows for `/api/file` and `/api/check`.
//: Stated here so the editor can refuse with a sentence rather than provoke a
//: 413; the server remains the authority, and this must not exceed it.
const EDIT_BODY_LIMIT = 4 * 1024 * 1024;

import {ApiError, post, put} from '../api.js';
import Absent from '../components/Absent.jsx';
import Label from '../components/Label.jsx';
import Lean from '../components/Lean.jsx';
import Pill from '../components/Pill.jsx';
import Tex from '../components/Tex.jsx';

/** The 1-based line the caret sits on, for the diagnostics list and the rail. */
export function lineAt(text, caret) {
  return text.slice(0, caret).split('\n').length;
}

/**
 * The Lean identifier under the caret, or ''.
 *
 * Lean names are dotted: `Sylow.card_modEq_one` is one name, not two, so the
 * scan crosses a `.` rather than stopping at it. It does stop at a `.` that
 * ends the run -- `foo.` is `foo` -- because a trailing dot is being typed,
 * not part of a name the index holds.
 */
export function nameAt(text, caret) {
  const part = /[A-Za-z0-9_'!?.À-￿]/;
  let start = caret;
  let end = caret;
  while (start > 0 && part.test(text[start - 1])) start -= 1;
  while (end < text.length && part.test(text[end])) end += 1;
  return text.slice(start, end).replace(/^\.+|\.+$/g, '');
}

function Verdict({verdict}) {
  if (!verdict) {
    return (
      <span className="wb-editor__verdict">
        <Absent kind="na" /> <span className="panel__note">this file declares nothing to grade</span>
      </span>
    );
  }
  return (
    <span className="wb-editor__verdict">
      <Pill tone={verdict.tone}>{verdict.kind}</Pill>
      <span className="panel__note">{verdict.detail}</span>
    </span>
  );
}

export default function Editor({path, kind, text, truncated, verdict, onSaved, onName}) {
  const [buffer, setBuffer] = useState(text);
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState(null);
  const [caret, setCaret] = useState(0);
  const area = useRef(null);

  // A fresh file, or the same file re-read after a save, replaces the buffer.
  // Keyed on `path` *and* `text` so a save's own reload lands, and so that
  // opening a second file does not show the first one's contents.
  useEffect(() => {
    setBuffer(text);
  }, [path, text]);

  // The result is cleared when a DIFFERENT file is opened, and not when this
  // one's text changes. A save reloads the file it just wrote, so clearing on
  // `text` would wipe the save's own answer a few milliseconds after it
  // arrived -- and that answer is the whole point of the button. A refusal
  // that flashes and vanishes is worse than no refusal, because the reader
  // sees the file unchanged and no reason given.
  useEffect(() => {
    setResult(null);
  }, [path]);

  const dirty = buffer !== text;
  const editable = kind === 'lean' || kind === 'tex';

  // The name under the caret, reported upward so the rail can look it up.
  // Reported from here rather than read from the DOM by the rail: the buffer
  // and the caret are both this component's state, and a second reader of
  // the same textarea would be a second chance for the two to disagree.
  useEffect(() => {
    if (onName) onName(nameAt(buffer, caret));
  }, [buffer, caret, onName]);

  const run = async (what) => {
    // Measured on the ENCODED body, not on the buffer. `/api/file` serves a
    // file up to 1 MiB and the viewer presents it as complete and editable;
    // the same text as JSON is larger, because the envelope and the escaping
    // of every newline, quote and backslash come with it. The server allows
    // `MAX_EDIT_BODY` (4 MiB) for these two routes; refusing here as well
    // means a reader gets a sentence naming the figure instead of a bare 413
    // from a request that never had a chance.
    const encoded = new TextEncoder().encode(JSON.stringify({path, source: buffer})).length;
    if (encoded > EDIT_BODY_LIMIT) {
      setResult({
        ok: false,
        what,
        transport: true,
        output:
          `This buffer encodes to ${encoded.toLocaleString()} bytes, past the `
          + `${EDIT_BODY_LIMIT.toLocaleString()} the editor may send. `
          + 'Split the file, or edit it on disk.',
      });
      return;
    }
    setBusy(what);
    setResult(null);
    try {
      const answer = what === 'save'
        ? await put('/api/file', {path, source: buffer})
        : await post('/api/check', {path, source: buffer});
      setResult({...answer, what});
      if (what === 'save' && answer.ok && onSaved) onSaved();
    } catch (failure) {
      // The server's own sentence, whatever it is: 409 while a turn runs,
      // 400 for a path it will not take, 503 while it shuts down. A generic
      // "could not save" here would throw away the only useful half.
      setResult({
        ok: false,
        what,
        output: failure instanceof ApiError ? failure.message : String(failure),
        transport: true,
      });
    } finally {
      setBusy('');
    }
  };

  if (!editable) return null;

  const caretLine = lineAt(buffer, caret);

  return (
    <div className="wb-editor">
      <div className="wb-editor__bar">
        <span className="wb-editor__path">{path}</span>
        {dirty ? (
          <span className="wb-editor__verdict">
            <Absent kind="na" />{' '}
            <span className="panel__note">
              not this text {'·'} the verdict below describes what is saved, not what is in the editor
            </span>
          </span>
        ) : (
          <Verdict verdict={verdict} />
        )}
        <span className="wb-editor__spacer" />
        <span className="panel__note">l.{caretLine}</span>
        <button
          type="button"
          className="wb-editor__button"
          disabled={!!busy}
          onClick={() => run('check')}
          title="Runs Lean or LaTeX over this text and throws the result away. Nothing is written."
        >
          {busy === 'check' ? 'Checking...' : 'Check'}
        </button>
        <button
          type="button"
          className="wb-editor__button wb-editor__button--go"
          disabled={!!busy || !dirty}
          onClick={() => run('save')}
          title="Checks and saves through the session, exactly as the model's own save does."
        >
          {busy === 'save' ? 'Saving...' : 'Save & check'}
        </button>
      </div>

      {truncated ? (
        <div className="panel__note">
          Truncated: only the first megabyte was read, so saving this buffer would replace the file with the part
          shown. Editing is disabled until the file is opened on disk.
        </div>
      ) : (
        <textarea
          ref={area}
          className="wb-editor__area"
          spellCheck={false}
          value={buffer}
          onChange={(event) => {
            setBuffer(event.target.value);
            setCaret(event.target.selectionStart);
          }}
          onKeyUp={(event) => setCaret(event.target.selectionStart)}
          onClick={(event) => setCaret(event.target.selectionStart)}
        />
      )}

      {!dirty && !busy ? (
        <details className="wb-editor__highlight">
          <summary>highlighted</summary>
          {kind === 'lean' ? <Lean src={buffer} /> : <Tex src={buffer} />}
        </details>
      ) : null}

      {busy ? (
        <div className="panel__note">
          {busy === 'save' ? 'Saving' : 'Checking'} {path} through the session. Lean elaborates the file and rebuilds
          everything that imports it, which can take a while; nothing else in the session runs until it returns.
        </div>
      ) : null}

      {result ? (
        <div className="wb-editor__result">
          <Label>
            {result.what === 'save'
              ? result.ok ? 'saved' : result.transport ? 'not attempted' : 'refused'
              : result.ok ? 'checked' : 'check failed'}
          </Label>
          <pre className="wb-editor__output">{result.output}</pre>
        </div>
      ) : null}
    </div>
  );
}

export {Verdict};
