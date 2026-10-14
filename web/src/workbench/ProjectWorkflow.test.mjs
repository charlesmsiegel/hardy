import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import React,{act} from 'react';
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createRoot}=await import('react-dom/client');
const server=await createServer({server:{middlewareMode:true,ws:false}});
const {ContextForm,LiveWorkflow,ObligationWork,ManuscriptIntake}=await server.ssrLoadModule('/src/workbench/ProjectWorkflow.jsx');
const {AdmissionReview,SelectedAssessment,SourceNavigation,IntakeReview,RefereeControl,AssessmentReport,BundleExport}=await server.ssrLoadModule('/src/workbench/ProjectReview.jsx');
const {filterObligations,useProjectAction,sourceChoiceKey,evidenceChoiceKey}=await server.ssrLoadModule('/src/workbench/projectForms.js');
const root=createRoot(document.getElementById('root'));
test.after(async () => {await act(() => root.unmount());await server.close();dom.window.close();});
const ref=(id,letter='a') => ({id,digest:letter.repeat(64)});
const row=(id,kind,letter='a') => ({...ref(id,letter),ref:ref(id,letter),kind,name:id,semantics:[],statement:id});
const type=async (name,value) => {
  const input=document.querySelector(`[name="${name}"]`);assert.ok(input,name);
  await act(() => {const prototype=input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : input.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype,'value').set.call(input,value);input.dispatchEvent(new window.Event(input.tagName === 'SELECT' ? 'change' : 'input',{bubbles:true}));});
};
const submit=async () => act(() => document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true})));
test('context and Research forms use accessible labelled exact selections',async () => {
  let request;globalThis.fetch=async (path,init) => {request=JSON.parse(init.body);return new Response('{"ok":true,"record":{}}');};
  await act(() => root.render(React.createElement(ContextForm,{project:'/p'})));
  await type('id','C');await type('label','Local context');await submit();
  assert.deepEqual(request.request,{id:'C',label:'Local context',parent:null,supersedes:null,declarations:[],bindings:[]});
  const target=row('T','claim');const scope={...row('S','scope','b'),must_prove:[ref('T')],allowed_background:[],allowed_interfaces:[]};
  await act(() => root.render(React.createElement(LiveWorkflow,{project:'/p',catalog:{items:[target],scopes:[scope]}})));
  await type('id','research');await type('target',`T@${'a'.repeat(64)}`);await type('scope',`S@${'b'.repeat(64)}`);await submit();
  assert.equal(request.operation,'research');assert.deepEqual(request.request.target,ref('T'));assert.deepEqual(request.request.scope,ref('S','b'));
  for (const control of document.querySelectorAll('input,textarea,select')) assert.ok(control.labels.length,`label for ${control.name}`);
});
test('obligation work includes all six states and accurate outstanding filter',async () => {
  const states=['open','investigating','blocked','resolved','dismissed','abandoned'];
  const obligations=states.map((status) => ({...row(status,'prove'),item:ref('T'),status,reason:status}));
  assert.deepEqual(filterObligations(obligations).map((value) => value.status),states.slice(0,3));
  for (const status of states) assert.equal(filterObligations(obligations,status).length,1);
  await act(() => root.render(React.createElement(ObligationWork,{catalog:{obligations}})));
  assert.match(document.body.textContent,/3 obligation/);assert.match(document.body.textContent,/Outstanding/);
  assert.equal(document.querySelectorAll('option').length,8);
});
test('admission approval is unavailable until backend gates are current',async () => {
  const request={id:'admission:1',digest:'c'.repeat(64),subject:ref('P'),scope:ref('S','b'),sources:[],missing_gates:['correspondence','search'],can_approve:false,reason:'Missing search',decisions:[]};
  await act(() => root.render(React.createElement(AdmissionReview,{project:'/p',catalog:{admissions:[request],items:[],scopes:[]}})));
  await type('admission_selection','admission:1');
  assert.equal(document.querySelector('option[value="approve"]').disabled,true);
  assert.match(document.body.textContent,/Missing search/);assert.match(document.body.textContent,/correspondence, search/);
});
test('a late selected-argument report cannot update another selection',async () => {
  let finish;globalThis.fetch=async () => new Promise((resolve) => {finish=() => resolve(new Response('{"ok":true,"record":{"target":{"statement":"OLD RESPONSE"}}}'));});
  const target=row('T','claim');const original={...row('A','argument','b'),semantics:[['argument-role','original']]};
  const repaired={...row('R','argument','c'),semantics:[['argument-role','repaired']]};
  const catalog={items:[target,original,repaired],scopes:[],relations:[{kind:'supports',source:ref('A','b'),target:ref('T')},{kind:'supports',source:ref('R','c'),target:ref('T')}]};
  await act(() => root.render(React.createElement(SelectedAssessment,{project:'/p',target,catalog})));
  await submit();await type('report_repaired',`R@${'c'.repeat(64)}`);
  await act(async () => {finish();await new Promise((resolve) => setTimeout(resolve,0));});
  assert.doesNotMatch(document.body.textContent,/OLD RESPONSE/);
});
test('source passages use backend Unicode codepoint offsets for non-BMP text',async () => {
  const source=row('paper','source');
  const item={...row('claim','claim','b'),semantics:[['source-region',JSON.stringify({source:ref('paper'),start:4,end:5})]]};
  await act(() => root.render(React.createElement(SourceNavigation,{item,catalog:{sources:[{...source,text:'𝒳 = 𝒳'}]}})));
  assert.equal(document.querySelector('pre').textContent,'𝒳');
  assert.match(document.body.textContent,/characters 4–5/);
});
test('mapping approval displays proposed hypotheses, conventions and exact cited premises',async () => {
  const span={path:'paper.tex',digest:'f'.repeat(64),start:0,end:10,quote:'by Lemma 2'};
  const mappings=[{key:'ctx',kind:'context',name:'Local setup',span,context:'parent',
    declarations:[{id:'hyp',symbol:'hX',semantic_type:'X is compact',role:'local_hypothesis',dependencies:[ref('X')],justification:ref('Justification','b')}],
    bindings:[{id:'notation',kind:'convention',symbol:'V',meaning:'the selected completed space',target:ref('Representation','c')}],requirements:[{kind:'resolve_representation',reason:'Choose a model for V'}]},
  {key:'cite',kind:'citation',name:'Quoted external lemma',span,context:'ctx',source:'step1',target:'main',argument:'argument1',
    required_statement:'Every compact X admits the required finite cover',paper_id:'arxiv:2411.12345v2',statement_ref:'Lemma 2.7',declarations:[],bindings:[]}];
  const proposal={id:'proposal',request:{id:'paper',citation_depth:2,target_papers:['arxiv:2501.00001v3']},mappings,gaps:[],sources:[]};
  await act(() => root.render(React.createElement(IntakeReview,{project:'/p',intake:{proposals:[{ref:ref('proposal'),proposal,current:true}]}})));
  await type('proposal_selection',`proposal@${'a'.repeat(64)}`);
  const text=document.body.textContent;
  for (const expected of ['Local declaration','hX','Semantic type or hypothesis','X is compact','Declaration role','local_hypothesis','Justification','Declaration dependencies','Convention kind','convention','Convention meaning','the selected completed space','Convention target','Representation','Canonical cited paper and version','arxiv:2411.12345v2','Cited theorem or statement','Lemma 2.7','Exact required premise','Every compact X admits the required finite cover','Citation use site','step1','Parent context','parent','Choose a model for V']) assert.ok(text.includes(expected),expected);
  assert.equal(document.querySelector('[name="verdict_ctx"]').value,'unresolved');
  assert.equal(document.querySelector('[name="verdict_cite"]').value,'unresolved');
});
test('Referee submits the unchanged reviewed scope, ordered result sets and depth',async () => {
  let request;globalThis.fetch=async (path,init) => {request=JSON.parse(init.body);return new Response('{"ok":true,"record":{"receipt":{"status":"failed","detail":"Citation retrieval unavailable"}}}');};
  const proposalRef=ref('proposal','d');const review={id:'review',proposal:proposalRef,scope:ref('S','b'),main_results:[ref('B','c'),ref('A')],required_internal:[ref('L','e')]};
  const intake={proposals:[{ref:proposalRef,proposal:{request:{citation_depth:2}}}],reviews:[{ref:ref('review','f'),review,current:true}]};
  const catalog={items:[row('A','claim'),row('B','claim','c'),row('L','lemma','e')],scopes:[{...row('S','scope','b'),must_prove:review.main_results,allowed_background:[],allowed_interfaces:[]}]};
  await act(() => root.render(React.createElement(RefereeControl,{project:'/p',catalog,intake})));
  await type('referee_review',`review@${'f'.repeat(64)}`);await type('id','audit');await submit();
  assert.deepEqual(request.request.scope,review.scope);
  assert.deepEqual(request.request.main_results,review.main_results);
  assert.deepEqual(request.request.required_internal,review.required_internal);
  assert.equal(request.request.citation_depth,2);
  assert.deepEqual(request.request.original,[]);assert.deepEqual(request.request.repaired,[]);
  assert.match(document.body.textContent,/Reviewed citation depth: 2/);
  assert.match(document.querySelector('[role="status"]').textContent,/Failed.*Citation retrieval unavailable/);
});
test('Research displays blocked and failed owner outcomes returned with HTTP success',async () => {
  const target=row('T','claim');const scope={...row('S','scope','b'),must_prove:[ref('T')],allowed_background:[],allowed_interfaces:[]};
  for (const status of ['blocked','failed']) {
    globalThis.fetch=async () => new Response(JSON.stringify({ok:true,record:{status,detail:'Missing configured proof capability'}}));
    await act(() => root.render(React.createElement(LiveWorkflow,{key:status,project:'/p',catalog:{items:[target],scopes:[scope]}})));
    await type('id',status);await type('target',`T@${'a'.repeat(64)}`);await type('scope',`S@${'b'.repeat(64)}`);await submit();
    assert.match(document.querySelector('[role="status"]').textContent,new RegExp(`${status}.*Missing configured proof capability`,'i'));
    assert.doesNotMatch(document.querySelector('[role="status"]').textContent,/Recorded/);
  }
});
test('current supported assessments and an empty axiom audit display without absence claims',async () => {
  const report={target:{statement:'True'},statement:{status:'supported_by_reviewed_formal_proof',reason:null,unresolved:[],evidence_level:'reviewed_kernel_proof'},
    original_proof:{status:'defective',reason:'Missing inference',evidence_level:'attributed_review'},
    repaired_proof:{status:'supported_by_review',reason:null,unresolved:[],evidence_level:'attributed_review'},
    intermediate_claims:[],formal_proof:{status:'kernel_verified',axioms:[]},translation:{status:'approved'},assumptions:[],coverage_gaps:[]};
  await act(() => root.render(React.createElement(AssessmentReport,{report})));
  assert.match(document.body.textContent,/supported_by_reviewed_formal_proof/);
  assert.match(document.body.textContent,/Formal kernel audit: kernel_verified/);
  assert.match(document.body.textContent,/Translation correspondence: approved/);
  assert.match(document.body.textContent,/Kernel axioms: none in recorded audit/);
  assert.doesNotMatch(document.body.textContent,/No current assessment has been recorded|no current axiom audit recorded/);
});

