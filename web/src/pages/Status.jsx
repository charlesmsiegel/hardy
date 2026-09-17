// Status: what `/status` prints, and `Spend · per scope`.
//
// This is the page the whole client's rule is clearest on, and it says so in
// its own footer: a dash means the field does not apply to that scope; *not
// reported* means the backend gave no figure; neither is 0. So the page's
// real work is being exact about which of those three each empty cell is.
//
// Four spend cards, and only two of them carry figures. That is not an
// unfinished page. `SessionRecord.publish_usage` keeps one session-wide
// `Usage` and overwrites it, and `record_abandonment` stores a reason and no
// figures, so nothing in this system measures spend per conversation branch.
// The design prototype draws per-branch cards with numbers in them; those
// numbers do not exist. The cards are shown anyway, with the reason on them,
// because a card that is quietly left out is the page declining to say that
// it cannot say -- the same failure in a quieter form.
//
// The `Session` card's rows are whatever the endpoints actually carry.
// `/api/state` is read from any thread deliberately (its docstring says so)
// and is on the hot path for every page, so rows that would need a
// filesystem read are not added to it; they read *not reported* here
// instead.

import Absent from '../components/Absent.jsx';
import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import Pill, {toneForCheck, wordForCheck} from '../components/Pill.jsx';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

/** The label each figure carries on a card, in the order `/api/spend` sends. */
const FIGURE_LABELS = {
  exchanges: 'exchanges',
  cost_usd: 'cost usd',
  input_tokens: 'input tok',
  output_tokens: 'output tok',
  cache_write_tokens: 'cache write',
  cache_read_tokens: 'cache read',
};

function figure(value) {
  // `null` is "the backend gave no figure"; `0` is a measurement. The whole
  // page turns on not confusing them, so this is the only place either is
  // rendered.
  if (value === null || value === undefined) return <Absent kind="unreported" />;
  if (typeof value === 'number' && !Number.isInteger(value)) return value.toFixed(4);
  return typeof value === 'number' ? value.toLocaleString() : String(value);
}

function ScopeCard({scope, figures}) {
  return (
    <div className="wb-card wb-spend__card">
      <div className="wb-spend__head">
        <span className="wb-spend__label">{scope.label}</span>
        {scope.measured ? null : <Pill tone="muted">not measured</Pill>}
      </div>
      <div className="wb-spend__figures">
        {figures.map((name) => (
          <span className="wb-spend__figure" key={name}>
            <span className="wb-spend__figure-name">{FIGURE_LABELS[name] || name}</span>
            <span className="wb-spend__figure-value">{figure(scope.figures[name])}</span>
          </span>
        ))}
      </div>
      {scope.coverage && scope.exchanges
        ? Object.entries(scope.coverage)
            .filter(([, covered]) => covered < scope.exchanges)
            .map(([name, covered]) => (
              <div className="panel__note" key={name}>
                {FIGURE_LABELS[name] || name} covers {covered} of {scope.exchanges} exchanges, so the figure above
                is not a total for this session. It is not scaled up to compensate.
              </div>
            ))
        : null}
      <div className="panel__note">{scope.note}</div>
    </div>
  );
}

export default function Status() {
  const {revision} = useSession();
  const state = usePanel('/api/state', revision);
  const env = usePanel('/api/environment', revision);
  const spend = usePanel('/api/spend', revision);
  const record = usePanel('/api/record', revision);

  const error = [state, env, spend, record].map((panel) => panel.error).find(Boolean);
  if (error) return <p className="panel__error">{error}</p>;
  if (!state.data || !env.data || !spend.data || !record.data) {
    return <p className="panel__note">Reading the session's status...</p>;
  }

  const live = state.data;
  const prompts = live.prompts || [];
  const checks = env.data.checks || [];
  const failures = env.data.failures;
  const nothingYet = record.data.items === 0 && !live.turn_running && prompts.length === 0;

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Status</span>
        <span className="wb-page-subtitle">
          what /status prints {'·'} asked of the artifacts, not of the model
        </span>
      </div>

      {nothingYet ? (
        <Empty
          title="Nothing recorded yet"
          line="The cards below are the same ones a running project shows. A figure the backend has not supplied reads not reported; a measured zero reads 0."
        />
      ) : null}

      <div className="wb-cards-auto">
        <div className="wb-card">
          <Label>Session</Label>
          <Facts
            mono
            rows={[
              ['project', live.slug || <Absent kind="unreported" />],
              ['chat', live.chat || <Absent kind="unreported" />],
              ['model', live.model || <Absent kind="unreported" />],
              [
                'lean project',
                // Not on `/api/state`, and not added to it: that read is taken
                // from any thread deliberately and is on every page's hot
                // path. The Environment page's `lean` probe already carries
                // the real fact about this machine's toolchain.
                <Absent kind="unreported" key="lp" />,
              ],
              ['ledger items', record.data.items],
            ]}
          />
        </div>

        <div className="wb-card">
          <Label>Running now</Label>
          <Facts
            mono
            rows={[
              ['turn', live.turn_running ? 'running' : 'idle'],
              ['command', live.command_running ? 'running' : 'none'],
              ['queued', live.queued],
              ['prompts open', prompts.length],
              [
                'tool',
                // The in-flight tool's name is not on any endpoint this page
                // can read. Saying "none" would be a claim about a turn that
                // may well be inside one.
                live.turn_running ? <Absent kind="unreported" key="t" /> : <Absent kind="na" key="t" />,
              ],
            ]}
          />
        </div>

        <div className="wb-card">
          <Label>Toolchain</Label>
          <Facts
            mono
            rows={checks.map((check) => [
              check.name,
              <Pill tone={toneForCheck(check)} key={check.name}>
                {wordForCheck(check)}
              </Pill>,
            ])}
          />
          <div className="panel__note">
            {failures === 0
              ? 'every required probe passed'
              : `${failures} required probe${failures === 1 ? '' : 's'} failing`}
            {' · '}the full detail is on Environment
          </div>
        </div>
      </div>

      <div className="wb-spend">
        <div className="wb-spend__bar">
          <Label>Spend {'·'} per scope</Label>
          <span className="panel__note">
            ceilings:{' '}
            {spend.data.ceilings.slots === null ? (
              <Absent kind="unreported" />
            ) : (
              `${spend.data.ceilings.slots_in_use} of ${spend.data.ceilings.slots} slots in use`
            )}
          </span>
        </div>

        <div className="wb-spend__cards">
          {spend.data.scopes.map((scope) => (
            <ScopeCard key={scope.id} scope={scope} figures={spend.data.figures} />
          ))}
        </div>

        <div className="panel__note">
          A dash means the field does not apply to that scope; <em>not reported</em> means the backend gave no
          figure. Neither is 0.
        </div>
      </div>
    </div>
  );
}
