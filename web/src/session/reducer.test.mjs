// Local draft requests must not become server messages. The remaining cases
// pin the event ordering this reducer already owns, independently of the UI.
import test from 'node:test';
import assert from 'node:assert/strict';
import {initial, reducer} from './reducer.js';

const event = (state, payload) => reducer(state, {type: 'event', event: payload});
const text = (state, value) => event(state, {type: 'turn', kind: 'text', text: value});
const load = (state, transcript) => reducer(state, {
  type: 'loaded', status: initial.status, commands: [], projects: [], transcript,
});

test('compose replaces the draft and requests focus even for identical text without sending', () => {
  const first = reducer(initial, {type: 'compose', text: '/model '});
  const second = reducer(first, {type: 'compose', text: '/model '});
  assert.equal(initial.composeFocus, 0);
  assert.equal(first.draft, '/model ');
  assert.equal(first.composeFocus, 1);
  assert.equal(second.composeFocus, 2);
  assert.equal(second.messages, initial.messages);
  assert.equal(second.status, initial.status);
  assert.equal(initial.draft, '');
});

test('a late transcript deduplicates a streamed turn by its final leaf', () => {
  let state = text(initial, 'hello');
  state = event(state, {type: 'turn_end', leaf: 'leaf-a'});
  state = load(state, [{entry_id: 'leaf-a', role: 'assistant', text: 'hello'}]);
  assert.deepEqual(state.messages.map(({text}) => text), ['hello']);
  assert.equal(state.streamId, null);
});

test('turn_end drops a replay when the transcript arrived before the stream settled', () => {
  let state = load(initial, [{entry_id: 'leaf-a', role: 'assistant', text: 'hello'}]);
  state = text(state, 'hello');
  state = event(state, {type: 'turn_end', leaf: 'leaf-a'});
  assert.deepEqual(state.messages.map(({id, text}) => [id, text]), [['leaf-a', 'hello']]);
  assert.equal(state.streamId, null);
});

test('notices and command output do not split an assistant stream or steal its tool result', () => {
  let state = text(initial, 'one ');
  const streamId = state.streamId;
  state = event(state, {type: 'turn', kind: 'tool_use', call_id: 'c1', name: 'lean'});
  state = event(state, {type: 'notice', text: 'job finished'});
  state = event(state, {type: 'write', style: 'system', text: 'command output'});
  state = text(state, 'two');
  state = event(state, {type: 'turn', kind: 'tool_result', call_id: 'c1', ok: true});
  state = event(state, {type: 'turn', kind: 'reply', text: 'one two'});
  assert.equal(state.streamId, streamId);
  assert.deepEqual(state.messages.map(({kind, text}) => [kind, text]), [
    ['assistant', 'one two'], ['notice', 'job finished'], ['system', 'command output'],
  ]);
  assert.deepEqual(state.messages[0].tools, [{call_id: 'c1', name: 'lean', ok: true}]);
  state = event(state, {type: 'turn_end', leaf: 'leaf-b'});
  assert.equal(state.streamId, null);
  assert.equal(state.messages[0].leaf, 'leaf-b');
  assert.equal(event(initial, {type: 'turn', kind: 'tool_result', call_id: 'orphan'}), initial);
});

test('prompt events and overlapping snapshots merge by id without dropping an open card', () => {
  const first = {id: 'p1', kind: 'confirm', title: 'First'};
  const second = {id: 'p2', kind: 'line', title: 'Second'};
  let state = event(initial, {type: 'prompt', ...first});
  state = event(state, {type: 'state', prompts: [first, second]});
  state = event(state, {type: 'prompt', ...second});
  state = event(state, {type: 'state', prompts: []});
  assert.deepEqual(state.prompts.map(({id, title}) => [id, title]), [['p1', 'First'], ['p2', 'Second']]);
  state = event(state, {type: 'prompt_closed', id: 'p1'});
  assert.deepEqual(state.prompts.map(({id}) => id), ['p2']);
});

test('resync ends the old stream and a chat switch clears old prompts and refusals', () => {
  let state = text(initial, 'partial');
  state = event(state, {type: 'prompt', id: 'p1'});
  state = reducer(state, {type: 'refused', message: 'busy'});
  state = reducer(state, {type: 'resynced', status: {prompts: [{id: 'p2'}]}, transcript: []});
  assert.equal(state.streamId, null);
  assert.deepEqual(state.prompts.map(({id}) => id), ['p1', 'p2']);
  state = reducer(state, {type: 'switched', transcript: [{entry_id: 'new', role: 'user', text: 'new chat'}]});
  assert.deepEqual(state.messages.map(({text}) => text), ['new chat']);
  assert.deepEqual(state.prompts, []);
  assert.equal(state.refusal, '');
});
