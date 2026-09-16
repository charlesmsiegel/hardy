// Jobs: the attention inbox and the delegation tree from `/api/jobs`, whose
// shape is `{counts, root, delegations:[{id, state, objective, parent}],
// attention:[{id, summary, actionable}], usage}` -- read straight from
// `panels/session.py`'s `jobs()`. Two things the design calls for are not in
// that payload at all: a per-delegation `kind` (explore/prove/compile) and a
// per-delegation budget line (`2/6 checks`). Both come from `Delegation.spec`
// and the lease ledger inside `DelegationController`, and `panels/session.py`
// does not carry either one out to JSON yet. Rather than infer a kind from an
// objective string or fabricate a budget, both are drawn as `Absent
// kind="unreported"` in the detail pane, exactly where Tree.jsx (Task 13a)
// put the fields `/api/tree` does not answer for either.
//
// `state` is a real `DelegationState` value (`queued|active|waiting|paused|
// completed|partial|failed|cancelled|exhausted|unknown` --
// `src/hardy/workflows/delegation/contracts.py:25-35`), routed through
// `Pill.jsx`'s `toneForDelegation` -- its own vocabulary, not `toneForState`.
// An earlier pass of this page used `toneForState`, which happens to answer
// for two of these ten words (`partial`, `unknown`) and flattens the other
// eight -- `active` included -- to one grey, exactly the "two vocabularies
// share a word so the wrong one looks right" mistake `toneForVerdict` vs.
// `toneForState` already exists to prevent, one level down. See
// `Pill.jsx`'s `DELEGATION_TONE` for the prototype evidence (or its absence,
// and the reasoning for each considered default) behind all ten. The smoke
// fixture's `FakeDelegations` (`tests/unit/web_fakes.py`) answers `"running"`
// for every delegation, which is not a `DelegationState` value at all; this
// page prints whatever string arrives and colours it through
// `toneForDelegation`'s own fallback to `muted`, so it does not care that
// the fixture is wrong.
//
// Controls sit behind confirm cards, per the design line for this page.
// `ConfirmCard` (`components/Cards.jsx`, Task 9, zero importers before this)
// fits them exactly: every command below is checked against the registry in
// `src/hardy/app/tui/handlers.py` before it is offered.
//
//   Pause…               /jobs pause <id>       (handle_jobs, words[0]=="pause")
//   Resume…               /jobs resume <id>      (handle_jobs, words[0]=="resume")
//   Reinforce +2 checks…  /jobs reinforce <id> 2 (handle_jobs, words[0]=="reinforce")
//   Cancel…               /cancel <id>           (handle_cancel, its own command)
//   Handle…               /jobs handle <id>      (handle_jobs, words[0]=="handle", on an attention item)
//
// Three controls the prototype draws are left out because no command backs
// them honestly. "Unpin…" needs a pin kind (`min_attention` etc. --
// `_PIN_KINDS` in handlers.py) that nothing in `/api/jobs` says is set on a
// delegation, so there is no way to know which kind to unpin. "Subscribe…"
// needs trigger names and a `DeliveryMode` the page has no source for. Both
// would mean guessing an argument rather than reading one, which is exactly
// what this task's brief rules out -- they are omitted rather than offered
// with an invented default.
//
// Root's lease/usage numbers (`status()`'s `root.lease`/`root.usage`, each a
// `ResourceLease`/`ResourceUsage.model_dump()`) are two different absences.
// `null` in a *lease* dimension is `ResourceLease`'s own documented meaning
// for "no ceiling on this axis" (`contracts.py`: "None means unbounded... never
// zero"), which is a fact about the root, not a gap in reporting -- so it is
// drawn `Absent kind="na"`. `null`/`0` in a *usage* dimension is an ordinary
// measurement (`official_checks`/`active_seconds` default to real `0`), so it
// goes through `orAbsent` the same as everywhere else.

import {useState} from 'react';
import Absent, {orAbsent} from '../components/Absent.jsx';
import {ConfirmCard} from '../components/Cards.jsx';
import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import Pill, {toneForDelegation} from '../components/Pill.jsx';
import Table from '../components/Table.jsx';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

/** `root.lease[dim]` -- `null` is "no ceiling", not "not reported". */
function leaseValue(root, dim) {
  const value = root?.lease?.[dim];
  return value === null || value === undefined ? <Absent kind="na" /> : value;
}

/** Every delegation row in tree order (root's children, then theirs), depth
 *  first so the indentation reads the way the prototype's own `├`/`└` rows
 *  do. A row whose `parent` names an id not reached from `root` -- data the
 *  server did not describe, not something this page should assume cannot
 *  happen -- is appended at depth 0 rather than silently dropped. */
function flattenTree(rows) {
  const byParent = new Map();
  for (const row of rows) {
    if (!byParent.has(row.parent)) byParent.set(row.parent, []);
    byParent.get(row.parent).push(row);
  }
  const out = [];
  const seen = new Set();
  const visit = (parentId, depth) => {
    for (const row of byParent.get(parentId) || []) {
      out.push({...row, depth});
      seen.add(row.id);
      visit(row.id, depth + 1);
    }
  };
  visit('root', 0);
  for (const row of rows) {
    if (!seen.has(row.id)) out.push({...row, depth: 0});
  }
  return out;
}

