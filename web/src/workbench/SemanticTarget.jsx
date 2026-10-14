import {useId} from 'react';
import {Choice} from './ProjectWorkflow.jsx';
import {CorrespondenceReview} from './ProjectReview.jsx';
import {useProjectAction} from './projectForms.js';

const ref = (record) => `${record.id}@${record.digest}`;
const pre = {whiteSpace:'pre-wrap', overflowWrap:'anywhere', fontSize:13, margin:0, padding:6, background:'var(--bg)'};
const heading = {margin:'6px 0 0',fontSize:14};
const statusText = (status) => ({supported_by_reviewed_formal_proof:'Supported by a reviewed formal proof',
  kernel_verified:'Kernel verified',approved:'Approved',disputed:'Disputed',unresolved:'Unresolved',
  stale:'Stale',unassessed:'Unassessed'}[status] || status);

function useAction(project, onDone, selection='') {
  const action=useProjectAction(project,selection,onDone);
  return {...action,submit:(operation,fields) => action.submit(operation,fields,false)};
}
function Feedback({action}) {
  return <><div role="status">{action.message}</div>{action.error ? <p role="alert" className="panel__error">{action.error}</p> : null}</>;
}
function Field({label,name,area=false,required=true,initial=''}) {
  const id = useId();
  const props = {id,name,required,defaultValue:initial,style:{width:'100%',boxSizing:'border-box',padding:6,border:'1px solid var(--border)',borderRadius:4,background:'var(--bg)',color:'var(--fg)',font:'inherit'}};
  return <label htmlFor={id} style={{display:'block',marginBottom:8}}>{label}{area ? <textarea {...props} rows={3}/> : <input {...props}/>}</label>;
}

export function NewTarget({project,catalog={},disabled=false,onDone}) {
  const action = useAction(project,onDone);
  return <details className="wb-card"><summary>Create semantic target</summary>
    <p>Record the intended statement and its proposed Lean version. Review and proof begin as open work.</p>
    <form onChange={action.invalidate} onSubmit={(event) => {
      event.preventDefault();
      const fields = Object.fromEntries(new window.FormData(event.currentTarget));
      const request = {identity:fields.identity,statement:fields.statement,formal:fields.formal};
      if (fields.context.trim()) request.context=fields.context;
      if (fields.supersedes.trim()) request.supersedes=fields.supersedes;
      action.submit('target',request);
    }}><fieldset disabled={disabled || action.pending || !project} style={{border:0,padding:0}}>
      <Field label="Target ID" name="identity"/>
      <Field label="Informal statement" name="statement" area/>
      <Field label="Proposed Lean statement" name="formal" area/>
      <Choice label="Mathematical context" name="context" required={false} empty="Standalone target" values={catalog.contexts || []}/>
      <Choice label="Target being revised" name="supersedes" required={false} empty="New target" values={(catalog.items || []).filter((item) => item.semantics?.some(([key,value]) => key === 'role' && value === 'semantic_target'))}/>
      <button type="submit" className="button">Create target</button>
    </fieldset></form><Feedback action={action}/>
  </details>;
}

