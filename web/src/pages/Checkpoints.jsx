// Checkpoints: `/api/checkpoints` (`panels/checkpoints.py: checkpoints`), a
// read-only reshape of `list_checkpoints`'s own `Checkpoint` dataclasses
// (`id`, `slug`, `name`, `created`, `chat`, `files`, `bytes`, plus the
// `label` property carried alongside) -- oldest first, its own order.
//
// **Restoring is destructive and this endpoint serves the list only.** The
// module's own docstring says so plainly: "There is no mutation route here,
// and none should be added." Restore runs the same way every other
// session-changing command on this client does -- through `send()`
// (`useSession`'s `/api/input` channel, the same one `pages/Jobs.jsx`'s
// confirm cards use), gated behind a `ConfirmCard` that names the exact
// command, following `Jobs.jsx`'s established pattern rather than inventing
// a new one.
//
// **The restore command is `/checkpoint restore <id>`** -- verified against
// `handlers.py:1136` (`CHECKPOINT_USAGE`) and `handle_checkpoint`'s own
// dispatch (`verb == "restore"`, `handlers.py:1168`) -- which the design
// also names correctly. Two things beside it are not, though:
//
//  - **Saving is `/checkpoint <name>`, not `/checkpoint save <name>`.** The
//    design's own composer shows "-> /checkpoint save before-assembling-cases"
//    (`Hardy Workbench.dc.html:965`). Read `handle_checkpoint` closely
//    (`handlers.py:1152-1170`): only `list` and `restore` are recognised
//    first words; anything else falls through to `name = argument.strip()`
//    and the *entire* string is saved as the name. Sending the design's own
//    example would create a checkpoint literally named "save
//    before-assembling-cases", not one named "before-assembling-cases" -- a
//    third invented command this shipment has found, on top of the two
//    Task 6's review already named for Publications. The composer below
//    sends the real, bare form.
//  - **The restore confirmation is not the design's itemised diff card.**
//    `Hardy Workbench.dc.html:974-978` shows a card computing an
//    auto-checkpoint name, a "loses from view" list of specific theorems and
//    reports, and a "cancels: delegation d-08 (running)" line. No field
//    anywhere in `/api/checkpoints` or `_restore_checkpoint`
//    (`handlers.py:1180-1223`) carries any of that -- the real confirmation
//    the TUI itself asks is one plain sentence: `"Replace {slug} as it
//    stands with checkpoint {id}? (What is replaced is checkpointed
//    first.)"` (`handlers.py:1189-1190`), which is what this page's
//    `ConfirmCard` title reproduces, verbatim, with the row's own real
//    fields (taken/files/size) alongside it as `facts` rather than a
//    fabricated diff.
//
// **"contents at the time"** (`Hardy Workbench.dc.html:963`: "6 theorems ·
// ledger rev 41 · taken before a restore that was then declined") has no
// backing field either: `Checkpoint` (`workflows/checkpoints.py:52-61`)
// carries `files`/`bytes`, a plain file count and byte total, never a
// theorem count or a ledger revision. Omitted with a note here, the same
// "no endpoint carries this, so it is left out rather than drawn as a
// column of not reported" call `Results.jsx`'s `OMITTED_NOTE` already makes
// for its own four missing sections -- `not reported` would claim the
// backend was asked and had nothing, when nothing asks at all.

import {useState} from 'react';
import Absent from '../components/Absent.jsx';
import {ConfirmCard} from '../components/Cards.jsx';
import Empty from '../components/Empty.jsx';
import Label from '../components/Label.jsx';
import Table from '../components/Table.jsx';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

const CONTENTS_NOTE =
  'No "contents at the time" column: Checkpoint (workflows/checkpoints.py:52-61) carries a file count and a ' +
  'byte total, never a theorem count or a ledger revision. Omitted rather than drawn as not reported -- that ' +
  'would claim the backend was asked and had nothing, when nothing asks at all.';

function bytesText(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} kB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * `save now as`, the only way to make a checkpoint from the browser.
 *
 * Rendered in the empty state as well as the populated one. It used to sit
 * below the zero-checkpoint early return, so a project with no checkpoints
 * could not create its first from this page -- the control only appeared once
 * a checkpoint existed, which is the one situation where it was not needed.
 *
 * One component rather than two copies of the markup, because the two states
 * must not drift apart on what the command is or on what it warns about.
 */
