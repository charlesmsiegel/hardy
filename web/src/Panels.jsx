// The right rail: one tab at a time, and only the tab that is showing exists.
//
// Mounting only the active panel is what makes "refetch on activation" true
// without a line of code saying so -- a hidden panel has no effect running, so
// opening it is a mount and a mount is a fetch. It also keeps the graph from
// laying itself out with dagre every time the ledger moves while somebody is
// reading the summary.
//
// Nothing here polls. Between one `changed` event and the next, a panel's
// answer is the session's own last word on the subject.

import {useState} from 'react';
import Files from './panels/Files.jsx';
import Graph from './panels/Graph.jsx';
import Jobs from './panels/Jobs.jsx';
import Summary from './panels/Summary.jsx';
import Tree from './panels/Tree.jsx';

const TABS = [
  ['summary', 'Summary'],
  ['files', 'Files'],
  ['jobs', 'Jobs'],
  ['tree', 'Tree'],
  ['graph', 'Graph'],
];

export default function Panels({revision, onSend, onDraft}) {
  const [tab, setTab] = useState('summary');

  return (
    <aside className="rail rail--right">
      <div className="tabs" role="tablist" aria-label="Panels">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`tab-${id}`}
            aria-selected={tab === id}
            aria-controls="panel"
            className={tab === id ? 'tab tab--on' : 'tab'}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="panel" id="panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === 'summary' ? <Summary revision={revision} /> : null}
        {tab === 'files' ? <Files revision={revision} onSend={onSend} /> : null}
        {tab === 'jobs' ? <Jobs revision={revision} /> : null}
        {tab === 'tree' ? <Tree revision={revision} onSend={onSend} /> : null}
        {tab === 'graph' ? <Graph revision={revision} onDraft={onDraft} /> : null}
      </div>
    </aside>
  );
}
