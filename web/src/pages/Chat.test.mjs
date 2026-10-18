// Use the real mounted Chat, Header and ModelPicker. Only transport and
// session snapshots are controlled: late title replies must retain their
// owner, and selecting a model sends a command without guessing its result.
import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import React,{act} from 'react';

const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/#/chat'});
globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createRoot}=await import('react-dom/client');
const server=await createServer({server:{middlewareMode:true,ws:false}});
const {default:Chat}=await server.ssrLoadModule('/src/pages/Chat.jsx');
const {SessionContext}=await server.ssrLoadModule('/src/session/SessionProvider.jsx');
const root=createRoot(document.getElementById('root'));
let status,revision=0,requests,sent,refreshes,reply,refreshReply,rows;
const noop=()=>{};
const draw=()=>act(async () => root.render(React.createElement(SessionContext.Provider,{value:{status,revision,commands:[],messages:[],prompts:[],draft:'',loaded:true,
  send:(line)=>sent.push(line),answer:noop,cancel:noop,setDraft:noop,blocked:noop,refreshProjects:()=>{refreshes++;return refreshReply;}}},React.createElement(Chat))));
const navigate=(hash)=>act(async()=>{window.history.replaceState(null,'',hash);window.dispatchEvent(new window.HashChangeEvent('hashchange'));});
const form=(kind)=>document.querySelector(`form[data-chat-action="${kind}"]`);
const type=async(node,value)=>act(async()=>{
  assert.ok(node,'control exists');
  const proto=node.tagName==='SELECT'?window.HTMLSelectElement.prototype:window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto,'value').set.call(node,value);
  node.dispatchEvent(new window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));
});
const submit=(kind)=>act(async()=>{assert.ok(form(kind),`${kind} form exists`);form(kind).dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));});
const tick=()=>act(async()=>new Promise(resolve=>setTimeout(resolve,0)));
test.beforeEach(async(t)=>{
  revision++;requests=[];sent=[];refreshes=0;reply=null;refreshReply=undefined;
  status={open:true,path:'/p/first',slug:'sylow',chat:'discussion',model:'model-one',turn_running:false,command_running:false,provider_active:false};
  rows=[{id:'main',title:'main',turns:0},{id:'discussion',title:'Original title',turns:1},{id:'other',title:'Other title',turns:0}];
  t.mock.method(globalThis,'fetch',async(path,init)=>{
    requests.push([path,init]);
    if(init.method!=='GET'){
      if(path==='/api/open')return new Response('{}');
      return reply||new Response(JSON.stringify({id:'created-chat',title:'Sylow',created:1}));
    }
    const data={'/api/summary':{goal:''},'/api/chats':rows,'/api/tree':{entries:[]},'/api/jobs':{attention:[]},
      '/api/models':{backend:'fake',authentication:'local test',rows:[{value:'model-one',label:'Model one'},{value:'model-two',label:'Model two'}]},'/api/graph':{nodes:[],edges:[]}};
    assert.ok(path in data,path);return new Response(JSON.stringify(data[path]));
  });
  await navigate('#/chat');await draw();
});
test.afterEach(async()=>act(()=>root.render(null)));
test.after(async()=>{await act(()=>root.unmount());await server.close();dom.window.close();});

test('New chat posts a labelled title then refreshes and follows the existing chat open route',async()=>{
  await type(form('new')?.querySelector('[name="title"]'),'Sylow');await submit('new');await tick();
  const mutations=requests.filter(([,init])=>init.method!=='GET');
  assert.deepEqual(mutations.map(([path,init])=>[path,init.method,JSON.parse(init.body)]),[
    ['/api/projects/sylow/chats','POST',{title:'Sylow'}],['/api/open','POST',{path:'/p/first',chat:'created-chat'}],
  ]);
  assert.equal(window.location.hash,'#/chat/created-chat');assert.equal(refreshes,1);
  assert.ok(requests.filter(([path])=>path==='/api/chats').length>=2,'reloads real chat rows');
  assert.ok(form('new').querySelector('[name="title"]').labels.length);
});

