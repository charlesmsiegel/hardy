// What a pickable table row owes a keyboard: a tab stop, a selected state a
// screen reader can announce, and Enter or Space to do what a click does.
// Rendered in jsdom, because "reachable" is a claim about the DOM and a
// source-text check would pass a row that merely mentions `tabIndex`.
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
const {default: Table} = await server.ssrLoadModule('/src/components/Table.jsx');
const root = createRoot(document.getElementById('root'));

test.after(async () => {
  await act(() => root.unmount());
  await server.close();
  dom.window.close();
});

const rows = [
  {key: 'first', cells: ['one']},
  {key: 'second', cells: ['two', React.createElement('a', {href: '#', key: 'link'}, 'link')]},
];
const draw = (props) => act(() => root.render(React.createElement(Table, {head: ['name', 'extra'], rows, ...props})));
const bodyRows = () => [...document.querySelectorAll('tbody tr')];
const press = (node, key) => {
  const event = new window.KeyboardEvent('keydown', {key, bubbles: true, cancelable: true});
  act(() => {
    node.dispatchEvent(event);
  });
  return event;
};

test('a pickable row is reachable and activates from the keyboard', async () => {
  const picked = [];
  await draw({onPick: (key) => picked.push(key), selected: 'first'});
  assert.deepEqual(bodyRows().map((row) => row.getAttribute('tabindex')), ['0', '0']);
  assert.deepEqual(bodyRows().map((row) => row.getAttribute('aria-selected')), ['true', 'false']);

  const enter = press(bodyRows()[1], 'Enter');
  assert.deepEqual(picked, ['second']);
  assert.equal(enter.defaultPrevented, true, 'Enter is consumed, not also sent on to the page');

  const space = press(bodyRows()[1], ' ');
  assert.deepEqual(picked, ['second', 'second']);
  assert.equal(space.defaultPrevented, true, 'Space must not also scroll the page');

  press(bodyRows()[0], 'a');
  press(bodyRows()[0], 'Tab');
  assert.deepEqual(picked, ['second', 'second'], 'only Enter and Space pick');
});

test('a row without onPick is plain table content', async () => {
  const picked = [];
  await draw({selected: 'first'});
  for (const row of bodyRows()) {
    assert.equal(row.hasAttribute('tabindex'), false);
    assert.equal(row.hasAttribute('aria-selected'), false);
  }
  const enter = press(bodyRows()[1], 'Enter');
  assert.equal(enter.defaultPrevented, false);
  assert.deepEqual(picked, []);
});

test('a key pressed in a control inside a row is that control\'s, not the row\'s', async () => {
  const picked = [];
  await draw({onPick: (key) => picked.push(key)});
  const enter = press(bodyRows()[1].querySelector('a'), 'Enter');
  assert.deepEqual(picked, [], 'Enter on a link in a cell follows the link; it does not also pick the row');
  assert.equal(enter.defaultPrevented, false, 'the link keeps its own default');
});

test('a click still picks the row', async () => {
  const picked = [];
  await draw({onPick: (key) => picked.push(key)});
  await act(() => bodyRows()[0].click());
  assert.deepEqual(picked, ['first']);
});
