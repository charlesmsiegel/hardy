// Home: the goal, three headline cards, the chats table, and what is still
// open -- the one page every project lands on.
//
// Five endpoints, five independent `usePanel` calls: each one is already
// exactly the shape this page draws (`/api/record`'s counters, `/api/jobs`'s
// delegation and usage rows, `/api/environment`'s probe list), so nothing
// here recomputes a total the server already sent. The prototype's own Home
// card carries a "set by/when" line and a Lean statement under the goal, and
// its Record card breaks theorems down by kernel-axiom tier (standard /
// modulo an approved axiom / sorryAx) -- neither exists as a field anywhere
// in this shipment's `/api/summary` or `/api/record` (the goal is a plain
// string with no author or date attached, and the ledger tracks families and
// kinds, not axiom tiers over saved proofs). Rather than invent those facts,
// this page draws only what was asked for and reads what the record
// actually counts: `by_kind`, the exact kinds `vocabulary.py` refuses to let
// a family (`result`/`research`/`concept`/`document`/`other`) stand in for
// -- "the family is for colour only... the exact kind travels beside the
// family and is what the label prints." `by_family.result` sums six kinds
// (theorem, lemma, proposition, corollary, claim, external_result), so it
// cannot be what the headline's own word "theorems" counts without silently
// calling a saved lemma a theorem; `by_kind.theorem` is the one number that
// means exactly what the label says, and the breakdown beneath it prints
// every kind `by_kind` names, lemmas and corollaries included, rather than
// folding them into a family bucket.
//
// The one rule that must survive contact with real data, called out by name
// in the brief: a chat's `turns` is `null` when its transcript could not be
// read and `0` when it was read and is empty. Those are two different claims
// and `orAbsent` is what keeps them apart -- `0` prints as `Absent
// kind="zero"`, `null` prints as `Absent kind="unreported"`, and neither
// path is reachable by writing the string directly.
//
// The Delegations card's `job.state` is a `DelegationState`
// (`src/hardy/workflows/delegation/contracts.py:25-35`), coloured through
// `Pill.jsx`'s `toneForDelegation` rather than `toneForState` -- the latter
// answers a different vocabulary that happens to share two of its ten
// words, which is exactly what let this card's earlier use of it look
// right while flattening the other eight (`active` included) to grey. See
// `Pill.jsx`'s `DELEGATION_TONE` for the evidence behind each of the ten.

import Absent, {orAbsent} from '../components/Absent.jsx';
import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import Pill, {toneForCheck, toneForDelegation, wordForCheck} from '../components/Pill.jsx';
import Table from '../components/Table.jsx';
import useHash from '../session/useHash.js';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

