// Delegations in flight, what is waiting on a human, and what has been spent.
//
// The one rule this panel keeps is about absence. `Usage.summary` answers
// `null` for a figure no backend reported, deliberately and never 0, so a
// blank here is printed as "not reported" rather than as a number. A panel
// that drew 0 tokens beside a running delegation would be reporting a
// measurement that was never taken.

import usePanel from './usePanel.js';

/** A value from the usage or lease summary, in words rather than as JSON where it can be. */
function value(shown) {
  if (shown === null || shown === undefined) return 'not reported';
  if (typeof shown === 'object') {
    return Object.entries(shown)
      .map(([key, inner]) => `${key} ${inner === null ? 'not reported' : String(inner)}`)
      .join(', ');
  }
  return String(shown);
}

function Rows({entries}) {
  return (
    <dl className="kv">
      {entries.map(([key, shown]) => (
        <div className="kv__row" key={key}>
          <dt>{key}</dt>
          <dd>{value(shown)}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function Jobs({revision}) {
  const {data, error} = usePanel('/api/jobs', revision);

  if (error) return <p className="panel__error">{error}</p>;
  if (!data) return <p className="panel__note">Reading the delegations...</p>;

  const counts = Object.entries(data.counts);
  const root = Object.entries(data.root);

  return (
    <div className="panel__body">
      <h2 className="panel__heading">Jobs</h2>
      <p className="panel__counts">
        {counts.length ? counts.map(([state, count]) => `${state} ${count}`).join(' · ') : 'no delegations'}
      </p>

      <h3 className="panel__subheading">Root</h3>
      {root.length ? <Rows entries={root} /> : <p className="panel__note">no lease taken</p>}

      <h3 className="panel__subheading">Delegations</h3>
      {data.delegations.length ? (
        <ul className="jobs">
          {data.delegations.map((job) => (
            <li className="jobs__row" key={job.id}>
              <div className="jobs__head">
                <span className="jobs__id">{job.id}</span>
                <span className="chip">{job.state}</span>
              </div>
              <div className="jobs__objective">{job.objective || 'no objective recorded'}</div>
              <div className="panel__note">under {job.parent}</div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="panel__note">nothing delegated</p>
      )}

      <h3 className="panel__subheading">Attention</h3>
      {data.attention.length ? (
        <ul className="jobs">
          {data.attention.map((item) => (
            <li className="jobs__row" key={item.id}>
              <div className="jobs__head">
                <span className="jobs__id">{item.id}</span>
                {item.actionable ? <span className="chip chip--bad">action required</span> : null}
              </div>
              <div className="jobs__objective">{item.summary}</div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="panel__note">nothing waiting on you</p>
      )}

      <h3 className="panel__subheading">Usage</h3>
      <Rows entries={Object.entries(data.usage)} />
    </div>
  );
}
