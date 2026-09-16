// One GET, refetched when the session says its artifacts moved, and never on
// a timer.
//
// Every panel reads a file the session is also writing, so there are exactly
// two moments at which a panel's answer can be out of date: the moment it was
// opened, and the moment something changed underneath it. `revision` is the
// page's count of `changed` events, so keying the effect on it covers the
// second; mounting covers the first, because `Panels` mounts only the tab that
// is showing. Polling would cover both and also every moment in between, at
// the cost of a request a second against a session that mostly sits still.
//
// A failure is kept, not swallowed: `error` is the server's own sentence, and
// the panel prints it where the answer would have been.

import {useCallback, useEffect, useRef, useState} from 'react';
import {get} from '../api.js';

// Coalesces concurrent callers of the same `path`, at the same `revision`,
// into one request. Every page fetches its own data -- that self-sufficiency
// is the point of the `PAGES` lookup, and this cache does not change it --
// but nothing stops two mounted components from wanting the same path at
// once: `Shell.jsx` reads `/api/jobs` once for the Jobs tab's own red count
// pill, and `pages/Jobs.jsx`, `pages/Home.jsx` and `pages/Chat.jsx` each read
// it again for their own content. Without this, opening any of those three
// routes fires two GETs for one answer, for no reason a page's own code would
// ever show. Keyed on `path` alone; `revision` inside the cached entry is
// what says whether it is still good for a caller arriving after it settled.
const shared = new Map();

function fetchShared(path, revision, force) {
  const cached = shared.get(path);
  if (!force && cached && cached.revision === revision) return cached.promise;
  const promise = get(path);
  shared.set(path, {revision, promise});
  return promise;
}

export default function usePanel(path, revision) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  // Bumped by `reload` for the changes the server does not announce: staging
  // an upload and discarding one write a file without touching the session, so
  // no `changed` event follows them.
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((count) => count + 1), []);
  //: The `nonce` this instance last fetched for, so a genuine `reload()` --
  //: the one case the shared cache above must never satisfy from a stale
  //: entry, since its whole reason to exist is a change nothing else knows
  //: happened -- can be told apart from an ordinary re-render or a
  //: `revision` bump that would otherwise also change this effect's deps.
  //: `-1` on the first run means "nothing fetched yet", so the initial
  //: mount is never treated as a reload.
  const fetchedNonce = useRef(-1);

  useEffect(() => {
    let live = true;
    const isReload = fetchedNonce.current !== -1 && fetchedNonce.current !== nonce;
    fetchedNonce.current = nonce;
    fetchShared(path, revision, isReload)
      .then((value) => {
        if (!live) return;
        setData(value);
        setError('');
      })
      .catch((failure) => {
        if (!live) return;
        setError(String(failure?.message ?? failure));
      });
    return () => {
      live = false;
    };
  }, [path, revision, nonce]);

  return {data, error, reload};
}
