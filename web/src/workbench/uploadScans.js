// What `DropOverlay` computed for a file it just staged -- the SHA-256, and
// for a `.lean` file, the `theorem|lemma|def|axiom` names it declares and
// whether it contains `sorry` -- kept just long enough for Files' Uploads
// cards to show them.
//
// `/api/uploads` (`uploads.staged`, `uploads._describe`) does not carry
// either value: there is no server-side scan, and no read endpoint for a
// staged file's bytes (`/api/file` serves only `lean/`, `tex/` and `cas/`,
// per `SERVED_TREES` in `src/hardy/app/web/panels/workspace.py`). The only
// place these can come from is the browser that computed them, so this is a
// plain module-scoped map, the same shared-state idiom `session/usePanel.js`
// already uses for its request cache -- two components that are not in each
// other's tree (`DropOverlay`, mounted once at the shell; `Files`, mounted
// only while its tab is open) need the same answer without a prop path
// between them, and this is not session state the reducer needs to fold
// server events into.
//
// Keyed by the name `/api/upload` actually staged the file as, which can
// differ from the name it was dropped under: `uploads.stage` suffixes a
// collision (`A.lean` a second time becomes `A-2.lean`), and a card matches
// this record by the same name `/api/uploads` lists, not the one the
// `File` object carried off the user's disk.
//
// Good only for the life of the tab: a reload of the page loses every entry,
// same as it loses every held response the old panel discarded outright.
// `scanFor` answers `null` for a name this browser never computed one for,
// and `Files.jsx` renders that as `Absent kind="unreported"`, which is the
// honest state -- not a fabricated "not scanned" verdict.

const scans = new Map();
//: Comfortably past any real staging session; a cap only so a tab left open
//: for a very long time staging many files does not grow this without bound.
const LIMIT = 200;

export function record(name, entry) {
  scans.set(name, entry);
  if (scans.size > LIMIT) scans.delete(scans.keys().next().value);
}

export function scanFor(name) {
  return scans.get(name) ?? null;
}
