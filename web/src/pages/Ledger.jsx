// Ledger, list + graph + item: `/api/ledger` (a row per current head, for the
// list and its filter pills) and `/api/graph` (the family-coloured,
// staleness-aware drawing) share this page and its one right-hand item
// detail pane, driven by `arg` -- the ledger id in `#/ledger/<id>`, the same
// convention `Peek.jsx` and `pages/Results.jsx`'s "§ record says › ledger"
// link already write to. Selecting a row, clicking a graph node, or landing
// here from a link all do the same thing: set `arg`, which fetches
// `/api/ledger/item?id=` and renders it on the right. There is no separate
// local "which node is open" state any more -- an earlier pass of this page
// kept a small inline drawer under the graph for that, before the item page
// existed to hold it; that drawer is gone, folded into the one item pane
// both views now share.
//
// `family` (per node/list row) and `style` (per edge) are read straight off
// the payload and turned into a CSS variable lookup, never recomputed from
// `kind`: `panels/vocabulary.py` is the one place that classification is
// allowed to happen, and `family`'s five values (`result`, `research`,
// `concept`, `document`, `other`) are exactly the five design tokens
// (`--result`, `--research`, `--concept`, `--document`, `--other`) by
// construction, so `var(--${family})` is the whole mapping. A `style` this
// page does not recognise (only `solid` and `dashed` exist today) falls
// back to solid, the same "print the word, never guess a family" shape
// `Pill.jsx`'s own fallback uses.
//
// A stale edge is drawn in `--error`. The stale line below names real
// versions -- "recorded against v3; order_40 is now v5", the design's own
// copy (`Hardy Workbench.dc.html:393`) -- exactly when `panels/record.py:
// graph` had them to give: `expected_version`/`current_version` come from
// `LedgerViews.stale_artifacts()` (`ledger/views.py:158`), which only knows
// about a `documents`/`formalizes` relation whose *target* moved. An edge
// stale for any other reason -- a `depends_on` relation, or one stale
// because its *source* moved rather than its target -- has no entry there,
// so both fields come back `null` and the line stays the generic one this
// page always printed: "recorded against a version of one of these items
// that has since been revised... not known to be false, and not known to
// still hold." Never a fabricated version number in that case.
//
// **List filters are client-side.** `/api/ledger` returns every head
// unfiltered (`panels/record.py: ledger_list`'s own docstring: "a round trip
// per pill would cost more than it saves"), so the five prototype pills --
// `kind`, `origin`, `evidence`, `open obligations`, `stale only` -- all run
// against the already-fetched list in the browser; nothing here adds a query
// parameter. Four of the five read fields `/api/ledger` already carries.
// `stale only` is the exception: no per-item staleness lives in that list at
// all, only per-*edge* staleness in `/api/graph`'s `edges[].stale` -- which
// this page already fetches for the graph half regardless of which view is
// showing. `staleIds` below is a set of every item id touched by a stale
// edge, as source or target, read off that same payload; filtering against
// it costs nothing this page was not already paying, and adds no new
// endpoint. `openObligations` reads the literal `ObligationStatus.OPEN`
// count, not a broader "still outstanding" bucket that would also catch
// `investigating`/`blocked` -- the pill's own word is "open", and the enum
// already has a value spelled exactly that, so matching it literally invents
// nothing. A defensible judgement call, not a transcribed fact; revisit if a
// reviewer reads the pill differently.
//
// **The item page's `versions` list is `LedgerSnapshot`'s central promise in
// one grid**: "advancing a logical item never changes what an earlier
// theorem referenced" (`workflows/ledger/state.py:2-3`). Every prior
// revision stays addressable -- clicking an older `v` previews its own
// `statement`/`origin`/`evidence`/`artifacts`/`research`, the same fields
// `graph()`'s own nodes carry (`_item_summary`'s own docstring: "mirrors
// `graph()`'s node fields -- same names") -- while `obligations` and
// `relations` stay the *current* head's throughout, exactly as
// `ledger_item`'s own docstring says they must: those are themselves
// versioned records with their own heads, not a history of this item.
// Rendered as inline markup, not a pickable `Table` -- Shipment 1's review
// found `Table`'s `rows: [{key, cells}]` shape does not fit a version list
// (there is no one natural "cell grid" for a revision; the head one is
// highlighted and clickable, the rest are not rows of equal weight).
//
// Two things the prototype's own item page draws that this one does not,
// because the data to back them honestly is not in `/api/ledger/item`'s
// payload -- printed here rather than left to be rediscovered by grep:
//   - No inline Lean source. `_item_summary` carries `artifacts` (bare
//     URIs) but never Lean text or a signature -- unlike `/api/results`,
//     which resolves a theorem to its own Lean module. A `.lean`-suffixed
//     artifact URI is instead a link into Files (`#/files/<uri>`, the same
//     viewer `pages/Results.jsx`'s own "open in the editor →" already
//     points at), reusing that page's real content rather than fabricating
//     a second Lean pane here.
//   - No "double-click a node to jump to Results". The prototype's own
//     graph note claims this; nothing in `/api/graph` or `/api/ledger/item`
//     ties a ledger item id to the `module:name` id `/api/results` actually
//     keys its rows by (that link only runs the other way, from a Results
//     row's own `record.id`). Guessing one would be exactly the kind of
//     invented command this task's brief rules out, so it is left undone.
//
// **The Export proof card (Task 10, `ExportCard`)** folds into this same
// `ItemDetail`, rather than reviving the prototype's separate graph-only
// `gsel`/`gx` selection state (`Hardy Workbench.dc.html:394-412`): this file
// already unified "which item is open" into one `arg`-driven pane for both
// the list and the graph (see above), and an export view of an item is a
// view of that same item, not a second one. It reads
// `/api/ledger/export?id=`, which computes a publication closure against a
// throwaway sentinel `Scope` when no real scope names the item
// (`panels/record.py`'s `_UNSCOPED`) -- so `ready` and each row's
// `unestablished` come back `null`, not a computed `false`, and are rendered
// as genuinely unasked rather than "not ready". `writeup.words` and
// `writeup.reader_agreed` are always `null` too, for a different reason: no
// such field exists anywhere in the ledger contracts, so they print as
// `unreported`, never guessed from a verdict. Several scopes can name the
// same item; the card says so rather than silently picking the first. The
// design's generated `/export proof ...` command does not exist
// (`handlers.py:1529` has no `proof` subcommand), so the control is
// permanently disabled with the reason printed as visible text, not only a
// tooltip -- see `ExportCard`'s own comment for which of this file's two
// existing disabled-control patterns (`Tree.jsx`'s Resume/Compare vs.
// `Files.jsx`'s Import) it follows and why.