function ActionHarness({selection}) {
  const action=useProjectAction('/p',selection);
  return React.createElement('div',null,
    React.createElement('button',{onClick:() => action.submit('research',{id:selection})},'Run'),
    React.createElement('button',{onClick:action.invalidate},'Edit'),
    React.createElement('output',{'data-pending':String(action.pending)},action.message));
}
const click=async (text) => act(() => [...document.querySelectorAll('button')].find((button) => button.textContent === text).click());
test('an old A response after A to B to A cannot clear the newer request busy state',async () => {
  const finish=[];
  globalThis.fetch=async () => new Promise((resolve) => finish.push((detail) => resolve(new Response(JSON.stringify({ok:true,record:{status:'completed',detail}})))));
  await act(() => root.render(React.createElement(ActionHarness,{selection:'A'})));await click('Run');
  await act(() => root.render(React.createElement(ActionHarness,{selection:'B'})));
  await act(() => root.render(React.createElement(ActionHarness,{selection:'A'})));await click('Run');
  assert.equal(finish.length,2);assert.equal(document.querySelector('output').dataset.pending,'true');
  await act(async () => {finish[0]('old A');await new Promise((resolve) => setTimeout(resolve,0));});
  assert.equal(document.querySelector('output').dataset.pending,'true');
  assert.doesNotMatch(document.body.textContent,/old A/);
  await act(async () => {finish[1]('current A');await new Promise((resolve) => setTimeout(resolve,0));});
  assert.equal(document.querySelector('output').dataset.pending,'false');
  assert.match(document.body.textContent,/current A/);
});
test('editing a pending form invalidates its response and still clears that request busy state',async () => {
  let finish;
  globalThis.fetch=async () => new Promise((resolve) => {finish=() => resolve(new Response('{"ok":true,"record":{"status":"completed","detail":"outdated response"}}'));});
  await act(() => root.render(React.createElement(ActionHarness,{selection:'C'})));await click('Run');await click('Edit');
  assert.equal(document.querySelector('output').dataset.pending,'true');
  await act(async () => {finish();await new Promise((resolve) => setTimeout(resolve,0));});
  assert.equal(document.querySelector('output').dataset.pending,'false');
  assert.doesNotMatch(document.body.textContent,/outdated response/);
});
test('model-only correspondence evidence never becomes a human approval',async () => {
  const translation={status:'approved',formalization_accepted:true,human_review:null,
    evidence:[{kind:'faithfulness',subject:ref('target'),producer:'hardy.live-formal',artifact:{uri:'hardy-live-formal:70e5f64c132a43a193989bb012b017a6',digest:'d'.repeat(64),locator:'faithful'}}]};
  await act(() => root.render(React.createElement(AssessmentReport,{report:{target:{statement:'True'},translation}})));
  assert.match(document.body.textContent,/Translation correspondence: approved/);
  assert.match(document.body.textContent,/Correspondence evidence producer: hardy.live-formal/);
  assert.match(document.body.textContent,/faithful/);
  assert.doesNotMatch(document.body.textContent,/Human.*approved/);
});
test('human attribution uses actual direct or known legacy review records',async () => {
  const review={id:'human:1',subject:ref('target'),translation:ref('formal','b'),relation:ref('relation','c'),scope:ref('scope','d'),
    verdict:'approved',reviewer:'Recorded operator',conditions:'Side-by-side supplied statements; no independence asserted',reason:'The exact meanings agree',recorded_at:'2026-10-14'};
  for (const translation of [{status:'approved',human_review:{...review,actor:'human'}},{status:'approved',reviews:[review]}]) {
    await act(() => root.render(React.createElement(AssessmentReport,{report:{target:{statement:'True'},translation}})));
    assert.match(document.body.textContent,/Human correspondence review record: approved/);
    assert.match(document.body.textContent,/Reviewer label: Recorded operator/);
    assert.match(document.body.textContent,/Actual review conditions: Side-by-side supplied statements; no independence asserted/);
    assert.match(document.body.textContent,/The exact meanings agree/);
  }
});
test('source choice survives insertion and clears a revised exact source version',async () => {
  const a={path:'A.tex',digest:'a'.repeat(64)},b={path:'B.tex',digest:'b'.repeat(64)},added={path:'NEW.tex',digest:'c'.repeat(64)};
  const requests=[];globalThis.fetch=async (path,init) => {requests.push(JSON.parse(init.body));return new Response('{"ok":true,"record":{}}');};
  await act(() => root.render(React.createElement(ManuscriptIntake,{project:'/p',intake:{source_choices:[a,b]}})));
  await type('id','intake');await type('scope_id','scope');await type('source',sourceChoiceKey(b));
  await act(() => root.render(React.createElement(ManuscriptIntake,{project:'/p',intake:{source_choices:[added,a,b]}})));
  await submit();assert.equal(requests[0].request.sources[0].path,'B.tex');assert.equal(requests[0].request.sources[0].digest,b.digest);
  const revised={...b,digest:'d'.repeat(64)};
  await act(() => root.render(React.createElement(ManuscriptIntake,{project:'/p',intake:{source_choices:[added,a,revised]}})));
  assert.equal(document.querySelector('[name="source"]').selectedOptions.length,0);
  await submit();assert.equal(requests.length,1);assert.match(document.body.textContent,/selected source version changed/);
  await type('source',sourceChoiceKey(revised));await type('id','intake-revised');await submit();
  assert.equal(requests[1].request.sources[0].digest,revised.digest);
});
test('formal evidence choice survives reordered candidates and blocks a missing exact selection',async () => {
  const evidence=(letter) => ({kind:'formal',subject:ref('T'),producer:'owner',artifact:{uri:`proof:${letter}`,digest:letter.repeat(64)}});
  const a=evidence('a'),b=evidence('b'),added=evidence('c');const candidate=(reference) => ({reference,label:reference.artifact.uri});
  let request;globalThis.fetch=async (path,init) => {request=JSON.parse(init.body);return new Response('{"ok":true,"record":"bundle"}');};
  const props={project:'/p',selection:{target:ref('T')}};
  await act(() => root.render(React.createElement(BundleExport,{...props,catalog:{evidence_refs:[candidate(a),candidate(b)]}})));
  await type('formal',evidenceChoiceKey(b));await type('output','bundle');
  await act(() => root.render(React.createElement(BundleExport,{...props,catalog:{evidence_refs:[candidate(added),candidate(a),candidate(b)]}})));
  await submit();assert.deepEqual(request.request.formal,b);
  await act(() => root.render(React.createElement(BundleExport,{...props,catalog:{evidence_refs:[candidate(added),candidate(a)]}})));
  assert.equal(document.querySelector('button[type="submit"]').disabled,true);assert.match(document.body.textContent,/Selected formal evidence changed/);
});
test('a changed member cannot silently shrink a multi-source intake selection',async () => {
  const a={path:'A.tex',digest:'a'.repeat(64)},b={path:'B.tex',digest:'b'.repeat(64)};
  const requests=[];globalThis.fetch=async (path,init) => {requests.push(JSON.parse(init.body));return new Response('{"ok":true,"record":{}}');};
  await act(() => root.render(React.createElement(ManuscriptIntake,{project:'/p',intake:{source_choices:[a,b]}})));
  await type('id','both');await type('scope_id','scope');
  await act(() => {const input=document.querySelector('[name="source"]');for (const option of input.options) option.selected=true;input.dispatchEvent(new window.Event('change',{bubbles:true}));});
  await act(() => root.render(React.createElement(ManuscriptIntake,{project:'/p',intake:{source_choices:[a,{...b,digest:'c'.repeat(64)}]}})));
  assert.equal(document.querySelector('button[type="submit"]').disabled,true);await submit();assert.equal(requests.length,0);
  await type('source',sourceChoiceKey(a));await submit();assert.equal(requests.length,1);assert.equal(requests[0].request.sources.length,1);
});
test('historical source bytes and admission passages remain readable after revision',async () => {
  const old=row('paper','source'),current=row('paper','source','b');
  const region={source:ref('paper'),start:0,end:8};
  const item={...row('argument','argument'),semantics:[['source-region',JSON.stringify(region)]]};
  const catalog={sources:[{...current,text:'REVISED'}],source_history:[{...old,text:'ORIGINAL passage'}]};
  await act(() => root.render(React.createElement(SourceNavigation,{item,catalog})));
  assert.equal(document.querySelector('pre').textContent,'ORIGINAL');
  const request={id:'admission:historical',digest:'c'.repeat(64),subject:ref('P'),scope:ref('S','d'),sources:[region],missing_gates:['search'],can_approve:false,reason:'Missing search',decisions:[]};
  await act(() => root.render(React.createElement(AdmissionReview,{project:'/p',catalog:{...catalog,admissions:[request],items:[{...row('P','external_result'),statement:'Exact proposed premise'},{...row('T','claim','e'),statement:'Target required conclusion'}],scopes:[{...row('S','scope','d'),must_prove:[ref('T','e')]}]}})));
  await type('admission_selection','admission:historical');
  assert.match(document.body.textContent,/ORIGINAL/);assert.match(document.body.textContent,/Exact proposed premise/);assert.match(document.body.textContent,/Target required conclusion/);
});
