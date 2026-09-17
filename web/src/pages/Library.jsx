// Library: the bibliography and the seeds this project reads through, from
// `/api/sources` -> `{bibliography, seeds}` (`panels/workspace.py:sources`).
//
// The brief names a second source, `/api/library`, for "artifacts by digest
// with edition candidates and a confirm-edition flow". `src/hardy/app/web/
// server.py`'s `_api_get` dispatch has no `library` branch at all -- the
// only route by that name is the `do_POST` one that runs an import -- so
// there is no GET endpoint this page could read for that section. The
// personal library it would describe (`ManagedLibrary`, catalog editions,
// structural maps) is a real thing (`src/hardy/literature/sources/library.py`)
// but nothing in this shipment's HTTP surface reads it back out. Rather than
// invent a fetch against a path the server would 404, or draw a table with
// invented rows, this page omits that section and the right-hand artifact
// detail pane it would drive, and says so below the sections that do have
// data. `/library confirm <digest> <edition>`, the command the prototype's
// confirm-edition flow would raise, is not in the registry either
// (`src/hardy/app/tui/handlers.py` has no `library` command at all) -- a
// second reason, independent of the missing data, not to build that flow.
//
// "Unseed…" is drawn per the design but disabled the same way: no `seed` or
// `unseed` command exists in the registry, so there is nothing honest for
// the button to submit.
//
// A citation line is `Entry.rendered()`'s own field order
// (`src/hardy/literature/bibliography.py:272-296`), copied without its TeX
// escaping -- this is a browser tab, not a `.bib` file, and the title reads
// better in `<em>` than wrapped in `\emph{}`.

import Absent, {orAbsent} from '../components/Absent.jsx';
import Empty from '../components/Empty.jsx';
import Label from '../components/Label.jsx';
import Table from '../components/Table.jsx';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

/** `Entry.rendered()`'s field order, minus the TeX escaping and the
 *  `\bibitem{key}` wrapper -- this reads on a page, not into a `.bib` file. */
function Citation({entry}) {
  const parts = [];
  if (entry.authors?.length) parts.push(entry.authors.join(', '));
  parts.push(<em key="title">{entry.title}</em>);
  if (entry.edition_label) parts.push(entry.edition_label);
  if (entry.publisher) parts.push(entry.publisher);
  if (entry.year) parts.push(`(${entry.year})`);
  if (entry.journal_ref) parts.push(entry.journal_ref);
  if (entry.isbn) parts.push(`ISBN ${entry.isbn}`);
  if (entry.arxiv_id) parts.push(`arXiv:${entry.arxiv_id}`);
  if (entry.doi) parts.push(`doi:${entry.doi}`);
  return (
    <>
      {parts.map((part, index) => (
        <span key={index}>
          {part}
          {index < parts.length - 1 ? '. ' : '.'}
        </span>
      ))}
    </>
  );
}

/** Which bibliography entry, if any, names `digest` among what it was read
 *  through -- a join over data the server already sent, not an inference of
 *  a fact it did not. */
function readThrough(digest, bibliography) {
  return bibliography.find((entry) => entry.read_artifacts?.includes(digest));
}

export default function Library() {
  const {revision} = useSession();
  const sources = usePanel('/api/sources', revision);

  if (sources.error) return <p className="panel__error">{sources.error}</p>;
  if (!sources.data) return <p className="panel__note">Reading the library...</p>;

  const {bibliography, seeds, bibliography_readable: bibliographyReadable} = sources.data;

  // A corrupt `bibliography.json` (`panels/workspace.py:sources`) comes back
  // as `bibliography: []` too, the same shape a fresh project's honest zero
  // has -- so `bibliography_readable` is what tells them apart, and it has
  // to be checked before the empty-state branch below, which would
  // otherwise print "Library is empty · 0 sources" for a library that was
  // never read at all (issue #169).
  if (!bibliographyReadable) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Library</span>
          <span className="wb-page-subtitle">bibliography.json could not be read</span>
        </div>
        <Empty
          title="Bibliography unreadable"
          line="bibliography.json exists but did not parse. What it holds is not reported, not empty -- this is not a fresh project's zero."
        />
        <Label>seeds</Label>
        <div className="panel__note">
          Seeds read independently of the bibliography: this project seeds {orAbsent(seeds.length)}.
        </div>
      </div>
    );
  }

  const empty = bibliography.length === 0 && seeds.length === 0;

  if (empty) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Library</span>
          <span className="wb-page-subtitle">fresh project · nothing recorded</span>
        </div>
        <Empty
          title="Library is empty"
          line="0 sources. Drop a PDF anywhere in the workbench to add one by digest; the edition will be asked."
        />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />; a
          figure the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Library and sources</span>
        <span className="wb-page-subtitle">
          this project seeds {seeds.length} · cites {bibliography.length}
        </span>
      </div>

      <div className="wb-section">
        <Label>{`Bibliography · ${bibliography.length} · as citations, with what each was read through`}</Label>
        <div className="wb-library__bib">
          {bibliography.map((entry) => (
            <div className="wb-card" key={entry.key}>
              <div>
                <Citation entry={entry} />
              </div>
              <div className="wb-library__bib-meta">
                {entry.key}
                {entry.read_artifacts?.length ? (
                  <> · read through artifact {entry.read_artifacts[0].slice(0, 8)}</>
                ) : (
                  <> · not read through the library; cited from elsewhere</>
                )}
                {entry.cited_at ? <> · cited at {entry.cited_at}</> : null}
              </div>
            </div>
          ))}
          {bibliography.length === 0 ? <div className="panel__note">No citations recorded.</div> : null}
        </div>
      </div>

      <div className="wb-section">
        <Label>{`Seeds · ${seeds.length} · documents this session may read`}</Label>
        {seeds.length ? (
          <Table
            head={['artifact', 'priority', 'intent', '']}
            rows={seeds.map((seed) => {
              const entry = readThrough(seed.artifact, bibliography);
              return {
                key: seed.id,
                cells: [
                  <span key="artifact" className="wb-library__digest">
                    {seed.artifact.slice(0, 8)}
                    {entry ? <> · {entry.title}</> : null}
                  </span>,
                  orAbsent(seed.priority),
                  seed.intent || <Absent kind="na" />,
                  <button
                    key="unseed"
                    type="button"
                    className="button"
                    disabled
                    title="No /seed or /unseed command exists in the registry (src/hardy/app/tui/handlers.py); this control is disabled rather than raising invented syntax."
                  >
                    Unseed…
                  </button>,
                ],
              };
            })}
          />
        ) : (
          <div className="panel__note">Nothing is seeded.</div>
        )}
      </div>

      <div className="wb-section">
        <Label>Library · artifacts by digest</Label>
        <div className="panel__note">
          Not available in this shipment: <code>/api/library</code> has no read route in
          `src/hardy/app/web/server.py`, only the POST that imports a staged upload, so there is no listing of
          artifacts, edition candidates, or a structural map to draw here. The confirm-edition flow the design
          calls for would submit <code>/library confirm &lt;digest&gt; &lt;edition&gt;</code>, which is also not a
          registered command.
        </div>
      </div>
    </div>
  );
}
