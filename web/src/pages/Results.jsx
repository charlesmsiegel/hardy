// Results: the design's central screen, and the one place all three lanes a
// theorem's status can rest on sit side by side without ever merging into
// one table -- `⊢ kernel says` (the stored audit verdict), `§ record says`
// (a matching ledger item's own claim), and `" model said "` (the model's
// own report, quoted). `/api/results` (`panels/record.py:results`) already
// keeps them as three independent sub-structures per row -- `kernel`,
// `record`, `model` -- built by three functions that each read one source
// and never one another's result. This page's whole job is to keep them
// that way in the browser: three separate bordered `.wb-card`s in the
// detail pane, never a merged grid.
//
// **The `§` lane is `null` for nearly every theorem, and that is the
// truth.** Issue #171: `workflows/interactive/formal.py` writes no ledger
// record when a theorem is saved interactively, so `row.record` comes back
// `null` for essentially everything the Lean tree declares. This page
// renders that as `Absent kind="na"` -- the same "a single optional link
// most items simply don't have" reading `Ledger.jsx` already gives its own
// `research` field -- and never falls back to the kernel's verdict or the
// model's summary to fill the gap. Borrowing another lane's answer there
// would assert something no one recorded, which is the one failure this
// design exists to prevent.
//
// `row.tone` is computed server-side (`vocabulary.py`'s `_VERDICT_TONE`,
// total over `audit.GRADES`) and used directly on every verdict pill here --
// never `Pill.jsx`'s `toneForVerdict`, whose own vocabulary
// (`kernel_verified`/`accepted`/`ok`/...) is a different, file-level word
// list that happens to share no members with `declaration_status`'s
// per-declaration grades (`ambiguous`/`unaudited`/`stale`/`unapproved`/
// `open`/`assumed`/`verified`) -- routing one through the other's map would
// silently flatten every one of these seven to the fallback `muted`.
//
// Left column sections with no backing endpoint (Task 1's own finding, not
// a further guess by this page) are omitted with a note rather than drawn
// as a column of `not reported`: *not reported* means the backend was asked
// and had no figure, which is a different claim from "nothing serves
// this". See the comment above `OMITTED` below for exactly which four and
// why. `Standing assumptions`, `Naming registry` and `Failed attempts` come
// from `/api/summary`'s own `sections` -- but that endpoint hands back each
// section already reduced through `Section.shown` (`interactive/summary.py`),
// which folds "no items" into a one-line prose sentence ("nothing is
// registered.") rather than an empty list. There is no way to tell that
// sentence apart from one real line without pattern-matching the sentinel
// text, so none of those three sections carries a `· N` count in its own
// label here -- only `Open obligations`, sourced from `summary.obligations`
// (a genuinely empty list when nothing is owed, the same field
// `pages/Home.jsx` already counts), gets one.

import {useMemo} from 'react';
import Absent, {orAbsent} from '../components/Absent.jsx';
import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import Lean from '../components/Lean.jsx';
import Pill from '../components/Pill.jsx';
import Table from '../components/Table.jsx';
import useHash from '../session/useHash.js';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

// The four left-column sections the design also lists (Reports, Imported
// files, Quarantined proposals, Computer algebra export) that Task 1 found
// no endpoint for at all:
//  - Reports: no panel exposes a report's own id, turn or claim list --
//    `/api/results`' `model` lane quotes a report only per theorem it
//    names, which is what the detail pane already shows; there is no way
//    to recover a report's identity well enough to dedupe it across rows.
//  - Imported files: `/api/uploads` answers staged (not-yet-imported)
//    files only (`uploads.py`: "never committed"), and `/api/files` lists
//    bare paths with no per-file provenance (kind, sha256, "from upload").
//  - Quarantined proposals: the concept is real
//    (`workflows/admission.py`'s `FaithfulnessDisposition.QUARANTINE`) but
//    nothing under `src/hardy/app/web` serves it.
//  - Computer algebra export: `hardy.app.mcp.cas_export` is an MCP tool,
//    not a web panel; no endpoint carries an export record's replay
//    verdict.
const OMITTED_NOTE =
  'Reports, Imported files, Quarantined proposals and Computer algebra export are not drawn here: no endpoint in ' +
  'this shipment carries a report’s own id/turn/claims (only the per-theorem quote above, already shown for ' +
  'the selected theorem), a file’s import provenance, a quarantined proposal, or a computer-algebra export ' +
  'record. Omitted, not drawn as a column of not reported — that would claim the backend was asked and had ' +
  'nothing, when nothing asks at all.';

