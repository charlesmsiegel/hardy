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
// `/api/files` answers `{lean, tex, cas, pdf}` as paths only
// (`panels/workspace.py:files`) -- no size, no mtime, no verdict word. Every
// row therefore shows `<Absent kind="unreported" />` in those three columns;
// this is not a placeholder pending a later pass, it is what the backend
// actually said. The tree *header* lines carry real data where it exists:
// `/api/environment`'s `lean`/`mathlib`/`latex`/`cas` probes (`wordForCheck`/
// `toneForCheck`, the same pair `Environment.jsx` and `Home.jsx` already use)
// are a fact about this machine's toolchain, not a fabricated per-tree
// build status, so they are shown; a `last build` time or an overall
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
// registry the way this task's brief requires -- but one of them is
// confirmed, not merely believed, to always refuse. See
// `IMPORT_OWN_TREE_NOTE` below for the evidence; the short version is that
// `/import lean|reference|tex` is genuine
// (`src/hardy/app/tui/handlers.py:559-621`) and its syntax below is real,
// but `session.py`'s `_read_import` refuses any source path already inside
// the problem's own tree, and every file staged here lives under this
// problem's own `.local/uploads/`. The button stays enabled rather than
// disabled: this is a real command, not invented syntax, and the honest
// thing to do with a real command whose outcome is known is to say so in
// words and still let the session's own answer land in the transcript, the
// same principle `Tree.jsx`'s Fork and `Jobs.jsx`'s confirm cards already
// apply to actions that might be refused for reasons a click cannot always
// predict. "Add to library..." (`POST /api/library`) is not affected -- it
// reads the staged file directly (`uploads.library_import`), never through
// `_read_import` -- and is offered without that caveat.

import {useEffect, useState} from 'react';
import Absent from '../components/Absent.jsx';
import {ConfirmCard} from '../components/Cards.jsx';
import Empty from '../components/Empty.jsx';
import Label from '../components/Label.jsx';
import Lean from '../components/Lean.jsx';
import Pill, {toneForCheck, wordForCheck} from '../components/Pill.jsx';
import Tex from '../components/Tex.jsx';
import {del, post} from '../api.js';
import useHash from '../session/useHash.js';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';
import {scanFor} from '../workbench/uploadScans.js';

const TREES = [
  {key: 'lean', label: 'lean/', checks: ['lean', 'mathlib']},
  {key: 'tex', label: 'tex/', checks: ['latex']},
  {key: 'cas', label: 'cas/', checks: ['cas']},
];

//: The one place this page states the own-tree finding, so every card's
//: Import buttons can point at the same sentence rather than each writing
//: their own paraphrase of it. Confirmed by running `import_lean` against a
//: freshly staged upload: the refusal text quoted below is the session's
//: own, not summarised.
const IMPORT_OWN_TREE_NOTE =
  '/import lean|reference|tex is a real command (src/hardy/app/tui/handlers.py:559-621) and the line below ' +
  'is exactly what it sends. But every file staged here lives under this problem’s own .local/uploads/, ' +
  'and session.py’s _read_import refuses any source path already inside the problem’s own tree -- ' +
  'empirically confirmed: it answers "...is inside this problem’s own tree; importing is for files that ' +
  'arrived from outside." This looks like a gap between uploads.py’s own docstring, which says /import is ' +
  'what admits a staged file, and session.py’s guard -- not something this read-only page can fix. Yes ' +
  'sends the real command; the transcript shows whatever the session actually says, refusal included.';

function bytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} kB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
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
  if (data.lean.includes(path)) return 'lean';
  if (data.tex.includes(path)) return 'tex';
  if (data.cas.includes(path)) return 'cas';
  if (data.pdf.includes(path)) return 'pdf';
  return null;
}

