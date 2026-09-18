// The ten tabs a project is organised into, plus the dock toggle -- the
// prototype draws the toggle at the right end of this row rather than in the
// top bar, so it stays beside the thing it controls.
//
// `Tree` is reachable only from inside the Chat page (a transcript/tree
// toggle Task 13 builds), so it has no tab of its own; the `Chat` tab is
// drawn "on" for it too, which is the one exception to "highlight the active
// route" below.

import Pill from '../components/Pill.jsx';

const TABS = [
  ['home', 'Home'],
  ['chat', 'Chat ●'],
  ['results', 'Results'],
  ['ledger', 'Ledger'],
  ['jobs', 'Jobs'],
  ['runs', 'Runs'],
  ['publications', 'Publications'],
  ['library', 'Library'],
  ['files', 'Files'],
  ['checkpoints', 'Checkpoints'],
];

//: `pinned -> strip`, `strip -> hidden`, `hidden -> pinned` -- one cycle, no
//: state the label does not also name, so the button always says what
//: pressing it does next rather than what it is now.
const NEXT = {pinned: 'strip', strip: 'hidden', hidden: 'pinned'};
const LABEL = {
  pinned: 'chat: pinned ▸ collapse to strip',
  strip: 'chat: strip ▸ pin',
  hidden: 'chat: hidden ▸ show',
};

export default function TabBar({route, go, dock, onToggleDock, jobsAttention, disabled = false}) {
  const isChat = route.page === 'chat';

  return (
    <div className={disabled ? 'wb-tabbar wb-tabbar--disabled' : 'wb-tabbar'}>
      {TABS.map(([id, label]) => {
        // Nothing lit while nothing is open: every tab is a page about a
        // project, and there is none. The buttons stay, greyed, so the
        // layout does not jump when one is opened.
        const on = !disabled && (id === route.page || (id === 'chat' && route.page === 'tree'));
        return (
          <button
            key={id}
            type="button"
            className={on ? 'wb-tab wb-tab--on' : 'wb-tab'}
            disabled={disabled}
            title={disabled ? 'open a project first' : undefined}
            onClick={() => go(id)}
          >
            {label}
            {id === 'jobs' && jobsAttention ? <Pill tone="error">{jobsAttention}</Pill> : null}
          </button>
        );
      })}
      {/* Toggling the dock on the Chat page itself would have nothing to show
          for it -- Chat is already full width there -- so the button is not
          drawn rather than drawn to do nothing. */}
      {isChat || disabled ? null : (
        <button type="button" className="wb-dock-toggle" onClick={() => onToggleDock(NEXT[dock])}>
          {LABEL[dock]}
        </button>
      )}
    </div>
  );
}
