// The workflow selection changes visibility, not ownership. These tests use
// the real frame and action forms, with only their HTTP transport controlled,
// to pin pending replies, project isolation and inspection-only Ledger.
import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import React, {act} from 'react';

const dom = new JSDOM('<!doctype html><div id="root"></div>', {url: 'http://localhost/#/workflows/context'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const {createRoot} = await import('react-dom/client');
const server = await createServer({server: {middlewareMode: true, ws: false}});
const {default: Shell} = await server.ssrLoadModule('/src/workbench/Shell.jsx');
const {SessionContext} = await server.ssrLoadModule('/src/session/SessionProvider.jsx');
const {PAGES} = await server.ssrLoadModule('/src/pages/index.js');
const chat = PAGES.chat;
const root = createRoot(document.getElementById('root'));
const ref = (id, letter = 'a') => ({id, digest: letter.repeat(64)});
const CATALOG = {
  items: [{...ref('T'), name: 'Target', kind: 'claim', semantics: [], statement: 'True'}],
  contexts: [], scopes: [{...ref('S', 'b'), label: 'Scope', must_prove: [ref('T')], allowed_background: [], allowed_interfaces: []}],
  obligations: [], sources: [], admissions: [], evidence_refs: [],
};
const noop = () => {};
let requests;
let pendingResponse;
let cancellations;
let revision = 0;
let path = '/p/first';
const draw = () => act(async () => root.render(React.createElement(SessionContext.Provider, {value: {
  status: {path, root: 'demo', slug: 'demo', chat: 'main', open: true},
  revision, commands: [], projects: [], prompts: [], messages: [], loaded: true,
  setProjects: noop, refreshProjects: noop, cancel: () => {cancellations++;}, setDraft: noop,
}}, React.createElement(Shell))));
const navigate = (hash) => act(async () => {
  window.history.replaceState(null, '', hash);
  window.dispatchEvent(new window.HashChangeEvent('hashchange'));
});
const region = (id) => document.getElementById(`workflow-${id}`);
const type = async (id, name, value) => act(async () => {
  const input = region(id).querySelector(`[name="${name}"]`);
  assert.ok(input, `${id}.${name}`);
  const prototype = input.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, value);
  input.dispatchEvent(new window.Event(input.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true}));
});
const submit = (id) => act(async () => region(id).querySelector('form').dispatchEvent(new window.Event('submit', {bubbles: true, cancelable: true})));
const choose = async (id) => {
  await act(async () => document.querySelector(`.wb-workflows__nav [data-workflow="${id}"]`).click());
  await navigate(window.location.hash);
};

test.beforeEach(async (t) => {
  requests = [];
  pendingResponse = null;
  cancellations = 0;
  path = '/p/first';
  revision += 1;
  PAGES.chat = () => React.createElement('p', null, 'Chat stays mounted');
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    requests.push([url, init]);
    if (init.method === 'POST') {
      assert.equal(url, '/api/project');
      return pendingResponse || new Response('{"ok":true,"record":{}}');
    }
    if (url.startsWith('/api/project-state?')) return new Response(JSON.stringify({
      project: new URL(url, 'http://localhost').searchParams.get('project'), catalog: CATALOG,
      workflow: {running: false, capabilities: {research: {available: true}}, attempts: [], calls: []},
      intake: {source_choices: [], proposals: [], reviews: []}, referee: {reports: []}, errors: [],
    }));
    const data = {'/api/jobs': {attention: []}, '/api/chats': [], '/api/ledger': {items: []}, '/api/graph': {nodes: [], edges: [], tones: {}, revision: 0}};
    assert.ok(url in data, `unexpected read ${url}`);
    return new Response(JSON.stringify(data[url]));
  });
  await navigate('#/workflows/context');
});
test.afterEach(async () => { await act(() => root.render(null)); PAGES.chat = chat; });
test.after(async () => { await act(() => root.unmount()); await server.close(); dom.window.close(); });

test('workflow descriptors identify exactly the twelve implemented forms in valid ordered groups', async () => {
  const {WORKFLOW_FORMS, WORKFLOW_GROUPS} = await server.ssrLoadModule('/src/workbench/workflowForms.js');
  assert.deepEqual(WORKFLOW_FORMS.map(({id}) => id), ['target', 'context', 'scope', 'intake', 'mappings', 'live', 'referee', 'admission', 'review', 'arguments', 'obligations', 'obligation-new']);
  assert.equal(new Set(WORKFLOW_FORMS.map(({id}) => id)).size, 12);
  assert.deepEqual(WORKFLOW_GROUPS.map(({id}) => id), ['define', 'sources', 'investigate', 'judge', 'record']);
  for (const form of WORKFLOW_FORMS) {
    assert.ok(WORKFLOW_GROUPS.some(({id}) => id === form.group));
    assert.ok(form.title && form.summary);
  }
  assert.equal(typeof PAGES.workflows, 'function');
});