test('rename targets the current chat with encoded segments and refreshes server rows',async()=>{
  status={...status,slug:'sylow space',chat:'discussion space'};rows=[{id:'discussion space',title:'Original title'}];revision++;await draw();
  const input=form('rename')?.querySelector('[name="title"]');assert.ok(input?.labels.length);assert.equal(input.value,'Original title');
  rows=[{id:'discussion space',title:'Renamed title'}];
  await type(input,'Renamed title');await submit('rename');
  const [path,init]=requests.find(([,init])=>init.method==='PATCH');
  assert.equal(path,'/api/projects/sylow%20space/chats/discussion%20space');assert.deepEqual(JSON.parse(init.body),{title:'Renamed title'});
  assert.equal(refreshes,1);assert.match(document.querySelector('.wb-chat-rail__chats').textContent,/Renamed title/);
});

test('the main chat and busy operations disable title mutations; server errors are printed',async()=>{
  status={...status,chat:'main'};await draw();assert.equal(form('rename')?.querySelector('button').matches(':disabled'),true);
  status={...status,chat:'discussion',provider_active:true};await draw();
  for(const kind of ['new','rename'])assert.equal(form(kind).querySelector('input').matches(':disabled'),true);
  status={...status,provider_active:false};await draw();
  reply=new Response('{"error":"a chat needs a title"}',{status:400});
  await type(form('new').querySelector('input'),'Title');await submit('new');
  assert.equal(form('new').querySelector('[role="alert"]').textContent,'a chat needs a title');assert.equal(refreshes,0);
});

test('a late create response from an old project cannot navigate, refresh or leak the draft',async()=>{
  let finish;reply=new Promise(resolve=>{finish=resolve;});
  await type(form('new')?.querySelector('input'),'Old project title');await submit('new');
  assert.equal(form('new').querySelector('input').matches(':disabled'),true);
  status={...status,path:'/p/second',slug:'second'};await draw();
  assert.equal(form('new').querySelector('input').value,'');
  await act(async()=>finish(new Response('{"id":"old-created","title":"Old project title","created":1}')));await tick();
  assert.equal(window.location.hash,'#/chat');assert.equal(refreshes,0);assert.equal(requests.some(([path])=>path==='/api/open'),false);
});

test('changing project while the create refresh is deferred does not follow its old chat',async()=>{
  let finish;refreshReply=new Promise(resolve=>{finish=resolve;});
  await type(form('new')?.querySelector('input'),'Old project title');await submit('new');assert.equal(refreshes,1);
  status={...status,path:'/p/second',slug:'second'};await draw();
  await act(async()=>finish());await tick();
  assert.equal(window.location.hash,'#/chat');assert.equal(requests.some(([path])=>path==='/api/open'),false);
});

test('switching chats during rename pins the PATCH and discards its late error and draft',async()=>{
  let finish;reply=new Promise(resolve=>{finish=resolve;});
  await type(form('rename')?.querySelector('input'),'Old pending rename');await submit('rename');
  status={...status,chat:'other'};await draw();
  assert.equal(form('rename').querySelector('input').value,'Other title');assert.equal(form('rename').querySelector('input').disabled,false);
  await act(async()=>finish(new Response('{"error":"OLD CHAT ERROR"}',{status:400})));
  assert.doesNotMatch(document.body.textContent,/OLD CHAT ERROR/);assert.equal(refreshes,0);
  assert.equal(requests.find(([,init])=>init.method==='PATCH')[0],'/api/projects/sylow/chats/discussion');
});

test('the Header model picker binds server state and sends through session without optimistic model changes',async()=>{
  const picker=document.querySelector('.wb-chat-header select[aria-label="Model"]');assert.ok(picker);assert.equal(picker.value,'model-one');
  await type(picker,'model-two');assert.deepEqual(sent,['/model model-two']);assert.equal(picker.value,'model-one');
  status={...status,model:'model-two'};await draw();assert.equal(picker.value,'model-two');
});

test('typed model identity is labelled, blocked while busy and reset on project switch',async()=>{
  await type(document.querySelector('.wb-chat-header select[aria-label="Model"]'),'__other__');
  let input=document.querySelector('[aria-label="Model identity"]');assert.ok(input);await type(input,'private-old-model');
  status={...status,command_running:true};await draw();assert.equal(input.disabled,true);
  await act(()=>input.closest('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));assert.deepEqual(sent,[]);
  status={...status,path:'/p/second',slug:'second',command_running:false};await draw();
  assert.equal(document.querySelector('[aria-label="Model identity"]'),null);
  await type(document.querySelector('.wb-chat-header select[aria-label="Model"]'),'__other__');
  input=document.querySelector('[aria-label="Model identity"]');assert.equal(input.value,'');await type(input,'new-model');
  await act(()=>input.closest('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));assert.deepEqual(sent,['/model new-model']);
});
