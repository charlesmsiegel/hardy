// Two controls in the top bar stand between a person and a stuck session: Stop,
// which must be there exactly while something runs, and the count of cards
// waiting for an answer, which must lead to the chat they are in. Both are
// claims about the DOM a person (or a screen reader) is handed, so they are
// checked against the real component rendered in jsdom: that Stop is absent
// when idle matters as much as that it is present when busy, because a Stop
// that is always drawn teaches people it does nothing. Absence is asserted on
// a boolean or a count, never on the node: a failing `assert.equal` prints the
// jsdom element it was handed, which is slow enough to look like a hang.
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
const {default: TopBar} = await server.ssrLoadModule('/src/workbench/TopBar.jsx');
const root = createRoot(document.getElementById('root'));

test.after(async () => {
  await act(() => root.unmount());
  await server.close();
  dom.window.close();
});

const noop = () => {};
const IDLE = {open: true, root: 'demo', path: '/p/demo', turn_running: false, command_running: false, queued: 0};
const draw = (props) =>
  act(() =>
    root.render(
      React.createElement(TopBar, {
        status: IDLE,
        runningTool: '',
        projects: [],
        setProjects: noop,
        refreshProjects: noop,
        revision: 0,
        waiting: 0,
        onStop: noop,
        go: noop,
        ...props,
      }),
    ),
  );
const button = (text) => [...document.querySelectorAll('.wb-topbar button')].find((node) => node.textContent === text);

test('Stop is drawn while a turn runs and stops it once per press', async () => {
  let stops = 0;
  await draw({status: {...IDLE, turn_running: true}, onStop: () => (stops += 1)});
  const stop = button('Stop');
  assert.ok(stop, 'a running turn has a Stop button');
  assert.equal(stop.type, 'button');
  assert.ok(stop.classList.contains('button--danger') && stop.classList.contains('button--small'), 'it is the small danger button');
  await act(() => stop.click());
  assert.equal(stops, 1);
});

test('Stop is drawn while a command runs and not otherwise', async () => {
  await draw({status: {...IDLE, command_running: true}});
  assert.ok(button('Stop'), 'a running command has a Stop button');
  await draw({});
  assert.equal(Boolean(button('Stop')), false, 'nothing running, nothing to stop');
});

test('cards waiting are counted, and the count opens the chat', async () => {
  const went = [];
  await draw({waiting: 2, go: (page) => went.push(page)});
  const waiting = button('2 waiting');
  assert.ok(waiting, 'two cards give "2 waiting"');
  assert.equal(waiting.type, 'button');
  await act(() => waiting.click());
  assert.deepEqual(went, ['chat']);

  await draw({waiting: 1});
  assert.ok(button('1 waiting'));
  await draw({waiting: 0});
  const indicators = [...document.querySelectorAll('.wb-topbar button')].filter((node) => /waiting/.test(node.textContent));
  assert.equal(indicators.length, 0, 'no cards, no indicator');
});