import {useMemo, useState} from 'react';
import dagre from 'dagre';
import Absent from '../components/Absent.jsx';
import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import Pill from '../components/Pill.jsx';
import Table from '../components/Table.jsx';
import useHash from '../session/useHash.js';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

const VIEW_W = 760;
const VIEW_H = 420;
const PAD = 20;
const NODE_H = 34;
const CHAR = 7;
const MIN_W = 80;
const MAX_W = 180;
//: What the "F" badge claims, quoted from the prototype's own legend
//: (Hardy Workbench.dc.html:360) rather than paraphrased.
const F_MEANING = 'formal evidence recorded — a claim in the ledger; the verdict is on Results';

//: What the "open obligations" pill actually filters, for a reader who takes
//: "open" colloquially. `filterItems` below matches the literal
//: `ObligationStatus.OPEN` value only -- the design's own rule, never a
//: broader "still outstanding" bucket that would also catch
//: `investigating`/`blocked` -- so an item whose only obligation is
//: `blocked` vanishes under this filter, which reads as "nothing
//: outstanding here" if taken loosely. False. Task 9 review's carried fix:
//: the label and the literal filter both stay exactly as the prototype has
//: them; only this tooltip is new, the same `title={F_MEANING}` shape this
//: file already gives its own F badge two lines up.
const OPEN_OBLIGATIONS_MEANING =
  'Matches the literal ObligationStatus.OPEN value only -- not investigating, blocked, or any other still-open ' +
  'status. An item whose only obligation is blocked, say, will not appear here even though something is still owed.';

const DEFAULT_FILTERS = {kind: 'all', origin: 'all', evidence: 'any', openObligations: false, staleOnly: false};

function boxWidth(label) {
  return Math.max(MIN_W, Math.min(MAX_W, 20 + label.length * CHAR));
}

function fitted(label, width) {
  const room = Math.max(1, Math.floor((width - 14) / CHAR));
  return label.length <= room ? label : `${label.slice(0, Math.max(0, room - 1))}…`;
}

/** Place every node and route every edge with dagre, then report the
 *  bounding box so the caller can scale the whole drawing to fit the frame. */
function layout(nodes, edges) {
  const graph = new dagre.graphlib.Graph({multigraph: true});
  graph.setGraph({rankdir: 'LR', nodesep: 14, ranksep: 44, marginx: 10, marginy: 10});
  graph.setDefaultEdgeLabel(() => ({}));
  for (const node of nodes) {
    graph.setNode(node.id, {width: boxWidth(node.name || node.id), height: NODE_H});
  }
  const known = new Set(nodes.map((node) => node.id));
  const drawn = edges.filter((edge) => known.has(edge.source) && known.has(edge.target));
  for (const edge of drawn) graph.setEdge(edge.source, edge.target, {}, edge.id);
  dagre.layout(graph);
  const placed = nodes.map((node) => ({...node, box: graph.node(node.id)}));
  const routed = drawn.map((edge) => ({
    ...edge,
    points: graph.edge({v: edge.source, w: edge.target, name: edge.id}).points,
  }));
  const size = graph.graph();
  return {placed, routed, width: size.width || 1, height: size.height || 1, dropped: edges.length - drawn.length};
}

const path = (points) => points.map((point, index) => `${index ? 'L' : 'M'}${point.x} ${point.y}`).join(' ');

