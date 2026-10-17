import {Choice, Field, Feedback} from './ProjectWorkflow.jsx';
import {CorrespondenceReview} from './ProjectReview.jsx';
import {useProjectAction, JUDGMENTS} from './projectForms.js';

const ref = (record) => `${record.id}@${record.digest}`;
const statusText = (status) => ({supported_by_reviewed_formal_proof:'Supported by a reviewed formal proof',
  kernel_verified:'Kernel verified',approved:'Approved',disputed:'Disputed',unresolved:'Unresolved',
  stale:'Stale',unassessed:'Unassessed'}[status] || status);

function useAction(project, onDone, selection='') {
  const action=useProjectAction(project,selection,onDone);
  return {...action,submit:(operation,fields) => action.submit(operation,fields,false)};
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
    }}><fieldset disabled={disabled || action.pending || !project} className="wb-fieldset">
      <Field label="Target ID" name="identity"/>
      <Field label="Informal statement" name="statement" area/>
      <Field label="Proposed Lean statement" name="formal" area/>
      <Choice label="Mathematical context" name="context" required={false} empty="Standalone target" values={catalog.contexts || []}/>
      <Choice label="Target being revised" name="supersedes" required={false} empty="New target" values={(catalog.items || []).filter((item) => item.semantics?.some(([key,value]) => key === 'role' && value === 'semantic_target'))}/>
      <button type="submit" className="button button--primary">Create target</button>
    </fieldset></form><Feedback action={action}/>
  </details>;
}

export function TargetReview({project,report,disabled=false,onDone}) {
  const scope=report.scope_ref;
  const action = useAction(project,onDone,`${ref(report.target)}:${ref(report.formal)}:${scope ? ref(scope) : ''}`);
  const frozen = disabled || action.pending || report.project !== project || !scope;
  const pair = {target:ref(report.target),translation:ref(report.formal),...(scope ? {scope:ref(scope)} : {})};
  return <section className="wb-card" aria-label="Semantic target review">
    <h3 className="wb-subhead">Statement and proof review</h3>
    <h4 className="wb-subhead">Informal statement</h4><pre className="wb-pre">{report.target.statement}</pre>
    <h4 className="wb-subhead">Proposed Lean statement</h4><pre className="wb-pre">{report.formal.statement}</pre>
    <p className="wb-wrap">Context: {report.context ? ref(report.context) : 'standalone'}</p>
    {(report.context_hypotheses || []).map((hypothesis) => <pre key={hypothesis.id} className="wb-pre">{hypothesis.statement}</pre>)}
    <dl className="wb-facts">
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
    }}><fieldset disabled={frozen} className="wb-fieldset">
      <Choice label="Correspondence judgment" name="verdict" required={false} empty={null} defaultValue="unresolved" values={JUDGMENTS}/>
      <Field label="Reviewer" name="reviewer"/>
      <Field label="Actual review conditions" name="conditions" area/>
      <Field label="Reason" name="reason" area/>
      <button type="submit" className="button button--primary">Record translation review</button>
    </fieldset></form>
    {!scope ? <p className="panel__note">Reload the report to obtain its exact reviewed scope before submitting a review or binding.</p> : null}
    <form onSubmit={(event) => {
      event.preventDefault();
      const saved = new window.FormData(event.currentTarget).get('saved');
      if (saved) action.submit('bind',{...pair,saved});
    }}><fieldset disabled={frozen || !(report.saved_candidates || []).length} className="wb-fieldset wb-fieldset--apart">
      <Choice label="Checked result" name="saved" empty="Choose a checked save"
        values={(report.saved_candidates || []).map((item) => ({value:ref(item),label:`${item.name} · ${item.digest.slice(0,8)}`}))}/>
      <button type="submit" className="button button--primary">Bind checked save</button>
    </fieldset></form>
    {!(report.saved_candidates || []).length ? <p className="panel__note">Save the exact Lean statement through Files before binding a checked result.</p> : null}
    <form onSubmit={(event) => {
      event.preventDefault();
      action.submit('export',{target:pair.target,output:new window.FormData(event.currentTarget).get('output')});
    }}><fieldset disabled={frozen} className="wb-fieldset wb-fieldset--apart">
      <Field label="Report export name" name="output"/>
      <button type="submit" className="button button--primary">Export assessment report</button>
    </fieldset></form>
    <details><summary>Review history · {(report.translation.reviews || []).length}</summary>
      {(report.translation.reviews || []).map((review) => <div key={review.id} className="wb-record">
        <strong>{review.verdict}</strong> · {review.reviewer} · {review.recorded_at}
        <p>{review.conditions}</p><p>{review.reason}</p>
      </div>)}
    </details>
    <Feedback action={action}/>
  </section>;
}