export default function Jobs() {
  const {revision, send, refusal} = useSession();
  const jobs = usePanel('/api/jobs', revision);
  const [selectedId, setSelectedId] = useState(null);
  //: The one confirm card open at a time, wherever it was raised from --
  //: an attention item's "Handle…" or a selected delegation's own actions.
  const [confirm, setConfirm] = useState(null);

  if (jobs.error) return <p className="panel__error">{jobs.error}</p>;
  if (!jobs.data) return <p className="panel__note">Reading jobs...</p>;

  const {counts, root, delegations, attention} = jobs.data;
  const empty = delegations.length === 0 && attention.length === 0;

  if (empty) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Jobs</span>
          <span className="wb-page-subtitle">fresh project · nothing recorded</span>
        </div>
        <Empty
          title="Nothing has run"
          line="0 delegations · 0 computations. /delegate and /cas start jobs; their attention items land here."
        />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />; a
          figure the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  const runCommand = (command) => {
    send(command);
    setConfirm(null);
  };

  const rows = flattenTree(delegations);
  const selected = delegations.find((row) => row.id === selectedId) || null;
  const children = selected ? delegations.filter((row) => row.parent === selected.id) : [];
  const requiring = attention.filter((item) => item.actionable).length;
  const countsLine =
    Object.entries(counts)
      .map(([state, count]) => `${state} ${count}`)
      .join(' · ') || 'no delegations';

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Jobs</span>
        <span className="wb-page-subtitle">{countsLine}</span>
      </div>

      {confirm ? (
        <ConfirmCard
          command={confirm.command}
          title={confirm.title}
          facts={confirm.facts}
          onYes={() => runCommand(confirm.command)}
          onNo={() => setConfirm(null)}
        />
      ) : null}
      {refusal ? <div className="wb-jobs__refusal">{refusal}</div> : null}

      <div className="wb-jobs">
        <div className="wb-jobs__left">
          <div className="wb-section">
            <Label>{`Attention · ${attention.length} · ${requiring} require action`}</Label>
            <div className="wb-jobs__attention">
              {attention.map((item) => (
                <div
                  key={item.id}
                  className={
                    item.actionable
                      ? 'wb-jobs__attention-item wb-jobs__attention-item--actionable'
                      : 'wb-jobs__attention-item'
                  }
                >
                  <span className="wb-jobs__attention-id">{item.id}</span>
                  <span>{item.summary}</span>
                  {item.actionable ? (
                    <button
                      type="button"
                      className="button"
                      onClick={() =>
                        setConfirm({
                          command: `/jobs handle ${item.id}`,
                          title: `Handle ${item.id}?`,
                          facts: [['raised', item.summary]],
                        })
                      }
                    >
                      Handle…
                    </button>
                  ) : (
                    <span className="panel__note">seen</span>
                  )}
                </div>
              ))}
              {attention.length === 0 ? <div className="panel__note">Nothing is waiting on a decision.</div> : null}
            </div>
          </div>

          <div className="wb-section">
            <Label>
              {'Tree · root ceilings '}
              {leaseValue(root, 'official_checks')} checks · {leaseValue(root, 'active_seconds')} active s
            </Label>
            <Table
              head={['node · objective', 'state']}
              onPick={setSelectedId}
              selected={selectedId}
              rows={rows.map((row) => ({
                key: row.id,
                cells: [
                  <span key="node" className="wb-jobs__node">
                    {'▸ '.repeat(row.depth)}
                    <span className="wb-jobs__node-id">{row.id}</span>{' '}
                    <span className="wb-jobs__node-objective">{orAbsent(row.objective)}</span>
                  </span>,
                  <Pill key="state" tone={toneForDelegation(row.state)}>
                    {row.state}
                  </Pill>,
                ],
              }))}
            />
          </div>
        </div>

        <div className="wb-jobs__right">
          {selected ? (
            <>
              <div className="wb-jobs__detail-path">
                Delegations › <span className="wb-jobs__detail-id">{selected.id}</span>
              </div>
              <div className="wb-jobs__detail-head">
                <span className="wb-jobs__detail-id">{selected.id}</span>
                <Pill tone={toneForDelegation(selected.state)}>{selected.state}</Pill>
              </div>
              <div style={{fontSize: 14}}>{orAbsent(selected.objective)}</div>
              <Facts
                rows={[
                  ['parent', selected.parent],
                  ['children', children.length],
                  ['kind', <Absent key="kind" kind="unreported" />],
                  ['budget used', <Absent key="budget" kind="unreported" />],
                  ['waiting on', <Absent key="waiting" kind="unreported" />],
                ]}
              />
              <div className="wb-jobs__actions">
                <button
                  type="button"
                  className="button"
                  onClick={() => setConfirm({command: `/jobs pause ${selected.id}`, title: `Pause ${selected.id}?`})}
                >
                  Pause…
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() => setConfirm({command: `/jobs resume ${selected.id}`, title: `Resume ${selected.id}?`})}
                >
                  Resume…
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() =>
                    setConfirm({
                      command: `/jobs reinforce ${selected.id} 2`,
                      title: `Reinforce ${selected.id} with 2 more official checks?`,
                    })
                  }
                >
                  Reinforce +2 checks…
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() => setConfirm({command: `/cancel ${selected.id}`, title: `Cancel ${selected.id} and its descendants?`})}
                >
                  Cancel…
                </button>
              </div>
              <div className="panel__note">
                No <code>/jobs unpin</code> or <code>/jobs subscribe</code> control is offered here: unpinning needs
                a pin kind (<code>min_attention</code>, <code>forbid_spend</code>, <code>reinforce</code>,{' '}
                <code>reserve_exploration</code>) and subscribing needs trigger names and a delivery mode, and{' '}
                <code>/api/jobs</code> states neither for a delegation.
              </div>
            </>
          ) : (
            <div className="panel__note">Select a delegation to see its detail.</div>
          )}
        </div>
      </div>
    </div>
  );
}