/** The distinct values a filter's own list-and-pick dropdown offers, read
 *  off the items actually present rather than the full server-side enum --
 *  23 `ProjectItemKind` values hard-coded here would drift the moment
 *  `contracts.py` grows one, and a dropdown full of kinds nothing on this
 *  project holds is not a filter, it is noise. */
function distinct(items, pick) {
  return [...new Set(items.flatMap(pick))].sort();
}

function filterItems(items, filters, staleIds) {
  return items.filter((item) => {
    if (filters.kind !== 'all' && item.kind !== filters.kind) return false;
    if (filters.origin !== 'all' && item.origin !== filters.origin) return false;
    if (filters.evidence !== 'any' && !item.evidence.includes(filters.evidence)) return false;
    if (filters.openObligations && !(item.obligations.open > 0)) return false;
    if (filters.staleOnly && !staleIds.has(item.id)) return false;
    return true;
  });
}

function FilterSelect({label, value, options, onChange, allLabel}) {
  return (
    <label className="wb-ledger__filter">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value={allLabel}>{allLabel}</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

function BoolPill({on, onClick, title, children}) {
  return (
    <button
      type="button"
      className={on ? 'wb-ledger__bool-pill wb-ledger__bool-pill--on' : 'wb-ledger__bool-pill'}
      onClick={onClick}
      title={title}
    >
      {children}
      {on ? ' ✓' : ''}
    </button>
  );
}

function kindBadge(kind, family, key) {
  return (
    <span key={key} className="wb-ledger__kind-badge" style={{background: `var(--${family}, var(--other))`}}>
      {kind}
    </span>
  );
}

/** The `oblig.` column and the item page's own obligations card share this:
 *  every status `/api/graph`'s `tones` map answers for (it is total over
 *  `ObligationStatus`, `panels/record.py: graph`'s own comment), coloured,
 *  never left to a client-side default. */
function obligationCounts(obligations, tones) {
  const entries = Object.entries(obligations);
  if (!entries.length) return <Absent kind="zero" />;
  return (
    <span style={{display: 'flex', gap: 6, flexWrap: 'wrap'}}>
      {entries.map(([status, count]) => (
        <Pill key={status} tone={tones[status]}>
          {count} {status}
        </Pill>
      ))}
    </span>
  );
}

//: What a `null` `ready` or a `null` row `unestablished` actually claims --
//: quoted (not paraphrased) from `panels/record.py: ledger_export`'s own
//: `_UNSCOPED` docstring, because the exact distinction is the point: no
//: `Scope.must_prove` names this item, so the question was never asked at
//: all -- a stronger, different claim from a computed `false`. Rendering
//: this as "not ready" would be exactly the substitution the Export card
//: exists to prevent.
const EXPORT_UNASKED_NOTE = 'no scope names this item to prove, so this was never asked';

/** `ready`'s cell: `null` prints as `na` plus the unasked note (never a
 *  guessed "not ready"); a real boolean prints as a pill, `not ready`
 *  toned `warning` the same way `STATE_TONE`'s own `stale`/`interrupted`
 *  entries read "recorded, not resolved" rather than an error. */
function readyCell(ready) {
  if (ready === null) {
    return (
      <span>
        <Absent kind="na" /> <span className="panel__note" style={{margin: 0}}>({EXPORT_UNASKED_NOTE})</span>
      </span>
    );
  }
  return <Pill tone={ready ? 'accent' : 'warning'}>{ready ? 'ready' : 'not ready'}</Pill>;
}

/** A closure row's own `unestablished`: same `null`-means-unasked rule as
 *  `readyCell`, since both come from the same scope-gated computation
 *  (`ledger/views.py: publication`). */
function unestablishedCell(value) {
  if (value === null) return <Absent kind="na" />;
  return value ? <Pill tone="warning">not established</Pill> : <span style={{color: 'var(--muted)'}}>established</span>;
}

function staleCell(stale) {
  return stale ? <Pill tone="warning">stale</Pill> : <span style={{color: 'var(--muted)'}}>current</span>;
}

/** A closure row's writeup state. `documented` and `assumed` are real facts
 *  (a current `DOCUMENTS` relation; scope membership) -- `assumed` still
 *  reads `na` when `null`, the same unasked reason as `readyCell`. `words`
 *  and `reader_agreed` are always `null`: no such field exists anywhere in
 *  the ledger contracts (`ledger_export`'s own docstring), so neither is
 *  ever synthesised from `verdict` or from `documented` -- both print as
 *  `unreported`, unconditionally. */
function writeupCell(writeup) {
  return (
    <span style={{display: 'flex', flexDirection: 'column', gap: 2}}>
      <span>{writeup.documented ? 'documented' : 'not documented'}</span>
      <span className="panel__note" style={{margin: 0}}>
        words <Absent kind="unreported" /> · reader agreed <Absent kind="unreported" /> · assumed{' '}
        {writeup.assumed === null ? <Absent kind="na" /> : writeup.assumed ? 'yes' : 'no'}
      </span>
    </span>
  );
}

/** The Export proof card (Task 10): `/api/ledger/export?id=`'s dependency
 *  closure for whichever item `ItemDetail` has open, plus the control the
 *  prototype shows generating `/export proof X --deps --writeups --lean
 *  --verdicts --format pdf` (`Hardy Workbench.dc.html:396-411`).
 *
 *  That command does not exist. `handlers.py:1529` registers `/export` with
 *  no `proof` subcommand and none of those flags -- it writes one shareable
 *  HTML account of the whole session, not a per-theorem PDF -- and
 *  `ledger_export`'s own `export_command` field already reports why in place
 *  of the invented syntax. This project's rule (this task's own brief):
 *  offer an action when its outcome is genuinely unknown, state the outcome
 *  when it is not. This one is not, so the button below is permanently
 *  disabled with the reason printed as ordinary text beside it, not only in
 *  a tooltip -- `Tree.jsx`'s Resume/Compare pattern (`disabled` +
 *  `title` + a visible `panel__note` repeating the same sentence), chosen
 *  over `Files.jsx`'s Import buttons because the two are answering
 *  different questions: Files' `/import` is a real command whose *outcome*
 *  is unknown until it runs (issue #165), so it stays enabled and sends
 *  nothing until reviewed; here there is no real command at all, the same
 *  shape `/resume` and `/fork --name` are missing for Tree.
 *
 *  The design's include-toggles (writeups/Lean/verdicts/Mathlib) and format
 *  tabs (pdf/tex/md) exist only to build that same fabricated command --
 *  with the command gone, so is anything those controls would do. Drawing
 *  interactive-looking checkboxes with nothing behind them would misstate
 *  the control exactly as the invented command text would, so they are not
 *  drawn; the closure they would have configured is shown in full below
 *  instead, which is the one part of the card backed by real data. */
function ExportCard({id, revision}) {
  const exportPanel = usePanel(`/api/ledger/export?id=${encodeURIComponent(id)}`, revision);

  if (exportPanel.error) {
    return (
      <div className="wb-card">
        <Label>Export proof</Label>
        <div className="panel__error">{exportPanel.error}</div>
      </div>
    );
  }
  if (!exportPanel.data) {
    return (
      <div className="wb-card">
        <Label>Export proof</Label>
        <p className="panel__note">Reading the dependency closure...</p>
      </div>
    );
  }

  const data = exportPanel.data;
  const candidateCount = data.scope_candidates.length;

  return (
    <div className="wb-card">
      <Label>
        {`Export proof · dependency closure · ${data.rows.length} in this project · `}
        read from the ⊢ axioms and § uses relations, not inferred
      </Label>

      {candidateCount === 0 ? (
        <div className="panel__note">
          No scope names {data.name} in its must_prove, so readiness below, and each row's "unestablished" column,
          were never asked -- not computed and false, genuinely unasked.
        </div>
      ) : null}
      {candidateCount > 1 ? (
        <div className="panel__note">
          {candidateCount} scopes claim {data.name} as something to prove ({data.scope_candidates.join(', ')}).
          Nothing in the schema picks one over another; the closure below is computed against {data.scope}, the
          first by id -- said here rather than picked silently.
        </div>
      ) : null}

      <Facts rows={[['ready', readyCell(data.ready)]]} />

      <Table
        head={['item', '⊢ verdict', 'writeup', 'unestablished', 'stale']}
        rows={data.rows.map((row) => ({
          key: row.id,
          cells: [
            <div key="item">
              {kindBadge(row.kind, row.family, 'kind')}{' '}
              <span style={{fontFamily: 'var(--mono)'}}>{row.name}</span>{' '}
              <span className="panel__note" style={{margin: 0}}>
                {row.id}
              </span>
            </div>,
            <Pill key="verdict" tone={row.tone}>
              {row.verdict}
            </Pill>,
            writeupCell(row.writeup),
            unestablishedCell(row.unestablished),
            staleCell(row.stale),
          ],
        }))}
      />

      <div className="panel__note">
        declarations required ·{' '}
        {data.required_declarations.length ? data.required_declarations.join(', ') : <Absent kind="zero" />}
        {' · '}bindings required ·{' '}
        {data.required_bindings.length ? data.required_bindings.join(', ') : <Absent kind="zero" />}
      </div>

      {data.obligations.length ? (
        <div style={{display: 'flex', flexDirection: 'column', gap: 4}}>
          <span className="section-label">obligations blocking readiness · {data.obligations.length}</span>
          {data.obligations.map((obligation) => (
            <div key={obligation.id} style={{display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '2px 12px'}}>
              <Pill tone={obligation.tone}>{obligation.status}</Pill>
              <span>{obligation.reason ?? <Absent kind="na" />}</span>
            </div>
          ))}
        </div>
      ) : null}

      {data.citations_open.length ? (
        <div style={{display: 'flex', flexDirection: 'column', gap: 4}}>
          <span className="section-label">citations open · {data.citations_open.length}</span>
          {data.citations_open.map((citation) => (
            <div key={citation.id} className="panel__note" style={{margin: 0}}>
              {citation.paper_id} v{citation.paper_version} · {citation.status}
            </div>
          ))}
        </div>
      ) : null}

      <div style={{display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap'}}>
        <button type="button" className="button" disabled title={data.export_command.reason}>
          Export proof…
        </button>
      </div>
      <div className="panel__note">{data.export_command.reason}</div>
    </div>
  );
}

/** The right-hand item page, shared by both the list and the graph: whichever
 *  one navigates here (a row pick, a node click, a `§ record says` link)
 *  lands on the same `arg`-driven pane. `key={id}` on the caller resets
 *  `viewVersion` when a different item is opened. */
function ItemDetail({id, revision, nodeName, go, setDraft, onShowInGraph}) {
  const itemPanel = usePanel(`/api/ledger/item?id=${encodeURIComponent(id)}`, revision);
  //: Which version's own fields (kind/name/statement/origin/evidence/
  //: artifacts/research) are previewed above -- `null` means the head.
  //: Obligations and relations never move with this: `ledger_item`'s own
  //: docstring is explicit that those are the current head's, not a history.
  const [viewVersion, setViewVersion] = useState(null);

  if (itemPanel.error) return <div className="panel__error">{itemPanel.error}</div>;
  if (!itemPanel.data) return <p className="panel__note">Reading {id}...</p>;

  const head = itemPanel.data;
  const shown = viewVersion == null ? head : head.versions.find((version) => version.version === viewVersion) || head;
  const historical = shown.version !== head.version;
  const otherVersions = head.versions.filter((version) => version.version !== head.version);

  const outbound = head.relations.filter((relation) => relation.source === id);
  const inbound = head.relations.filter((relation) => relation.target === id && relation.source !== id);

  const obligationTally = new Map();
  for (const obligation of head.obligations) {
    obligationTally.set(obligation.status, (obligationTally.get(obligation.status) || 0) + 1);
  }
  const obligationLine = [...obligationTally.entries()].map(([status, count]) => `${count} ${status}`).join(' · ') || 'none';

  const leanArtifacts = shown.artifacts.filter((uri) => uri.endsWith('.lean'));
  const otherArtifacts = shown.artifacts.filter((uri) => !uri.endsWith('.lean'));

  return (
    <div className="wb-ledger__item">
      <div className="panel__note">
        {'Ledger › '}
        {head.id} <span style={{color: 'var(--fg)'}}>v{head.version}</span>
        {otherVersions.length ? (
          <span style={{color: 'var(--muted)'}}>
            {' · '}
            {otherVersions.map((version) => (
              <button
                key={version.version}
                type="button"
                className={
                  version.version === shown.version
                    ? 'wb-ledger__version-link wb-ledger__version-link--on'
                    : 'wb-ledger__version-link'
                }
                onClick={() => setViewVersion(version.version)}
              >
                v{version.version}
              </button>
            ))}
            {' also addressable'}
          </span>
        ) : null}
      </div>

      <div style={{display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap'}}>
        {kindBadge(shown.kind, shown.family)}
        <span style={{fontFamily: 'var(--mono)', fontWeight: 600, fontSize: 16, overflowWrap: 'anywhere'}}>
          {shown.name}
        </span>
        <span className="panel__note" style={{margin: 0}}>
          origin: {shown.origin}
        </span>
      </div>

      {historical ? (
        <div className="panel__note">
          Viewing v{shown.version} of {head.version} — historical. Obligations and relations below are always the
          current head's, not shown per-version.{' '}
          <button type="button" className="wb-ledger__version-link" onClick={() => setViewVersion(null)}>
            back to v{head.version}
          </button>
        </div>
      ) : null}

      {/* `statement` is an ordinary optional field on every kind, not a kind-
          conditioned one -- nothing in the schema says whether a null one
          here means "not written yet" or "this kind does not carry one",
          so neither `Absent`'s `na` nor its `zero` cleanly fits (it is not
          a collection, and "does not apply" is a stronger claim than the
          schema supports). Prose, not an `Absent` kind -- the same call the
          drawer this page replaces already made. */}
      <div style={{fontSize: 14}}>{shown.statement || <span className="panel__note">No statement recorded.</span>}</div>

      <div className="wb-card">
        <Facts
          rows={[
            ['digest', <span key="digest" style={{fontFamily: 'var(--mono)'}}>{shown.digest.slice(0, 8)}</span>],
            ['research', shown.research ?? <Absent key="research" kind="na" />],
          ]}
        />
      </div>

      <div className="wb-card">
        <Label>{`artifacts · ${shown.artifacts.length}`}</Label>
        {shown.artifacts.length ? (
          <div style={{display: 'flex', flexDirection: 'column', gap: 2, fontFamily: 'var(--mono)', fontSize: 11}}>
            {leanArtifacts.map((uri) => (
              <a
                key={uri}
                href="#"
                onClick={(event) => {
                  event.preventDefault();
                  go({page: 'files', arg: uri});
                }}
              >
                {uri}
              </a>
            ))}
            {otherArtifacts.map((uri) => (
              <span key={uri}>{uri}</span>
            ))}
          </div>
        ) : (
          <Absent kind="zero" />
        )}
      </div>

      <div className="wb-card">
        <Label>{`§ evidence · ${shown.evidence.length} · claims made by their producers, not verdicts`}</Label>
        {shown.evidence.length ? (
          <div style={{fontFamily: 'var(--mono)', fontSize: 12}}>{shown.evidence.join(', ')}</div>
        ) : (
          <Absent kind="zero" />
        )}
      </div>

      <div className="wb-card">
        <Label>{`obligations · ${obligationLine}`}</Label>
        {head.obligations.length ? (
          <div style={{display: 'flex', flexDirection: 'column', gap: 4}}>
            {head.obligations.map((obligation) => (
              <div
                key={obligation.id}
                style={{display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '2px 12px', alignItems: 'start'}}
              >
                <Pill tone={obligation.tone}>{obligation.status}</Pill>
                {/* `reason` is optional the same way `research` is -- most
                    obligations may simply not have one recorded. */}
                <span>{obligation.reason ?? <Absent kind="na" />}</span>
              </div>
            ))}
          </div>
        ) : (
          <Absent kind="zero" />
        )}
      </div>

      <div className="wb-card">
        <Label>{`relations · ${outbound.length} out · ${inbound.length} in`}</Label>
        {head.relations.length ? (
          <div style={{display: 'flex', flexDirection: 'column', gap: 2, fontFamily: 'var(--mono)', fontSize: 11}}>
            {outbound.map((relation) => (
              <div key={relation.id}>
                <span style={{color: 'var(--muted)'}}>→ {relation.kind}</span> {relation.target}{' '}
                <span style={{color: 'var(--muted)'}}>{nodeName(relation.target)}</span>
              </div>
            ))}
            {inbound.map((relation) => (
              <div key={relation.id}>
                <span style={{color: 'var(--muted)'}}>← {relation.kind}</span> {relation.source}{' '}
                <span style={{color: 'var(--muted)'}}>{nodeName(relation.source)}</span>
              </div>
            ))}
          </div>
        ) : (
          <Absent kind="zero" />
        )}
      </div>

      <div className="wb-card">
        <Label>{`versions · ${head.versions.length}`}</Label>
        <div style={{display: 'flex', flexDirection: 'column', gap: 3, fontSize: 11, fontFamily: 'var(--mono)'}}>
          {[...head.versions].reverse().map((version) => (
            <div
              key={version.version}
              className="wb-ledger__version-row"
              style={{color: version.version === shown.version ? 'var(--fg)' : 'var(--muted)'}}
              onClick={() => setViewVersion(version.version === head.version ? null : version.version)}
            >
              <span>v{version.version}</span>
              {kindBadge(version.kind, version.family)}
              <span>{version.name}</span>
              <span>{version.digest.slice(0, 8)}</span>
            </div>
          ))}
        </div>
      </div>

      <ExportCard id={id} revision={revision} />

      <div style={{display: 'flex', gap: 8, flexWrap: 'wrap'}}>
        <button type="button" className="button" onClick={onShowInGraph}>
          Show in graph
        </button>
        <button
          type="button"
          className="button"
          onClick={() => {
            // Populates the composer's draft rather than submitting: the
            // same rule Tree.jsx (Task 13a) applies to /fork -- /delegate
            // takes options a click cannot guess.
            setDraft(`/delegate ${head.id} `);
            go('chat');
          }}
        >
          Put /delegate {head.id} in composer
        </button>
      </div>
    </div>
  );
}

export default function Ledger({arg}) {
  const {revision, setDraft} = useSession();
  const [, go] = useHash();
  const ledgerPanel = usePanel('/api/ledger', revision);
  const graphPanel = usePanel('/api/graph', revision);
  const [view, setView] = useState('list');
  const [filters, setFilters] = useState(DEFAULT_FILTERS);

  const laid = useMemo(
    () => (graphPanel.data ? layout(graphPanel.data.nodes, graphPanel.data.edges) : null),
    [graphPanel.data],
  );

  if (ledgerPanel.error) return <p className="panel__error">{ledgerPanel.error}</p>;
  if (!ledgerPanel.data) return <p className="panel__note">Reading the ledger...</p>;

  const {items} = ledgerPanel.data;

  if (items.length === 0) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Ledger</span>
          <span className="wb-page-subtitle">fresh project · nothing recorded</span>
        </div>
        <Empty
          title="Ledger is empty"
          line="0 items · 0 relations · revision 0. Items appear when a theorem is saved, an assumption approved, or a research note recorded."
        />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />; a
          figure the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  if (graphPanel.error) return <p className="panel__error">{graphPanel.error}</p>;
  if (!graphPanel.data || !laid) return <p className="panel__note">Reading the project...</p>;

  const {nodes, edges, revision: rev, tones} = graphPanel.data;

  const scale = Math.min(1, (VIEW_W - PAD * 2) / laid.width, (VIEW_H - PAD * 2) / laid.height);
  const offsetX = (VIEW_W - laid.width * scale) / 2;
  const offsetY = (VIEW_H - laid.height * scale) / 2;

  const staleEdges = laid.routed.filter((edge) => edge.stale);
  // Every item id touched by a stale edge, as source or target -- the one
  // reading of "stale only" `/api/ledger` itself cannot answer, borrowed
  // from the graph payload this page already fetches. See the module
  // comment for why this is not a new endpoint.
  const staleIds = new Set(staleEdges.flatMap((edge) => [edge.source, edge.target]));
  // Current-head name for a node id -- never the id itself when a name is
  // on record. Built off `nodes`, not `laid.placed`, so it works the same
  // whether or not the id survived layout's `known` filter.
  const nodeName = (id) => nodes.find((node) => node.id === id)?.name || id;

  const kindOptions = distinct(items, (item) => [item.kind]);
  const originOptions = distinct(items, (item) => [item.origin]);
  const evidenceOptions = distinct(items, (item) => item.evidence);
  const filtered = filterItems(items, filters, staleIds);

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Ledger</span>
        <span className="wb-page-subtitle">
          {nodes.length} items · {edges.length} relations · revision {rev} · {staleEdges.length} stale
        </span>
      </div>

      <div className="wb-ledger__toggle">
        <button
          type="button"
          className={view === 'list' ? 'wb-ledger__toggle-btn wb-ledger__toggle-btn--on' : 'wb-ledger__toggle-btn'}
          onClick={() => setView('list')}
        >
          List
        </button>
        <button
          type="button"
          className={view === 'graph' ? 'wb-ledger__toggle-btn wb-ledger__toggle-btn--on' : 'wb-ledger__toggle-btn'}
          onClick={() => setView('graph')}
        >
          Graph
        </button>
      </div>

      <div className="wb-ledger__split">
        <div className="wb-ledger__left">
          {view === 'graph' ? (
            <>
              <div className="wb-ledger__legend">
                <span>
                  <span className="wb-ledger__swatch" style={{background: 'var(--result)'}} /> result
                </span>
                <span>
                  <span className="wb-ledger__swatch" style={{background: 'var(--research)'}} /> research
                </span>
                <span>
                  <span className="wb-ledger__swatch" style={{background: 'var(--concept)'}} /> concept
                </span>
                <span>
                  <span className="wb-ledger__swatch" style={{background: 'var(--document)'}} /> document
                </span>
                <span>
                  <span className="wb-ledger__swatch" style={{background: 'var(--other)'}} /> other
                </span>
                <span className="wb-ledger__legend-sep" />
                <span>— dependency (solid)</span>
                <span>╌╌ intention (dashed)</span>
                <span style={{color: 'var(--error)'}}>— stale</span>
                <span className="wb-ledger__legend-sep" />
                <span title={F_MEANING}>
                  <span className="wb-ledger__f-badge">F</span> {F_MEANING}
                </span>
              </div>

              <svg
                viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
                className="wb-ledger__svg"
                role="img"
                aria-label="The project ledger as a graph."
              >
                <defs>
                  <marker
                    id="ledger-arrow"
                    viewBox="0 0 8 8"
                    refX="7"
                    refY="4"
                    markerWidth="8"
                    markerHeight="8"
                    markerUnits="userSpaceOnUse"
                    orient="auto"
                  >
                    <path d="M0 0 L8 4 L0 8 z" fill="var(--muted)" />
                  </marker>
                  <marker
                    id="ledger-arrow-stale"
                    viewBox="0 0 8 8"
                    refX="7"
                    refY="4"
                    markerWidth="8"
                    markerHeight="8"
                    markerUnits="userSpaceOnUse"
                    orient="auto"
                  >
                    <path d="M0 0 L8 4 L0 8 z" fill="var(--error)" />
                  </marker>
                </defs>
                <g transform={`translate(${offsetX} ${offsetY}) scale(${scale})`}>
                  {laid.routed.map((edge) => (
                    <path
                      key={edge.id}
                      d={path(edge.points)}
                      fill="none"
                      stroke={edge.stale ? 'var(--error)' : 'var(--muted)'}
                      strokeWidth="1.6"
                      strokeDasharray={edge.style === 'dashed' ? '6 4' : undefined}
                      markerEnd={`url(#${edge.stale ? 'ledger-arrow-stale' : 'ledger-arrow'})`}
                    >
                      <title>{`${edge.kind}${edge.stale ? ' (stale)' : ''}`}</title>
                    </path>
                  ))}
                  {laid.placed.map((node) => {
                    const {x, y, width, height} = node.box;
                    const formal = node.evidence.includes('formal');
                    return (
                      <g
                        key={node.id}
                        className="wb-ledger__node"
                        transform={`translate(${x - width / 2} ${y - height / 2})`}
                        onClick={() => go({page: 'ledger', arg: node.id})}
                      >
                        <rect
                          width={width}
                          height={height}
                          rx="6"
                          fill={`var(--${node.family}, var(--other))`}
                          stroke={node.id === arg ? 'var(--accent)' : 'var(--border)'}
                          strokeWidth={node.id === arg ? 2 : 1}
                        />
                        <text x="8" y={height / 2 + 4} fill="var(--fg)" fontFamily="var(--mono)" fontSize="11">
                          {fitted(node.name || node.id, width)}
                        </text>
                        {formal ? (
                          <g>
                            <title>{F_MEANING}</title>
                            <circle cx={width - 11} cy="11" r="8" fill="var(--accent)" />
                            <text x={width - 11} y="15" textAnchor="middle" fill="var(--panel)" fontSize="10" fontWeight="700">
                              F
                            </text>
                          </g>
                        ) : null}
                        <title>{`${node.name || node.id} (${node.kind})`}</title>
                      </g>
                    );
                  })}
                </g>
              </svg>

              {laid.dropped ? (
                <div className="panel__note">{laid.dropped} relation(s) point outside the current heads and are not drawn.</div>
              ) : null}
              <div className="panel__note">Click a node to open its item on the right.</div>

              {staleEdges.length ? (
                <div className="wb-ledger__stale">
                  <span className="section-label">stale relations · {staleEdges.length}</span>
                  {staleEdges.map((edge) => {
                    // `null` when `stale_artifacts()` had no entry for this edge
                    // (panels/record.py:graph) -- generic sentence, never a
                    // guessed version. `!= null` catches both undefined and null.
                    const named = edge.expected_version != null && edge.current_version != null;
                    return (
                      <div key={edge.id} className="wb-ledger__stale-line">
                        <span style={{fontFamily: 'var(--mono)'}}>
                          {edge.source} —{edge.kind}→ {edge.target}
                        </span>
                        {named ? (
                          <>
                            {' — recorded against v'}
                            {edge.expected_version}
                            {'; '}
                            {nodeName(edge.target)}
                            {' is now v'}
                            {edge.current_version}
                            {'.'}
                          </>
                        ) : (
                          <>
                            {' — recorded against a version of one of these items that has since been revised; nothing has re-checked it. '}
                            Not known to be false, and not known to still hold.
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </>
          ) : (
            <>
              <div className="wb-ledger__filters">
                <FilterSelect label="kind" value={filters.kind} options={kindOptions} allLabel="all" onChange={(kind) => setFilters((f) => ({...f, kind}))} />
                <FilterSelect label="origin" value={filters.origin} options={originOptions} allLabel="all" onChange={(origin) => setFilters((f) => ({...f, origin}))} />
                <FilterSelect
                  label="evidence"
                  value={filters.evidence}
                  options={evidenceOptions}
                  allLabel="any"
                  onChange={(evidence) => setFilters((f) => ({...f, evidence}))}
                />
                <BoolPill
                  on={filters.openObligations}
                  onClick={() => setFilters((f) => ({...f, openObligations: !f.openObligations}))}
                  title={OPEN_OBLIGATIONS_MEANING}
                >
                  open obligations
                </BoolPill>
                <BoolPill on={filters.staleOnly} onClick={() => setFilters((f) => ({...f, staleOnly: !f.staleOnly}))}>
                  stale only
                </BoolPill>
                {filters !== DEFAULT_FILTERS ? (
                  <button type="button" className="wb-ledger__bool-pill" onClick={() => setFilters(DEFAULT_FILTERS)}>
                    reset
                  </button>
                ) : null}
              </div>

              <Table
                head={['id', 'kind', 'item', 'origin', '§ evidence', 'oblig.', 'v']}
                onPick={(id) => go({page: 'ledger', arg: id})}
                selected={arg || ''}
                rows={filtered.map((item) => ({
                  key: item.id,
                  cells: [
                    <span key="id" style={{fontFamily: 'var(--mono)', color: 'var(--muted)'}}>
                      {item.id}
                    </span>,
                    kindBadge(item.kind, item.family, 'kind'),
                    <span key="name" style={{fontFamily: 'var(--mono)'}}>
                      {item.name}
                    </span>,
                    <span key="origin" style={{color: 'var(--muted)'}}>
                      {item.origin}
                    </span>,
                    <span key="evidence" style={{fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)'}}>
                      {item.evidence.length ? item.evidence.join(', ') : <Absent kind="zero" />}
                    </span>,
                    <span key="obligations">{obligationCounts(item.obligations, tones)}</span>,
                    <span key="version" style={{fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)'}}>
                      v{item.version}
                    </span>,
                  ],
                }))}
              />
              <div className="panel__note">
                {filtered.length} of {items.length} items shown.
              </div>
            </>
          )}
        </div>

        <div className="wb-ledger__right">
          {arg ? (
            <ItemDetail
              key={arg}
              id={arg}
              revision={revision}
              nodeName={nodeName}
              go={go}
              setDraft={setDraft}
              onShowInGraph={() => setView('graph')}
            />
          ) : (
            <div className="panel__note">Select an item from the list or the graph to see its detail.</div>
          )}
        </div>
      </div>
    </div>
  );
}