export function TargetReview({project,report,disabled=false,onDone}) {
  const scope=report.scope_ref;
  const action = useAction(project,onDone,`${ref(report.target)}:${ref(report.formal)}:${scope ? ref(scope) : ''}`);
  const frozen = disabled || action.pending || report.project !== project || !scope;
  const pair = {target:ref(report.target),translation:ref(report.formal),...(scope ? {scope:ref(scope)} : {})};
  return <section className="wb-card" aria-label="Semantic target review">
    <h3 style={heading}>Statement and proof review</h3>
    <h4 style={heading}>Informal statement</h4><pre style={pre}>{report.target.statement}</pre>
    <h4 style={heading}>Proposed Lean statement</h4><pre style={pre}>{report.formal.statement}</pre>
    <p style={{overflowWrap:'anywhere'}}>Context: {report.context ? ref(report.context) : 'standalone'}</p>
    {(report.context_hypotheses || []).map((hypothesis) => <pre key={hypothesis.id} style={pre}>{hypothesis.statement}</pre>)}
    <dl style={{margin:0}}>
      <dt>Statement assessment</dt><dd>{statusText(report.statement.status)}</dd>
      <dt>Translation review</dt><dd>{statusText(report.translation.status)}</dd>
      <dt>Formal proof</dt><dd>{statusText(report.formal_proof.status)}{report.formal_proof.reason ? ` — ${report.formal_proof.reason}` : ''}</dd>
      <dt>Original supplied proof</dt><dd>{statusText(report.original_proof.status)} — {report.original_proof.reason}</dd>
      <dt>Repaired proof</dt><dd>{statusText(report.repaired_proof.status)} — {report.repaired_proof.reason}</dd>
      <dt>External assumptions used</dt><dd>{report.assumptions.length ? report.assumptions.map(ref).join(', ') : 'none established by this report'}</dd>
      <dt>Kernel axioms recorded</dt><dd>{report.formal_proof.axioms == null ? 'not recorded' : report.formal_proof.axioms.join(', ') || 'none in recorded audit'}</dd>
    </dl>
    <CorrespondenceReview translation={report.translation}/>
    {report.coverage_gaps.map((gap) => <p key={gap} className="panel__note">{gap}</p>)}
    <form data-review onChange={action.invalidate} onSubmit={(event) => {
      event.preventDefault();
      if (!scope) return;
      action.submit('review',{...pair,...Object.fromEntries(new window.FormData(event.currentTarget))});
    }}><fieldset disabled={frozen} style={{border:0,padding:0}}>
      <label>Correspondence judgment <select name="verdict" defaultValue="unresolved">
        <option value="unresolved">Unresolved</option><option value="approved">Approved</option><option value="disputed">Disputed</option>
      </select></label>
      <Field label="Reviewer" name="reviewer"/>
      <Field label="Actual review conditions" name="conditions" area/>
      <Field label="Reason" name="reason" area/>
      <button type="submit" className="button">Record translation review</button>
    </fieldset></form>
    {!scope ? <p className="panel__note">Reload the report to obtain its exact reviewed scope before submitting a review or binding.</p> : null}
    <form onSubmit={(event) => {
      event.preventDefault();
      const saved = new window.FormData(event.currentTarget).get('saved');
      if (saved) action.submit('bind',{...pair,saved});
    }}><fieldset disabled={frozen || !(report.saved_candidates || []).length} style={{border:0,padding:0,marginTop:16}}>
      <label>Checked result <select name="saved" required defaultValue="">
        <option value="">Choose a checked save</option>
        {(report.saved_candidates || []).map((item) => <option key={ref(item)} value={ref(item)}>{item.name} · {item.digest.slice(0,8)}</option>)}
      </select></label>
      <button type="submit" className="button">Bind checked save</button>
    </fieldset></form>
    {!(report.saved_candidates || []).length ? <p className="panel__note">Save the exact Lean statement through Files before binding a checked result.</p> : null}
    <form onSubmit={(event) => {
      event.preventDefault();
      action.submit('export',{target:pair.target,output:new window.FormData(event.currentTarget).get('output')});
    }}><fieldset disabled={frozen} style={{border:0,padding:0,marginTop:16}}>
      <Field label="Report export name" name="output"/>
      <button type="submit" className="button">Export assessment report</button>
    </fieldset></form>
    <details><summary>Review history · {(report.translation.reviews || []).length}</summary>
      {(report.translation.reviews || []).map((review) => <div key={review.id} style={{marginTop:8}}>
        <strong>{review.verdict}</strong> · {review.reviewer} · {review.recorded_at}
        <p>{review.conditions}</p><p>{review.reason}</p>
      </div>)}
    </details>
    <Feedback action={action}/>
  </section>;
}
