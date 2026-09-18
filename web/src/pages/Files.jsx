// Files (read-only): the four trees from `/api/files`, a viewer for what is
// picked, and the staged-uploads surface -- everything this shipment's brief
// asks for except the editor. `Save & check`, diagnostics, goals at cursor,
// declaration lookup and the Mathlib trace are shipment 3's; this page reads
// `/api/file`, `/api/pdf` and `/api/cas/cells`, and nothing writes to
// `lean/`, `tex/` or `cas/` through it. No toolbar is drawn for any of
// that -- the brief's own rule ("the toolbar is not drawn yet rather than
// drawn disabled") -- so the viewer below is a highlighted `<pre>`/notebook/
// `<object>`, never a `<textarea>`.
//
// The tree *header* lines carry real data where it exists:
// `/api/environment`'s `lean`/`mathlib`/`latex`/`tectonic`/`cas` probes
// (`wordForCheck`/`toneForCheck`, the same pair `Environment.jsx` and
// `Home.jsx` already use) are a fact about this machine's toolchain, not a
// fabricated per-tree build status, so they are shown -- `tex/`'s header
// prefers `latex` but falls back to `tectonic` (`treeFacts`, below) so a
// project on the alternate backend still gets a real fact instead of
// silence; a `last build` time or an overall
// pass/fail for the tree is not sourced by anything and is not shown at all
// (not even as `Absent` -- there is no single cell for it the way there is
// for a row's size/mtime/verdict).
//
// The Uploads section is where Task 11 left two computed values with
// nowhere to go: `workbench/DropOverlay.jsx` computes a SHA-256 and, for a
// `.lean` file, scans it for `theorem|lemma|def|axiom` names and `sorry`,
// then (as of this task) hands both to `workbench/uploadScans.js`, an
// in-memory record keyed by the name `/api/upload` staged the file as. A
// card for a file dropped this browser session shows the real digest and
// scan; one for a file already staged before this session started (a reload,
// say) shows `Absent kind="unreported"` for both -- the honest state, since
// nothing server-side computed either and there is no endpoint to read a
// staged file's bytes back (`/api/file` serves only `lean/`, `tex/`, `cas/`;
// `.local/uploads/` is not one of `SERVED_TREES`).
//
// Promotion is real commands and a real endpoint, checked against the
// registry the way this task's brief requires. `/import lean|reference|tex`
// is genuine (`src/hardy/app/tui/handlers.py:559-621`) and the command text
// shown beside each button is exactly what it would send. Issue #165 was
// that `session.py`'s `_read_import` refused *every* source path inside the
// problem's own tree, unconditionally -- and every file staged here (by
// `uploads.stage`) lives under this problem's own `.local/uploads/`, so the
// three buttons could never once succeed. Fixed on the session side:
// `uploads.stage` now leaves a sidecar recording the digest of what it
// staged (`workflows/layout.py`'s `record_staged_arrival`), and
// `_read_import` admits a file inside the tree only when that sidecar's
// digest matches the bytes on disk -- a provenance record, not the
// directory alone, so a copy of the project's own work dropped into
// `uploads/` by hand still refuses. That makes the outcome of a click
// genuinely unknown until it runs (the file could have been edited on disk
// since it staged, say), which is `Tree.jsx`'s own rule for Fork: a
// session-changing action is reviewed before it runs, not auto-submitted,
// so each button here fills the composer's draft with the real command
// (`useSession().setDraft`, then navigates to Chat, exactly as Fork does)
// rather than sending it itself. "Add to library..." (`POST /api/library`)
// was never affected -- it reads the staged file directly
// (`uploads.library_import`), never through `_read_import`.

import {useEffect, useState} from 'react';
import Absent from '../components/Absent.jsx';
import Empty from '../components/Empty.jsx';
import Label from '../components/Label.jsx';
import Lean from '../components/Lean.jsx';
import Pill, {toneForCheck, wordForCheck} from '../components/Pill.jsx';
import Tex from '../components/Tex.jsx';
import {del, get, post} from '../api.js';
import {AtCursor, RecordLane, Scratch} from '../files/AtCursor.jsx';
import Editor from '../files/Editor.jsx';
import Trace from '../files/Trace.jsx';
import useHash from '../session/useHash.js';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';
import {scanFor} from '../workbench/uploadScans.js';