test('Context is shown with all other form roots mounted and hidden, using unique labelled headings', async () => {
  await draw();
  assert.ok(region('context'), 'Context is a registered page selection');
  const regions = [...document.querySelectorAll('.wb-workflows__form')];
  assert.equal(regions.length, 12);
  assert.equal(region('context').hidden, false);
  for (const node of regions) {
    assert.equal(node.hidden, node.id !== 'workflow-context');
    const form = node.querySelector(':scope > section.wb-form');
    assert.ok(form, node.id);
    const title = document.getElementById(form.getAttribute('aria-labelledby'));
    assert.equal(title.tagName, 'H2');
    assert.ok(title.classList.contains('wb-form__title'));
  }
  const ids = [...document.querySelectorAll('.wb-page [id]')].map(({id}) => id);
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(document.querySelector('.wb-workflows__nav [data-workflow="context"]').getAttribute('aria-current'), 'page');
});

test('changing form selection preserves drafts, a pending Research request and its completed reply', async () => {
  await draw();
  await type('context', 'id', 'keep-context-draft');
  const context = region('context');
  await choose('live');
  await type('live', 'id', 'research-1');
  await type('live', 'target', `T@${'a'.repeat(64)}`);
  await type('live', 'scope', `S@${'b'.repeat(64)}`);
  let finish;
  pendingResponse = new Promise((resolve) => { finish = resolve; });
  await submit('live');
  assert.match(region('live').textContent, /Leaving this page drops this form's reply; the run continues and its record appears in workflow history\./);
  assert.deepEqual(JSON.parse(requests.find(([, init]) => init.method === 'POST')[1].body), {
    operation: 'research', project: '/p/first', request: {id: 'research-1', target: ref('T'), scope: ref('S', 'b'), operation: 'prove', context: null},
  });
  const live = region('live');
  await choose('context');
  assert.equal(region('context') === context, true);
  assert.equal(context.querySelector('[name="id"]').value, 'keep-context-draft');
  await choose('live');
  assert.equal(region('live') === live, true);
  assert.equal(live.querySelector('fieldset').disabled, true);
  await act(async () => finish(new Response('{"ok":true,"record":{"status":"completed","detail":"Research reply retained"}}')));
  assert.match(live.textContent, /Completed.*Research reply retained/);
});

test('a project switch resets the old drafts and ignores a late reply from the old project', async () => {
  await draw();
  await type('context', 'id', 'old-context');
  await type('context', 'label', 'Old context');
  let finish;
  pendingResponse = new Promise((resolve) => { finish = resolve; });
  await submit('context');
  const previous = region('context');
  path = '/p/second';
  await draw();
  assert.equal(region('context') === previous, false);
  assert.equal(region('context').querySelector('[name="id"]').value, '');
  await act(async () => finish(new Response('{"ok":true,"record":{"status":"completed","detail":"OLD PROJECT REPLY"}}')));
  assert.doesNotMatch(document.body.textContent, /OLD PROJECT REPLY/);
  pendingResponse = null;
  await type('context', 'id', 'new-context');
  await type('context', 'label', 'New context');
  await submit('context');
  assert.deepEqual(requests.filter(([, init]) => init.method === 'POST').map(([, init]) => JSON.parse(init.body).project), ['/p/first', '/p/second']);
});

test('Ledger offers workflow links and contains no workflow action forms', async () => {
  await navigate('#/ledger');
  await draw();
  assert.equal(document.querySelectorAll('.wb-page form').length, 0);
  assert.equal(document.querySelectorAll('.wb-page .wb-form').length, 0);
  const menu = document.querySelector('.wb-page details.wb-new-menu');
  assert.match(menu?.querySelector('summary')?.textContent || '', /New/);
  assert.equal(menu.querySelectorAll('a[href^="#/workflows/"]').length, 12);
  assert.ok(requests.some(([url]) => url.startsWith('/api/project-state?')), 'Ledger retains the catalog read for its inspection views');
});

test('Open an obligation is reachable from the palette and retains its draft and pending reply while hidden',async () => {
  await navigate('#/ledger');await draw();
  await act(async () => window.dispatchEvent(new window.KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true,cancelable:true})));
  const option=[...document.querySelectorAll('[role="option"]')].find((node) => node.textContent.includes('Open an obligation'));
  assert.ok(option);await act(async () => option.click());await navigate(window.location.hash);
  assert.equal(window.location.hash,'#/workflows/obligation-new');
  const form=region('obligation-new');assert.equal(form.hidden,false);
  await type('obligation-new','subject','T@'+ 'a'.repeat(64));await type('obligation-new','scope','S@'+ 'b'.repeat(64));
  await type('obligation-new','kind','prove');await type('obligation-new','reason','needed');
  let finish;pendingResponse=new Promise((resolve) => {finish=resolve;});await submit('obligation-new');
  await choose('obligations');assert.equal(form.hidden,true);
  await choose('obligation-new');assert.equal(region('obligation-new'),form);assert.equal(form.querySelector('[name="reason"]').value,'needed');
  assert.equal(form.querySelector('fieldset').disabled,true);
  await act(async () => finish(new Response('{"ok":true,"record":{}}')));
  assert.match(form.querySelector('[role="status"]').textContent,/Recorded/);
  assert.deepEqual(JSON.parse(requests.find(([,init]) => init.method==='POST')[1].body),{operation:'obligation',project:'/p/first',request:{subject:ref('T'),scope:ref('S','b'),kind:'prove',reason:'needed'}});
});

