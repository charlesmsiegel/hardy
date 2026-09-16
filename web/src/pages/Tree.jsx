// Tree: the git-style graph over `/api/tree`'s `{entry_id, parent_id, type,
// lesson}` list -- every branch of the conversation, not just the active
// one. Reached only through the transcript/tree toggle in Chat's own header
// (`TabBar.jsx` gives it no tab of its own), so this page and `pages/Chat.jsx`
// share `chat/Header.jsx` and both read `/api/tree` independently.
//
// `chat/tree.js`'s `layout()` does the actual git-log column assignment; this
// file turns that into SVG coordinates and the two buttons the data can
// really back. Two of the prototype's four buttons cannot be wired honestly:
// see the comment on `RESUME_NOTE` and `COMPARE_NOTE` below, and the report
// for Task 13a, for why -- the short version is that `/resume` and a
// `/fork --name` flag do not exist in `src/hardy/app/tui/handlers.py`'s
// command registry, and inventing a command string the server would refuse
// is worse than saying plainly that no such action exists yet.
//
// Fork -- the one real command here -- is never sent from this click
// handler. It fills the composer's draft (`useSession().setDraft`) and
// leaves submitting it to the user, per the prototype's own line ("Resume
// and fork raise a line card in the composer") and the design's rule that a
// session-changing action is reviewed before it runs, not auto-submitted.

import {useState} from 'react';
import Absent, {orAbsent} from '../components/Absent.jsx';
import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import Header from '../chat/Header.jsx';
import {childCount, headerStats, isAbandon, isFork, layout} from '../chat/tree.js';
import useHash from '../session/useHash.js';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

const ROW = 34;
const LANE_GAP = 40;
const LANE_X0 = 24;
const laneX = (lane) => LANE_X0 + lane * LANE_GAP;

const RESUME_NOTE =
  'No /resume command exists in the registry (src/hardy/app/tui/handlers.py) -- only /fork moves the active leaf, so this button raises no command rather than one the server would refuse as unknown.';
const COMPARE_NOTE = 'No comparison endpoint exists yet, so this raises nothing rather than a command that would do nothing.';

/** What a row's kind word and body text honestly are: for a fork/abandon
 *  marker, the two known fields say everything there is to say; for
 *  anything else, only an entry currently on the active branch has text at
 *  all -- `messages` is `useSession()`'s own list, which `/api/transcript`
 *  fills for the active path and nothing else. A live-streamed message not
 *  yet folded into the transcript carries the entry id as `.leaf` instead of
 *  `.id`, so both are checked. */
function summaryFor(entry, messages) {
  if (isAbandon(entry)) return `abandoned · ${entry.lesson}`;
  if (isFork(entry)) return `forked from ${entry.parent_id ?? 'root'}`;
  const message = messages.find((item) => item.id === entry.entry_id || item.leaf === entry.entry_id);
  return message ? message.text || '' : null;
}