function TreeSection({def: treeDef, count, paths, envChecks, selected, onPick}) {
  return (
    <div className="wb-files__tree">
      <div className="wb-files__tree-head">
        {treeDef.label}
        {treeDef.checks.map((name) => {
          const check = envChecks.find((c) => c.name === name);
          if (!check) return null;
          return (
            <span key={name}>
              {' · '}
              <span style={{color: `var(--${toneForCheck(check)})`}}>{wordForCheck(check)}</span> {check.detail}
            </span>
          );
        })}
        {' · '}
        {count} file{count === 1 ? '' : 's'}
      </div>
      {paths.length ? (
        <div className="wb-files__rows">
          {paths.map((path) => {
            const {stripped, depth} = tail(path, treeDef.key);
            return (
              <div
                key={path}
                className={path === selected ? 'wb-files__row wb-files__row--on' : 'wb-files__row'}
                style={{paddingLeft: depth * 12}}
                onClick={() => onPick(path)}
              >
                <span className="wb-files__row-name">{stripped}</span>
                <span className="wb-files__row-meta"><Absent kind="unreported" /></span>
                <span className="wb-files__row-meta"><Absent kind="unreported" /></span>
                <span className="wb-files__row-meta"><Absent kind="unreported" /></span>
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

function PdfSection({count, paths, selected, onPick}) {
  return (
    <div className="wb-files__tree">
      <div className="wb-files__tree-head">build/ {'·'} pdf {'·'} {count} file{count === 1 ? '' : 's'}</div>
      {paths.length ? (
        <div className="wb-files__rows">
          {paths.map((path) => (
            <div
              key={path}
              className={path === selected ? 'wb-files__row wb-files__row--on' : 'wb-files__row'}
              onClick={() => onPick(path)}
            >
              <span className="wb-files__row-name">{path}</span>
              <span className="wb-files__row-meta"><Absent kind="unreported" /></span>
              <span className="wb-files__row-meta"><Absent kind="unreported" /></span>
              <span className="wb-files__row-meta"><Absent kind="unreported" /></span>
            </div>
          ))}
        </div>
      ) : (
        <div className="panel__note">nothing compiled yet</div>
      )}
    </div>
  );
}

function LeanTexViewer({path, kind, revision}) {
  const filePanel = usePanel(`/api/file?path=${encodeURIComponent(path)}`, revision);
  if (filePanel.error) return <p className="panel__error">{filePanel.error}</p>;
  if (!filePanel.data) return <p className="panel__note">Reading {path}...</p>;
  return (
    <>
      {filePanel.data.truncated ? (
        <div className="panel__note">
          Truncated: only the first megabyte is shown. Open the file on disk to read the rest.
        </div>
      ) : null}
      {kind === 'lean' ? <Lean src={filePanel.data.text} /> : <Tex src={filePanel.data.text} />}
      <div className="panel__note">
        Read-only in this shipment: editing, Save &amp; check, diagnostics, goals at cursor, declaration lookup and
        the Mathlib trace are a later shipment's.
      </div>
    </>
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

function Cell({cell}) {
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
      {segments.map((seg) => (
        <div key={seg} className="wb-files__notebook-segment">
          <Label>{seg === segment ? `live · segment ${seg}` : `history · segment ${seg} · not live state`}</Label>
          <div className="wb-files__notebook-cells">
            {bySegment.get(seg).map((cell) => (
              <Cell key={`${cell.segment}-${cell.seq}`} cell={cell} />
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

function Viewer({path, kind, revision}) {
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
      {kind === 'lean' || kind === 'tex' ? <LeanTexViewer path={path} kind={kind} revision={revision} /> : null}
      {kind === 'cas' ? <CasViewer path={path} revision={revision} /> : null}
      {kind === 'pdf' ? <PdfViewer path={path} /> : null}
    </div>
  );
}

function UploadCard({file, send, reload}) {
  const [confirm, setConfirm] = useState(null);
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

  const runImport = (command) => {
    send(command);
    setConfirm(null);
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
              <button
                type="button"
                className="button"
                onClick={() =>
                  setConfirm({
                    command: importCommand('lean', `Imported/${file.name}`),
                    title: `Import ${file.name} as authored Lean?`,
                    note: IMPORT_OWN_TREE_NOTE,
                  })
                }
              >
                Import as authored Lean...
              </button>
              <button
                type="button"
                className="button"
                onClick={() =>
                  setConfirm({
                    command: importCommand('reference', ''),
                    title: `Import ${file.name} as reference Lean?`,
                    note: IMPORT_OWN_TREE_NOTE,
                  })
                }
              >
                Import as reference...
              </button>
            </>
          ) : null}
          {file.kind === 'tex' ? (
            <button
              type="button"
              className="button"
              onClick={() =>
                setConfirm({
                  command: importCommand('tex', `imported/${file.name}`),
                  title: `Import ${file.name} into tex/?`,
                  note: IMPORT_OWN_TREE_NOTE,
                })
              }
            >
              Import into tex/...
            </button>
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

      {result ? (
        <div className="wb-files__upload-result">
          artifact {result.artifact.slice(0, 12)}... {'·'} format {result.format} {'·'} reused{' '}
          {String(result.reused)} {'·'} seed {result.seed}
        </div>
      ) : null}
      {failure ? <p className="panel__error">{failure}</p> : null}

      {confirm ? (
        <ConfirmCard
          command={confirm.command}
          title={confirm.title}
          onYes={() => runImport(confirm.command)}
          onNo={() => setConfirm(null)}
        />
      ) : null}
      {confirm ? <div className="panel__note">{confirm.note}</div> : null}
    </div>
  );
}

export default function Files({arg}) {
  const {revision, send, refusal} = useSession();
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
    const onDrop = () => setTimeout(uploadsPanel.reload, 200);
    window.addEventListener('drop', onDrop);
    return () => window.removeEventListener('drop', onDrop);
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
              paths={data[treeDef.key]}
              envChecks={checks}
              selected={selectedPath}
              onPick={pick}
            />
          ))}
          <PdfSection count={data.pdf.length} paths={data.pdf} selected={selectedPath} onPick={pick} />

          <div className="wb-files__uploads">
            <Label>{`Uploads · staged ${staged.length}`}</Label>
            <div className="wb-files__dropzone">
              Drop files anywhere in the workbench to stage them. Nothing is admitted until promoted.
            </div>
            {staged.map((file) => (
              <UploadCard key={file.name} file={file} send={send} reload={uploadsPanel.reload} />
            ))}
            {staged.length === 0 ? <div className="panel__note">0 staged</div> : null}
          </div>
        </div>

        <div className="wb-files__right">
          <div className="wb-files__viewer-head">
            Files {'›'} <span style={{color: 'var(--fg)'}}>{selectedPath || <Absent kind="na" />}</span>
            {selectedPath ? (
              <>
                {' '}
                <Absent kind="unreported" /> {'·'} <Absent kind="unreported" />
              </>
            ) : null}
          </div>
          <Viewer path={selectedPath} kind={selectedKind} revision={revision} />
        </div>
      </div>
    </div>
  );
}
