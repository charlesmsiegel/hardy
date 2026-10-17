// The sidebar is the one way to every page, so two things are checked here
// against the real modules rather than a copy of their lists: that the
// navigation model and the page registry agree entry for entry, and that the
// rendered sidebar says which page is current and what needs attention in
// the DOM a screen reader reads. Rendered in jsdom for the second, because
// "the Jobs entry is current" is a claim about attributes on a button.
import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import React, {act} from 'react';

const dom = new JSDOM('<!doctype html><div id="root"></div>', {url: 'http://localhost/'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const {createRoot} = await import('react-dom/client');
const server = await createServer({server: {middlewareMode: true, ws: false}});
const {NAV_GROUPS, navPages} = await server.ssrLoadModule('/src/workbench/navigation.js');
const {PAGES} = await server.ssrLoadModule('/src/pages/index.js');
const {default: Sidebar} = await server.ssrLoadModule('/src/workbench/Sidebar.jsx');
const root = createRoot(document.getElementById('root'));

test.after(async () => {
  await act(() => root.unmount());
  await server.close();
  dom.window.close();
});

const noop = () => {};
const draw = (props) =>
  act(() =>
    root.render(
      React.createElement(Sidebar, {
        route: {page: 'home', arg: ''},
        go: noop,
        badges: {},
        collapsed: false,
        onToggleCollapsed: noop,
        dock: 'pinned',
        onToggleDock: noop,
        ...props,
      }),
    ),
  );
/** The page entries, not the footer's dock and collapse toggles. */
const entries = () => [...document.querySelectorAll('nav[aria-label="Workbench"] li > button')];
/** The footer's buttons: the dock's cycle when it is drawn, then the collapse. */
const footer = () => [...document.querySelectorAll('nav[aria-label="Workbench"] .wb-sidebar__foot > button')];
const dockButton = () => footer().find((node) => !node.classList.contains('wb-sidebar__collapse'));
const collapseButton = () => footer().find((node) => node.classList.contains('wb-sidebar__collapse'));
const entry = (label) => entries().find((node) => node.querySelector('.wb-nav__label')?.textContent === label);

test('every page has exactly one entry and every entry a page', () => {
  const pages = navPages();
  assert.equal(new Set(pages).size, pages.length, `duplicated entries: ${pages.filter((page, at) => pages.indexOf(page) !== at)}`);
  const missing = Object.keys(PAGES).filter((page) => !pages.includes(page));
  assert.deepEqual(missing, [], 'every registered page has a sidebar entry');
  // `workflows` is listed before its page lands (Task 8); nothing else may be.
  const orphans = pages.filter((page) => !(page in PAGES) && page !== 'workflows');
  assert.deepEqual(orphans, [], 'every sidebar entry names a registered page');
});

test('sidebar marks the current page and shows badges', async () => {
  const went = [];
  await draw({route: {page: 'jobs', arg: ''}, go: (page) => went.push(page), badges: {jobs: 2}});
  const jobs = entry('Jobs');
  assert.ok(jobs, 'the Jobs entry is drawn');
  assert.equal(jobs.getAttribute('aria-current'), 'page');
  assert.equal(jobs.querySelector('.wb-nav__badge')?.textContent, '2');
  assert.deepEqual(
    entries().filter((node) => node.hasAttribute('aria-current')),
    [jobs],
    'only the current page is marked',
  );
  assert.equal(entry('Chat').querySelector('.wb-nav__badge'), null, 'no count, no badge');

  await act(() => entry('Ledger').click());
  assert.deepEqual(went, ['ledger']);

  await draw({disabled: true, badges: {jobs: 2}});
  assert.ok(entries().length > 0);
  for (const node of entries()) {
    assert.equal(node.tagName, 'BUTTON');
    assert.equal(node.disabled, true, `${node.textContent} is disabled while nothing is open`);
    assert.equal(node.hasAttribute('aria-current'), false, 'nothing is lit while nothing is open');
  }
});

test('Tree lights Tree and Chat lights Chat', async () => {
  await draw({route: {page: 'tree', arg: ''}});
  assert.equal(entry('Tree').getAttribute('aria-current'), 'page');
  assert.equal(entry('Chat').hasAttribute('aria-current'), false);
  await draw({route: {page: 'chat', arg: 'main'}});
  assert.equal(entry('Chat').getAttribute('aria-current'), 'page');
});

test('an entry whose page has not landed is not drawn', async () => {
  await draw({});
  const drawn = entries().map((node) => node.querySelector('.wb-nav__label').textContent);
  const built = NAV_GROUPS.flatMap((group) => group.items).filter((item) => item.page in PAGES);
  assert.deepEqual(drawn, built.map((item) => item.label));
});

test('collapsed, every entry keeps its name and a tooltip', async () => {
  await draw({collapsed: true, badges: {chat: 1}});
  assert.ok(document.querySelector('nav.wb-sidebar--collapsed'), 'the rail is drawn collapsed');
  for (const node of entries()) {
    const label = node.querySelector('.wb-nav__label').textContent;
    assert.ok(node.getAttribute('aria-label')?.startsWith(label), `${label} keeps its name`);
    assert.equal(node.title, label, `${label} has a tooltip`);
    assert.equal(node.querySelector('.wb-nav__glyph').getAttribute('aria-hidden'), 'true');
  }
  assert.match(entry('Chat').getAttribute('aria-label'), /^Chat, 1 /, 'the count is part of the name');
});

test('the Jobs count is drawn as attention and the Chat count is not', async () => {
  await draw({badges: {jobs: 3, chat: 1}});
  const jobs = entry('Jobs').querySelector('.wb-nav__badge');
  const chat = entry('Chat').querySelector('.wb-nav__badge');
  assert.ok(jobs.classList.contains('wb-nav__badge--attention'), 'Jobs says "act on this"');
  assert.equal(chat.classList.contains('wb-nav__badge--attention'), false, 'a waiting card is a count, not an alarm');
});

test('the dock button cycles pinned, strip, hidden and names what it does next', async () => {
  const chosen = [];
  for (const [dock, next] of [['pinned', 'strip'], ['strip', 'hidden'], ['hidden', 'pinned']]) {
    await draw({dock, onToggleDock: (value) => chosen.push(value)});
    const button = dockButton();
    assert.ok(button, `the dock button is drawn while the dock is ${dock}`);
    assert.ok(button.getAttribute('aria-label').startsWith(`chat: ${dock}`), `its name says the dock is ${dock}`);
    await act(() => button.click());
    assert.equal(chosen.at(-1), next, `${dock} cycles to ${next}`);
  }
  assert.equal(chosen.length, 3);
});

test('the dock button is not drawn where it would change nothing', async () => {
  await draw({route: {page: 'home', arg: ''}});
  assert.ok(dockButton(), 'drawn on an ordinary page');
  for (const page of ['chat', 'tree']) {
    await draw({route: {page, arg: ''}});
    assert.equal(Boolean(dockButton()), false, `not drawn on ${page}`);
    assert.ok(collapseButton(), `the collapse toggle stays on ${page}`);
  }
  await draw({disabled: true});
  assert.equal(Boolean(dockButton()), false, 'not drawn while nothing is open');
});

test('the collapse button asks for the opposite of what is drawn', async () => {
  const asked = [];
  await draw({collapsed: false, onToggleCollapsed: (value) => asked.push(value)});
  assert.equal(collapseButton().getAttribute('aria-label'), 'collapse sidebar');
  await act(() => collapseButton().click());
  await draw({collapsed: true, onToggleCollapsed: (value) => asked.push(value)});
  assert.equal(collapseButton().getAttribute('aria-label'), 'expand sidebar');
  await act(() => collapseButton().click());
  assert.deepEqual(asked, [true, false]);
});
