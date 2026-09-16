// Environment: every probe `hardy doctor` (and `/env`) already ran, one card
// per probe, laid out `auto-fit minmax(280px, 1fr)` so the grid holds
// whatever count of checks this host produces without a fixed column count.
//
// The rule this page exists to test: a failing probe is drawn exactly like a
// passing one, in the same grid, never filtered out or pushed to a separate
// "problems" section. `detail` already carries the honest sentence ("not
// found on PATH; install elan...") -- this page's only job is to print it,
// not to summarise or hide it.

import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

/** `check`'s own word for its state, matching `doctor.Check.line`'s own
 *  `ok  ` / `FAIL` / `warn` marks: a required check that failed is a
 *  failure, an optional one that failed is only a warning. */
function word(check) {
  if (check.ok) return 'ok';
  return check.required ? 'fail' : 'warn';
}

function tone(check) {
  if (check.ok) return 'var(--accent)';
  return check.required ? 'var(--error)' : 'var(--warning)';
}

export default function Environment() {
  const {revision} = useSession();
  const {data, error} = usePanel('/api/environment', revision);

  if (error) return <p className="panel__error">{error}</p>;
  if (!data) return <p className="panel__note">Probing the environment...</p>;

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Environment</span>
        <span className="wb-page-subtitle">what /env prints · nothing here is inferred</span>
      </div>

      {data.checks.length ? (
        <>
          <div className="wb-cards-auto">
            {data.checks.map((check) => (
              <div className="wb-card" key={check.name}>
                <Label>{check.name}</Label>
                <Facts
                  mono
                  rows={[[<span key="word" style={{color: tone(check)}}>{word(check)}</span>, check.detail]]}
                />
              </div>
            ))}
          </div>
          <div className="panel__note">
            Values are read from the tools themselves at probe time. A tool that is not on PATH is
            shown as not found, not omitted.
          </div>
        </>
      ) : (
        <Empty title="No probes returned" line="The environment check produced nothing to show." />
      )}
    </div>
  );
}
