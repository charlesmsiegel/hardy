// Where every page sits in the sidebar: one list, grouped by what a person is
// doing there rather than by which task built it. It replaced three
// navigation systems at once -- a row of ten tabs, three top-bar links, and a
// Tree page reachable only from inside Chat -- so the one rule it keeps is
// that every page has exactly one entry here (`navigation.test.mjs` checks it
// against `pages/index.js`).
//
// Data only, no components: the sidebar draws it, and anything else that has
// to name every page reads the same list rather than keeping its own.
//
// The glyphs are Unicode, never an icon font (the page runs under
// `font-src 'self'`); they are what the collapsed rail shows, with the label
// as each button's name and tooltip.
//
// `workflows` is listed before its page exists. `Sidebar.jsx` draws only the
// entries `PAGES` can answer, so it stays out of sight until the page lands
// rather than leading to "unknown route".

export const NAV_GROUPS = [
  {
    id: 'project',
    label: 'Project',
    items: [
      {page: 'home', label: 'Home', glyph: '⌂'},
      {page: 'chat', label: 'Chat', glyph: '✎'},
      {page: 'tree', label: 'Tree', glyph: '⑂'},
    ],
  },
  {
    id: 'mathematics',
    label: 'Mathematics',
    items: [
      {page: 'ledger', label: 'Ledger', glyph: '⊢'},
      {page: 'workflows', label: 'Workflows', glyph: '▶'},
      {page: 'results', label: 'Results', glyph: '∴'},
      {page: 'publications', label: 'Publications', glyph: '❡'},
    ],
  },
  {
    id: 'execution',
    label: 'Execution',
    items: [
      {page: 'jobs', label: 'Jobs', glyph: '⚙'},
      {page: 'runs', label: 'Runs', glyph: '⟳'},
      {page: 'checkpoints', label: 'Checkpoints', glyph: '⚑'},
    ],
  },
  {
    id: 'sources',
    label: 'Sources',
    items: [
      {page: 'library', label: 'Library', glyph: '❧'},
      {page: 'files', label: 'Files', glyph: '▤'},
    ],
  },
  {
    id: 'session',
    label: 'Session',
    items: [
      {page: 'status', label: 'Status', glyph: '◉'},
      {page: 'environment', label: 'Environment', glyph: '⌁'},
      {page: 'help', label: 'Help', glyph: '?'},
    ],
  },
];

/** Every page the sidebar lists, in sidebar order. */
export function navPages() {
  return NAV_GROUPS.flatMap((group) => group.items.map((item) => item.page));
}
