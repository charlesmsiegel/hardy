import {useId, useState} from 'react';
import {exact, selector, fromSelector, formValues, optionalRef, lines, sourceChoiceKey, useProjectAction, filterObligations, OBLIGATION_STATES} from './projectForms.js';

export const pre = {whiteSpace:'pre-wrap',overflowWrap:'anywhere',fontSize:13,margin:'6px 0',padding:8,background:'var(--bg)'};
const inputStyle = {width:'100%',boxSizing:'border-box',padding:6,border:'1px solid var(--border)',borderRadius:4,background:'var(--bg)',color:'var(--fg)',font:'inherit'};
export function Field({label,name,area=false,required=true,type='text',initial='',...rest}) {
  const id = useId();
  const props = {id,name,required,defaultValue:initial,style:inputStyle,...rest};
  return <label htmlFor={id} style={{display:'block',marginBottom:8}}>{label}
    {area ? <textarea {...props} rows={4}/> : <input {...props} type={type}/>}
  </label>;
}
export function Choice({label,name,values=[],required=true,empty='Choose…',value,onChange,multiple=false}) {
  const id=useId();
  return <label htmlFor={id} style={{display:'block',marginBottom:8}}>{label}
    <select id={id} name={name} required={required} multiple={multiple} style={inputStyle}
      {...(value === undefined ? {defaultValue:multiple ? [] : ''} : {value})} onChange={onChange}>
      {!multiple ? <option value="">{empty}</option> : null}
      {values.map((item) => <option key={item.value ?? selector(item)} value={item.value ?? selector(item)}>
        {item.label || `${item.name || item.label || item.id} · ${item.kind || 'v'} · ${(item.digest || item.ref?.digest || '').slice(0,8)}`}
      </option>)}
    </select>
  </label>;
}
export function Feedback({action}) {
  return <><div role="status">{action.pending ? 'Running. Escape or Stop cancels the active operation.' : action.message}</div>
    {action.error ? <p role="alert" className="panel__error">{action.error}</p> : null}</>;
}
export function CapabilityStatus({workflow}) {
  if (!workflow) return <p className="panel__note">Workflow capability status has not been loaded.</p>;
  return <div aria-label="Workflow capabilities">
    {Object.entries(workflow.capabilities || {}).map(([name,capability]) => <p key={name} className="panel__note">
      <strong>{name}</strong>: {capability.available === true ? 'available' : capability.available === false ? 'unavailable' : capability.readiness || 'not probed'}
      {capability.configured ? ` · ${capability.configured}` : ''}{capability.reason ? ` — ${capability.reason}` : ''}
    </p>)}
    {workflow.running || workflow.provider_active ? <p role="status">A workflow is running; current project edits wait for its model and tools to stop.</p> : null}
  </div>;
}

