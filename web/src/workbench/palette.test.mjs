// Ranking is checked with literal expected orders, then the real dialog's
// keyboard and focus contract is checked against the DOM.
import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import React, {act} from 'react';

const dom = new JSDOM('<!doctype html><button id="origin">Origin</button><div id="root"></div>', {url: 'http://localhost/'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const {createRoot} = await import('react-dom/client');
const server = await createServer({server: {middlewareMode: true, ws: false}});
const root = createRoot(document.getElementById('root'));
test.after(async () => { await act(() => root.unmount()); await server.close(); dom.window.close(); });

test('entries preserve page, chat, form and command identities with explicit navigation or composition', async () => {
  const {paletteEntries} = await import('./palette.js');
  assert.deepEqual(paletteEntries({
    pages: [{page: 'ledger', label: 'Ledger', detail: 'Mathematics'}],
    chats: [{id: 'main', title: 'Main discussion'}],
    forms: [{id: 'context', title: 'Define context', summary: 'Choose the context.'}],
    commands: [{name: 'model', summary: 'switch the model'}],
  }), [
    {id: 'page:ledger', kind: 'page', label: 'Ledger', detail: 'Mathematics', run: {page: 'ledger'}},
    {id: 'chat:main', kind: 'chat', label: 'Main discussion', detail: 'Chat', run: {page: 'chat', arg: 'main'}},
    {id: 'form:context', kind: 'form', label: 'Define context', detail: 'Choose the context.', run: {page: 'workflows', arg: 'context'}},
    {id: 'command:model', kind: 'command', label: '/model', detail: 'switch the model', run: {compose: '/model '}},
  ]);
});

test('ranking keeps matching details, puts prefixes before substrings and keeps pages first for an empty query', async () => {
  const {rankEntries} = await import('./palette.js');
  const entries = [
    {kind: 'command', label: '/help', detail: 'get help'},
    {kind: 'page', label: 'Ledger', detail: 'Project records'},
    {kind: 'page', label: 'Help', detail: 'Session'},
    {kind: 'command', label: '/model', detail: 'switch the model'},
  ];
  const labels = (query) => rankEntries(query, entries).map(({label}) => label);
  assert.deepEqual(labels('he'), ['Help', '/help', '/model']);
  assert.deepEqual(labels('HELP'), ['Help', '/help']);
  assert.deepEqual(labels('led'), ['Ledger']);
  assert.deepEqual(labels(''), ['Ledger', 'Help', '/help', '/model']);
  assert.deepEqual(labels('zzzzz'), []);
  assert.deepEqual(entries.map(({label}) => label), ['/help', 'Ledger', 'Help', '/model'], 'ranking does not reorder its input');
  const tiers = ['some help', 'shelper', 'help', 'helper', '/help', 'another help'].map((label) => ({label, detail: ''}));
  assert.deepEqual(rankEntries('help', tiers).map(({label}) => label), ['help', 'helper', 'some help', '/help', 'another help', 'shelper']);
});

test('Enter selects a command draft; Escape closes and restores focus; Tab stays in the dialog', async () => {
  const {default: CommandPalette} = await server.ssrLoadModule('/src/workbench/CommandPalette.jsx');
  const origin = document.getElementById('origin');
  origin.focus();
  const runs = [];
  let closed = 0;
  const props = {entries: [{id: 'command:model', kind: 'command', label: '/model', detail: 'switch the model', run: {compose: '/model '}}], onRun: (run) => runs.push(run), onClose: () => { closed += 1; }};
  const draw = (open) => act(() => root.render(React.createElement(CommandPalette, {...props, open})));
  await draw(true);
  const dialog = document.querySelector('[role="dialog"]');
  assert.equal(dialog.getAttribute('aria-modal'), 'true');
  const input = dialog.querySelector('input');
  assert.ok(input.labels.length, 'search has a visible label');
  assert.equal(document.activeElement === input, true);
  const press = (key, extra = {}) => act(() => document.activeElement.dispatchEvent(new window.KeyboardEvent('keydown', {key, bubbles: true, cancelable: true, ...extra})));
  await press('ArrowDown');
  assert.equal(document.querySelector('[role="option"]').getAttribute('aria-selected'), 'true');
  await press('Enter');
  assert.deepEqual(runs, [{compose: '/model '}]);
  await press('Tab', {shiftKey: true});
  assert.equal(document.activeElement.textContent, 'Close');
  await press('Tab');
  assert.equal(document.activeElement === input, true);
  await press('Escape');
  assert.equal(closed, 1);
  await draw(false);
  assert.equal(document.activeElement === origin, true);
});