function SaveControl({name, onName, send, populated}) {
  const command = name.trim() ? `/checkpoint ${name.trim()}` : null;
  return (
    <div className="wb-section">
      <div className="wb-checkpoints__save">
        <span className="panel__note">save now as</span>
        <input
          type="text"
          className="wb-checkpoints__input"
          value={name}
          placeholder="before-assembling-cases"
          onChange={(event) => onName(event.target.value)}
        />
        <button
          type="button"
          className="button"
          disabled={!command}
          onClick={() => command && send(command)}
        >
          Save…
        </button>
        <span className="wb-checkpoints__command">{command || '/checkpoint ...'}</span>
      </div>
      <div className="panel__note">
        Not gated behind a confirm card: saving creates a new checkpoint and replaces nothing
        {populated ? ', unlike restore below' : ''}.
      </div>
    </div>
  );
}


export default function Checkpoints() {
  const {revision, send, refusal} = useSession();
  const panel = usePanel('/api/checkpoints', revision);
  const [saveName, setSaveName] = useState('');
  //: The one confirm card open at a time -- always a restore here, since
  //: saving runs straight through (see the module comment: it is not
  //: destructive and the design itself gates only restore, not save).
  const [confirm, setConfirm] = useState(null);

  if (panel.error) return <p className="panel__error">{panel.error}</p>;
  if (!panel.data) return <p className="panel__note">Reading checkpoints...</p>;

  const {checkpoints} = panel.data;

  if (checkpoints.length === 0) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Checkpoints</span>
          <span className="wb-page-subtitle">fresh project · nothing recorded</span>
        </div>
        <Empty
          title="No checkpoints"
          line="Nothing to restore yet. Save one below, or run /checkpoint in chat."
        />
        <SaveControl name={saveName} onName={setSaveName} send={send} />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />; a
          figure the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  const totalBytes = checkpoints.reduce((sum, checkpoint) => sum + checkpoint.bytes, 0);

  const runCommand = (command) => {
    send(command);
    setConfirm(null);
  };


  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Checkpoints</span>
        <span className="wb-page-subtitle">
          whole-workspace snapshots · {checkpoints.length} · {bytesText(totalBytes)}
        </span>
      </div>

      {confirm ? (
        <ConfirmCard
          command={confirm.command}
          title={confirm.title}
          facts={confirm.facts}
          onYes={() => runCommand(confirm.command)}
          onNo={() => setConfirm(null)}
        />
      ) : null}
      {refusal ? <div className="wb-checkpoints__refusal">{refusal}</div> : null}

      <SaveControl name={saveName} onName={setSaveName} send={send} populated />

      <div className="wb-section">
        <Table
          head={['name', 'taken', 'files', 'size', '']}
          rows={checkpoints.map((checkpoint) => ({
            key: checkpoint.id,
            cells: [
              <span key="name" style={{fontFamily: 'var(--mono)'}}>
                {checkpoint.name || checkpoint.id}
              </span>,
              <span key="taken" style={{fontFamily: 'var(--mono)', color: 'var(--muted)'}}>
                {checkpoint.created}
              </span>,
              <span key="files">{checkpoint.files}</span>,
              <span key="size">{bytesText(checkpoint.bytes)}</span>,
              <button
                key="restore"
                type="button"
                className="button"
                onClick={() =>
                  setConfirm({
                    command: `/checkpoint restore ${checkpoint.id}`,
                    title: `Replace ${checkpoint.slug} as it stands with checkpoint ${checkpoint.id}? (What is replaced is checkpointed first.)`,
                    facts: [
                      ['taken', checkpoint.created],
                      ['files', checkpoint.files],
                      ['size', bytesText(checkpoint.bytes)],
                      ['chat', checkpoint.chat],
                    ],
                  })
                }
              >
                Restore…
              </button>,
            ],
          }))}
        />
        <div className="panel__note">{CONTENTS_NOTE}</div>
      </div>
    </div>
  );
}
