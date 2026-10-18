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
const RUNNING = {...IDLE, turn_running: true};
//: Every test starts from an idle bar, so the Stop button a previous test left
//: mounted (and in whatever phase) is gone, as it is when a run ends.
const fresh = () => draw({});
const tick = (t, ms) => act(() => t.mock.timers.tick(ms));

test('Stop is drawn while a turn runs and stops it once per press', async () => {
  await fresh();
  let stops = 0;
  await draw({status: RUNNING, onStop: () => (stops += 1)});
  const stop = button('Stop');
  assert.ok(stop, 'a running turn has a Stop button');
  assert.equal(stop.type, 'button');
  assert.ok(stop.classList.contains('button--danger') && stop.classList.contains('button--small'), 'it is the small danger button');
  await act(() => stop.click());
  assert.equal(stops, 1);
});

test('Stop is drawn while a command runs and not otherwise', async () => {
  await fresh();
  await draw({status: {...IDLE, command_running: true}});
  assert.ok(button('Stop'), 'a running command has a Stop button');
  await draw({});
  assert.equal(Boolean(button('Stop')), false, 'nothing running, nothing to stop');
});

test('a double click on Stop is one cancel, not a cancel and a kill', async () => {
  await fresh();
  let stops = 0;
  const onStop = (...args) => {
    assert.equal(args.length, 0, 'the click event is not passed on to the session');
    stops += 1;
  };
  await draw({status: RUNNING, onStop, forceAfterMs: 10000});
  const stop = button('Stop');
  await act(() => stop.click());
  await act(() => stop.click());
  assert.equal(stops, 1, 'the second click of a double click reaches nothing');
  const stopping = button('Stopping…');
  assert.ok(stopping, 'the button says it is waiting');
  assert.equal(stopping.disabled, false, 'waiting must not remove the keyboard focus target');
  assert.equal(stopping.getAttribute('aria-disabled'), 'true');
  assert.equal(Boolean(button('Stop')), false);
});

test('stop phases remain focusable and announce their changes politely', async (t) => {
  await fresh();
  t.mock.timers.enable({apis: ['setTimeout']});
  await draw({status: RUNNING, forceAfterMs: 3000});
  const stop = button('Stop');
  stop.focus();
  await act(() => stop.click());
  assert.equal(document.activeElement === stop, true);
  assert.equal(stop.disabled, false);
  assert.equal(stop.getAttribute('aria-disabled'), 'true');
  const live = document.querySelector('[aria-live="polite"]');
  assert.match(live?.textContent || '', /Stopping…/);
  await tick(t, 3000);
  assert.equal(document.activeElement === stop, true);
  assert.equal(stop.getAttribute('aria-disabled'), null);
  assert.match(live.textContent, /Force stop/);
  await act(() => stop.click());
  assert.equal(document.activeElement === stop, true);
  assert.equal(stop.getAttribute('aria-disabled'), 'true');
  assert.match(live.textContent, /Force stopping…/);
});

test('a stop that has not worked offers to force it, once, and says what that costs', async (t) => {
  await fresh();
  t.mock.timers.enable({apis: ['setTimeout']});
  let stops = 0;
  await draw({status: RUNNING, onStop: () => (stops += 1), forceAfterMs: 3000});
  await act(() => button('Stop').click());
  assert.equal(stops, 1);
  await tick(t, 3000);
  const force = button('Force stop');
  assert.ok(force, 'after the delay the button offers to force the stop');
  assert.equal(force.disabled, false);
  assert.match(force.title, /kills/i);
  assert.match(force.title, /computer algebra kernel/i, 'the title names what is lost');
  await act(() => force.click());
  await act(() => force.click());
  assert.equal(stops, 2, 'forcing is exactly one more cancel');
  const forced = button('Force stopping…');
  assert.ok(forced, 'it then says the kill is under way');
  assert.equal(forced.disabled, false, 'the focused button remains reachable');
  assert.equal(forced.getAttribute('aria-disabled'), 'true', 'and cannot be pressed again');
});

test('the force offer is not made before its delay', async (t) => {
  await fresh();
  t.mock.timers.enable({apis: ['setTimeout']});
  await draw({status: RUNNING, forceAfterMs: 3000});
  await act(() => button('Stop').click());
  await tick(t, 2999);
  assert.equal(Boolean(button('Force stop')), false);
  assert.ok(button('Stopping…'));
  await tick(t, 1);
  assert.ok(button('Force stop'));
});

test('when a run ends before the force timer the next run starts again at Stop', async (t) => {
  await fresh();
  t.mock.timers.enable({apis: ['setTimeout']});
  let stops = 0;
  await draw({status: RUNNING, onStop: () => (stops += 1), forceAfterMs: 3000});
  await act(() => button('Stop').click());
  await tick(t, 1000);
  assert.ok(button('Stopping…'), 'the first run ends while its force timer is pending');
  await draw({status: IDLE, onStop: () => (stops += 1), forceAfterMs: 3000});
  assert.equal(document.querySelectorAll('.wb-topbar .button--danger').length, 0, 'nothing running, no button in any phase');
  await draw({status: RUNNING, onStop: () => (stops += 1), forceAfterMs: 3000});
  const stop = button('Stop');
  assert.ok(stop, 'a later run starts again at Stop');
  assert.equal(stop.disabled, false);
  await tick(t, 3000);
  assert.ok(button('Stop'), 'and a stale timer does not push it on to Force stop');
  await act(() => button('Stop').click());
  assert.equal(stops, 2);
});

test('cards waiting are counted, and the count opens the chat', async () => {
  await fresh();
  const went = [];
  await draw({waiting: 2, go: (page) => went.push(page)});
  const waiting = button('2 waiting');
  assert.ok(waiting, 'two cards give "2 waiting"');
  assert.equal(waiting.type, 'button');
  const name = waiting.getAttribute('aria-label');
  assert.ok(name.startsWith('2 waiting'), 'the name begins with the words on the button');
  assert.match(name, /answer/);
  assert.match(name, /Chat/);
  await act(() => waiting.click());
  assert.deepEqual(went, ['chat']);

  await draw({waiting: 1});
  assert.ok(button('1 waiting'));
  await draw({waiting: 0});
  const indicators = [...document.querySelectorAll('.wb-topbar button')].filter((node) => /waiting/.test(node.textContent));
  assert.equal(indicators.length, 0, 'no cards, no indicator');
});