/** `module_path()`'s inverse in `formal/syntax.py`: `A.B.Foo` -> `lean/A/B/Foo.lean`. */
function leanPathFor(module) {
  return `lean/${module.split('.').join('/')}.lean`;
}

function section(summary, title) {
  return summary.sections.find((entry) => entry.title === title) || null;
}

/** `axioms` is `null` when the kernel never reported (unestablished), and a
 *  real, possibly-empty list otherwise -- Task 1's own field note, kept
 *  apart the way `Absent.jsx` keeps `unreported` apart from `zero`. */
function axiomsText(axioms) {
  if (axioms === null) return <Absent kind="unreported" />;
  if (axioms.length === 0) return <Absent kind="zero" />;
  return axioms.join(', ');
}

function sorryText(sorry) {
  if (sorry === null) return <Absent kind="unreported" />;
  return sorry ? 'yes' : 'no';
}

function claimedText(record) {
  if (!record) return <Absent kind="na" />;
  if (!record.claimed_in.length) return <Absent kind="zero" />;
  return record.claimed_in.map((c) => `${c.scope} (${c.role})`).join(', ');
}

/** Plain-text lines from a `Section` (already reduced through `.shown`),
 *  rendered as-is -- see the module comment for why none of these carry a
 *  count. */
function LinesCard({lines}) {
  return (
    <div className="wb-card">
      {lines.map((line, index) => (
        <pre key={index} className="wb-results__pre">
          {line}
        </pre>
      ))}
    </div>
  );
}

/** The selected theorem's own module, fetched whole and highlighted -- a
 *  separate component so its `usePanel` call only ever runs while a
 *  theorem (and so a real path) is selected, the same shape
 *  `pages/Files.jsx`'s `LeanTexViewer` uses for the same reason. There is
 *  no per-declaration slice of a module's source anywhere in this
 *  shipment's API (`kernel.signature` is a build identity, not Lean text),
 *  so the whole file is shown, exactly as Files itself would show it. */
function TheoremLean({module, known, onName, revision, go}) {
  const path = leanPathFor(module);
  const filePanel = usePanel(`/api/file?path=${encodeURIComponent(path)}`, revision);
  if (filePanel.error) return <p className="panel__error">{filePanel.error}</p>;
  if (!filePanel.data) return <p className="panel__note">Reading {path}...</p>;
  return (
    <div className="wb-results__lean">
      {filePanel.data.truncated ? (
        <div className="panel__note">
          Truncated: only the first megabyte is shown. Open the file on disk to read the rest.
        </div>
      ) : null}
      <Lean src={filePanel.data.text} known={known} onName={onName} />
      <div className="wb-results__lean-foot">
        <span className="panel__note">dotted names open their definition</span>
        <a
          href="#"
          onClick={(event) => {
            event.preventDefault();
            go({page: 'files', arg: path});
          }}
        >
          open {path} in the editor →
        </a>
      </div>
    </div>
  );
}

