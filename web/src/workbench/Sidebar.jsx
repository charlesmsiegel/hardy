// The workbench's one navigation: every page in `navigation.js`'s groups, a
// count on the entries something is waiting on, and at the foot the two
// pieces of chrome the old tab bar carried -- the chat dock's cycle and this
// rail's own collapse.
//
// Stateless on purpose, like the tab bar it replaced: which page is current
// lives in the hash, the counts come from reads `Shell` already makes, and
// whether the rail is collapsed is `Shell`'s chrome state beside `dock`.
//
// Collapsing happens in two ways that must draw the same: the user's choice
// (`collapsed`, a class here) and a viewport under 1100px (a media query in
// `styles.css`, which this component never hears about). So the DOM does not
// change shape between them -- both only narrow the rail, and a container
// query on the rail's own width hides the words. That is also why every
// button carries its name in `aria-label` and its label in `title`: the
// visible words can disappear without a render, and the name and tooltip
// must not go with them.

import {PAGES} from '../pages/index.js';
import {NAV_GROUPS} from './navigation.js';

//: The entries that lead somewhere: one whose page is not registered yet
//: (Workflows, until it lands) is left out rather than drawn as a way to
//: "unknown route". A group left with nothing is left out with it.
const GROUPS = NAV_GROUPS.map((group) => ({...group, items: group.items.filter((item) => item.page in PAGES)})).filter(
  (group) => group.items.length > 0,
);

//: What each count counts, for the name a screen reader reads -- the digit
//: on its own says how many but not of what. `attention` is the count that
//: means "act on this", drawn in the error tone as the old tab bar's red pill
//: was, so it is not read as "selected"; a card waiting for an answer is a
//: count of things in the chat and keeps the accent.
const COUNTED = {
  jobs: {what: 'need attention', attention: true},
  chat: {what: 'waiting for an answer', attention: false},
};

//: `pinned -> strip`, `strip -> hidden`, `hidden -> pinned` -- one cycle, no
//: state the label does not also name, so the button always says what
//: pressing it does next rather than what it is now.
const NEXT = {pinned: 'strip', strip: 'hidden', hidden: 'pinned'};
const DOCK_LABEL = {
  pinned: 'chat: pinned ▸ collapse to strip',
  strip: 'chat: strip ▸ pin',
  hidden: 'chat: hidden ▸ show',
};

function Entry({item, current, count, disabled, go}) {
  const counted = COUNTED[item.page];
  const name = count ? `${item.label}, ${count} ${counted?.what ?? ''}`.trim() : item.label;
  return (
    <li>
      <button
        type="button"
        className="wb-nav__item"
        aria-current={current ? 'page' : undefined}
        aria-label={name}
        title={disabled ? `${item.label} · open a project first` : item.label}
        disabled={disabled}
        onClick={() => go(item.page)}
      >
        <span className="wb-nav__glyph" aria-hidden="true">
          {item.glyph}
        </span>
        <span className="wb-nav__label">{item.label}</span>
        {count ? (
          <span className={counted?.attention ? 'wb-nav__badge wb-nav__badge--attention' : 'wb-nav__badge'}>{count}</span>
        ) : null}
      </button>
    </li>
  );
}

export default function Sidebar({route, go, badges, collapsed, onToggleCollapsed, dock, onToggleDock, disabled = false}) {
  // On Chat the chat is already the whole page, and on Tree the dock is not
  // drawn at all, so cycling it there would change nothing on screen; the
  // button is left out rather than drawn to do nothing.
  const dockless = disabled || route.page === 'chat' || route.page === 'tree';
  const collapseLabel = collapsed ? 'expand sidebar' : 'collapse sidebar';

  return (
    <nav aria-label="Workbench" className={collapsed ? 'wb-sidebar wb-sidebar--collapsed' : 'wb-sidebar'}>
      {GROUPS.map((group) => (
        <div key={group.id} className="wb-nav__group">
          <span id={`wb-nav-${group.id}`} className="section-label wb-nav__heading">
            {group.label}
          </span>
          <ul className="wb-nav__list" aria-labelledby={`wb-nav-${group.id}`}>
            {group.items.map((item) => (
              <Entry
                key={item.page}
                item={item}
                // Nothing lit and nothing counted while nothing is open:
                // every entry is a page about a project, and there is none.
                // The buttons stay, greyed, so the layout does not jump when
                // one is opened.
                current={!disabled && item.page === route.page}
                count={disabled ? 0 : badges[item.page] || 0}
                disabled={disabled}
                go={go}
              />
            ))}
          </ul>
        </div>
      ))}
      <div className="wb-sidebar__foot">
        {dockless ? null : (
          <button
            type="button"
            className="wb-nav__item"
            aria-label={DOCK_LABEL[dock]}
            title={DOCK_LABEL[dock]}
            onClick={() => onToggleDock(NEXT[dock])}
          >
            <span className="wb-nav__glyph" aria-hidden="true">
              ◨
            </span>
            <span className="wb-nav__label">{DOCK_LABEL[dock]}</span>
          </button>
        )}
        <button
          type="button"
          className="wb-nav__item wb-sidebar__collapse"
          aria-label={collapseLabel}
          title={collapseLabel}
          onClick={() => onToggleCollapsed(!collapsed)}
        >
          <span className="wb-nav__glyph" aria-hidden="true">
            {collapsed ? '»' : '«'}
          </span>
          <span className="wb-nav__label">{collapseLabel}</span>
        </button>
      </div>
    </nav>
  );
}