export function ContextForm({project,catalog={},disabled=false,onDone}) {
  const action=useProjectAction(project,'context',onDone);
  const [parent,setParent]=useState('');
  return <details className="wb-card"><summary>Create or revise mathematical context</summary>
    <p>Contexts retain exact declarations, local hypotheses and representation conventions. A revision uses a new ID.</p>
    <form onChange={action.invalidate} onSubmit={(event) => {
      event.preventDefault();const f=formValues(event.currentTarget);
      const request={id:f.id,label:f.label,parent:optionalRef(f.parent),supersedes:optionalRef(f.supersedes),declarations:[],bindings:[]};
      if (f.declaration_id) request.declarations.push({id:f.declaration_id,symbol:f.symbol,semantic_type:f.semantic_type,role:f.role,
        ...(f.justification ? {justification:fromSelector(f.justification)} : {})});
      if (f.binding_id) request.bindings.push({id:f.binding_id,kind:f.binding_kind,symbol:f.binding_symbol,meaning:f.meaning,
        ...(f.binding_target ? {target:fromSelector(f.binding_target)} : {})});
      action.submit('context',request);
    }}><fieldset disabled={disabled || action.pending || !project} style={{border:0,padding:0}}>
      <Field label="New context ID" name="id"/><Field label="Context label" name="label"/>
      <Choice label="Parent context" name="parent" required={false} empty="New empty root context" values={catalog.contexts || []} value={parent} onChange={(event) => setParent(event.target.value)}/>
      <Choice label="Context being revised" name="supersedes" required={false} empty="No previous context" values={catalog.contexts || []}/>
      {parent ? <details><summary>Add a local declaration or representation convention</summary>
        <Field label="Declaration ID" name="declaration_id" required={false}/><Field label="Symbol" name="symbol" required={false}/>
        <Field label="Semantic type or local hypothesis" name="semantic_type" required={false} area/>
        <label>Declaration role <select name="role" defaultValue="arbitrary">{['arbitrary','chosen','derived','local_hypothesis'].map((value) => <option key={value}>{value}</option>)}</select></label>
        <Choice label="Exact justification" name="justification" required={false} values={catalog.items || []}/>
        <Field label="Convention ID" name="binding_id" required={false}/><Field label="Convention symbol" name="binding_symbol" required={false}/>
        <label>Convention kind <select name="binding_kind" defaultValue="convention">{['alias','notation','convention','ambient'].map((value) => <option key={value}>{value}</option>)}</select></label>
        <Field label="Meaning or representation choice" name="meaning" required={false} area/>
        <Choice label="Convention target" name="binding_target" required={false} values={catalog.items || []}/>
      </details> : <p className="panel__note">Create an empty root first, then select it as parent to add local declarations.</p>}
      <button className="button" type="submit">Record context</button>
    </fieldset></form><Feedback action={action}/>
  </details>;
}

export function ManuscriptIntake({project,catalog={},intake={},disabled=false,onDone}) {
  const choices=intake.source_choices || [];
  const [sourceSelection,setSourceSelection]=useState([]);
  const currentSources=sourceSelection.filter((key) => choices.some((choice) => sourceChoiceKey(choice) === key));
  const sourceSelectionChanged=currentSources.length !== sourceSelection.length;
  const action=useProjectAction(project,`intake:${currentSources.join('|')}`,onDone);
  return <details className="wb-card"><summary>Import manuscript and propose source mappings</summary>
    <form onChange={action.invalidate} onSubmit={(event) => {
      event.preventDefault();if (sourceSelectionChanged) return;const f=formValues(event.currentTarget);
      const selected=new window.FormData(event.currentTarget).getAll('source').map((key) => choices.find((choice) => sourceChoiceKey(choice) === key)).filter(Boolean);
      if (selected.length) action.submit('intake',{id:f.id,scope_id:f.scope_id,
        sources:selected.map(({path,digest,artifact,representation,retained}) => ({path,digest,...(artifact ? {artifact} : {}),...(representation ? {representation} : {}),...(retained ? {retained} : {})})),citation_depth:Number(f.citation_depth),target_papers:lines(f.target_papers)});
    }}><fieldset disabled={disabled || action.pending || !project || !choices.length} style={{border:0,padding:0}}>
      <Field label="Intake run ID" name="id"/><Field label="New manuscript scope ID" name="scope_id"/>
      <Choice label="Exact TeX manuscript sources" name="source" multiple value={currentSources} onChange={(event) => setSourceSelection([...event.target.selectedOptions].map((option) => option.value))} values={choices.map((value) => ({value:sourceChoiceKey(value),label:`${value.path} · ${value.digest?.slice(0,8)}${value.retained ? ' · retained paste' : ''}`}))}/>
      {sourceSelectionChanged ? <p className="panel__note">A selected source version changed or was removed. Explicitly select or remove it before intake.</p> : null}
      <Field label="Target paper identities and versions, one per line" name="target_papers" area required={false}/>
      <label>Citation depth <select name="citation_depth" defaultValue="0"><option value="0">0 · manuscript only</option><option value="1">1 · cited sources</option><option value="2">2 · citations of cited sources</option></select></label>
      <button type="submit" className="button" disabled={sourceSelectionChanged || !currentSources.length}>Propose manuscript mappings</button>
    </fieldset></form>
    {!choices.length ? <p className="panel__note">Save a TeX source through Files or retain pasted manuscript text below, then refresh source choices.</p> : null}
    <details><summary>Paste manuscript TeX</summary><form onChange={action.invalidate} onSubmit={(event) => {
      event.preventDefault();const f=formValues(event.currentTarget);action.submit('source',{id:f.source_id,text:f.text});
    }}><fieldset disabled={disabled || action.pending || !project} style={{border:0,padding:0}}>
      <Field label="Pasted manuscript source ID" name="source_id"/><Field label="Manuscript TeX" name="text" area/>
      <button type="submit" className="button">Retain manuscript text</button>
    </fieldset></form></details><Feedback action={action}/>
  </details>;
}

