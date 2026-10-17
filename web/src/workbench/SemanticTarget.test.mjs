import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import React, {act} from 'react';
const dom = new JSDOM('<!doctype html><div id="root"></div>', {url:'http://localhost/'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const {createRoot} = await import('react-dom/client');
const server = await createServer({server:{middlewareMode:true, ws:false}});
let NewTarget, TargetReview;
try {
  ({NewTarget, TargetReview} = await server.ssrLoadModule('/src/workbench/SemanticTarget.jsx'));
} catch { /* Missing feature is asserted below; always release the Vite server. */ }
const root = createRoot(document.getElementById('root'));
test.after(async () => { await act(() => root.unmount()); await server.close(); dom.window.close(); });
const type = async (name, value) => {
  const input = document.querySelector(`[name="${name}"]`);
  assert.ok(input, name);
  await act(() => {
    const prototype = input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input,value);
    input.dispatchEvent(new window.Event('input',{bubbles:true}));
  });
};
test('target creation preserves both statements and its selected project', async () => {
  assert.equal(typeof NewTarget, 'function', 'semantic target creation control is available');
  let request;
  globalThis.fetch = async (path, init) => { request = {path, body:JSON.parse(init.body)}; return new Response('{"ok":true}'); };
  await act(() => root.render(React.createElement(NewTarget,{project:'/p/first'})));
  await type('identity','Target');
  await type('statement','For all x, "x" is itself.');
  await type('formal',"lemma target (x : Nat) : x = x\n-- x's identity");
  await act(() => document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(request.path, '/api/project');
  assert.equal(request.body.project,'/p/first');
  assert.equal(request.body.formal,"lemma target (x : Nat) : x = x\n-- x's identity");
  assert.equal(request.body.statement,'For all x, "x" is itself.');
});
test('review starts unresolved and stale submissions never display an approval', async () => {
  assert.equal(typeof TargetReview, 'function', 'semantic review control is available');
  const report = {project:'/p/first',target:{id:'T',digest:'a'.repeat(64),statement:'True'},
    formal:{id:'F',digest:'b'.repeat(64),statement:'lemma saved_fact : True'},scope:{must_prove:[],allowed_background:[],allowed_interfaces:[]},scope_ref:{id:'S',digest:'c'.repeat(64)},
    context:null,context_hypotheses:[],translation:{status:'unresolved',reviews:[]},
    formal_proof:{status:'unresolved',reason:'No proof',axioms:[]},statement:{status:'unresolved'},
    original_proof:{status:'unassessed',reason:'No supplied proof'},repaired_proof:{status:'unassessed',reason:'No repair'},
    assumptions:[],coverage_gaps:['No supplied proof'],bindings:[]};
  let request;
  globalThis.fetch = async (path,init) => { request=JSON.parse(init.body); return new Response('{"error":"Cannot review stale statement versions."}',{status:409}); };
  await act(() => root.render(React.createElement(TargetReview,{project:'/p/first',report})));
  assert.equal(document.querySelector('[name="verdict"]').value,'unresolved');
  await type('reviewer','test operator'); await type('conditions','side by side'); await type('reason','Requires review');
  await act(() => document.querySelector('form[data-review]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(request.target, `T@${'a'.repeat(64)}`);
  assert.equal(request.translation, `F@${'b'.repeat(64)}`);
  assert.equal(request.scope, `S@${'c'.repeat(64)}`);
  assert.match(document.body.textContent,/Cannot review stale/);
  assert.match(document.body.textContent,/Original supplied proof/);
  assert.match(document.body.textContent,/unassessed/i);
});
test('late creation response stays with its original project', async () => {
  let finish, request;
  let refreshed = 0;
  globalThis.fetch = async (path,init) => { request=JSON.parse(init.body); return new Promise((resolve) => {finish=() => resolve(new Response('{"ok":true}'));}); };
  await act(() => root.render(React.createElement(NewTarget,{project:'/p/old',onDone:() => refreshed++})));
  await type('identity','Late');await type('statement','True');await type('formal','lemma late : True');
  await act(() => document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
  await act(() => root.render(React.createElement(NewTarget,{project:'/p/new',onDone:() => refreshed++})));
  await act(async () => {finish();await new Promise((resolve) => setTimeout(resolve,0));});
  assert.equal(request.project,'/p/old');
  assert.equal(refreshed,0);
  assert.doesNotMatch(document.body.textContent,/Recorded\./);
});
test('target forms use the shared field classes and carry no inline style', async () => {
  const report = {project:'/p/first',target:{id:'T',digest:'a'.repeat(64),statement:'True'},
    formal:{id:'F',digest:'b'.repeat(64),statement:'lemma saved_fact : True'},scope:{must_prove:[],allowed_background:[],allowed_interfaces:[]},scope_ref:{id:'S',digest:'c'.repeat(64)},
    context:null,context_hypotheses:[{id:'H',statement:'x : Nat'}],translation:{status:'unresolved',reviews:[{id:'r',verdict:'approved',reviewer:'me',recorded_at:'now',conditions:'c',reason:'r'}]},
    formal_proof:{status:'unresolved',reason:'No proof',axioms:[]},statement:{status:'unresolved'},
    original_proof:{status:'unassessed',reason:'No supplied proof'},repaired_proof:{status:'unassessed',reason:'No repair'},
    assumptions:[],coverage_gaps:[],bindings:[],saved_candidates:[{id:'L',digest:'d'.repeat(64),name:'Saved'}]};
  for (const element of [React.createElement(NewTarget,{project:'/p/first'}), React.createElement(TargetReview,{project:'/p/first',report})]) {
    await act(() => root.render(element));
    const controls = [...document.querySelectorAll('#root input,#root select,#root textarea')];
    assert.ok(controls.length);
    for (const control of controls) {
      assert.ok(control.labels.length, `label for ${control.name}`);
      assert.ok(control.classList.contains('field__control'), `${control.name} is a field__control`);
      assert.ok(control.labels[0].classList.contains('field'), `${control.name} is labelled by a .field`);
    }
    assert.deepEqual([...document.querySelectorAll('#root [style]')].map((node) => node.tagName), []);
  }
});
