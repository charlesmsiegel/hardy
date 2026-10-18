// Keep failure feedback and remaining accessible controls at their real
// mount boundaries. HTTP/session snapshots are the only controlled seams.
import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import React,{act} from 'react';
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/#/home'});
globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createRoot}=await import('react-dom/client');
const server=await createServer({server:{middlewareMode:true,ws:false}});
const modules={};
for(const [name,path] of Object.entries({Shell:'workbench/Shell',ModelPicker:'chat/ModelPicker',Prompt:'chat/Prompt',Publications:'pages/Publications',Checkpoints:'pages/Checkpoints',Files:'pages/Files',Tree:'pages/Tree',Palette:'workbench/CommandPalette'})){
  modules[name]=(await server.ssrLoadModule(`/src/${path}.jsx`)).default;
}
const {default:SessionProvider,SessionContext}=await server.ssrLoadModule('/src/session/SessionProvider.jsx');
const root=createRoot(document.getElementById('root'));
let revision=900,status,data,requests,sent,composed,sources,modelReply,inputReply;
const noop=()=>{};
const draw=(name,props={},provider=false)=>act(async()=>root.render(provider
  ?React.createElement(SessionProvider,null,React.createElement(modules[name],props))
  :React.createElement(SessionContext.Provider,{value:{status,revision,messages:[],send:line=>sent.push(line),compose:line=>composed.push(line),setDraft:noop}},React.createElement(modules[name],props))));
