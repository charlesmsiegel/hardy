// A page exception must leave a way out: the message and retry belong in
// the page, while navigation, Stop and the one mounted Chat stay usable.
// The Shell cases exercise that placement, beyond the boundary's own reset.
import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import React, {act, useEffect} from 'react';

const dom = new JSDOM('<!doctype html><div id="root"></div>', {url: 'http://localhost/'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const {createRoot} = await import('react-dom/client');
const server = await createServer({server: {middlewareMode: true, ws: false}});
const {default: Shell} = await server.ssrLoadModule('/src/workbench/Shell.jsx');
const {SessionContext} = await server.ssrLoadModule('/src/session/SessionProvider.jsx');
const {PAGES} = await server.ssrLoadModule('/src/pages/index.js');
const root = createRoot(document.getElementById('root'));
const originalPages = {...PAGES};
const noop = () => {};
const STATUS = {open: true, root: 'demo', path: '/p/demo', chat: 'first', turn_running: true, queued: 0};

const draw = (node) => act(() => root.render(node));
const navigate = (hash) => act(() => {
  window.history.replaceState(null, '', hash);
  window.dispatchEvent(new window.HashChangeEvent('hashchange'));
});
const shell = (status = STATUS, cancel = noop) => draw(
  React.createElement(SessionContext.Provider, {value: {
    status, cancel, runningTool: '', prompts: [], refusal: '', projects: [],
    setProjects: noop, refreshProjects: noop, revision: 0, loaded: true, messages: [],
  }}, React.createElement(Shell)),
);
function Broken() {
  throw new Error('malformed page payload');
}
function Page({arg}) {
  return arg === 'bad' ? React.createElement(Broken) : React.createElement('p', null, 'page recovered');
}

test.beforeEach((t) => {
  // The chrome's reads; these child pages make no requests of their own.
  t.mock.method(globalThis, 'fetch', async (path) => {
    assert.ok(['/api/jobs', '/api/chats'].includes(path));
    return {ok: true, text: async () => path === '/api/chats' ? '[]' : '{"attention":[]}'};
  });
  // React reports the deliberately thrown errors; they are asserted in the DOM.
  t.mock.method(console, 'error', noop);
});
test.afterEach(async () => {
  await draw(null);
  Object.assign(PAGES, originalPages);
});
test.after(async () => {
  await act(() => root.unmount());
  await server.close();
  dom.window.close();
});

test('a page error preserves the chrome, live Stop and the mounted Chat dock', async () => {
  let mounts = 0;
  function Chat() {
    useEffect(() => { mounts += 1; }, []);
    return React.createElement('input', {defaultValue: 'unsent draft', 'aria-label': 'chat draft'});
  }
  PAGES.home = Page;
  PAGES.help = Page;
  PAGES.chat = Chat;
  await navigate('#/home');
  let stops = 0;
  await shell(STATUS, () => { stops += 1; });
  const frame = ['.wb-topbar', '.wb-sidebar', '.wb-strip', '.wb-dock'].map((selector) => document.querySelector(selector));
  const draft = document.querySelector('[aria-label="chat draft"]');
  draft.value = 'keep my draft';

  await navigate('#/home/bad');
  assert.match(document.querySelector('.wb-page [role="alert"]')?.textContent || '', /malformed page payload/);
  ['.wb-topbar', '.wb-sidebar', '.wb-strip', '.wb-dock'].forEach((selector, i) => {
    assert.ok(frame[i], `${selector} exists`);
    assert.equal(document.querySelector(selector) === frame[i], true, `${selector} stays mounted`);
  });
  assert.equal(document.querySelector('[aria-label="chat draft"]') === draft, true);
  assert.equal(draft.value, 'keep my draft');
  assert.equal(mounts, 1);
  const stop = [...document.querySelectorAll('.wb-topbar button')].find((button) => button.textContent === 'Stop');
  await act(() => stop.click());
  assert.equal(stops, 1, 'Stop still reaches the session');

  await act(() => document.querySelector('.wb-sidebar [aria-label="Help"]').click());
  await navigate(window.location.hash);
  assert.match(document.querySelector('.wb-page').textContent, /page recovered/);
  assert.equal(document.querySelectorAll('.wb-page [role="alert"]').length, 0);
  assert.equal(mounts, 1, 'navigating away from the error does not remount Chat');
});

test('a healthy sub-route or project can recover from a failed page', async () => {
  PAGES.home = Page;
  PAGES.chat = () => React.createElement('p', null, 'chat');
  await navigate('#/home/bad');
  await shell();
  assert.ok(document.querySelector('.wb-page [role="alert"]'));
  await navigate('#/home/good');
  assert.match(document.querySelector('.wb-page').textContent, /page recovered/);

  await navigate('#/home/bad');
  assert.ok(document.querySelector('.wb-page [role="alert"]'));
  PAGES.home = () => React.createElement('p', null, 'new project');
  await shell({...STATUS, path: '/p/another'});
  assert.match(document.querySelector('.wb-page').textContent, /new project/);
});

test('a Chat error stays in its own dock while another page and navigation work', async () => {
  PAGES.home = Page;
  PAGES.help = Page;
  PAGES.chat = Broken;
  await navigate('#/home');
  await shell();
  assert.match(document.querySelector('.wb-dock [role="alert"]')?.textContent || '', /malformed page payload/);
  assert.match(document.querySelector('.wb-page').textContent, /page recovered/);
  await act(() => document.querySelector('.wb-sidebar [aria-label="Help"]').click());
  await navigate(window.location.hash);
  assert.equal(window.location.hash, '#/help');
  assert.match(document.querySelector('.wb-page').textContent, /page recovered/);
});

test('an unregistered Chat page uses the unknown-route fallback', async () => {
  delete PAGES.chat;
  await navigate('#/chat');
  await shell();
  assert.match(document.querySelector('.wb-dock').textContent, /unknown route/);
});

test('a boundary reports only the message and resets when its key changes', async () => {
  const {default: ErrorBoundary} = await server.ssrLoadModule('/src/components/ErrorBoundary.jsx');
  const boundary = (key, child) => React.createElement(ErrorBoundary, {resetKey: key}, child);
  await draw(boundary('first', React.createElement(Broken)));
  const alert = document.querySelector('[role="alert"]');
  assert.equal(alert?.textContent, 'This page failed to draw: malformed page payload');
  assert.ok(alert.classList.contains('panel__error'));
  assert.equal(document.querySelectorAll('pre').length, 0, 'no stack is drawn');
  await draw(boundary('second', React.createElement('p', null, 'healthy child')));
  assert.match(document.body.textContent, /healthy child/);
  assert.equal(document.querySelectorAll('[role="alert"]').length, 0);
});

test('Reload page retries the child without changing the route', async () => {
  const {default: ErrorBoundary} = await server.ssrLoadModule('/src/components/ErrorBoundary.jsx');
  let broken = true;
  function SometimesBroken() {
    return broken ? React.createElement(Broken) : React.createElement('p', null, 'retry worked');
  }
  await draw(React.createElement(ErrorBoundary, {resetKey: 'same'}, React.createElement(SometimesBroken)));
  const retry = [...document.querySelectorAll('button')].find((button) => button.textContent === 'Reload page');
  assert.ok(retry?.classList.contains('button'));
  broken = false;
  await act(() => retry.click());
  assert.match(document.body.textContent, /retry worked/);
  assert.equal(document.querySelectorAll('[role="alert"]').length, 0);
});