export default function Results({arg, onPeek}) {
  const {revision, setDraft} = useSession();
  const [, go] = useHash();
  const results = usePanel('/api/results', revision);
  const summaryPanel = usePanel('/api/summary', revision);
  const graphPanel = usePanel('/api/graph', revision);

  // The Lean names a `<Lean/>` block can dot and click, straight off
  // `/api/graph`'s own node names -- the same source and the same reasoning
  // `pages/Chat.jsx:68` already gives: a name not recorded there is not
  // clickable, which is the honest state before a real declaration-lookup
  // endpoint exists.
  const known = useMemo(() => (graphPanel.data ? graphPanel.data.nodes.map((node) => node.name) : []), [graphPanel.data]);
  const nameInfo = useMemo(() => {
    const map = new Map();
    for (const node of graphPanel.data?.nodes ?? []) map.set(node.name, node);
    return map;
  }, [graphPanel.data]);
  const onLeanName = (token, event) => {
    const node = nameInfo.get(token);
    onPeek?.({
      name: token,
      kind: node ? node.kind : <Absent kind="unreported" />,
      source: node ? node.artifacts[0] || <Absent kind="unreported" /> : <Absent kind="unreported" />,
      informal: node ? node.statement || <span className="panel__note">No statement recorded.</span> : <Absent kind="unreported" />,
      x: event.clientX,
      y: event.clientY,
    });
  };

  if (results.error) return <p className="panel__error">{results.error}</p>;
  if (!results.data) return <p className="panel__note">Reading the results...</p>;

  const {theorems, revision: resultsRevision} = results.data;

  if (theorems.length === 0) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Results</span>
          <span className="wb-page-subtitle">fresh project · nothing recorded</span>
        </div>
        <Empty
          title="Nothing saved"
          line="0 theorems on record. Nothing is proved, assumed, or reported — this page will show all three separately once something is."
        />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />; a
          figure the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  const otherErrors = [summaryPanel, graphPanel].map((panel) => panel.error).filter(Boolean);
  if (otherErrors.length) return <p className="panel__error">{otherErrors[0]}</p>;
  if (!summaryPanel.data || !graphPanel.data) return <p className="panel__note">Reading the project...</p>;

  const summary = summaryPanel.data;
  const assumptions = section(summary, 'Standing assumptions');
  const registry = section(summary, 'Naming registry');
  const failed = section(summary, 'Failed attempts');
  const obligations = summary.obligations || [];

  const selected = arg
    ? theorems.find((row) => row.id === arg) || null
    : theorems[0];
  const notFound = Boolean(arg) && !selected;

  const verdictCounts = new Map();
  for (const row of theorems) verdictCounts.set(row.verdict, (verdictCounts.get(row.verdict) || 0) + 1);
  const verdictLine = [...verdictCounts.entries()].map(([verdict, count]) => `${count} ${verdict}`).join(' · ');

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Results</span>
        <span className="wb-page-subtitle">
          {theorems.length} theorem{theorems.length === 1 ? '' : 's'} · revision {resultsRevision}
        </span>
        <span className="wb-results__legend">⊢ kernel · § record · “ model</span>
      </div>

      <div className="wb-results">
        <div className="wb-results__left">
          <div className="wb-section">
            <Table
              head={['theorem', '⊢ kernel audit', '§ claimed', 'module']}
              onPick={(id) => go({page: 'results', arg: id})}
              selected={selected?.id ?? ''}
              rows={theorems.map((row) => ({
                key: row.id,
                cells: [
                  <div key="name">
                    <span style={{fontFamily: 'var(--mono)'}}>{row.name}</span>{' '}
                    <span className="panel__note">({row.declared_kind})</span>
                    {row.record?.statement ? (
                      <div className="panel__note">{row.record.statement}</div>
                    ) : null}
                  </div>,
                  <div key="kernel">
                    <Pill tone={row.tone}>{row.verdict}</Pill>
                    <div className="wb-results__axioms">{axiomsText(row.axioms)}</div>
                  </div>,
                  <span key="claimed" style={{fontFamily: 'var(--mono)'}}>{claimedText(row.record)}</span>,
                  <span key="module" style={{fontFamily: 'var(--mono)', color: 'var(--muted)'}}>{row.module}</span>,
                ],
              }))}
            />
            <div className="panel__note">{verdictLine}</div>
          </div>

          <div className="wb-section">
            <Label>Standing assumptions</Label>
            <LinesCard lines={assumptions ? assumptions.lines : []} />
          </div>

          <div style={{display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16}}>
            <div className="wb-section">
              <Label>Naming registry</Label>
              <LinesCard lines={registry ? registry.lines : []} />
            </div>
            <div className="wb-section">
              <Label>Failed attempts</Label>
              <LinesCard lines={failed ? failed.lines : []} />
            </div>
            <div className="wb-section">
              <Label>{`Open obligations · ${obligations.length}`}</Label>
              {obligations.length ? (
                <div style={{display: 'flex', flexDirection: 'column', gap: 3, fontSize: 12}}>
                  {obligations.map((line, index) => (
                    <div key={index}>{line}</div>
                  ))}
                </div>
              ) : (
                <div className="panel__note">none open</div>
              )}
            </div>
          </div>

          <div className="panel__note">{OMITTED_NOTE}</div>
        </div>

        <div className="wb-results__right">
          {notFound ? (
            <div className="panel__note">
              {arg} is not a theorem on record now. Pick one from the table.
            </div>
          ) : selected ? (
            <>
              <div className="wb-results__detail-path">
                Results › <span style={{color: 'var(--fg)'}}>{selected.name}</span>
              </div>
              <div className="wb-results__name">{selected.name}</div>
              {/* Two different absences, per `Absent.jsx`'s note. No record at
                  all is the settled lookup: `na`, with the reason spelled
                  out in the `§ record says` card below. A record whose
                  `statement` is null is an absence with no recorded cause:
                  prose, not an `Absent` kind. */}
              <div style={{fontSize: 14}}>
                {selected.record ? (
                  selected.record.statement || <span className="panel__note">No statement recorded.</span>
                ) : (
                  <Absent kind="na" />
                )}
              </div>

              <TheoremLean module={selected.module} known={known} onName={onLeanName} revision={revision} go={go} />

              <div className="wb-card">
                <Label>⊢ kernel says</Label>
                <Facts
                  rows={[
                    ['verdict', <Pill key="v" tone={selected.tone}>{selected.kernel.verdict}</Pill>],
                    ['detail', selected.kernel.detail],
                    ['axioms', axiomsText(selected.kernel.axioms)],
                    ['sorryAx', sorryText(selected.kernel.sorry)],
                    ['signature', orAbsent(selected.kernel.signature)],
                  ]}
                />
                {selected.kernel.revalidated ? null : (
                  <div className="panel__note">
                    As last established, not revalidated against the tree now on disk. This page runs no Lean and
                    cannot recompute a build signature, so a file edited outside the session keeps the verdict its
                    last audit gave it. A theorem the tree no longer declares does read as expired.
                  </div>
                )}
              </div>

              <div className="wb-card">
                <Label>§ record says</Label>
                {selected.record ? (
                  <Facts
                    rows={[
                      ['claimed in', claimedText(selected.record)],
                      [
                        'assumptions',
                        selected.record.assumptions.length ? selected.record.assumptions.join(', ') : <Absent kind="zero" />,
                      ],
                      [
                        'ledger',
                        <a
                          key="ledger"
                          href="#"
                          onClick={(event) => {
                            event.preventDefault();
                            go({page: 'ledger', arg: selected.record.id});
                          }}
                        >
                          {selected.record.id}
                        </a>,
                      ],
                    ]}
                  />
                ) : (
                  <div className="panel__note">
                    <Absent kind="na" /> — no ledger item names this Lean declaration. Issue #171: an interactive
                    save writes no ledger record, so this is the ordinary case for nearly every theorem here, not a
                    lookup failure.
                  </div>
                )}
              </div>

              <div className="wb-card">
                <Label>“ model said</Label>
                {selected.model ? (
                  <div style={{display: 'flex', flexDirection: 'column', gap: 8}}>
                    {selected.model.map((entry, index) => (
                      <div key={index} className="wb-results__model-entry">
                        <div className="panel__note">
                          status {orAbsent(entry.status)} · open {entry.open.length ? entry.open.join(', ') : <Absent kind="zero" />} ·
                          on {entry.assumptions.length ? entry.assumptions.join(', ') : <Absent kind="zero" />}
                        </div>
                        <div className="wb-results__quote">{entry.summary}</div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="panel__note">
                    <Absent kind="na" /> — no report has named this theorem yet.
                  </div>
                )}
              </div>

              <div className="wb-results__actions">
                <button
                  type="button"
                  className="button"
                  onClick={() => go({page: 'files', arg: leanPathFor(selected.module)})}
                >
                  Open Lean file
                </button>
                {selected.record ? (
                  <button
                    type="button"
                    className="button"
                    onClick={() => {
                      setDraft(`/delegate ${selected.record.id} `);
                      go('chat');
                    }}
                  >
                    Put /delegate {selected.record.id} in composer
                  </button>
                ) : null}
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    navigator.clipboard?.writeText(selected.name).catch(() => {});
                  }}
                >
                  Copy name
                </button>
              </div>
            </>
          ) : (
            <div className="panel__note">Select a theorem to see its detail.</div>
          )}
        </div>
      </div>
    </div>
  );
}
