// Runs: a table of `/prove` runs from `/api/runs`, and one run's detail from
// `/api/runs/item?id=` -- both read straight off `RunManifest`
// (`workflows/contracts.py:344`) through `panels/runs.py` (Task 5). See that
// module's own docstring first; this page exists to render exactly the
// asymmetry it documents, not to paper over it.
//
// **`claim_sha256` is the page's whole point, and the statement is shown
// beside it.** Every check a run made -- the faithfulness read, the kernel's
// final verification, the writeup -- was against one frozen Lean statement,
// and this hash is what ties them together
// (`Grades.faithfulness_review.claim_sha256` and
// `Grades.verification_evidence.claim_sha256` both name the same value when
// a run reaches them). `RunManifest` never stores the text, but the run
// directory does (`formalization.json`, the `FrozenClaim` `prove.py` writes
// at approval), and `/api/runs/item` reads it back as `claim` (issue #174)
// -- only after proving it is the statement this hash is the hash *of*:
// `panels/runs.py`'s `_frozen_claim` refuses a file whose hash is not the
// manifest's, and one whose text does not re-freeze to the hash it carries.
// So `claim` is either the statement, proven, or `null` with `claim_error`
// saying why *this run* cannot show it -- a run whose manifest names a hash
// but whose directory lost the file says so, rather than the page implying
// the text is never served anywhere. Both are `null` exactly when
// `claim_sha256` is: nothing was ever frozen, which is `na`, not a failure.
//
// **An unreadable run stays a row, never a blank or an omission.** `_row`
// emits `{dir, readable:false, error, run_id:null, ...}` for a directory
// whose `manifest.json` will not parse. It has no `run_id`, so it cannot be
// addressed by `/api/runs/item` (a 400 refusal, deliberately: "there is
// nothing honest to show for one run when the single artifact naming what
// happened cannot be parsed" -- `run_item`'s own docstring) -- so clicking
// one here never fires that request at all. It opens a dedicated right-pane
// block instead, keyed off the same `dir` the list already showed, saying
// plainly that this run could not be read and why. The row itself always
// draws a `readable: false` pill, not a blank cell.
//
// **Phase gets no colour.** The prototype's own list rows never draw
// `phase` as a pill -- only the three grades (`⊢ formal`, faithfulness,
// document) are bordered chips; phase and the terminal reason beside it are
// plain text. Nothing in `panels/vocabulary.py` gives `RunPhase` or
// `TerminalReason` a tone either, so this page does not invent one -- the
// "fifth vocabulary" the brief warns against is a trap for a value that
// looks like it wants a colour, not a reason to give it one it was never
// designed to carry. Same for `FaithfulnessOutcome` (`agreed`/`disputed`/
// `unavailable`) in the detail pane's reader card: a different word space
// from `Grades.faithfulness` (`user_approved`/`not_approved`, which *does*
// have a server tone via `tones.faithfulness` and is used for the run-level
// pill), so it prints as plain mono text rather than borrowing that tone or
// guessing its own.
//
// **The reasoning is quoted, never edited.** `Grades.faithfulness_review`
// (`FaithfulnessVerdict`, `contracts.py:150`) carries `review.notes` and
// `review.divergences` for an answered read, or `detail` alone for one the
// gate could not get (`outcome: unavailable`). Both are rendered verbatim in
// `<q className="wb-runs__quote">` -- no truncation, no re-wording -- for
// the identical reason `pages/Results.jsx`'s `“ model said”` lane never
// touches `entry.summary`.

import Absent, {orAbsent} from '../components/Absent.jsx';
import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import Pill from '../components/Pill.jsx';
import Table from '../components/Table.jsx';
import useHash from '../session/useHash.js';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

const UNREADABLE_PREFIX = 'unreadable:';

/** The one key `<Table/>`'s `onPick`/`selected` need for a row that may or
 *  may not have a `run_id` -- an unreadable row is addressed by its `dir`,
 *  prefixed so it can never collide with a real (`UUID`-derived) run id. */
function rowKey(row) {
  return row.readable ? row.run_id : `${UNREADABLE_PREFIX}${row.dir}`;
}

/** `created_at`/a trajectory event's `timestamp`: both are `.isoformat()`
 *  strings (`panels/runs.py`), not the Unix-epoch-seconds `pages/Home.jsx`'s
 *  own `when()` expects -- a different source shape, so a separate,
 *  smaller formatter rather than a shared one that would silently multiply
 *  an ISO string by 1000. */