const button=label=>[...document.querySelectorAll('button')].find(node=>node.textContent.trim()===label);
const click=async label=>{assert.ok(button(label),label);await act(()=>button(label).click());};
const type=(node,value)=>act(()=>{
  assert.ok(node,'labelled control exists');
  const prototype=node.tagName==='SELECT'?window.HTMLSelectElement.prototype:node.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,value);
  node.dispatchEvent(new window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));
});
const submit=()=>act(async()=>document.querySelector('form[data-action="goal"]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
const visible=node=>{for(let parent=node;parent;parent=parent.parentElement)if(parent.hidden||parent.style.display==='none')return false;return true;};
const emit=event=>act(async()=>sources.at(-1).onmessage({data:JSON.stringify(event)}));
const hideDock=async()=>{
  const toggle=document.querySelector('.wb-sidebar__foot button:not(.wb-sidebar__collapse)');assert.ok(toggle);
  await act(()=>toggle.click());await act(()=>toggle.click());
  assert.equal(document.querySelector('.wb-dock').style.display,'none');
};
const goalErrors=()=>[...document.querySelectorAll('.wb-page [role="alert"]')].filter(visible).map(node=>node.textContent);
test.beforeEach(async t=>{
  revision++;requests=[];sent=[];composed=[];sources=[];modelReply=null;
  inputReply=()=>new Response('{"error":"GOAL REFUSED BY SERVER"}',{status:409});
  status={open:true,path:'/p/first',slug:'first',chat:'main',model:'reported-model',turn_running:false,command_running:false,provider_active:false,prompts:[]};
  window.history.replaceState(null,'','#/home');
  data={
    '/api/state':status,'/api/commands':[],'/api/projects':[],'/api/transcript':[],
    '/api/summary':{goal:'Original goal'},'/api/record':{items:0,by_kind:{}},'/api/chats':[{id:'main',title:'main',turns:0}],
    '/api/jobs':{counts:{},delegations:[],attention:[]},'/api/environment':{checks:[]},
    '/api/tree':{entries:[]},'/api/models':{backend:'fake',rows:[]},'/api/graph':{nodes:[],edges:[]},
    '/api/files':{lean:[],tex:[],cas:[],pdf:[]},'/api/uploads':[],
  };
  globalThis.EventSource=class {constructor(){sources.push(this);}close(){}};
  t.mock.method(globalThis,'fetch',async(path,init)=>{
    requests.push([path,init]);
    if(path==='/api/models' && modelReply)return modelReply;
    if(path==='/api/input')return inputReply();
    assert.ok(path in data,path);return new Response(JSON.stringify(data[path]));
  });
});
test.afterEach(async()=>act(()=>root.render(null)));
test.after(async()=>{await act(()=>root.unmount());await server.close();dom.window.close();});

test('models-only 503 stays visible beside the authoritative picker, including typed identity and busy controls',async()=>{
  modelReply=new Response('{"error":"MODEL CATALOG TEMPORARILY UNAVAILABLE"}',{status:503});
  const props={model:'reported-model',revision,onSend:line=>sent.push(line),owner:status.path};
  await draw('ModelPicker',props);
  assert.match(document.body.textContent,/MODEL CATALOG TEMPORARILY UNAVAILABLE/);
  const select=document.querySelector('select[aria-label="Model"]');assert.equal(select.value,'reported-model');assert.doesNotMatch(select.title,/undefined/);
  await type(select,'__other__');assert.match(document.body.textContent,/MODEL CATALOG TEMPORARILY UNAVAILABLE/);
  await type(document.querySelector('[aria-label="Model identity"]'),'private-model');
  await draw('ModelPicker',{...props,busy:true});assert.equal(document.querySelector('input').disabled,true);
  await act(()=>document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));assert.deepEqual(sent,[]);
  await draw('ModelPicker',{...props,busy:false});await click('Use model');assert.deepEqual(sent,['/model private-model']);
  assert.equal(document.querySelector('select').value,'reported-model');
});

test('an old project catalog failure cannot replace a new project catalog',async()=>{
  let finish;modelReply=new Promise(resolve=>{finish=resolve;});
  const props={model:'reported-model',revision,onSend:noop,owner:'/p/old'};await draw('ModelPicker',props);
  modelReply=null;await draw('ModelPicker',{...props,owner:'/p/new'});
  await act(async()=>finish(new Response('{"error":"OLD PROJECT FAILURE"}',{status:503})));
  assert.doesNotMatch(document.body.textContent,/OLD PROJECT FAILURE/);assert.match(document.querySelector('select').title,/fake/);
});

test('a real Shell shows a refused goal with dock hidden, follows shared refusal lifetime and clears on project switch',async()=>{
  await draw('Shell',{},true);
  const toggle=document.querySelector('.wb-sidebar__foot button:not(.wb-sidebar__collapse)');assert.ok(toggle);
  await act(()=>toggle.click());await act(()=>toggle.click());
  await type(document.querySelector('form[data-action="goal"] textarea'),'Rejected goal');await submit();
  const alert=[...document.querySelectorAll('.wb-page [role="alert"]')].find(node=>node.textContent==='GOAL REFUSED BY SERVER');
  assert.ok(alert && visible(alert),'the server refusal is visible beside the form while the idle snapshot is stale');
  assert.deepEqual(requests.filter(([path])=>path==='/api/input').map(([,init])=>JSON.parse(init.body)),[{text:'/goal Rejected goal'}]);
  assert.equal(data['/api/summary'].goal,'Original goal','no fabricated successful goal');
  await emit({type:'state',...status,turn_running:true,seq:1});assert.match(document.querySelector('.wb-page').textContent,/GOAL REFUSED BY SERVER/);
  await emit({type:'state',...status,seq:2});assert.doesNotMatch(document.querySelector('.wb-page').textContent,/GOAL REFUSED BY SERVER/);
  await submit();assert.match(document.querySelector('.wb-page').textContent,/GOAL REFUSED BY SERVER/);
  status={...status,path:'/p/second',slug:'second'};data['/api/summary'].goal='Second project goal';
  await emit({type:'state',...status,seq:3});
  assert.doesNotMatch(document.querySelector('.wb-page').textContent,/GOAL REFUSED BY SERVER/);
  assert.equal(document.querySelector('form[data-action="goal"] textarea').value,'Second project goal');
});

for(const [label,reply,message] of [
  ['HTTP 503',()=>new Response('{"error":"GOAL WRITE FAILED"}',{status:503}),'GOAL WRITE FAILED'],
  ['rejected fetch',()=>{throw new TypeError('Network request failed');},'Network request failed'],
])test(`a real Shell shows this goal's ${label} failure with Chat hidden and preserves the transcript error`,async()=>{
  inputReply=reply;await draw('Shell',{},true);await hideDock();
  await emit({type:'error',text:'UNRELATED OLD ERROR',seq:1});
  assert.deepEqual(goalErrors(),[],'transcript history is not this form\'s result');
  await type(document.querySelector('form[data-action="goal"] textarea'),'Rejected goal');await submit();
  assert.deepEqual(goalErrors(),[message]);
  assert.ok(document.querySelector('.wb-dock').textContent.includes(message),'the existing transcript error is retained');
  assert.deepEqual(requests.filter(([path])=>path==='/api/input').map(([,init])=>JSON.parse(init.body)),[{text:'/goal Rejected goal'}]);
  assert.equal(data['/api/summary'].goal,'Original goal','no fabricated successful goal');
  await emit({type:'state',...status,seq:2});assert.deepEqual(goalErrors(),[message],'an idle snapshot does not erase a request failure');
  await type(document.querySelector('form[data-action="goal"] textarea'),'Revised goal');assert.deepEqual(goalErrors(),[]);
});

for(const owner of ['project','chat'])test(`a delayed goal failure stays out of the replacement ${owner}'s form`,async()=>{
  let finish;inputReply=()=>new Promise(resolve=>{finish=resolve;});
  await draw('Shell',{},true);await hideDock();
  await type(document.querySelector('form[data-action="goal"] textarea'),'Old owner goal');await submit();
  status=owner==='project'?{...status,path:'/p/goal-feedback-second',slug:'goal-feedback-second'}:{...status,chat:'other'};
  data['/api/summary'].goal='Current owner goal';await emit({type:'state',...status,seq:1});
  await act(async()=>finish(new Response('{"error":"OLD OWNER FAILED"}',{status:503})));
  assert.deepEqual(goalErrors(),[]);
  assert.equal(document.querySelector('form[data-action="goal"] textarea').value,'Current owner goal');
});

test('a late goal failure does not attach to an edited draft',async()=>{
  let finish;inputReply=()=>new Promise(resolve=>{finish=resolve;});
  await draw('Shell',{},true);await hideDock();
  await type(document.querySelector('form[data-action="goal"] textarea'),'Submitted goal');await submit();
  await type(document.querySelector('form[data-action="goal"] textarea'),'Different draft');
  await act(async()=>finish(new Response('{"error":"PREVIOUS DRAFT FAILED"}',{status:503})));
  assert.deepEqual(goalErrors(),[]);assert.equal(document.querySelector('form[data-action="goal"] textarea').value,'Different draft');
});

test('a newer goal submission owns its feedback and a retry clears it without inventing success',async()=>{
  let finish;inputReply=()=>new Promise(resolve=>{finish=resolve;});
  await draw('Shell',{},true);await hideDock();
  await type(document.querySelector('form[data-action="goal"] textarea'),'Retry goal');await submit();
  inputReply=()=>new Response('{"error":"LATEST REQUEST FAILED"}',{status:503});await submit();
  assert.deepEqual(goalErrors(),['LATEST REQUEST FAILED']);
  await act(async()=>finish(new Response('{"error":"EARLIER REQUEST FAILED"}',{status:503})));
  assert.deepEqual(goalErrors(),['LATEST REQUEST FAILED']);
  inputReply=()=>new Response('{}');await submit();assert.deepEqual(goalErrors(),[]);
  assert.equal(data['/api/summary'].goal,'Original goal');
});

test('the Prompt line is named by its visible question and retains Enter/Escape answers',async()=>{
  const answers=[];const props={prompt:{id:'question',kind:'line',title:'Name this checkpoint'},onAnswer:(...args)=>answers.push(args)};
  await draw('Prompt',props);let input=document.querySelector('input');
  const heading=document.getElementById(input.getAttribute('aria-labelledby'));assert.equal(heading?.textContent,'Name this checkpoint');
  await type(input,'before-search');await act(()=>input.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Enter',bubbles:true})));
  await act(()=>input.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
  assert.deepEqual(answers,[['question','before-search'],['question',null]]);
});

test('Publications Link selectors have visible names and still confirm the exact selected source and target',async()=>{
  const item=(id,kind,family,links=[])=>({id,name:id,kind,family,visibility:'internal',visibility_tone:'muted',role:null,link_source_kinds:links});
  data['/api/publications']={items:[item('illustration','example','other',['illustrates']),item('claim','theorem','result')],candidates:[],revision:1};
  await draw('Publications');const selects=[...document.querySelectorAll('select')];assert.equal(selects.length,2);
  assert.deepEqual(selects.map(control=>[...control.labels].map(label=>label.textContent).join('')),['source (illustrates/documents only)','target (any other item)']);
  assert.equal(document.querySelector('[aria-label="bundle name"]').tagName,'INPUT');
  await type(selects[0],'illustration');await click('illustrates');await type(selects[1],'claim');await click('Link…');
  assert.deepEqual(sent,[]);assert.match(document.querySelector('.card-shell').textContent,/\/project link illustration illustrates claim/);
  await click('Yes');assert.deepEqual(sent,['/project link illustration illustrates claim']);
});

test('Checkpoints names the save field, sends Save immediately and confirms the exact restore row',async()=>{
  data['/api/checkpoints']={checkpoints:[{id:'cp-12',slug:'first',name:'Before search',created:'today',chat:'main',files:3,bytes:64}]};
  await draw('Checkpoints');await type(document.querySelector('[aria-label="checkpoint name"]'),'next-checkpoint');
  await click('Save');assert.deepEqual(sent,['/checkpoint next-checkpoint']);
  await click('Restore…');assert.match(document.querySelector('.card-shell').textContent,/\/checkpoint restore cp-12/);assert.equal(sent.length,1);
});

test('CAS row rerun and the named new-cell field compose their own exact commands',async()=>{
  data['/api/files'].cas=[{path:'cas/a.py',bytes:1,modified:1}];
  data['/api/cas/cells']={cells:[{path:'cas/a.py',seq:1,segment:1,live:true,accepted:true,status:'ok',author:'human',duration_ms:1}],segment:1,total:1,truncated:false};
  await draw('Files',{arg:'cas/a.py'});await act(async()=>new Promise(resolve=>setTimeout(resolve,0)));
  const input=document.querySelector('[aria-label="new CAS cell"]');assert.ok(input);await type(input,'factor(x**2-1)');await click('Compose');await click('Re-run');
  assert.deepEqual(composed,['/cas factor(x**2-1)','/cas run "cas/a.py"']);assert.deepEqual(sent,[]);
});

test('Tree with entries and no goal is a real tree; a goal alone does not fabricate entries',async()=>{
  data['/api/summary'].goal='';data['/api/tree']={entries:[{entry_id:'e12',parent_id:null,type:'message'}],active_leaf:'e12'};
  await draw('Tree');assert.equal(document.querySelectorAll('.wb-tree__row').length,1);assert.doesNotMatch(document.body.textContent,/No conversation yet/);
  revision++;data['/api/summary'].goal='A goal';data['/api/tree']={entries:[]};await draw('Tree');
  assert.match(document.body.textContent,/No conversation yet/);assert.equal(document.querySelectorAll('.wb-tree__row').length,0);
});

test('palette instructions distinguish opening a workflow form from composing a command',async()=>{
  const runs=[];await draw('Palette',{open:true,onClose:noop,onRun:run=>runs.push(run),entries:[
    {id:'form:context',kind:'form',label:'Create context',detail:'Record context',run:{page:'workflows',arg:'context'}},
    {id:'command:model',kind:'command',label:'/model',detail:'Switch model',run:{compose:'/model '}},
  ]});
  assert.match(document.querySelector('label').textContent,/workflow forms/);
  assert.match(document.querySelector('.panel__note').textContent,/Forms open on Workflows/);
  await act(()=>document.querySelector('[role="option"]').click());assert.deepEqual(runs,[{page:'workflows',arg:'context'}]);
});
