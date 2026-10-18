// Exercise the actual provider, frame and mounted Chat. Only the HTTP/event
// transport is replaced: a palette choice must edit a visible composer and
// never call the server's input route until the person explicitly sends.
import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import React, {act} from 'react';

const dom = new JSDOM('<!doctype html><div id="root"></div>', {url: 'http://localhost/#/home'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const {createRoot} = await import('react-dom/client');
const server = await createServer({server: {middlewareMode: true, ws: false}});
const {default: Shell} = await server.ssrLoadModule('/src/workbench/Shell.jsx');
const {default: SessionProvider} = await server.ssrLoadModule('/src/session/SessionProvider.jsx');
const {default: useSession} = await server.ssrLoadModule('/src/session/useSession.js');
const {PAGES} = await server.ssrLoadModule('/src/pages/index.js');
const {ConfirmCard} = await server.ssrLoadModule('/src/components/Cards.jsx');
const home = PAGES.home;
const tree = PAGES.tree;
const root = createRoot(document.getElementById('root'));
const STATUS = {open: true, slug: 'demo', root: 'demo', path: '/p/demo', chat: 'main', prompts: [], turn_running: false};
let current;
let stream;
let requests;
let dismissed;
let readChats;
let allowChatOpen;
const previousEventSource = globalThis.EventSource;
globalThis.EventSource = class {
  constructor() { stream = this; }
  close() {}
};
function Controls() {
  current = useSession();
  return React.createElement(React.Fragment, null,
    React.createElement('input', {'aria-label': 'file editor'}),
    React.createElement(ConfirmCard, {command: '/reset', title: 'Confirm', onNo: () => { dismissed += 1; }, onYes: () => { throw new Error('confirmation unexpectedly submitted'); }}),
  );
}
const key = async (node, value, extra = {}) => {
  const event = new window.KeyboardEvent('keydown', {key: value, bubbles: true, cancelable: true, ...extra});
  await act(async () => { node.dispatchEvent(event); });
  return event;
};
const navigate = (hash) => act(async () => {
  window.history.replaceState(null, '', hash);
  window.dispatchEvent(new window.HashChangeEvent('hashchange'));
});
const dialog = () => document.querySelector('[role="dialog"]');

test.beforeEach(async (t) => {
  requests = [];
  dismissed = 0;
  readChats = () => [{id: 'main', title: 'Main discussion'}];
  allowChatOpen = false;
  PAGES.home = Controls;
  t.mock.method(globalThis, 'fetch', async (path, options) => {
    requests.push([path, options]);
    if (allowChatOpen && path === '/api/open' && options.method === 'POST') {
      return {ok: true, text: async () => '{}'};
    }
    assert.equal(options.method, 'GET', 'palette and compose never post');
    if (path === '/api/chats') {
      const answer = readChats();
      return {ok: true, text: async () => JSON.stringify(await answer)};
    }
    const data = {
      '/api/state': STATUS, '/api/transcript': [], '/api/projects': [],
      '/api/commands': [{name: 'model', summary: 'switch the model', safe_in_flight: false}],
      '/api/jobs': {attention: []},
      '/api/summary': {goal: ''}, '/api/tree': {entries: []}, '/api/models': {backend: 'fake', rows: []},
      '/api/graph': {nodes: [], edges: []},
    };
    assert.ok(path in data, `unexpected endpoint ${path}`);
    return {ok: true, text: async () => JSON.stringify(data[path])};
  });
  await navigate('#/home');
  await act(async () => root.render(React.createElement(SessionProvider, null, React.createElement(Shell))));
  assert.ok(document.querySelector('.composer__box'), 'real Chat has loaded');
});
test.afterEach(async () => { await act(() => root.render(null)); PAGES.home = home; PAGES.tree = tree; });
test.after(async () => { await act(() => root.unmount()); await server.close(); dom.window.close(); globalThis.EventSource = previousEventSource; });

test('Ctrl/Cmd+K opens from composer, editor and confirmation; Escape restores each focus without dismissing the card', async () => {
  const nodes = [document.querySelector('.composer__box'), document.querySelector('[aria-label="file editor"]'), [...document.querySelectorAll('.card-shell button')].find((node) => node.textContent === 'No')];
  for (const node of nodes) {
    for (const modifier of ['ctrlKey', 'metaKey']) {
      node.focus();
      await key(node, 'k');
      assert.equal(Boolean(dialog()), false, 'ordinary typing never opens the palette');
      const opening = await key(node, 'k', {[modifier]: true});
      assert.equal(opening.defaultPrevented, true);
      assert.ok(dialog());
      await key(document.activeElement, 'Escape');
      assert.equal(Boolean(dialog()), false);
      assert.equal(document.activeElement === node, true);
    }
  }
  assert.equal(dismissed, 0);
});

test('the palette toggles and closes on route, sub-route and project changes', async () => {
  const open = () => key(document.activeElement, 'k', {ctrlKey: true});
  await open();
  assert.ok(dialog());
  await open();
  assert.equal(Boolean(dialog()), false);
  await open();
  await navigate('#/help');
  assert.equal(Boolean(dialog()), false);
  await open();
  await navigate('#/help/another');
  assert.equal(Boolean(dialog()), false);
  await open();
  await act(async () => stream.onmessage({data: JSON.stringify({type: 'state', ...STATUS, path: '/p/other'})}));
  assert.equal(Boolean(dialog()), false);
});

test('compose reveals Chat when the dock is hidden, focuses its labelled box and never sends', async () => {
  const cycle = () => document.querySelector('.wb-sidebar button[aria-label^="chat:"]');
  await act(() => cycle().click());
  await act(() => cycle().click());
  assert.equal(document.querySelector('.wb-dock').style.display, 'none');
  const before = requests.length;
  await act(() => current.compose('/model '));
  await navigate(window.location.hash);
  assert.equal(window.location.hash, '#/chat');
  const composer = document.querySelector('.composer__box');
  assert.equal(composer.value, '/model ');
  assert.equal(document.activeElement === composer, true);
  assert.ok(composer.labels.length, 'the composer has a visible label');
  assert.equal(requests.slice(before).some(([, options]) => options.method !== 'GET'), false);
});

test('a palette command only composes and focuses even when the same draft was composed before', async () => {
  await key(document.activeElement, 'k', {ctrlKey: true});
  const command = [...document.querySelectorAll('[role="option"]')].find((node) => node.textContent.includes('/model'));
  assert.ok(command);
  await act(() => command.click());
  assert.equal(Boolean(dialog()), false);
  const composer = document.querySelector('.composer__box');
  assert.equal(composer.value, '/model ');
  assert.equal(document.activeElement === composer, true);
  document.querySelector('[aria-label="file editor"]').focus();
  await act(() => current.compose('/model '));
  assert.equal(document.activeElement === composer, true, 'an identical draft still requests focus');
  assert.equal(current.messages.length, 0);
  const help = document.querySelector('.wb-sidebar [aria-label="Help"]');
  help.focus();
  await navigate('#/help');
  assert.equal(document.activeElement === help, true, 'a fulfilled focus request does not steal focus on later navigation');
});

test('a compose focus request retries when the same mounted box moves from hidden Tree to visible Chat', async (t) => {
  PAGES.tree = Controls;
  // jsdom has no layout and permits focusing display:none elements. Model
  // that browser restriction at the focus operation, leaving Chat real.
  const focus = window.HTMLTextAreaElement.prototype.focus;
  t.mock.method(window.HTMLTextAreaElement.prototype, 'focus', function (...args) {
    if (this.closest('.wb-dock')?.style.display !== 'none') focus.apply(this, args);
  });
  await navigate('#/tree');
  const composer = document.querySelector('.composer__box');
  assert.equal(document.querySelector('.wb-dock').style.display, 'none');
  await act(async () => current.compose('/model '));
  await navigate(window.location.hash);
  assert.equal(window.location.hash, '#/chat');
  assert.equal(document.querySelector('.composer__box') === composer, true, 'Tree and Chat share this Composer mount');
  assert.equal(document.activeElement === composer, true);
});

test('palette chats retain same-project refresh data but omit another project until its response arrives', async () => {
  const change = async (path, changed = true) => act(async () => {
    stream.onmessage({data: JSON.stringify({type: 'state', ...STATUS, path})});
    if (changed) stream.onmessage({data: JSON.stringify({type: 'changed'})});
  });
  const options = () => [...document.querySelectorAll('[role="option"]')].map((node) => node.textContent);
  readChats = () => [{id: 'old-id', title: 'First project only'}];
  await change('/p/first');
  await key(document.activeElement, 'k', {ctrlKey: true});
  assert.ok(options().some((text) => text.includes('First project only')));

  let finishRefresh;
  const refresh = new Promise((resolve) => { finishRefresh = resolve; });
  readChats = () => refresh;
  await act(async () => stream.onmessage({data: JSON.stringify({type: 'changed'})}));
  assert.ok(options().some((text) => text.includes('First project only')), 'refreshing within one project keeps its loaded choices');
  await act(async () => finishRefresh([{id: 'old-id', title: 'First project refreshed'}]));
  assert.ok(options().some((text) => text.includes('First project refreshed')));

  let finishSecond;
  const second = new Promise((resolve) => { finishSecond = resolve; });
  readChats = () => second;
  const readsBeforeSwitch = requests.filter(([path]) => path === '/api/chats').length;
  await change('/p/second', false);
  assert.equal(requests.filter(([path]) => path === '/api/chats').length, readsBeforeSwitch + 1,
    'the new owner reads even without a revision event, and Shell and Chat share that request');
  assert.equal(Boolean(dialog()), false, 'the project switch closes the old dialog');
  await key(document.activeElement, 'k', {ctrlKey: true});
  assert.equal(options().some((text) => text.includes('First project')), false, 'reopening cannot offer the previous project\'s chat');
  assert.equal(options().some((text) => text.includes('Second project only')), false, 'the new project has not answered yet');
  await act(async () => finishSecond([{id: 'new-id', title: 'Second project only'}]));
  assert.equal(options().some((text) => text.includes('First project')), false);
  assert.ok(options().some((text) => text.includes('Second project only')));
  allowChatOpen = true;
  const selected = [...document.querySelectorAll('[role="option"]')].find((node) => node.textContent.includes('Second project only'));
  await act(async () => selected.click());
  await navigate(window.location.hash);
  const opened = requests.filter(([path]) => path === '/api/open');
  assert.equal(opened.length, 1);
  assert.deepEqual(JSON.parse(opened[0][1].body), {path: '/p/second', chat: 'new-id'});
});

test('a late chat response from a previous project cannot replace the current choices', async () => {
  let finishFirst;
  let finishSecond;
  const first = new Promise((resolve) => { finishFirst = resolve; });
  const second = new Promise((resolve) => { finishSecond = resolve; });
  readChats = () => first;
  await act(async () => stream.onmessage({data: JSON.stringify({type: 'state', ...STATUS, path: '/p/late-first'})}));
  readChats = () => second;
  await act(async () => stream.onmessage({data: JSON.stringify({type: 'state', ...STATUS, path: '/p/late-second'})}));
  await act(async () => finishSecond([{id: 'new-id', title: 'Second project current'}]));
  await key(document.activeElement, 'k', {ctrlKey: true});
  assert.match(dialog().textContent, /Second project current/);
  await act(async () => finishFirst([{id: 'old-id', title: 'First project late'}]));
  assert.match(dialog().textContent, /Second project current/);
  assert.doesNotMatch(dialog().textContent, /First project late/);
});
