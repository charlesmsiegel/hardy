// The drop-to-import overlay: dragging any file over the window shows a
// fixed full-viewport hint of where each kind lands; dropping it stages the
// file with the server and hands the user to Files to see what landed.
//
// Listens on `window` rather than a wrapper div around the page, so it does
// not have to thread a drag handler through every element `Shell` renders --
// the prototype attaches to its own root node, which happens to cover the
// same area `window` does here. `dragenter`/`dragleave` are counted rather
// than toggled on the bare `dragover` the brief names, because `dragover`
// fires continuously while hovering and every element under the cursor
// dispatches its own `dragenter`/`dragleave` pair as the mouse crosses it;
// a depth counter is what keeps the overlay from flickering as it does.
// `dragover` is still handled, but only to call `preventDefault` -- the one
// thing a drop target must do for the browser to allow a drop at all.
//
// On drop, each file's SHA-256 is computed and, for a `.lean` file, its
// `theorem`/`lemma`/`def`/`axiom` names and any `sorry` are scanned, exactly
// as the brief asks. Both are handed to `uploadScans.record`, keyed by the
// name `/api/upload` actually staged the file as (`stage`'s own collision
// suffix, not the dropped name), so Files' Uploads cards -- the page this
// shipment gives them a place on -- can read them back. The scan itself must
// not block the upload: a `.lean` file that is not valid UTF-8, say, fails
// `file.text()` per file, and that failure is swallowed so the rest still
// stage; only the digest, computed first, is unconditional.

import {useCallback, useEffect, useRef, useState} from 'react';
import {upload} from '../api.js';
import {record} from './uploadScans.js';

async function digestOf(file) {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer()));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

//: The names bound by `theorem`/`lemma`/`def`/`axiom`, and whether `sorry`
//: occurs anywhere in the text -- what the brief asks a dropped `.lean`
//: file to be scanned for.
function scanLean(text) {
  const names = [...text.matchAll(/\b(?:theorem|lemma|def|axiom)\s+([A-Za-z_][A-Za-z0-9_'.]*)/g)].map(
    (match) => match[1],
  );
  return {names, sorry: /\bsorry\b/.test(text)};
}

async function stageOne(file) {
  const sha256 = await digestOf(file);
  let scan = null;
  if (file.name.toLowerCase().endsWith('.lean')) {
    try {
      scan = scanLean(await file.text());
    } catch {
      // Not valid UTF-8, say -- the file still stages, it just carries no
      // scan; `Files.jsx` renders that as `Absent kind="unreported"`.
      scan = null;
    }
  }
  const staged = await upload(file);
  record(staged.name, {sha256, scan});
  // Announced per file, after the upload has landed. `pages/Files.jsx`
  // listens for this to refetch `/api/uploads`: staging writes a file without
  // touching the session, so no `changed` event follows it, and there is
  // otherwise no signal that the staged list moved.
  window.dispatchEvent(new CustomEvent('hardy:staged', {detail: {name: staged.name}}));
}

export default function DropOverlay({go}) {
  const depth = useRef(0);
  const [dragging, setDragging] = useState(false);
  //: One line per file that did not stage, shown until the next drop.
  const [failed, setFailed] = useState([]);

  const stage = useCallback(
    async (files) => {
      setFailed([]);
      const failures = [];
      for (const file of files) {
        try {
          await stageOne(file);
        } catch (error) {
          // One bad file does not stop the rest, but it must not vanish
          // either. Files can list what landed; it has nothing to say about a
          // file that never arrived, so dropping a single rejected file used
          // to look exactly like dropping nothing. The server's own sentence
          // is what gets shown -- it is the only thing that explains an
          // oversized file, a refused name, or a read that failed.
          failures.push(`${file.name}: ${error?.message ?? error}`);
        }
      }
      setFailed(failures);
      // Navigate only when something landed. Going to Files on a total
      // failure would replace the one surface carrying the explanation.
      if (failures.length < files.length) go('files');
    },
    [go],
  );

  useEffect(() => {
    const onDragEnter = (event) => {
      event.preventDefault();
      depth.current += 1;
      setDragging(true);
    };
    const onDragOver = (event) => event.preventDefault();
    const onDragLeave = (event) => {
      event.preventDefault();
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    };
    const onDrop = (event) => {
      event.preventDefault();
      depth.current = 0;
      setDragging(false);
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (files.length) stage(files);
    };
    window.addEventListener('dragenter', onDragEnter);
    window.addEventListener('dragover', onDragOver);
    window.addEventListener('dragleave', onDragLeave);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('dragenter', onDragEnter);
      window.removeEventListener('dragover', onDragOver);
      window.removeEventListener('dragleave', onDragLeave);
      window.removeEventListener('drop', onDrop);
    };
  }, [stage]);

  // The failure card outlives the drag on purpose. `dragging` is false by the
  // time a staging error is known -- the drop already happened -- so returning
  // null on `!dragging` alone would collect the failures and never show them.
  if (!dragging) {
    if (!failed.length) return null;
    return (
      <div className="wb-drop wb-drop--report">
        <div className="wb-drop__card">
          <div className="wb-drop__title">
            {failed.length} file{failed.length === 1 ? '' : 's'} did not stage
          </div>
          <div className="wb-drop__failures">
            {failed.map((line) => (
              <div key={line}>{line}</div>
            ))}
          </div>
          <div className="wb-drop__note">
            Each line is the server's own sentence. Nothing was admitted for these; the files that did stage are
            on Files.
          </div>
          <button type="button" className="button" onClick={() => setFailed([])}>
            Dismiss
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="wb-drop">
      <div className="wb-drop__card">
        <div className="wb-drop__title">Drop to stage in this project</div>
        <div className="wb-drop__routes">
          <span>.lean</span>
          <span>→ uploads/ · promote into lean/ from Files</span>
          <span>.tex</span>
          <span>→ uploads/ · promote into tex/ from Files</span>
          <span>.g</span>
          <span>→ uploads/ · no promotion path this shipment</span>
          <span>.pdf</span>
          <span>→ library/ by digest · title and author left blank, not asked</span>
        </div>
        <div className="wb-drop__note">
          Nothing is admitted until you promote it. Lean and TeX promotion runs the real `/import` command,
          which Files raises into the composer for review rather than sending -- a staged file is admitted only
          while the digest recorded when it arrived still matches the bytes on disk, so the outcome is not known
          until it runs. Library import goes straight over `POST /api/library`, not the transcript.
        </div>
      </div>
    </div>
  );
}