export function LiveWorkflow({project,catalog={},workflow,disabled=false,onDone,onCancel}) {
  const [kind,setKind]=useState('research');
  const [target,setTarget]=useState('');const [scope,setScope]=useState('');const [original,setOriginal]=useState('');
  const action=useProjectAction(project,`${kind}:${target}:${scope}:${original}`,onDone);
  const obligations=filterObligations(catalog.obligations || []);
  const arguments_=(catalog.items || []).filter((item) => item.kind === 'argument');
  const blocked=disabled || action.pending || workflow?.running || workflow?.provider_active;
  return <details className="wb-card"><summary>Research, Explore and Repair</summary>
    <CapabilityStatus workflow={workflow}/>
    <form onChange={action.invalidate} onSubmit={(event) => {
      event.preventDefault();const f=formValues(event.currentTarget);const request={id:f.id};
      if (kind === 'research') Object.assign(request,{target:fromSelector(f.target),scope:fromSelector(f.scope),operation:f.research_operation,context:optionalRef(f.context)});
      if (kind === 'explore') Object.assign(request,{action:f.explore_action,text:f.text,context:optionalRef(f.context),scope:optionalRef(f.scope),concept:optionalRef(f.concept),use_site:optionalRef(f.use_site)});
      if (kind === 'repair') Object.assign(request,{obligation:fromSelector(f.obligation),original_argument:fromSelector(f.original)});
      action.submit(kind,request);
    }}><fieldset disabled={blocked || !project} style={{border:0,padding:0}}>
      <label>Workflow <select value={kind} onChange={(event) => setKind(event.target.value)}><option value="research">Research</option><option value="explore">Explore</option><option value="repair">Repair</option></select></label>
      <Field label="Workflow run ID" name="id"/>
      {kind === 'research' ? <>
        <Choice label="Target or required internal result" name="target" values={(catalog.items || []).filter((item) => !['source','argument','critique','declaration','proof_step'].includes(item.kind))} value={target} onChange={(event) => setTarget(event.target.value)}/>
        <Choice label="Research scope" name="scope" values={catalog.scopes || []} value={scope} onChange={(event) => setScope(event.target.value)}/>
        <label>Research objective <select name="research_operation" defaultValue="prove"><option value="prove">Prove</option><option value="refute">Refute</option><option value="compute">Compute</option></select></label>
        <Choice label="Exact context" name="context" required={false} values={catalog.contexts || []}/>
      </> : kind === 'explore' ? <>
        <label>Explore action <select name="explore_action" defaultValue="concept"><option value="context">Context</option><option value="concept">Concept</option><option value="representation">Representation</option></select></label>
        <Field label="Mathematical direction or intended use" name="text" area/>
        <Choice label="Exact context" name="context" required={false} values={catalog.contexts || []}/>
        <Choice label="Explore scope" name="scope" required={false} values={catalog.scopes || []}/>
        <Choice label="Existing concept" name="concept" required={false} values={(catalog.items || []).filter((item) => item.kind === 'concept')}/>
        <Choice label="Representation use site" name="use_site" required={false} values={catalog.items || []}/>
      </> : <>
        <Choice label="Outstanding obligation to repair" name="obligation" values={obligations.map((value) => ({...value,name:`${value.item.id}: ${value.kind} · ${value.status}`}))}/>
        <Choice label="Exact original supplied argument" name="original" values={arguments_} value={original} onChange={(event) => setOriginal(event.target.value)}/>
      </>}
      <button type="submit" className="button">Run {kind === 'research' ? 'Research' : kind === 'explore' ? 'Explore' : 'Repair'}</button>
    </fieldset></form>
    {(action.pending || workflow?.running || workflow?.provider_active) && onCancel ? <button type="button" className="button" onClick={onCancel}>Stop active workflow</button> : null}
    <Feedback action={action}/>
    <details><summary>Workflow history · {(workflow?.attempts || []).length}</summary>
      {(workflow?.attempts || []).map((attempt) => <article key={attempt.id} style={{marginTop:12}}><strong>{attempt.kind} · {attempt.request_id} · {attempt.status}</strong>
        <p>{attempt.detail}</p><p>{attempt.elapsed}s · {attempt.recorded_at}</p><pre style={pre}>{attempt.request_json}</pre>
      </article>)}
      {(workflow?.calls || []).map((call) => <details key={call.id}><summary>{call.stage} · {call.model} · {call.status}</summary>
        <p>{call.detail} · {call.elapsed}s</p><pre style={pre}>{JSON.stringify(call.usage,null,2)}</pre>
        <p>Backend: {call.backend || 'unreported'} · isolation: {call.isolation || 'unreported'}</p>
        <details><summary>Actual prompts and response</summary><pre style={pre}>{call.system_prompt}</pre><pre style={pre}>{call.user_prompt}</pre><pre style={pre}>{call.response}</pre></details>
      </details>)}
    </details>
  </details>;
}