const TREES = [
  {key: 'lean', label: 'lean/', checks: ['lean', 'mathlib'], preferOne: false},
  // TeX has two possible backends -- `doctor.py` probes both `latex`
  // (pdflatex/latexmk, the default) and `tectonic` as separate Checks, and a
  // project configured for the second has no `latex` Check to show. `latex`
  // and `tectonic` are alternatives describing the same one toolchain, not
  // two independent facts the way `lean`+`mathlib` are, so only the first
  // one actually present is shown -- omitting `tectonic` entirely when
  // `latex` is absent would silently drop a true fact this header could
  // state, for a project where it is the one that ran.
  {key: 'tex', label: 'tex/', checks: ['latex', 'tectonic'], preferOne: true},
  {key: 'cas', label: 'cas/', checks: ['cas'], preferOne: false},
];

/** The env-check facts a tree header actually shows: every named check that
 *  is present, or (`preferOne`) only the first one found, in `treeDef.checks`
 *  order. */
function treeFacts(treeDef, envChecks) {
  const found = treeDef.checks.map((name) => envChecks.find((c) => c.name === name)).filter(Boolean);
  return treeDef.preferOne ? found.slice(0, 1) : found;
}

//: What every card's Import row says under the buttons now that they run
//: real commands (issue #165's fix): the outcome is genuinely unknown until
//: `/import` runs -- this exact file, unedited since it staged, is what
//: makes it succeed -- so the buttons stay enabled and this states the rule
//: rather than a fixed verdict.
const IMPORT_NOTE =
  'Puts the real /import command in the composer for review; nothing is sent from this click. It succeeds for ' +
  'a file exactly as staged, and is refused -- same as any other import -- if the file on disk no longer ' +
  'matches what was staged, or the destination already exists.';

function bytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} kB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** A row's `modified` POSIX timestamp, formatted -- the same idiom
 *  `pages/Home.jsx`'s own `when()` uses for a chat's `created`/
 *  `last_activity`. `row.modified` is `null` only when `_row`'s `stat()`
 *  itself failed (`panels/workspace.py:_row`), which this never reaches:
 *  callers check that first and render `Absent kind="unreported"` instead. */
