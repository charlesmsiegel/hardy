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

import {useCallback, useEffect, useState} from 'react';
import {get} from '../api.js';

export default function usePanel(path, revision) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  // Bumped by `reload` for the changes the server does not announce: staging
  // an upload and discarding one write a file without touching the session, so
  // no `changed` event follows them.
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((count) => count + 1), []);

  useEffect(() => {
    let live = true;
    get(path)
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
