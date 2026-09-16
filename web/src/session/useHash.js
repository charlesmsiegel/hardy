// Navigation lives in the URL, not in a component. Three things follow from
// that and none of them are free otherwise: a reload lands where it left, the
// browser's own back and forward work, and a page is linkable -- so a refusal
// or a transcript line can point at one.
//
// The shape is `#/page/arg`, where `arg` is a theorem name, a ledger id or a
// file path. A path carries slashes, so `arg` is everything after the first
// segment and is decoded once, not split.

import {useCallback, useEffect, useState} from 'react';

export const HOME = {page: 'home', arg: ''};

/** `#/files/lean/A.lean` -> `{page: 'files', arg: 'lean/A.lean'}`. */
export function parse(hash) {
  const raw = (hash || '').replace(/^#\/?/, '');
  if (!raw) return HOME;
  const cut = raw.indexOf('/');
  if (cut === -1) return {page: decodeURIComponent(raw), arg: ''};
  return {page: decodeURIComponent(raw.slice(0, cut)), arg: decodeURIComponent(raw.slice(cut + 1))};
}

/** `{page: 'files', arg: 'lean/A.lean'}` -> `#/files/lean/A.lean`. */
export function format({page, arg}) {
  const head = `#/${encodeURIComponent(page)}`;
  if (!arg) return head;
  // The path separators stay readable; only the segments are escaped.
  return `${head}/${arg.split('/').map(encodeURIComponent).join('/')}`;
}

export default function useHash() {
  const [route, setRoute] = useState(() => parse(window.location.hash));

  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  // Assigning the hash is what moves: the listener above is the single place
  // route state is set, so a programmatic move and a back button take the same
  // path and cannot disagree.
  const go = useCallback((next) => {
    const target = typeof next === 'string' ? {page: next, arg: ''} : next;
    window.location.hash = format(target);
  }, []);

  return [route, go];
}