test('leaving Workflows while Research is pending unmounts its reply without cancelling the run',async () => {
  await draw();await choose('live');
  await type('live','id','research-leave');await type('live','target','T@'+ 'a'.repeat(64));await type('live','scope','S@'+ 'b'.repeat(64));
  let finish;pendingResponse=new Promise((resolve) => {finish=resolve;});await submit('live');
  assert.match(region('live').textContent,/Leaving this page drops this form's reply/);
  await navigate('#/ledger');assert.equal(region('live'),null);
  await act(async () => finish(new Response('{"ok":true,"record":{"status":"completed","detail":"OLD PAGE REPLY"}}')));
  await navigate('#/workflows/live');
  assert.equal(region('live').querySelector('[name="id"]').value,'');assert.doesNotMatch(region('live').textContent,/OLD PAGE REPLY/);
  assert.equal(cancellations,0,'leaving the page does not call session cancellation');
  assert.equal(requests.filter(([,init]) => init.method==='POST').length,1,'no cancellation request was sent on leave');
});

test('a pending new obligation belongs to its project and cannot fill a different project form',async () => {
  await navigate('#/workflows/obligation-new');await draw();
  await type('obligation-new','subject','T@'+ 'a'.repeat(64));await type('obligation-new','scope','S@'+ 'b'.repeat(64));
  await type('obligation-new','reason','old project work');
  let finish;pendingResponse=new Promise((resolve) => {finish=resolve;});await submit('obligation-new');
  const old=region('obligation-new');path='/p/second';await draw();
  assert.notEqual(region('obligation-new'),old);
  assert.equal(region('obligation-new').querySelector('[name="reason"]').value,'');
  assert.equal(region('obligation-new').querySelector('[name="subject"]').value,'');
  assert.equal(region('obligation-new').querySelector('fieldset').disabled,false);
  await act(async () => finish(new Response('{"ok":true,"record":{"status":"completed","detail":"OLD OBLIGATION REPLY"}}')));
  assert.doesNotMatch(region('obligation-new').textContent,/OLD OBLIGATION REPLY/);
  pendingResponse=null;
  await type('obligation-new','subject','T@'+ 'a'.repeat(64));await type('obligation-new','scope','S@'+ 'b'.repeat(64));
  await type('obligation-new','reason','current project work');await submit('obligation-new');
  assert.deepEqual(requests.filter(([,init]) => init.method==='POST').map(([,init]) => JSON.parse(init.body).project),['/p/first','/p/second']);
});

test('the palette navigates to an implemented workflow form without submitting an action', async () => {
  await navigate('#/ledger');
  await draw();
  await act(async () => window.dispatchEvent(new window.KeyboardEvent('keydown', {key: 'k', ctrlKey: true, bubbles: true, cancelable: true})));
  const option = [...document.querySelectorAll('[role="option"]')].find((node) => node.textContent.includes('Create or revise mathematical context'));
  assert.ok(option);
  await act(async () => option.click());
  await navigate(window.location.hash);
  assert.equal(window.location.hash, '#/workflows/context');
  assert.equal(region('context').hidden, false);
  assert.equal(requests.some(([, init]) => init.method !== 'GET'), false);
});