function when(iso) {
  if (!iso) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

/** `usage.cost_usd`: the same "<$0.01 is not the same fact as $0.00" rule
 *  `pages/Home.jsx`'s own `money()` applies, restated here because that one
 *  is not exported. */
function money(cost) {
  if (cost === null || cost === undefined) return null;
  if (cost > 0 && cost < 0.01) return '<$0.01';
  return `$${cost.toFixed(2)}`;
}

/** A hash, shown short (the prototype's own convention -- "hash c3d0a917",
 *  "manifest sha256 e77b…40c1") with the full value in `title` so nothing is
 *  actually hidden, only kept out of the flow. `null` renders through the
 *  caller's own `Absent`, never here -- this only ever receives a real
 *  string. */
function shortHash(hash) {
  return (
    <span className="wb-runs__hash" title={hash}>
      {hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-8)}` : hash}
    </span>
  );
}

/** The faithfulness reader's own reasoning, quoted verbatim: `review.notes`
 *  plus any `divergences` for an answered read (`FaithfulnessReview.agrees`,
 *  `contracts.py:169`, is exactly "no divergences and no notes" -- so an
 *  agreeing review's `notes` is ordinarily empty, and there is nothing to
 *  quote beyond "agrees, silently", which is itself the truth), or
 *  `verdict.detail` alone when the reader was never reached
 *  (`outcome: unavailable`, where `review` itself is `null` by
 *  `FaithfulnessVerdict`'s own validator). Never a synthesized sentence in
 *  either case. */
function faithfulnessQuote(verdict) {
  if (verdict.outcome === 'unavailable') {
    return verdict.detail ? <q className="wb-runs__quote">{verdict.detail}</q> : <Absent kind="unreported" />;
  }
  const review = verdict.review;
  if (!review) return <Absent kind="unreported" />;
  const parts = [];
  if (review.notes) parts.push(review.notes);
  for (const divergence of review.divergences) parts.push(divergence);
  if (!parts.length) {
    return (
      <span className="panel__note">
        agrees silently -- <code>FaithfulnessReview.agrees</code> requires no divergences and no notes, so an
        agreeing read leaves nothing to quote beyond that.
      </span>
    );
  }
  return (
    <div className="wb-runs__quote-list">
      {parts.map((text, index) => (
        <q key={index} className="wb-runs__quote">
          {text}
        </q>
      ))}
    </div>
  );
}

/** One trajectory event, rendered as `sequence · timestamp · phase · kind`
 *  plus its payload verbatim -- `TrajectoryEvent.payload` is
 *  `dict[str, Any]` (`workflows/storage.py:44`), an open shape this page has
 *  no business summarising field by field, so it is shown the same way
 *  `pages/Files.jsx` shows a file it cannot interpret: as itself. */
function TrajectoryLine({event}) {
  return (
    <div className="wb-runs__trajectory-line">
      <div className="panel__note">
        {event.sequence} · {when(event.timestamp)} · {event.phase} · <strong>{event.kind}</strong>
      </div>
      <pre className="wb-runs__pre">{JSON.stringify(event.payload, null, 2)}</pre>
    </div>
  );
}

/** The run detail pane, its own component for the reason
 *  `pages/Results.jsx`'s `TheoremLean` already gives: `usePanel` here must
 *  only ever run while a real, readable `runId` is selected, never while an
 *  unreadable row (which has none to give it) is. */
function RunDetail({runId, revision, go}) {
  const panel = usePanel(`/api/runs/item?id=${encodeURIComponent(runId)}`, revision);
  if (panel.error) return <p className="panel__error">{panel.error}</p>;
  if (!panel.data) return <p className="panel__note">Reading the run...</p>;
  const run = panel.data;
  const grades = run.grades;
  const verdict = grades.faithfulness_review;
  const evidence = grades.verification_evidence;
  const usage = run.usage || {};

  return (
    <>
      <div className="wb-runs__detail-path">
        Runs › <span style={{color: 'var(--fg)'}}>{run.dir}</span>
      </div>

      <div className="wb-card">
        <Label>request</Label>
        <Facts
          rows={[
            ['run id', <span key="id" className="wb-runs__hash" title={run.run_id}>{run.run_id}</span>],
            ['model', run.model],
            ['created', when(run.created_at)],
            ['phase', run.phase],
            [
              'ended',
              run.terminal_reason ?? <Absent kind="na" />,
            ],
            ['budget', `${run.limits.official_checks} checks · ${run.limits.active_seconds}s active · ${run.limits.proof_seconds}s proving`],
            ['prompt set', shortHash(run.prompt_set_sha256)],
          ]}
        />
        <div className="panel__note">
          The claim's own English text is the frozen formalization's <code>original_text</code>, shown in the
          next card when this run froze one. The run's chosen strategy (<code>strategy.json</code>) is written
          into the run directory (<code>prove.py</code>) but no endpoint reads it back out --{' '}
          <Absent kind="unreported" /> rather than a guess at it.
        </div>
      </div>

      <div className="wb-card">
        <Label>frozen formalization · every check below was against this exact statement</Label>
        {run.claim_sha256 ? (
          <>
            <div className="wb-runs__hash-line">
              hash <span className="wb-runs__hash">{run.claim_sha256}</span>
            </div>
            {run.claim ? (
              <>
                <pre className="wb-runs__pre">{run.claim.statement}</pre>
                <Facts
                  rows={[
                    ['claim', <q key="text" className="wb-runs__quote">{run.claim.original_text}</q>],
                    [
                      'restated',
                      run.claim.restatement && run.claim.restatement !== run.claim.original_text
                        ? <q key="restated" className="wb-runs__quote">{run.claim.restatement}</q>
                        : <Absent kind="na" />,
                    ],
                    ['imports', run.claim.imports.join(', ')],
                    ['approved', when(run.claim.approved_at)],
                  ]}
                />
                <div className="panel__note">
                  Read from this run's own <code>formalization.json</code> and shown only because it hashes to the
                  number above (<code>panels/runs.py</code>'s <code>_frozen_claim</code> re-freezes the text and
                  compares) -- the faithfulness read and the kernel verification below are each independently
                  keyed to that same hash.
                </div>
              </>
            ) : (
              <div className="panel__note">
                <Absent kind="unreported" /> -- this run's manifest names the hash above, but the statement it is
                the hash of could not be shown for this run: {run.claim_error}
              </div>
            )}
          </>
        ) : (
          <div className="panel__note">
            <Absent kind="na" /> -- this run never reached an approved formalization (
            <code>claim_sha256</code> is null), so there is no frozen statement for anything else in this run to
            be checked against.
          </div>
        )}
      </div>

      <div className="wb-card">
        <Label>⊢ formal · faithfulness · document</Label>
        <Facts
          rows={[
            ['formal', <Pill key="f" tone={run.tones.formal}>{grades.formal}</Pill>],
            ['faithfulness', <Pill key="ft" tone={run.tones.faithfulness}>{grades.faithfulness}</Pill>],
            ['document', <Pill key="d" tone={run.tones.document}>{grades.document}</Pill>],
            ['informal', grades.informal],
            ['assumed', grades.assumed.length ? grades.assumed.join(', ') : <Absent kind="zero" />],
            ['known gaps', grades.known_gaps.length ? grades.known_gaps.join('; ') : <Absent kind="zero" />],
          ]}
        />
        {evidence ? (
          <>
            <Label>verification evidence</Label>
            <Facts
              rows={[
                ['claim', shortHash(evidence.claim_sha256)],
                ['source', shortHash(evidence.source_sha256)],
                ['axioms', evidence.axioms.length ? evidence.axioms.join(', ') : <Absent kind="zero" />],
                ['toolchain', `Lean ${evidence.toolchain.lean_version} · Mathlib ${evidence.toolchain.mathlib_revision}`],
              ]}
            />
          </>
        ) : (
          <div className="panel__note">
            <Absent kind="na" /> -- <code>Grades.require_verification_evidence</code> only permits this record on
            a <code>kernel_verified</code>/<code>verified_modulo</code> grade; this run's formal grade is{' '}
            {grades.formal}.
          </div>
        )}
      </div>

      <div className="wb-card">
        <Label>faithfulness · independent reader</Label>
        {verdict ? (
          <>
            <Facts
              rows={[
                ['reader verdict', verdict.outcome],
                ['reviewer', `${verdict.reviewer_model} · ${verdict.reviewer_backend}`],
                ['isolation', orAbsent(verdict.reviewer_isolation)],
                [
                  'entailments',
                  verdict.review
                    ? `formalization ⊢ claim: ${verdict.review.formalization_entails_claim ? 'yes' : 'no'} · claim ⊢ formalization: ${verdict.review.claim_entails_formalization ? 'yes' : 'no'}`
                    : <Absent kind="na" />,
                ],
                ['prompt', shortHash(verdict.prompt_sha256)],
              ]}
            />
            <div className="panel__note">
              Shown to the reader, by construction (<code>faithfulness.py</code>'s own module docstring): the
              claim's original words and the frozen Lean signature, on an isolated thread with no tools and no
              sight of the conversation that produced the formalization -- nothing else.
            </div>
            <Label>reasoning</Label>
            {faithfulnessQuote(verdict)}
          </>
        ) : (
          <div className="panel__note">
            <Absent kind="na" /> -- this run never reached the faithfulness gate (no approved formalization to
            read).
          </div>
        )}
      </div>

      <div className="wb-card">
        <Label>{`trajectory${run.trajectory ? ` · ${run.trajectory.length} events` : ''}`}</Label>
        {run.trajectory === null ? (
          <div className="panel__note">
            <Absent kind="unreported" /> -- <code>trajectory.jsonl</code> exists but does not parse as this run's
            own events; <code>panels/runs.py</code>'s <code>_trajectory</code> refuses the whole file rather than
            return a prefix that would look complete and is not.
          </div>
        ) : run.trajectory.length === 0 ? (
          <div className="panel__note">
            <Absent kind="zero" /> -- no <code>trajectory.jsonl</code> yet, or an empty one: this run never
            appended a step.
          </div>
        ) : (
          <div className="wb-runs__trajectory">
            {run.trajectory.map((event) => (
              <TrajectoryLine key={event.sequence} event={event} />
            ))}
          </div>
        )}
      </div>

      <div className="wb-card">
        <Label>usage · timings</Label>
        <Facts
          rows={[
            ['exchanges', orAbsent(usage.exchanges)],
            ['cost', usage.cost_usd === null || usage.cost_usd === undefined ? <Absent kind="unreported" /> : money(usage.cost_usd)],
            ['input tok', orAbsent(usage.input_tokens)],
            ['output tok', orAbsent(usage.output_tokens)],
            ['cache write tok', orAbsent(usage.cache_write_tokens)],
            ['cache read tok', orAbsent(usage.cache_read_tokens)],
            ['total tok', orAbsent(usage.total_tokens)],
            ['active ms', orAbsent(run.timings_ms.active)],
            ['user-wait excluded ms', orAbsent(run.timings_ms.user_wait_excluded)],
          ]}
        />
        {run.environment ? (
          <>
            <Label>environment</Label>
            <Facts
              rows={[
                ['lean', run.environment.lean_version],
                ['lean commit', shortHash(run.environment.lean_commit)],
                ['mathlib', run.environment.mathlib_revision],
                ['lake manifest', shortHash(run.environment.lake_manifest_sha256)],
                ['imports', run.environment.imports.join(', ')],
              ]}
            />
          </>
        ) : (
          <div className="panel__note">
            <Absent kind="na" /> -- no Lean environment was frozen for this run.
          </div>
        )}
      </div>

      <div className="wb-card">
        <Label>{`artifacts · ${Object.keys(run.artifacts).length} files, each hashed`}</Label>
        <div className="panel__note">
          The Lean that verified and the writeup this design's own prototype previews here are among these files
          (<code>lean/Main.lean</code>, <code>writeup.tex</code>, <code>writeup.pdf</code>, when a run reached
          them) -- but no endpoint in this shipment serves a run file's content, only its inventory: this run
          directory's own name, every file's path, and its sha256.
        </div>
        {Object.keys(run.artifacts).length ? (
          <div className="wb-runs__artifacts">
            {Object.entries(run.artifacts)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([path, sha]) => (
                <div key={path} className="wb-runs__artifact-row">
                  <span className="wb-runs__hash">{path}</span>
                  <span className="panel__note">{shortHash(sha)}</span>
                </div>
              ))}
          </div>
        ) : (
          <Absent kind="zero" />
        )}
      </div>

      <div className="wb-runs__actions">
        <button
          type="button"
          className="button"
          onClick={() => navigator.clipboard?.writeText(run.run_id).catch(() => {})}
        >
          Copy run id
        </button>
        {run.claim_sha256 ? (
          <button
            type="button"
            className="button"
            onClick={() => navigator.clipboard?.writeText(run.claim_sha256).catch(() => {})}
          >
            Copy claim hash
          </button>
        ) : null}
      </div>
      <div className="panel__note">
        Grades are read from this run's own <code>manifest.json</code>. A run can be rechecked without a model or
        network: <code>hardy accept --recorded {run.dir}</code> (<code>cli.py</code>'s own <code>accept</code>{' '}
        subcommand -- "cross-check these recorded run directories ... and run nothing").
      </div>
    </>
  );
}

export default function Runs({arg}) {
  const {revision} = useSession();
  const [, go] = useHash();
  const runsPanel = usePanel('/api/runs', revision);

  if (runsPanel.error) return <p className="panel__error">{runsPanel.error}</p>;
  if (!runsPanel.data) return <p className="panel__note">Reading the runs...</p>;

  const rows = runsPanel.data.runs;

  if (rows.length === 0) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Runs</span>
          <span className="wb-page-subtitle">fresh project · nothing recorded</span>
        </div>
        <Empty
          title="No /prove runs"
          line="0 runs recorded. A run is created by /prove and keeps its request, frozen formalization and grades."
        />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />; a
          figure the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  const unreadableCount = rows.filter((row) => !row.readable).length;
  const selectedKey = arg || rowKey(rows[0]);
  const selectedRow = rows.find((row) => rowKey(row) === selectedKey) || null;
  const notFound = Boolean(arg) && !selectedRow;

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Runs</span>
        <span className="wb-page-subtitle">
          {rows.length} run{rows.length === 1 ? '' : 's'}
          {unreadableCount ? ` · ${unreadableCount} unreadable` : ''}
        </span>
      </div>

      <div className="wb-runs">
        <div className="wb-runs__left">
          <Table
            head={['run · claim', 'phase · ended', '⊢ formal', 'faithfulness', 'document']}
            onPick={(key) => go({page: 'runs', arg: key})}
            selected={selectedKey}
            rows={rows.map((row) => ({
              key: rowKey(row),
              cells: row.readable
                ? [
                    <div key="run">
                      <span className="wb-runs__hash">{row.dir}</span>
                      <div className="panel__note">
                        claim {row.claim_sha256 ? shortHash(row.claim_sha256) : <Absent kind="na" />}
                      </div>
                    </div>,
                    <span key="phase" className="wb-runs__hash">
                      {row.phase}
                      {row.terminal_reason ? ` · ${row.terminal_reason}` : ''}
                    </span>,
                    <Pill key="formal" tone={row.tones.formal}>
                      {row.grades.formal}
                    </Pill>,
                    <Pill key="faithfulness" tone={row.tones.faithfulness}>
                      {row.grades.faithfulness}
                    </Pill>,
                    <Pill key="document" tone={row.tones.document}>
                      {row.grades.document}
                    </Pill>,
                  ]
                : [
                    <div key="run">
                      <span className="wb-runs__hash">{row.dir}</span>
                      <div className="wb-runs__unreadable-note">could not be read: {row.error}</div>
                    </div>,
                    <Pill key="phase" tone="error">
                      unreadable
                    </Pill>,
                    <Absent key="formal" kind="unreported" />,
                    <Absent key="faithfulness" kind="unreported" />,
                    <Absent key="document" kind="unreported" />,
                  ],
            }))}
          />
          <div className="panel__note">
            Grades are read from each run's own <code>manifest.json</code>. A run directory whose manifest cannot
            be parsed is still listed -- <code>readable: false</code>, its <code>dir</code>, and why -- never
            dropped: an existing run this reader cannot read is not the same fact as no run
            (<code>panels/runs.py</code>).
          </div>
        </div>

        <div className="wb-runs__right">
          {notFound ? (
            <div className="panel__note">{arg} does not name a run on record now. Pick one from the table.</div>
          ) : selectedRow && selectedRow.readable ? (
            <RunDetail runId={selectedRow.run_id} revision={revision} go={go} />
          ) : selectedRow ? (
            <>
              <div className="wb-runs__detail-path">
                Runs › <span style={{color: 'var(--fg)'}}>{selectedRow.dir}</span>
              </div>
              <div className="wb-card">
                <Label>this run could not be read</Label>
                <Facts
                  rows={[
                    ['dir', selectedRow.dir],
                    ['readable', 'false'],
                    ['error', selectedRow.error],
                  ]}
                />
                <div className="panel__note">
                  It has no <code>run_id</code> -- its manifest never parsed far enough to name one -- so there is
                  nothing <code>/api/runs/item</code> could honestly answer for it either; that endpoint refuses
                  the same case with a 400 rather than guessing at a partial record. This is the whole detail
                  this run has: that it exists, at this <code>dir</code>, and could not be read.
                </div>
              </div>
            </>
          ) : (
            <div className="panel__note">Select a run to see its detail.</div>
          )}
        </div>
      </div>
    </div>
  );
}