export function ObligationWork({catalog={},go}) {
  const [status,setStatus]=useState('outstanding');
  const values=filterObligations(catalog.obligations || [],status);
  return <details className="wb-card"><summary>Obligations · work and history</summary>
    <label>Obligation status <select value={status} onChange={(event) => setStatus(event.target.value)}>
      <option value="outstanding">Outstanding · open, investigating, blocked</option><option value="all">All states</option>
      {OBLIGATION_STATES.map((value) => <option key={value} value={value}>{value}</option>)}
    </select></label>
    <p>{values.length} obligation(s)</p>
    {values.map((value) => <article key={selector(value)}><strong>{value.status} · {value.kind}</strong>
      <p><a href={`#/ledger/${encodeURIComponent(value.item.id)}`} onClick={go ? (event) => {event.preventDefault();go({page:'ledger',arg:value.item.id});} : undefined}>{value.item.id}</a> · {value.reason}</p>
    </article>)}
  </details>;
}

export function ScopeForm({project,catalog={},disabled=false,onDone}) {
  const action=useProjectAction(project,'scope',onDone);
  return <details className="wb-card"><summary>Select or extend required mathematical work</summary>
    <form onChange={action.invalidate} onSubmit={(event) => {
      event.preventDefault();const data=new window.FormData(event.currentTarget);
      action.submit('scope',{id:data.get('id'),must_prove:data.getAll('must_prove').map(fromSelector)});
    }}><fieldset disabled={disabled || action.pending || !project} style={{border:0,padding:0}}>
      <Field label="Scope ID to create or extend" name="id"/>
      <Choice label="Exact results that must be proved" name="must_prove" multiple values={(catalog.items || []).filter((item) => !['source','argument','critique','declaration','proof_step'].includes(item.kind))}/>
      <p className="panel__note">Extending a scope preserves its required items. New premises require the separate admission gates and exact human decision.</p>
      <button className="button" type="submit">Record required work</button>
    </fieldset></form><Feedback action={action}/>
  </details>;
}

export default function ProjectWorkflow(props) {
  return <section aria-label="Mathematical project workflows"><ContextForm {...props}/><ScopeForm {...props}/><ManuscriptIntake {...props}/><LiveWorkflow {...props}/><ObligationWork {...props}/></section>;
}