export default function Tree() {
  const {status, runningTool, messages, refusal, revision, setDraft} = useSession();
  const [route, go] = useHash();

  const summary = usePanel('/api/summary', revision);
  const tree = usePanel('/api/tree', revision);

  const [selectedId, setSelectedId] = useState(null);

  const errors = [summary, tree].map((panel) => panel.error).filter(Boolean);
  if (errors.length) return <p className="panel__error">{errors[0]}</p>;
  if (!summary.data || !tree.data) return <p className="panel__note">Reading the tree…</p>;

  const goal = (summary.data.goal || '').trim();
  if (!goal) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Conversation tree</span>
          <span className="wb-page-subtitle">fresh project · nothing recorded</span>
        </div>
        <Empty title="No conversation yet" line="0 entries · 0 lines. The tree grows from the first message in a chat." />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />; a
          figure the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  const {running, entries, lines} = headerStats({messages, status, runningTool, treeData: tree.data});
  const built = layout(tree.data.entries);
  const headLane = built.laneOf.get(tree.data.active_leaf);

  const laneColor = (lane) => {
    if (lane === headLane) return 'var(--accent)';
    const tip = built.laneTip.get(lane);
    return tip && isAbandon(tip) ? 'var(--muted)' : 'var(--border)';
  };

  const svgWidth = Math.max(150, built.laneCount * LANE_GAP + 40);
  const svgHeight = Math.max(ROW, built.rows.length * ROW);

  const paths = built.edges.map((edge, index) => {
    const fromX = laneX(edge.fromLane);
    const toX = laneX(edge.toLane);
    const fromY = edge.fromRow * ROW + ROW / 2;
    const toY = edge.toRow * ROW + ROW / 2;
    const color = laneColor(edge.toLane);
    if (!edge.curved) {
      return <path key={index} d={`M ${fromX} ${fromY} L ${toX} ${toY}`} stroke={color} strokeWidth="1.5" fill="none" />;
    }
    const midY = (fromY + toY) / 2;
    return (
      <path
        key={index}
        d={`M ${fromX} ${fromY} C ${fromX} ${midY}, ${toX} ${midY}, ${toX} ${toY}`}
        stroke={color}
        strokeWidth="1.5"
        strokeDasharray="4 3"
        fill="none"
      />
    );
  });

  const dots = built.rows.map(({entry, lane, row}) => {
    const cx = laneX(lane);
    const cy = row * ROW + ROW / 2;
    const head = entry.entry_id === tree.data.active_leaf;
    const abandoned = isAbandon(entry);
    if (abandoned) {
      return (
        <g key={entry.entry_id}>
          <circle cx={cx} cy={cy} r="4.5" fill="var(--panel)" stroke="var(--border)" strokeWidth="1.5" />
          <text x={cx} y={cy + 3} textAnchor="middle" fontSize="8" fill="var(--muted)">
            ×
          </text>
        </g>
      );
    }
    if (head) {
      return <circle key={entry.entry_id} cx={cx} cy={cy} r="6" fill={laneColor(lane)} />;
    }
    return <circle key={entry.entry_id} cx={cx} cy={cy} r="4.5" fill="var(--panel)" stroke={laneColor(lane)} strokeWidth="1.5" />;
  });

  const selected = selectedId ?? tree.data.active_leaf;
  const selectedEntry = tree.data.entries.find((entry) => entry.entry_id === selected);
  const selectedLane = selectedEntry ? built.laneOf.get(selectedEntry.entry_id) : null;
  const selectedSummary = selectedEntry ? summaryFor(selectedEntry, messages) : null;
  const children = selectedEntry ? childCount(tree.data.entries, selectedEntry.entry_id) : 0;
  const busy = status.turn_running || status.command_running;

  return (
    <div className="wb-chat-main">
      <Header chatLabel={status.chat || 'main'} running={running} entries={entries} lines={lines} active={route.page} go={go} />
      <div className="wb-tree">
        <div className="wb-tree__graph">
          <div className="wb-tree__title">
            <span className="page-title">Conversation tree</span>
            <span className="wb-page-subtitle">
              chat {status.chat || 'main'} · {entries} entries · {lines} lines · oldest at top
            </span>
            <a
              href="#/chat"
              className="wb-tree__back"
              onClick={(event) => {
                event.preventDefault();
                go('chat');
              }}
            >
              ← back to chat
            </a>
          </div>
          <div className="wb-tree__legend">
            {Array.from({length: built.laneCount}, (_, lane) => {
              const tip = built.laneTip.get(lane);
              const state = lane === headLane ? 'head' : tip && isAbandon(tip) ? 'abandoned' : 'open';
              return (
                <span key={lane} className="wb-tree__legend-item">
                  <span className="wb-tree__legend-dot" style={{background: laneColor(lane)}} />
                  {tip?.entry_id ?? `lane ${lane}`}
                  <span className="wb-tree__legend-state" style={{color: laneColor(lane)}}>
                    · {state}
                    {state === 'head' && status.turn_running ? ' · turn running' : ''}
                  </span>
                </span>
              );
            })}
            <span className="wb-tree__legend-key">● entry · ⑂ fork · × abandoned · ▶ head</span>
          </div>
          <div className="wb-tree__rows-wrap">
            <svg width={svgWidth} height={svgHeight} className="wb-tree__svg">
              {paths}
              {dots}
            </svg>
            <div className="wb-tree__rows">
              {built.rows.map(({entry, row}) => (
                <div
                  key={entry.entry_id}
                  className={entry.entry_id === selected ? 'wb-tree__row wb-tree__row--selected' : 'wb-tree__row'}
                  style={{paddingLeft: svgWidth, height: ROW}}
                  onClick={() => setSelectedId(entry.entry_id)}
                >
                  <span className="wb-tree__row-id">{entry.entry_id}</span>
                  <span className="wb-tree__row-kind">{entry.type}</span>
                  <span className="wb-tree__row-text">{summaryFor(entry, messages) ?? <Absent kind="unreported" />}</span>
                  <span className="wb-tree__row-when">
                    <Absent kind="unreported" />
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="panel__note">
            Every entry is addressable. Resuming from an entry starts a new line there; nothing above it is
            rewritten. Abandoned lines keep their spend and their lesson.
          </div>
        </div>

        <div className="wb-tree__detail">
          {selectedEntry ? (
            <>
              <div className="wb-tree__detail-path">
                Tree › <span className="wb-tree__detail-id">{selectedEntry.entry_id}</span> · lane{' '}
                {orAbsent(selectedLane)} · <Absent kind="unreported" />
              </div>
              <div className="wb-tree__detail-head">
                <span className="wb-tree__detail-kind">{selectedEntry.type}</span>
                <span>{selectedSummary || <Absent kind="unreported" />}</span>
              </div>
              <pre className="wb-tree__detail-body">{selectedSummary || <Absent kind="unreported" />}</pre>
              <div className="wb-tree__detail-facts">
                <span className="wb-tree__detail-facts-head section-label">entry facts</span>
                <Facts
                  rows={[
                    ['line', orAbsent(selectedLane)],
                    ['model', <Absent key="model" kind="unreported" />],
                    ['tokens', <Absent key="tokens" kind="unreported" />],
                    ['context after', <Absent key="ctx" kind="unreported" />],
                    ['§ effects', <Absent key="fx" kind="unreported" />],
                    ['children', children],
                  ]}
                />
              </div>
              <div className="wb-tree__detail-actions">
                <button type="button" className="button" disabled title={RESUME_NOTE}>
                  Resume from here…
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    // A line card, not an act: the prototype's own copy says
                    // "fork raise a line card in the composer", and the
                    // button's own label ends in an ellipsis, the design's
                    // convention for "opens something" rather than "does it
                    // now". Sending it here, unreviewed, would also make the
                    // resulting `/fork e-...` transcript line indistinguishable
                    // from one the user typed themselves -- exactly the
                    // synthesized-command problem Task 11 ruled against for
                    // `/project switch`. Navigating to Chat is not the
                    // prototype's own move, but is the only way to make the
                    // populated draft visible: the composer this line lands
                    // in is the one mounted (hidden) inside Chat's dock,
                    // never drawn on the Tree route itself.
                    setDraft(`/fork ${selectedEntry.entry_id}`);
                    go('chat');
                  }}
                >
                  Fork a line here…
                </button>
                <button type="button" className="button" disabled title={COMPARE_NOTE}>
                  Compare with head…
                </button>
                <a
                  href="#/chat"
                  className="button"
                  onClick={(event) => {
                    event.preventDefault();
                    go('chat');
                  }}
                >
                  Open in chat
                </a>
              </div>
              <div className="panel__note">
                Fork puts <span style={{fontFamily: 'var(--mono)', color: 'var(--fg)'}}>/fork {selectedEntry.entry_id}</span> in
                the composer for review; submitting it is refused while a turn is running on that line. No{' '}
                <code>/resume</code> command or <code>/fork --name</code> flag exists in the registry, so Resume
                and Compare are shown disabled rather than raising a command that does not exist.
              </div>
              {busy && refusal ? <div className="wb-tree__detail-refusal">{refusal}</div> : null}
              {isAbandon(selectedEntry) ? (
                <div className="wb-tree__detail-lesson">
                  <span className="section-label">what this line taught · recorded at /abandon</span>
                  <div className="wb-tree__detail-lesson-text">{selectedEntry.lesson}</div>
                </div>
              ) : null}
            </>
          ) : (
            <div className="panel__note">No entry selected.</div>
          )}
        </div>
      </div>
    </div>
  );
}
