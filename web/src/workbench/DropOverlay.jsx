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
}

export default function DropOverlay({go}) {
  const depth = useRef(0);
  const [dragging, setDragging] = useState(false);

  const stage = useCallback(
    async (files) => {
      for (const file of files) {
        try {
          await stageOne(file);
        } catch {
          // One bad file (a name the server refuses, a read that fails) does
          // not stop the rest from staging; Files (which this still opens)
          // is where the user finds out which ones landed.
        }
      }
      go('files');
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

  if (!dragging) return null;

  return (
    <div className="wb-drop">
      <div className="wb-drop__card">
        <div className="wb-drop__title">Drop to stage in this project</div>
        <div className="wb-drop__routes">
          <span>.lean</span>
          <span>→ uploads/ · then lean/ on import · lean_check runs</span>
          <span>.tex</span>
          <span>→ uploads/ · then tex/ · compiled on import</span>
          <span>.g</span>
          <span>→ uploads/ · then cas/ as a cell</span>
          <span>.pdf</span>
          <span>→ library/ by digest · edition asked</span>
        </div>
        <div className="wb-drop__note">Nothing is admitted until you promote it. The transcript records the promotion.</div>
      </div>
    </div>
  );
}
