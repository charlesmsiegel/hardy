// Page actions exercise the mounted components; only session snapshots and
// HTTP replies are controlled. A composed command must never become a send.
import test from 'node:test';
import assert from 'node:assert/strict';
import {File} from 'node:buffer';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import React, {act} from 'react';

const dom = new JSDOM('<!doctype html><div id="root"></div>', {url:'http://localhost/#/home'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const {createRoot} = await import('react-dom/client');
const server = await createServer({server:{middlewareMode:true, ws:false}});
const pages = Object.fromEntries(await Promise.all(['Help','Home','Tree','Files','Jobs'].map(async name =>
  [name, (await server.ssrLoadModule(`/src/pages/${name}.jsx`)).default])));
const {SessionContext} = await server.ssrLoadModule('/src/session/SessionProvider.jsx');
const root = createRoot(document.getElementById('root'));
let status, revision=400, sent, composed, requests, data, uploadReply, currentPage, arg;
const draw = async (page=currentPage) => {
  currentPage = page;
  await act(async () => root.render(React.createElement(SessionContext.Provider, {value:{status, revision,
    messages:[], commands:[], send:line=>sent.push(line), compose:line=>composed.push(line), setDraft(){}}},
  React.createElement(pages[page], {arg}))));
};
const tick = () => act(async () => new Promise(resolve=>setTimeout(resolve,0)));
const waitFor = async (predicate, message) => {
  for(let attempt=0;attempt<500 && !predicate();attempt++)await tick();
  assert.ok(predicate(),message);
};
const button = (text, scope=document) => [...scope.querySelectorAll('button')].find(b=>b.textContent.trim().startsWith(text));
const click = async text => {const b=button(text); assert.ok(b, text); await act(()=>b.click());};
const type = async (selector, value) => act(()=>{
  const node=document.querySelector(selector); assert.ok(node, selector);
  const prototype=node.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,value);
  node.dispatchEvent(new window.Event('input',{bubbles:true}));
});
const submit = async selector => act(()=>{
  const form=document.querySelector(selector); assert.ok(form,selector);
  form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
});
const pickJob = async id => act(()=>{
  const row=[...document.querySelectorAll('tbody tr')].find(row=>row.querySelector('.wb-jobs__node-id')?.textContent===id);
  assert.ok(row,id);row.click();
});
const pickEntry = async id => act(()=>{
  const row=[...document.querySelectorAll('.wb-tree__row')].find(row=>row.querySelector('.wb-tree__row-id').textContent===id);
  assert.ok(row,id);row.click();
});
const chooseFiles = async files => act(()=>{
  const input=document.querySelector('input[type=file]'); assert.ok(input,'Upload file input');
  Object.defineProperty(input,'files',{configurable:true,value:files});
  input.dispatchEvent(new window.Event('change',{bubbles:true}));
});

test.beforeEach(t=>{
  revision++;sent=[];composed=[];requests=[];arg='';uploadReply=null;
  status={path:'/p/first',chat:'main',open:true,turn_running:false,command_running:false,provider_active:false};
  data={
    '/api/commands':[{name:'goal',kind:'builtin',summary:'state goal'},{name:'prove-it',kind:'shortcut',summary:'prompt'}, {name:'my-command',kind:'project',summary:'project prompt'}],
    '/api/summary':{goal:'Original goal',entries:2,lines:1},
    '/api/record':{items:0,by_kind:{}}, '/api/chats':[{id:'main',title:'main',turns:0}],
    '/api/tree':{entries:[{entry_id:'e1',parent_id:null,type:'message'},{entry_id:'e2',parent_id:'e1',type:'message'}],active_leaf:'e2'},
    '/api/jobs':{counts:{queued:1,paused:1,active:1},root:{},delegations:[{id:'q',parent:'root',state:'queued',objective:'Queued task'},{id:'p',parent:'root',state:'paused',objective:'Paused task'},{id:'a',parent:'root',state:'active',objective:'Active task'}],attention:[{id:'att',actionable:true,summary:'Human decision'}]},
    '/api/files':{lean:[],tex:[],cas:[],pdf:[]},'/api/uploads':[], '/api/environment':{checks:[]},
    '/api/cas/cells':{cells:[],segment:1,total:0,truncated:false},
  };
  t.mock.method(globalThis,'fetch',async (path,init)=>{
    requests.push([path,init]);
    if(path==='/api/upload')return (typeof uploadReply==='function'?uploadReply(init):uploadReply) || new Response(JSON.stringify({name:init.headers['X-Hardy-Filename']}));
    assert.ok(path in data,path);return new Response(JSON.stringify(data[path]));
  });
});
test.afterEach(async()=>act(()=>root.render(null)));
test.after(async()=>{await act(()=>root.unmount());await server.close();dom.window.close();});

test('Help groups actual command kinds and every button composes the exact prefix',async()=>{
  await draw('Help');
  for(const [group,name] of [['Built-ins','goal'],['Prompt shortcuts','prove-it'],['Project commands','my-command']]){
    const section=[...document.querySelectorAll('[data-command-kind]')].find(node=>node.textContent.includes(group));
    assert.ok(section,group);await act(()=>button('/'+name,section).click());
  }
  assert.deepEqual(composed,['/goal ','/prove-it ','/my-command ']);assert.deepEqual(sent,[]);
});

test('Home has an explicit labelled goal form even in a fresh project; export only composes',async()=>{
  data['/api/jobs']={counts:{},delegations:[],attention:[]};await draw('Home');
  const input=document.querySelector('form[data-action="goal"] textarea');assert.ok(input?.labels.length);assert.equal(input.required,true);
  assert.equal(input.labels[0].textContent.trim(),'Goal','the populated textarea does not become part of its own label');
  await type('form[data-action="goal"] textarea','A new goal');assert.deepEqual(sent,[]);
  await submit('form[data-action="goal"]');assert.deepEqual(sent,['/goal A new goal']);
  await click('Export session');assert.deepEqual(composed,['/export ']);
  status={...status,turn_running:true};await draw();assert.equal(document.querySelector('form[data-action="goal"] button').disabled,true);
  await submit('form[data-action="goal"]');assert.equal(sent.length,1);
  status={...status,path:'/p/second',turn_running:false};data['/api/summary'].goal='Second goal';revision++;await draw();
  assert.equal(document.querySelector('form[data-action="goal"] textarea').value,'Second goal');
});

test('Tree requires a lesson and confirms the exact selected entry; Escape dismisses',async()=>{
  await draw('Tree');await pickEntry('e1');
  const lesson=document.querySelector('form[data-action="abandon"] textarea');assert.ok(lesson?.labels.length);assert.equal(lesson.required,true);
  await submit('form[data-action="abandon"]');assert.equal(Boolean(document.querySelector('.card-shell')),false);
  await type('form[data-action="abandon"] textarea','This branch repeats a failed argument');
  await submit('form[data-action="abandon"]');assert.deepEqual(sent,[]);
  assert.match(document.querySelector('.card-shell').textContent,/\/abandon e1 This branch repeats a failed argument/);
  assert.equal(document.activeElement.textContent,'No');
  await act(()=>document.activeElement.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
  assert.equal(Boolean(document.querySelector('.card-shell')),false);
  await submit('form[data-action="abandon"]');await click('Yes');
  assert.deepEqual(sent,['/abandon e1 This branch repeats a failed argument']);
});

test('Tree lesson changes, selection, chat/project ownership and busy admission invalidate stale targets',async()=>{
  await draw('Tree');await type('form[data-action="abandon"] textarea','Old lesson');await submit('form[data-action="abandon"]');
  await type('form[data-action="abandon"] textarea','Changed lesson');assert.equal(Boolean(document.querySelector('.card-shell')),false);
  await submit('form[data-action="abandon"]');await pickEntry('e1');assert.equal(Boolean(document.querySelector('.card-shell')),false);
  assert.equal(document.querySelector('form[data-action="abandon"] textarea').value,'');
  await type('form[data-action="abandon"] textarea','Another old lesson');await submit('form[data-action="abandon"]');
  status={...status,chat:'other'};await draw();assert.equal(Boolean(document.querySelector('.card-shell')),false);
  await type('form[data-action="abandon"] textarea','New chat lesson');await submit('form[data-action="abandon"]');
  status={...status,command_running:true};await draw();assert.equal(button('Yes').disabled,true);await click('Yes');assert.deepEqual(sent,[]);
  status={...status,path:'/p/second',command_running:false};revision++;await draw();
  assert.equal(Boolean(document.querySelector('.card-shell')),false);assert.equal(document.querySelector('form[data-action="abandon"] textarea').value,'');
});

test('Jobs only offers state-admissible controls; Finish and Pin compose reviewable exact IDs',async()=>{
  await draw('Jobs');await pickJob('q');assert.ok(button('Pause'));assert.equal(Boolean(button('Resume')),false);
  await click('Finish');await click('Pin');assert.deepEqual(composed,['/jobs finish q ','/jobs pin q ']);assert.deepEqual(sent,[]);
  await pickJob('p');assert.ok(button('Resume'));assert.equal(Boolean(button('Pause')),false);
  await pickJob('a');assert.equal(Boolean(button('Pause')),false);assert.equal(Boolean(button('Resume')),false);assert.ok(button('Cancel'));assert.ok(button('Reinforce'));
  data['/api/jobs'].delegations[2].state='waiting';revision++;await draw();
  assert.equal(Boolean(button('Pause')),false);assert.equal(Boolean(button('Resume')),false);assert.ok(button('Cancel'));assert.ok(button('Reinforce'));assert.ok(button('Finish'));
  for(const state of ['completed','partial','failed','cancelled','exhausted','unknown']){
    data['/api/jobs'].delegations[2].state=state;revision++;await draw();
    for(const label of ['Pause','Resume','Cancel','Reinforce','Finish'])assert.equal(Boolean(button(label)),false,`${state}: ${label}`);
    assert.ok(button('Pin'));
  }
});

test('Jobs reinforcement captures a labelled positive integer and confirms before send, including in flight',async()=>{
  await draw('Jobs');await pickJob('q');
  const amount=document.querySelector('input[name="checks"]');assert.ok(amount?.labels.length);assert.equal(amount.type,'number');assert.equal(amount.min,'1');assert.equal(amount.value,'2');
  await type('input[name="checks"]','7');await click('Reinforce');assert.deepEqual(sent,[]);assert.match(document.querySelector('.card-shell').textContent,/\/jobs reinforce q 7/);
  await type('input[name="checks"]','3');assert.equal(Boolean(document.querySelector('.card-shell')),false);
  await type('input[name="checks"]','0');assert.equal(button('Reinforce').disabled,true);
  await type('input[name="checks"]','4');await click('Reinforce');
  status={...status,turn_running:true};await draw();await click('Yes');assert.deepEqual(sent,['/jobs reinforce q 4']);
  await click('Pause');await click('Yes');assert.deepEqual(sent,['/jobs reinforce q 4','/jobs pause q']);
  await click('Handle');await click('Yes');assert.equal(sent.at(-1),'/jobs handle att');
});

test('Jobs state, selection and project changes remove stale confirmation and amount',async()=>{
  await draw('Jobs');await pickJob('q');await type('input[name="checks"]','9');await click('Reinforce');
  await pickJob('p');assert.equal(Boolean(document.querySelector('.card-shell')),false);assert.equal(document.querySelector('input[name="checks"]').value,'2');
  await click('Resume');data['/api/jobs'].delegations[1].state='completed';revision++;await draw();assert.equal(Boolean(document.querySelector('.card-shell')),false);
  data['/api/jobs'].delegations[1].state='paused';revision++;await draw();assert.equal(Boolean(document.querySelector('.card-shell')),false,'old confirmation does not return with the state');
  await pickJob('q');await click('Cancel');status={...status,path:'/p/second'};revision++;await draw();
  assert.equal(Boolean(document.querySelector('.card-shell')),false);assert.equal(Boolean(document.querySelector('.wb-jobs__detail-id')),false);assert.deepEqual(sent,[]);
});

test('Files stages each selected file once and refreshes staged rows through the drop notification',async()=>{
  await draw('Files');assert.ok(button('Upload'));const input=document.querySelector('input[type=file]');assert.equal(input.hidden,true);assert.equal(input.multiple,true);assert.ok(input.getAttribute('aria-label'));
  const first=new File(['hello'],'notes.txt'), second=new File(['theorem demo : True := by trivial'],'demo.lean');
  await chooseFiles([first,second]);await waitFor(()=>!button('Uploading'),'both uploads complete');
  const uploads=requests.filter(([path])=>path==='/api/upload');assert.equal(uploads.length,2);assert.equal(uploads[0][1].body,first);assert.equal(uploads[1][1].body,second);
  assert.ok(requests.filter(([path])=>path==='/api/uploads').length>=2);assert.deepEqual(sent,[]);
});

test('Files prints upload failures, continues the batch, and stops an old project batch after switching',async()=>{
  await draw('Files');
  uploadReply=init=>init.headers['X-Hardy-Filename']==='bad.txt'?new Response('{"error":"too large"}',{status:400}):null;
  await chooseFiles([new File(['bad'],'bad.txt'),new File(['good'],'good.txt')]);await waitFor(()=>!button('Uploading'),'the batch continues after failure');
  assert.match(document.body.textContent,/bad.txt: too large/);
  assert.equal(requests.filter(([path])=>path==='/api/upload').length,2,'failure does not stop the next file');
  let finish;uploadReply=new Promise(resolve=>{finish=resolve;});
  await chooseFiles([new File(['one'],'first.txt'),new File(['two'],'second.txt')]);
  await waitFor(()=>requests.some(([path,init])=>path==='/api/upload' && init.headers['X-Hardy-Filename']==='first.txt'),'the first request has reached transport');
  assert.equal(button('Uploading').disabled,true);
  status={...status,path:'/p/second'};revision++;await draw();
  await act(async()=>finish(new Response('{"name":"first.txt"}')));await tick();
  assert.equal(requests.filter(([path])=>path==='/api/upload').length,3);assert.doesNotMatch(document.body.textContent,/too large/);assert.ok(button('Upload'));
});

test('Files CAS State, Reset and Export only compose existing commands',async()=>{
  data['/api/files'].cas=[{path:'cas/cells.jsonl',bytes:0,modified:1}];arg='cas/cells.jsonl';await draw('Files');await tick();
  for(const label of ['State','Reset','Export'])await click(label);
  assert.deepEqual(composed,['/cas state','/cas reset','/cas export']);assert.deepEqual(sent,[]);
});
