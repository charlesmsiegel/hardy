// A placeholder is not a name: it disappears on the first keystroke and
// screen readers do not reliably announce it. Every text field of the
// registry forms (Landing, the switcher's New and Add) and the `#check`
// scratch line therefore carries an `aria-label` that says what the field
// holds. Checked in jsdom against the rendered controls.
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
const {default: Landing} = await server.ssrLoadModule('/src/workbench/Landing.jsx');
const {default: ProjectSwitcher} = await server.ssrLoadModule('/src/workbench/ProjectSwitcher.jsx');
const {Scratch} = await server.ssrLoadModule('/src/files/AtCursor.jsx');
const root = createRoot(document.getElementById('root'));

test.after(async () => {
  await act(() => root.unmount());
  await server.close();
  dom.window.close();
});

const draw = (element) => act(() => root.render(element));
const click = (node) => act(() => node.click());
const named = (text) => [...document.querySelectorAll('a,button')].find((node) => node.textContent.trim() === text);
/** What a screen reader would call each text field on screen, in order. */
const names = () =>
  [...document.querySelectorAll('#root input,#root textarea,#root select')].map(
    (control) => control.getAttribute('aria-label') || [...control.labels].map((label) => label.textContent).join(' ') || null,
  );
const status = {open: false, slug: '', default_root: '/home/me/hardy'};
const projects = [];
const noop = () => {};

test('the Landing forms name their fields', async () => {
  await draw(React.createElement(Landing, {status, projects, setProjects: noop, refreshProjects: noop}));
  assert.deepEqual(names(), ['project name', 'project location', 'project folder to add']);
});

test('the switcher\'s New and Add forms name their fields', async () => {
  await draw(React.createElement(ProjectSwitcher, {status, projects, setProjects: noop, refreshProjects: noop, revision: 0}));
  await click(document.querySelector('.wb-switcher__button'));
  await click(named('New project…'));
  assert.deepEqual(names(), ['project name', 'project location']);
  await click(named('New project…'));
  await click(named('Add existing…'));
  assert.deepEqual(names(), ['project folder to add']);
});

test('the #check scratch line names its field', async () => {
  await draw(React.createElement(Scratch, {path: 'lean/A.lean', imports: ''}));
  assert.deepEqual(names(), ['Lean expression to check']);
});