// `0.0` is `chats.py`'s own sentinel for "never tracked" (the legacy `main`
// chat predates the field, and an unreadable `chat.json` falls back to it
// too) -- a real turn or chat is never created at the Unix epoch, so `!ts`
// catches that placeholder the same way it catches `null`.
function when(ts) {
  if (!ts) return null;
  return new Date(ts * 1000).toLocaleString(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

/** `cost_usd`, in the same words `/status` uses: real spend under a cent does
 *  not round down to `$0.00`, which is indistinguishable from a backend that
 *  reported nothing at all. */
function money(cost) {
  if (cost === null || cost === undefined) return null;
  if (cost > 0 && cost < 0.01) return '<$0.01';
  return `$${cost.toFixed(2)}`;
}

function Loading({label}) {
  return <p className="panel__note">{label}</p>;
}

export default function Home() {
  const {revision} = useSession();
  const [, go] = useHash();
  const summary = usePanel('/api/summary', revision);
  const record = usePanel('/api/record', revision);
  const jobs = usePanel('/api/jobs', revision);
  const chats = usePanel('/api/chats', revision);
  const environment = usePanel('/api/environment', revision);

  const errors = [summary, record, jobs, chats, environment].map((p) => p.error).filter(Boolean);
  if (errors.length) return <p className="panel__error">{errors[0]}</p>;
  if (!summary.data || !record.data || !jobs.data || !chats.data || !environment.data) {
    return <Loading label="Reading the project..." />;
  }

  const goal = (summary.data.goal || '').trim();

  if (!goal) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Home</span>
          <span className="wb-page-subtitle">fresh project · nothing recorded</span>
        </div>
        <Empty
          title="Project hasn't started"
          line={
            <>
              No goal is set. Describe it in chat, or run /goal. Until then there is nothing to
              count: <Absent kind="zero" /> theorems, <Absent kind="zero" /> chats,{' '}
              <Absent kind="zero" /> delegations, <Absent kind="zero" /> tokens.
            </>
          }
        />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />;
          a figure the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  const byKind = record.data.by_kind || {};
  const theorems = byKind.theorem || 0;
  const kindRows = Object.entries(byKind).sort(([a], [b]) => a.localeCompare(b));
  const activeCount = jobs.data.counts?.active || 0;
  const attention = jobs.data.attention || [];
  const usage = jobs.data.usage || {};
  const obligations = summary.data.obligations || [];

  return (
    <div className="wb-page-body">
      <div className="wb-goal">
        <Label>Goal</Label>
        <div className="wb-goal__text">{goal}</div>
      </div>

      <div className="wb-cols3">
        <div className="wb-card">
          <Label>{`§ Record · ${theorems} theorems saved`}</Label>
          {kindRows.length ? (
            <Facts mono rows={kindRows.map(([kind, count]) => [count, kind])} />
          ) : (
            <div className="panel__note">nothing recorded</div>
          )}
          <a href="#/results" className="wb-card__link" onClick={(event) => { event.preventDefault(); go('results'); }}>
            Results →
          </a>
        </div>

        <div className="wb-card">
          <Label>{`Delegations · ${activeCount} running`}</Label>
          {jobs.data.delegations.length ? (
            <Facts
              mono
              rows={jobs.data.delegations.slice(0, 4).map((job) => [
                job.id,
                <span key={job.id}>{orAbsent(job.objective)} <Pill tone={toneForDelegation(job.state)}>{job.state}</Pill></span>,
              ])}
            />
          ) : (
            <div className="panel__note">nothing delegated</div>
          )}
          {attention.length ? (
            <div style={{display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, flexWrap: 'wrap'}}>
              <Pill tone="error">{`${attention.length} need action`}</Pill>
              <span className="panel__note">
                {attention.map((item) => `${item.id} ${item.summary}`).join(' · ')}
              </span>
            </div>
          ) : null}
          <a href="#/jobs" className="wb-card__link" onClick={(event) => { event.preventDefault(); go('jobs'); }}>
            Jobs →
          </a>
        </div>

        <div className="wb-card">
          <Label>Spend · all branches</Label>
          <Facts
            mono
            rows={[
              [orAbsent(usage.total_tokens), 'tokens'],
              [orAbsent(money(usage.cost_usd)), 'cost'],
              [orAbsent(usage.exchanges), `exchanges, ${chats.data.length} chats`],
            ]}
          />
          <a href="#/status" className="wb-card__link" onClick={(event) => { event.preventDefault(); go('status'); }}>
            Status →
          </a>
        </div>
      </div>

      <div className="wb-section">
        <Label>{`Chats · ${chats.data.length}`}</Label>
        <Table
          head={['chat', 'created', 'turns', 'last activity']}
          rows={chats.data.map((chat) => ({
            key: chat.id,
            cells: [
              <a
                key="title"
                href={`#/chat/${chat.id}`}
                onClick={(event) => { event.preventDefault(); go({page: 'chat', arg: chat.id}); }}
              >
                {chat.title}
              </a>,
              when(chat.created) || <Absent kind="unreported" />,
              orAbsent(chat.turns),
              when(chat.last_activity) || <Absent kind="unreported" />,
            ],
          }))}
        />
      </div>

      <div className="wb-split2" style={{gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 14}}>
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
        <div className="wb-section">
          <Label>Environment</Label>
          <Facts
            mono
            rows={environment.data.checks.map((check) => [
              <span key={check.name} style={{color: `var(--${toneForCheck(check)})`}}>{wordForCheck(check)}</span>,
              `${check.name}: ${check.detail}`,
            ])}
          />
        </div>
      </div>
    </div>
  );
}
