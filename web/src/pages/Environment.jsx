// Environment: every probe `hardy doctor` (and `/env`) already ran, one card
// per probe, laid out `auto-fit minmax(280px, 1fr)` so the grid holds
// whatever count of checks this host produces without a fixed column count.
//
// The rule this page exists to test: a failing probe is drawn exactly like a
// passing one, in the same grid, never filtered out or pushed to a separate
// "problems" section. `detail` already carries the honest sentence ("not
// found on PATH; install elan...") -- this page's only job is to print it,
// not to summarise or hide it.

import {useEffect, useRef, useState} from 'react';
import {get} from '../api.js';
import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import {toneForCheck, wordForCheck} from '../components/Pill.jsx';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

export default function Environment() {
  const {revision} = useSession();
  const panel = usePanel('/api/environment', revision);
  const request = useRef(0);
  const [failure, setFailure] = useState('');
  const [probing, setProbing] = useState(false);
  const data = panel.data;
  const error = failure || panel.error;
  useEffect(() => {
    request.current += 1;
    setProbing(false);
    setFailure('');
    return () => { request.current += 1; };
  }, [revision]);
  const reprobe = async () => {
    const attempt = ++request.current;
    setProbing(true);
    setFailure('');
    try {
      const answer = await get('/api/environment?refresh=1');
      if (request.current === attempt) panel.publish(answer);
    }
    catch (error) {
      if (request.current === attempt) setFailure(String(error?.message ?? error));
    }
    finally { if (request.current === attempt) setProbing(false); }
  };

  if (error && !data) return <p className="panel__error">{error}</p>;
  if (!data) return <p className="panel__note">Probing the environment...</p>;

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Environment</span>
        <span className="wb-page-subtitle">what /env prints · nothing here is inferred</span>
        <button type="button" className="button" disabled={probing} onClick={reprobe}>
          {probing ? 'Probing...' : 'Re-probe'}
        </button>
      </div>

      {error ? <p className="panel__error">{error}</p> : null}
      {data.checks.length ? (
        <>
          <div className="wb-cards-auto">
            {data.checks.map((check) => (
              <div className="wb-card" key={check.name}>
                <Label>{check.name}</Label>
                <Facts
                  mono
                  rows={[[
                    <span key="word" style={{color: `var(--${toneForCheck(check)})`}}>{wordForCheck(check)}</span>,
                    check.detail,
                  ]]}
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