function when(ts) {
  return new Date(ts * 1000).toLocaleString(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

/** A row's three meta columns: size, mtime, kernel verdict. Shared by
 *  `TreeSection` and `PdfSection` so the two trees stay pixel-for-pixel
 *  consistent. `bytes`/`modified` are `Absent kind="unreported"` only when
 *  the backend's own `stat()` failed (`_row`) -- the file still exists, the
 *  figure just could not be read. `verdict` is `Absent kind="na"` for every
 *  row `panels.files` does not attach one to: every non-`lean` tree, and a
 *  `lean` file that declares nothing (`record.file_verdicts`'s own
 *  docstring -- a file with no names has nothing for a verdict to be
 *  about). `row.verdict.tone` is used directly on the pill, never through
 *  `Pill.jsx`'s `toneForVerdict`: that helper answers a different,
 *  file-level word list (`kernel_verified`/`accepted`/...) that shares no
 *  members with `declaration_status`'s per-declaration grades this value
 *  actually is (`ambiguous`/`unaudited`/`stale`/`unapproved`/`open`/
 *  `assumed`/`verified`) -- `Results.jsx` documents the same trap. */
function RowMeta({row}) {
  return (
    <>
      <span className="wb-files__row-meta">{row.bytes == null ? <Absent kind="unreported" /> : bytes(row.bytes)}</span>
      <span className="wb-files__row-meta">
        {row.modified == null ? <Absent kind="unreported" /> : when(row.modified)}
      </span>
      <span className="wb-files__row-meta">
        {row.verdict ? <Pill tone={row.verdict.tone}>{row.verdict.kind}</Pill> : <Absent kind="na" />}
      </span>
    </>
  );
}

/** `lean/Sylow/Basic.lean` -> `Sylow/Basic.lean`, indented by depth -- the
 *  same convention the old three-column client's `panels/Files.jsx` used. */
function tail(path, treeKey) {
  const prefix = `${treeKey}/`;
  const stripped = path.startsWith(prefix) ? path.slice(prefix.length) : path;
  return {stripped, depth: stripped.split('/').length - 1};
}

/** Whether `value` survives inside a double-quoted shell-ish argument: the
 *  handler splits with `shlex`, and a name that itself holds `"` cannot be
 *  quoted that way at all (the same rule and the same wording the old
 *  `panels/Uploads.jsx` used). */
const quotable = (value) => !value.includes('"');

function kindOf(path, data) {
  if (data.lean.some((row) => row.path === path)) return 'lean';
  if (data.tex.some((row) => row.path === path)) return 'tex';
  if (data.cas.some((row) => row.path === path)) return 'cas';
  if (data.pdf.some((row) => row.path === path)) return 'pdf';
  return null;
}

function TreeSection({def: treeDef, count, rows, envChecks, selected, onPick}) {
  return (
    <div className="wb-files__tree">
      <div className="wb-files__tree-head">
        {treeDef.label}
        {treeFacts(treeDef, envChecks).map((check) => (
          <span key={check.name}>
            {' · '}
            <span style={{color: `var(--${toneForCheck(check)})`}}>{wordForCheck(check)}</span> {check.detail}
          </span>
        ))}
        {' · '}
        {count} file{count === 1 ? '' : 's'}
      </div>
      {rows.length ? (
        <div className="wb-files__rows">
          {rows.map((row) => {
            const {stripped, depth} = tail(row.path, treeDef.key);
            return (
              <div
                key={row.path}
                className={row.path === selected ? 'wb-files__row wb-files__row--on' : 'wb-files__row'}
                style={{paddingLeft: depth * 12}}
                onClick={() => onPick(row.path)}
              >
                <span className="wb-files__row-name">{stripped}</span>
                <RowMeta row={row} />
              </div>
            );
          })}
        </div>
      ) : (
        <div className="panel__note">nothing in {treeDef.key}/ yet</div>
      )}
    </div>
  );
}

function PdfSection({count, rows, selected, onPick}) {
  return (
    <div className="wb-files__tree">
      <div className="wb-files__tree-head">build/ {'·'} pdf {'·'} {count} file{count === 1 ? '' : 's'}</div>
      {rows.length ? (
        <div className="wb-files__rows">
          {rows.map((row) => (
            <div
              key={row.path}
              className={row.path === selected ? 'wb-files__row wb-files__row--on' : 'wb-files__row'}
              onClick={() => onPick(row.path)}
            >
              <span className="wb-files__row-name">{row.path}</span>
              <RowMeta row={row} />
            </div>
          ))}
        </div>
      ) : (
        <div className="panel__note">nothing compiled yet</div>
      )}
    </div>
  );
}

function LeanTexEditor({path, kind, revision, row}) {
  const filePanel = usePanel(`/api/file?path=${encodeURIComponent(path)}`, revision);
  const resultsPanel = usePanel('/api/results', revision);

  const [caretName, setCaretName] = useState('');
  const [trail, setTrail] = useState([]);

  // Following a name out of the trace view is the same lookup the rail does,
  // so it goes through the same endpoint rather than a second code path. A
  // name the index does not hold leaves the trail where it was: pushing a
  // `found: false` step would make the breadcrumb a list of dead ends.
  const follow = (name) =>
    get(`/api/declaration?name=${encodeURIComponent(name)}`)
      .then((answer) => answer.found && setTrail((previous) => [...previous, answer]))
      .catch(() => undefined);

  if (filePanel.error) return <p className="panel__error">{filePanel.error}</p>;
  if (!filePanel.data) return <p className="panel__note">Reading {path}...</p>;

  const text = filePanel.data.text;
  // What `#check` runs against. The panel's caption claims "this file's
  // import set", so that has to be what is sent: the typed line alone would
  // elaborate under no imports and answer `unknown identifier` for most of
  // Mathlib.
  const imports = text.split('\n').filter((line) => /^\s*import\s/.test(line)).join('\n');
  const theorems = resultsPanel.data?.theorems || null;
  // The recorded Lean-to-LaTeX correspondences, served beside the theorem
  // table by `panels/record.py`'s `_recorded_names`. They are the only source
  // for "where is this theorem stated in tex/": a ledger item carries no
  // label, and deriving one from a file or theorem name would be the page
  // asserting a correspondence nobody recorded.
  const texNames = resultsPanel.data?.names || null;

  if (trail.length) {
    return (
      <Trace
        trail={trail}
        revision={null}
        onFollow={follow}
        onBack={(index) => setTrail((previous) => previous.slice(0, index + 1))}
        onClose={() => setTrail([])}
      />
    );
  }

  return (
    <div className="wb-files__editor-split">
      <div className="wb-files__editor-main">
        <Editor
          path={path}
          kind={kind}
          text={text}
          truncated={filePanel.data.truncated}
          verdict={row ? row.verdict : null}
          onName={setCaretName}
          onSaved={() => {
            filePanel.reload();
            resultsPanel.reload();
          }}
        />
      </div>
      <div className="wb-files__editor-rail">
        {kind === 'lean' ? <AtCursor name={caretName} onTrace={(answer) => setTrail([answer])} /> : null}
        <RecordLane declares={row ? row.declares : []} results={theorems} texNames={texNames} />
        {kind === 'lean' ? <Scratch path={path} imports={imports} /> : null}
      </div>
    </div>
  );
}

function CellText({label, shown}) {
  if (!shown || !shown.text) return null;
  return (
    <div className="wb-files__cell-block">
      <Label>{label}</Label>
      <pre className="wb-files__cell-text">{shown.text}</pre>
      {shown.truncated ? <div className="panel__note">cut; the whole text is in the journal</div> : null}
    </div>
  );
}

function Cell({cell, onRaise}) {
  return (
    <div className="wb-files__cell" style={cell.live ? undefined : {opacity: 0.6}}>
      <div className="wb-files__cell-head">
        <span className="wb-files__cell-seq">[{cell.seq}]</span>
        <span className="wb-files__cell-path">{cell.path || 'inline'}</span>
        <Pill tone={cell.accepted ? 'accent' : 'error'}>
          {cell.status}
          {cell.accepted ? '' : ' · not accepted'}
        </Pill>
        <span className="wb-files__cell-meta">
          {cell.author} {'·'} {cell.duration_ms} ms
        </span>
        {cell.path && quotable(cell.path) ? (
          <button
            type="button"
            className="wb-editor__button"
            onClick={() => onRaise(`/cas run "${cell.path}"`)}
            title="Fills the composer with the command. A cell run is a session-changing action, so it is reviewed before it runs."
          >
            Re-run
          </button>
        ) : null}
      </div>
      {cell.restart_note ? <div className="panel__note">{cell.restart_note}</div> : null}
      <CellText label="source" shown={cell.source} />
      <CellText label="stdout" shown={cell.stdout} />
      <CellText label="stderr" shown={cell.stderr} />
      <CellText label="value" shown={cell.value_repr} />
    </div>
  );
}

function CasViewer({path, revision}) {
  const {setDraft} = useSession();
  const [, goTo] = useHash();
  const [composed, setComposed] = useState('');
  // The same route the Import buttons take, and for the same reason: a cell
  // run is a session-changing action whose outcome is genuinely unknown until
  // it runs, so it is raised into the composer for review rather than sent.
  // `/cas run <path>` and `/cas <expr>` are both real
  // (`app/tui/handlers.py`'s `handle_cas` and its argument parser); neither
  // is safe in flight, and the composer is where a refusal would be shown.
  const raise = (command) => {
    setDraft(command);
    goTo({page: 'chat'});
  };
  const cellsPanel = usePanel('/api/cas/cells', revision);
  if (cellsPanel.error) return <p className="panel__error">{cellsPanel.error}</p>;
  if (!cellsPanel.data) return <p className="panel__note">Reading the journal...</p>;
  const {cells, segment, total, truncated} = cellsPanel.data;
  const opened = cells.find((cell) => cell.path === path);
  const bySegment = new Map();
  for (const cell of cells) {
    if (!bySegment.has(cell.segment)) bySegment.set(cell.segment, []);
    bySegment.get(cell.segment).push(cell);
  }
  const segments = [...bySegment.keys()].sort((a, b) => a - b);
  return (
    <>
      <div className="panel__note">
        cas/cells.jsonl {'·'} {total} cell{total === 1 ? '' : 's'}
        {truncated ? ` · only the newest ${cells.length} are shown` : ''} {'·'} opens as the notebook, not
        as free text
      </div>
      {opened ? (
        <div className="panel__note">
          {path} is the source of cell <span style={{color: 'var(--fg)'}}>[{opened.seq}]</span> {'·'} shown in
          place below
        </div>
      ) : null}
      {cells.length === 0 ? <div className="panel__note">no cells have run yet</div> : null}
      <div className="wb-rail__panel">
        <Label>new cell</Label>
        <div className="wb-rail__scratch">
          <input
            className="wb-rail__input"
            placeholder="Order(SymmetricGroup(4));"
            spellCheck={false}
            value={composed}
            onChange={(event) => setComposed(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && composed.trim()) raise(`/cas ${composed.trim()}`);
            }}
          />
          <button
            type="button"
            className="wb-editor__button"
            disabled={!composed.trim()}
            onClick={() => raise(`/cas ${composed.trim()}`)}
          >
            Compose
          </button>
        </div>
        <div className="panel__note">
          Fills the composer with <span style={{color: 'var(--fg)'}}>/cas {'‹expr›'}</span> and opens Chat.
          The kernel is the one the model is also using, so a cell is refused while a turn is running --
          the composer is where that refusal appears.
        </div>
      </div>
      {segments.map((seg) => (
        <div key={seg} className="wb-files__notebook-segment">
          <Label>{seg === segment ? `live · segment ${seg}` : `history · segment ${seg} · not live state`}</Label>
          <div className="wb-files__notebook-cells">
            {bySegment.get(seg).map((cell) => (
              <Cell key={`${cell.segment}-${cell.seq}`} cell={cell} onRaise={raise} />
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

function PdfViewer({path}) {
  return (
    <object className="wb-files__pdf" data={`/api/pdf?path=${encodeURIComponent(path)}`} type="application/pdf">
      <p className="panel__note">This browser will not display the PDF inline.</p>
    </object>
  );
}

function Viewer({path, kind, revision, row}) {
  if (!path) return <div className="panel__note">Select a file to open it.</div>;
  if (!kind) {
    return (
      <div className="panel__note">
        {path} is not (or is no longer) one of the files /api/files lists. It may have been removed since this link
        was made.
      </div>
    );
  }
  return (
    <div className="wb-files__viewer-body">
      {kind === 'lean' || kind === 'tex'
        ? <LeanTexEditor path={path} kind={kind} revision={revision} row={row} />
        : null}
      {kind === 'cas' ? <CasViewer path={path} revision={revision} /> : null}
      {kind === 'pdf' ? <PdfViewer path={path} /> : null}
    </div>
  );
}

function UploadCard({file, reload}) {
  const {setDraft} = useSession();
  const [, go] = useHash();
  const [working, setWorking] = useState(false);
  const [result, setResult] = useState(null);
  const [failure, setFailure] = useState('');
  const scanned = scanFor(file.name);
  // The quoted argument is `file.path` (the absolute path `/api/upload`
  // resolved to), not `file.name`: `uploads.safe_name` already refuses a `"`
  // in the staged name, but an ancestor directory -- this project's own
  // folder, say -- is not something Hardy validates, so the path built from
  // it is what actually needs checking (the same target the old
  // `panels/Uploads.jsx` checked).
  const named = quotable(file.path);

  const scanLine = () => {
    if (file.kind === 'lean') {
      if (!scanned) return <Absent kind="unreported" />;
      const names = scanned.scan?.names ?? [];
      const decl = names.length ? `declares ${names.join(', ')}` : 'no theorem/lemma/def/axiom found';
      const sorry = scanned.scan?.sorry ? ' · contains sorry — will be recorded partial' : '';
      return `${decl}${sorry} · not yet checked`;
    }
    if (file.kind === 'tex') return 'not scanned in this shipment (only .lean is) · not yet compiled';
    if (file.kind === 'source') return 'library candidate · edition will be asked on import';
    return 'unknown kind · no promotion path in this shipment';
  };

  const importCommand = (verb, dest) => {
    const destArg = dest ? ` "${dest}"` : '';
    return `/import ${verb} "${file.path}"${destArg}`;
  };

  // Not sent from here -- the same rule `Tree.jsx`'s Fork follows: a
  // session-changing action is reviewed before it runs. This raises the
  // real command in the composer and lets the user submit (or edit) it.
  const runImport = (verb, dest) => {
    setDraft(importCommand(verb, dest));
    go('chat');
  };

  const runDiscard = () => {
    setFailure('');
    del(`/api/uploads/${encodeURIComponent(file.name)}`)
      .then(reload)
      .catch((error) => setFailure(String(error?.message ?? error)));
  };

  const runLibrary = () => {
    setWorking(true);
    setFailure('');
    post('/api/library', {name: file.name, title: '', author: '', intent: ''})
      .then((answer) => setResult(answer))
      .catch((error) => setFailure(String(error?.message ?? error)))
      .finally(() => setWorking(false));
  };

  return (
    <div className="wb-card">
      <div className="wb-files__upload-head">
        <span className="wb-files__upload-name">{file.name}</span>
        <span className="wb-files__upload-meta">
          {file.kind} {'·'} {bytes(file.size)} {'·'} sha256{' '}
          {scanned ? `${scanned.sha256.slice(0, 4)}…${scanned.sha256.slice(-4)}` : <Absent kind="unreported" />}
        </span>
      </div>
      <div className="wb-files__upload-scan">{scanLine()}</div>
      {!scanned ? (
        <div className="panel__note">
          The digest and scan are computed in the browser when a file is dropped and kept only for this tab's life;
          this card was not staged (or the page was reloaded) since this tab last computed one.
        </div>
      ) : null}

      {!named ? (
        <div className="panel__note">
          The staged path for this file contains a double quote, which cannot be carried through a quoted argument;
          no promotion command can be built for it here. Discard is still safe.
        </div>
      ) : (
        <div className="wb-files__upload-actions">
          {file.kind === 'lean' ? (
            <>
              <div className="wb-files__import-row">
                <button
                  type="button"
                  className="button"
                  onClick={() => runImport('lean', `Imported/${file.name}`)}
                >
                  Import as authored Lean...
                </button>
                <code className="wb-files__import-cmd">{importCommand('lean', `Imported/${file.name}`)}</code>
              </div>
              <div className="wb-files__import-row">
                <button type="button" className="button" onClick={() => runImport('reference', '')}>
                  Import as reference...
                </button>
                <code className="wb-files__import-cmd">{importCommand('reference', '')}</code>
              </div>
            </>
          ) : null}
          {file.kind === 'tex' ? (
            <div className="wb-files__import-row">
              <button type="button" className="button" onClick={() => runImport('tex', `imported/${file.name}`)}>
                Import into tex/...
              </button>
              <code className="wb-files__import-cmd">{importCommand('tex', `imported/${file.name}`)}</code>
            </div>
          ) : null}
          {file.kind === 'source' ? (
            <button type="button" className="button" disabled={working} onClick={runLibrary}>
              {working ? 'Adding...' : 'Add to library...'}
            </button>
          ) : null}
          {file.kind === 'other' ? (
            <span className="panel__note">
              No promotion path exists in this shipment for this kind of file: uploads.kind_of() (
              src/hardy/app/web/uploads.py) only recognises lean/tex/source; anything else stages here but has
              nowhere the server will admit it.
            </span>
          ) : null}
          <button type="button" className="button" onClick={runDiscard}>
            Discard
          </button>
        </div>
      )}
      {named && (file.kind === 'lean' || file.kind === 'tex') ? (
        <div className="panel__note">{IMPORT_NOTE}</div>
      ) : null}

      {result ? (
        <div className="wb-files__upload-result">
          artifact {result.artifact.slice(0, 12)}... {'·'} format {result.format} {'·'} reused{' '}
          {String(result.reused)} {'·'} seed {result.seed}
        </div>
      ) : null}
      {failure ? <p className="panel__error">{failure}</p> : null}
    </div>
  );
}

export default function Files({arg}) {
  const {revision, refusal} = useSession();
  const [, go] = useHash();
  const filesPanel = usePanel('/api/files', revision);
  const envPanel = usePanel('/api/environment', revision);
  const uploadsPanel = usePanel('/api/uploads', revision);

  // Staging writes a file without touching the session, so no `changed`
  // event follows it (`session/usePanel.js`'s own reasoning for exposing
  // `reload`). `DropOverlay` stages from outside this page's tree entirely,
  // so the only signal available here is the same `drop` the overlay itself
  // handles; this listens for it too, purely to know "something may have
  // changed, ask again" -- it never reads `event.dataTransfer` itself.
  useEffect(() => {
    // `hardy:staged` is dispatched by `stageOne` once an upload has actually
    // landed, one event per file. The old version guessed with a 200ms timer
    // after `drop`, which refetched before a large file's digest, read and
    // upload had finished -- and since staging emits no `changed` event and
    // `DropOverlay` navigates to a hash that is already current, nothing
    // fetched again afterwards. The file stayed invisible until some
    // unrelated refresh.
    const onStaged = () => uploadsPanel.reload();
    window.addEventListener('hardy:staged', onStaged);
    return () => window.removeEventListener('hardy:staged', onStaged);
  }, [uploadsPanel.reload]);

  const errors = [filesPanel, envPanel, uploadsPanel].map((panel) => panel.error).filter(Boolean);
  if (errors.length) return <p className="panel__error">{errors[0]}</p>;
  if (!filesPanel.data || !envPanel.data || !uploadsPanel.data) {
    return <p className="panel__note">Reading the project's files...</p>;
  }

  const data = filesPanel.data;
  const checks = envPanel.data.checks;
  const staged = uploadsPanel.data;
  const totalTracked = data.lean.length + data.tex.length + data.cas.length + data.pdf.length;
  const empty = totalTracked === 0 && staged.length === 0;

  if (empty) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Files</span>
          <span className="wb-page-subtitle">fresh project {'·'} nothing recorded</span>
        </div>
        <Empty
          title="No Lean files"
          line="lean/, tex/ and cas/ do not exist yet. The first saved theorem creates lean/; drop a .lean file to import one now."
        />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />; a figure
          the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  const selectedPath = arg || '';
  const selectedKind = selectedPath ? kindOf(selectedPath, data) : null;
  const pick = (path) => go({page: 'files', arg: path});
  // One lookup, shared by the head line and the editor: two would be two
  // chances for the header's facts and the editor's verdict to describe
  // different rows.
  const selectedRow = selectedKind
    ? (data[selectedKind] || []).find((row) => row.path === selectedPath) || null
    : null;

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Files</span>
        <span className="wb-page-subtitle">read here, written through the session {'·'} click a file to open it</span>
      </div>
      {refusal ? <div className="wb-files__refusal">{refusal}</div> : null}

      <div className="wb-files">
        <div className="wb-files__left">
          {TREES.map((treeDef) => (
            <TreeSection
              key={treeDef.key}
              def={treeDef}
              count={data[treeDef.key].length}
              rows={data[treeDef.key]}
              envChecks={checks}
              selected={selectedPath}
              onPick={pick}
            />
          ))}
          <PdfSection count={data.pdf.length} rows={data.pdf} selected={selectedPath} onPick={pick} />

          <div className="wb-files__uploads">
            <Label>{`Uploads · staged ${staged.length}`}</Label>
            <div className="wb-files__dropzone">
              Drop files anywhere in the workbench to stage them. Nothing is admitted until promoted.
            </div>
            {staged.map((file) => (
              <UploadCard key={file.name} file={file} reload={uploadsPanel.reload} />
            ))}
            {staged.length === 0 ? <div className="panel__note">0 staged</div> : null}
          </div>
        </div>

        <div className="wb-files__right">
          <div className="wb-files__viewer-head">
            Files {'›'} <span style={{color: 'var(--fg)'}}>{selectedPath || <Absent kind="na" />}</span>
            {selectedRow ? (
              <>
                {' '}
                {selectedRow.bytes === null ? <Absent kind="unreported" /> : bytes(selectedRow.bytes)} {'·'}{' '}
                {selectedRow.modified === null ? (
                  <Absent kind="unreported" />
                ) : (
                  new Date(selectedRow.modified * 1000).toLocaleString()
                )}
              </>
            ) : null}
          </div>
          <Viewer path={selectedPath} kind={selectedKind} revision={revision} row={selectedRow} />
        </div>
      </div>
    </div>
  );
}
