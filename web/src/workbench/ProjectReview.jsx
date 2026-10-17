import {useState} from 'react';
import {Check, Choice, Field, Feedback} from './ProjectWorkflow.jsx';
import {exact, selector, fromSelector, optionalRef, formValues, lines, evidenceChoiceKey, useProjectAction, JUDGMENTS, plain} from './projectForms.js';

const selectedValues=(form,name) => new window.FormData(form).getAll(name).filter(Boolean);
export function RefLink({value}) {
  return value ? <a href={`#/ledger/${encodeURIComponent(value.id)}`} title={value.digest}>{value.id} · {value.digest?.slice(0,8)}</a> : <span>none selected</span>;
}
function ReviewerFields() {
  return <><Field label="Reviewer" name="reviewer"/><Field label="Actual review conditions" name="conditions" area/><Field label="Review reason" name="reason" area/></>;
}

function MappingProposal({mapping}) {
  const role=(label,value) => <><dt>{label}</dt><dd>{value || 'not proposed'}</dd></>;
  return <section aria-label={`Exact semantic proposal: ${mapping.name}`}>
    <dl>
      {role('Mapping identity',mapping.key)}
      {role('Proposed semantic kind',mapping.kind)}
      {mapping.kind === 'claim' ? role('Claim kind',mapping.claim_kind) : null}
      {role('Source path',mapping.span.path)}{role('Exact source digest',mapping.span.digest)}
      {role('Source character range',`${mapping.span.start}–${mapping.span.end}`)}
      {role('Parent context',mapping.context)}{role('Conclusion or dependency target',mapping.target)}
      {role(mapping.kind === 'citation' ? 'Citation use site' : 'Dependency source',mapping.source)}
      {role('Containing argument',mapping.argument)}
    </dl>
    <pre className="wb-pre">{mapping.span.quote}</pre>
    {mapping.environment ? <section aria-label="Claim or proof environment"><strong>Claim or proof environment</strong>
      <p>{mapping.environment.path}:{mapping.environment.start}–{mapping.environment.end} · {mapping.environment.digest}</p><pre className="wb-pre">{mapping.environment.quote}</pre>
    </section> : null}
    {(mapping.declarations || []).map((declaration) => <section key={declaration.id} aria-label={`Local declaration: ${declaration.symbol}`}>
      <strong>Local declaration: {declaration.id}</strong><dl>
        {role('Declaration symbol',declaration.symbol)}{role('Semantic type or hypothesis',declaration.semantic_type)}{role('Declaration role',declaration.role)}
        <dt>Justification</dt><dd>{declaration.justification ? <RefLink value={declaration.justification}/> : 'none proposed'}</dd>
        <dt>Declaration dependencies</dt><dd>{(declaration.dependencies || []).length ? declaration.dependencies.map((ref) => <span key={selector(ref)}><RefLink value={ref}/>{' '}</span>) : 'none proposed'}</dd>
      </dl>
    </section>)}
    {(mapping.bindings || []).map((binding) => <section key={binding.id} aria-label={`Proposed convention: ${binding.symbol}`}>
      <strong>Proposed convention: {binding.id}</strong><dl>
        {role('Convention kind',binding.kind)}{role('Convention symbol',binding.symbol)}{role('Convention meaning',binding.meaning)}
        <dt>Convention target</dt><dd>{binding.target ? <RefLink value={binding.target}/> : 'none proposed'}</dd>
      </dl>
    </section>)}
    {mapping.kind === 'citation' ? <section aria-label="Proposed external premise"><dl>
      {role('Canonical cited paper and version',mapping.paper_id || 'unresolved canonical paper/version')}
      {role('Cited theorem or statement',mapping.statement_ref || 'unresolved source theorem identity')}
      <dt>Exact required premise</dt><dd><pre className="wb-pre">{mapping.required_statement || 'unresolved required statement'}</pre></dd>
    </dl></section> : null}
    {(mapping.requirements || []).map((requirement,index) => <p key={index}><strong>Required semantic work: {requirement.kind}</strong> — {requirement.reason}</p>)}
    {mapping.detail ? <p>Proposal detail: {mapping.detail}</p> : null}
  </section>;
}

export function IntakeReview({project,catalog={},intake={},disabled=false,onDone}) {
  const [selected,setSelected]=useState('');
  const action=useProjectAction(project,selected,onDone);
  const entry=(intake.proposals || []).find((value) => selector(value) === selected);
  const proposal=entry?.proposal;
  return <details className="wb-card"><summary>Review manuscript mappings and required results</summary>
    <Choice label="Exact intake proposal" name="proposal_selection" values={(intake.proposals || []).map((value) => ({...value,label:`${value.proposal.request.id} · ${value.proposal.status} · ${value.ref.digest.slice(0,8)}`}))}
      value={selected} onChange={(event) => setSelected(event.target.value)}/>
    {proposal ? <div key={selected}>
      <p>{proposal.detail}</p>{!entry.current ? <p role="alert">{entry.stale_reason || 'Proposal source versions are stale.'}</p> : null}
      <p>Proposed citation depth: {proposal.request.citation_depth}</p>
      <p>Target papers kept on the required proof path: {(proposal.request.target_papers || []).join(', ') || 'none explicitly selected'}</p>
      {(proposal.sources || []).map((source,index) => <p key={index}>Selected source: {source.path} · exact digest {source.digest}{source.paper_id ? ` · canonical paper ${source.paper_id}` : ''}{source.source ? <>{' '}<RefLink value={source.source}/></> : null}</p>)}
      <form onChange={action.invalidate} onSubmit={(event) => {
        event.preventDefault();const f=formValues(event.currentTarget);
        const decisions=proposal.mappings.map((mapping) => ({key:mapping.key,verdict:f[`verdict_${mapping.key}`],reason:f[`reason_${mapping.key}`] || 'Not reviewed'}));
        action.submit('intake-review',{proposal:exact(entry),decisions,reviewer:f.reviewer,conditions:f.conditions,reason:f.reason,
          main_results:selectedValues(event.currentTarget,'main_results'),required_internal:selectedValues(event.currentTarget,'required_internal'),
          accepted_background:selectedValues(event.currentTarget,'accepted_background').map(fromSelector),scope:optionalRef(f.scope)});
      }}><fieldset disabled={disabled || action.pending || !entry.current} className="wb-fieldset">
        {proposal.mappings.map((mapping) => <article key={mapping.key} className="wb-card">
          <strong>{mapping.name} · {mapping.kind}</strong><MappingProposal mapping={mapping}/>
          <div className="wb-stack">
            <Choice label={`Mapping judgment: ${mapping.name}`} name={`verdict_${mapping.key}`} required={false} empty={null} defaultValue="unresolved" values={JUDGMENTS}/>
            <Field label={`Mapping reason: ${mapping.name}`} name={`reason_${mapping.key}`} required={false}/>
            {mapping.kind === 'claim' ? <>
              <Check name="main_results" value={mapping.key} label={`Main result: ${mapping.name}`}/>
              <Check name="required_internal" value={mapping.key} label={`Required internal result: ${mapping.name}`}/>
            </> : null}
          </div>
        </article>)}
        <Choice label="Existing exact scope" name="scope" required={false} empty="Create the manuscript's empty-premise scope" values={catalog.scopes || []}/>
        <Choice label="Previously admitted background" name="accepted_background" required={false} multiple values={(catalog.items || []).filter((item) => (catalog.scopes || []).some((scope) => [...scope.allowed_background,...scope.allowed_interfaces].some((ref) => selector(ref) === selector(item))))}/>
        <p className="panel__note">Selecting prior material does not admit a new premise. Novel manuscript claims and required results remain on the proof path.</p>
        <ReviewerFields/><button type="submit" className="button button--primary">Record exact mapping review</button>
      </fieldset></form>
      <details><summary>Unmapped source and ambiguities · {proposal.gaps.length}</summary>{proposal.gaps.map((gap,index) => <article key={index}><strong>{gap.kind}</strong><p>{gap.reason}</p>{gap.span ? <pre className="wb-pre">{gap.span.path}:{gap.span.start}–{gap.span.end}{'\n'}{gap.span.quote}</pre> : null}</article>)}</details>
    </div> : <p className="panel__note">Run manuscript intake, then select its exact proposal for review.</p>}
    <Feedback action={action}/>
  </details>;
}

export function RefereeControl({project,catalog={},intake={},referee={},disabled=false,onDone,onCancel}) {
  const [selection,setSelection]=useState('');
  const action=useProjectAction(project,selection,onDone);
  const entry=(intake.reviews || []).find((value) => selector(value) === selection);
  const review=entry?.review;
  const reviewedProposal=(intake.proposals || []).find((value) => selector(value) === selector(review?.proposal))?.proposal;
  const reviewedDepth=reviewedProposal?.request?.citation_depth;
  const depthKnown=[0,1,2].includes(reviewedDepth);
  const arguments_=(catalog.items || []).filter((item) => item.kind === 'argument');
  return <details className="wb-card"><summary>Referee · main results, supplied arguments and repairs</summary>
    <Choice label="Exact reviewed manuscript mapping" name="referee_review" values={(intake.reviews || []).map((value) => ({...value,label:`${value.review.id} · ${value.current ? 'current' : 'stale'}`}))} value={selection} onChange={(event) => setSelection(event.target.value)}/>
    {review ? <form key={selection} onChange={action.invalidate} onSubmit={(event) => {
      event.preventDefault();const f=formValues(event.currentTarget);
      if (!depthKnown) return;
      action.submit('referee',{id:f.id,review:exact(entry),scope:review.scope,
        main_results:review.main_results,required_internal:review.required_internal,
        original:selectedValues(event.currentTarget,'original').map(fromSelector),repaired:selectedValues(event.currentTarget,'repaired').map(fromSelector),
        citation_depth:reviewedDepth,citation_max_nodes:Number(f.citation_max_nodes)});
    }}><fieldset disabled={disabled || action.pending || !entry.current || !depthKnown} className="wb-fieldset">
      <Field label="Referee run ID" name="id"/>
      <dl><dt>Exact reviewed audit scope</dt><dd><RefLink value={review.scope}/></dd>
        <dt>Reviewed main results, in order</dt><dd>{review.main_results.map((ref) => <p key={selector(ref)}><RefLink value={ref}/></p>)}</dd>
        <dt>Reviewed required internal results, in order</dt><dd>{review.required_internal.length ? review.required_internal.map((ref) => <p key={selector(ref)}><RefLink value={ref}/></p>) : 'none selected in this review'}</dd>
      </dl>
      <p>Reviewed citation depth: {depthKnown ? reviewedDepth : 'unavailable — reload the exact intake proposal'}</p>
      <p className="panel__note">To change result roles or citation depth, record a new intake/review. This run uses the exact ordered selection already reviewed.</p>
      <Choice label="Original supplied arguments to assess" name="original" required={false} multiple values={arguments_.filter((item) => item.semantics?.some(([key,value]) => key === 'argument-role' && value === 'original'))}/>
      <Choice label="Exact repair versions to assess" name="repaired" required={false} multiple values={arguments_.filter((item) => item.semantics?.some(([key,value]) => key === 'argument-role' && value === 'repaired'))}/>
      <Field label="Maximum citation nodes" name="citation_max_nodes" type="number" min="1" max="128" initial="32"/>
      <button type="submit" className="button button--primary">Run Referee</button>
    </fieldset>{!entry.current ? <p role="alert">{entry.stale_reason}</p> : null}</form> : <p className="panel__note">Approve the exact source mappings and select required results before running Referee.</p>}
    {action.pending && onCancel ? <button type="button" className="button button--danger" onClick={onCancel}>Stop Referee</button> : null}
    <Feedback action={action}/>
    <RefereeReports project={project} reports={referee.reports || []} disabled={disabled} onDone={onDone} availability={catalog.operations?.['referee-export']}/>
  </details>;
}

function AssessmentLane({label,lane}) {
  const reason=lane?.reason || (lane?.unresolved?.length ? lane.unresolved.join('; ') : null);
  return <article className="wb-record"><strong>{label}: {lane?.status || 'unassessed'}</strong>
    {lane?.subject ? <p><RefLink value={lane.subject}/></p> : null}
    {reason ? <p>{reason}</p> : !lane || lane.status === 'unassessed' ? <p>No current assessment has been recorded.</p> : null}
    <p>Evidence grade: {lane?.evidence_level || 'unassessed'}</p>
    {lane?.coverage?.length ? <p>Coverage: {lane.coverage.join('; ')}</p> : null}
    {lane?.assumptions?.length ? <p>Actual assumptions: {lane.assumptions.map((ref) => <span key={selector(ref)}><RefLink value={ref}/>{' '}</span>)}</p> : null}
    {lane?.evidence?.length ? <details><summary>Exact evidence · {lane.evidence.length}</summary><pre className="wb-pre">{JSON.stringify(lane.evidence,null,2)}</pre></details> : null}
    {lane?.assessments?.length ? <details><summary>Attributed assessment history · {lane.assessments.length}</summary>
      {lane.assessments.map((record) => <article key={record.id}><strong>{record.verdict} · {record.reviewer}</strong><p>Actual conditions: {record.conditions}</p><p>{record.reason}</p><p>{record.recorded_at}</p></article>)}
    </details> : null}
  </article>;
}

export function CorrespondenceReview({translation={}}) {
  const direct=translation.human_review;
  const legacy=(translation.reviews || []).filter((record) => record.actor === 'human' ||
    (record.actor == null && record.translation && record.relation));
  const human=[...(direct && direct.actor !== 'model' ? [direct] : []),...legacy]
    .filter((record,index,records) => records.findIndex((value) => value.id === record.id) === index);
  const models=[...(translation.model_review ? [translation.model_review] : []),
    ...(direct?.actor === 'model' ? [direct] : []),...(translation.reviews || []).filter((record) => record.actor === 'model')];
  return <section aria-label="Translation correspondence and review provenance">
    <p>Translation correspondence: {translation.status || 'unresolved'}{translation.reason ? ` — ${translation.reason}` : ''}</p>
    {human.map((record,index) => <article key={record.id || index}>
      <strong>Human correspondence review record: {record.verdict}</strong>
      <p>Reviewer label: {record.reviewer}</p><p>Actual review conditions: {record.conditions}</p>
      <p>{record.reason}</p><p>Recorded at: {record.recorded_at || 'unreported'}</p>
    </article>)}
    {models.map((record,index) => <article key={record.id || index}>
      <strong>Model correspondence review record: {record.verdict || record.status || 'unreported'}</strong>
      <p>Recorded model or reviewer: {record.model || record.reviewer || 'unreported'}</p>
      {record.conditions ? <p>Actual review conditions: {record.conditions}</p> : null}
      {record.reason ? <p>{record.reason}</p> : null}
    </article>)}
    {(translation.evidence || []).map((evidence,index) => <details key={evidence.artifact?.digest || index}>
      <summary>Correspondence evidence producer: {evidence.producer || 'unreported'}</summary>
      <p>Recorded outcome: {evidence.artifact?.locator || 'unreported'}</p><pre className="wb-pre">{JSON.stringify(evidence,null,2)}</pre>
    </details>)}
  </section>;
}

export function AssessmentReport({report}) {
  if (!report) return null;
  return <section aria-label="Selected mathematical assessments">
    <h3>Theorem conclusion</h3><pre className="wb-pre">{report.target?.statement}</pre>
    {report.formal?.statement ? <><h4>Exact Lean statement</h4><pre className="wb-pre">{report.formal.statement}</pre></> : null}
    <AssessmentLane label="Theorem statement" lane={report.statement}/>
    <AssessmentLane label="Original supplied proof" lane={report.original_proof}/>
    {(report.intermediate_claims || []).map((item,index) => <AssessmentLane key={item.subject ? selector(item.subject) : index} label={`Intermediate claim ${item.subject?.id || index+1}`} lane={item}/>) }
    <AssessmentLane label="Selected repair" lane={report.repaired_proof}/>
    <h4>Independent checks</h4>
    <p>Formal kernel audit: {report.formal_proof?.status || 'unresolved'}{report.formal_proof?.reason ? ` — ${report.formal_proof.reason}` : ''}</p>
    <CorrespondenceReview translation={report.translation}/>
    <p>Citation review: {report.citation_review?.status || 'unassessed'}</p>
    <p>Document compilation: {report.document_compilation?.status || 'unassessed'}</p>
    <p>Kernel axioms: {report.formal_proof?.axioms == null ? 'no current axiom audit recorded' : report.formal_proof.axioms.join(', ') || 'none in recorded audit'}</p>
    <p>Actual assumptions: {(report.assumptions || []).length ? report.assumptions.map((value) => <span key={selector(value)}><RefLink value={value}/>{' '}</span>) : 'none established by this report'}</p>
    {(report.coverage_gaps || []).map((gap,index) => <p key={index} className="panel__note">{typeof gap === 'string' ? gap : gap.reason}</p>)}
  </section>;
}

export function SelectedAssessment({project,catalog={},target,disabled=false,onDone}) {
  const [scope,setScope]=useState('');const [original,setOriginal]=useState('');const [repaired,setRepaired]=useState('');
  const action=useProjectAction(project,`${selector(target)}:${scope}:${original}:${repaired}`,onDone);
  const args=(catalog.items || []).filter((item) => item.kind === 'argument' && (catalog.relations || []).some((relation) => relation.kind === 'supports' && selector(relation.source) === selector(item) && selector(relation.target) === selector(target)));
  const fields={target:exact(target),scope:optionalRef(scope),original:optionalRef(original),repaired:optionalRef(repaired)};
  return <details className="wb-card"><summary>Select argument versions, report and reproducible export</summary>
    <form onChange={action.invalidate} onSubmit={(event) => {event.preventDefault();action.submit('report',fields);}}>
      <fieldset disabled={disabled || action.pending} className="wb-fieldset">
        <Choice label="Exact result scope" name="report_scope" values={(catalog.scopes || []).filter((value) => value.must_prove.some((ref) => selector(ref) === selector(target)))} required={false} value={scope} onChange={(event) => setScope(event.target.value)}/>
        <Choice label="Original argument version" name="report_original" values={args.filter((item) => item.semantics.some(([key,value]) => key === 'argument-role' && value === 'original'))} required={false} value={original} onChange={(event) => setOriginal(event.target.value)}/>
        <Choice label="Repair argument version" name="report_repaired" values={args.filter((item) => item.semantics.some(([key,value]) => key === 'argument-role' && value === 'repaired'))} required={false} value={repaired} onChange={(event) => setRepaired(event.target.value)}/>
        <button type="submit" className="button button--primary">Inspect selected assessments</button>
      </fieldset>
    </form>
    {[original,repaired].filter(Boolean).map((value) => {const item=args.find((arg) => selector(arg) === value);return item ? <article key={value}><h4>{value === original ? 'Original supplied proof text' : 'Selected repair text'}</h4><pre className="wb-pre">{item.statement}</pre><SourceNavigation item={item} catalog={catalog}/></article> : null;})}
    <AssessmentReport report={action.result}/><Feedback action={action}/>
    <BundleExport project={project} catalog={catalog} selection={fields} disabled={disabled || action.pending} onDone={onDone}/>
  </details>;
}

export function BundleExport({project,catalog={},selection,disabled=false,onDone}) {
  const [formalSelection,setFormalSelection]=useState('');
  const candidates=(catalog.evidence_refs || []).filter((item) => item.reference.kind === 'formal' && selector(item.reference.subject) === selector(selection.target));
  const formalCurrent=!formalSelection || candidates.some((item) => evidenceChoiceKey(item.reference) === formalSelection);
  const action=useProjectAction(project,`${JSON.stringify(selection)}:${formalSelection}:${formalCurrent}`,onDone);
  const availability=catalog.operations?.bundle;
  return <form onChange={action.invalidate} onSubmit={(event) => {
    event.preventDefault();const f=formValues(event.currentTarget);
    if (!formalCurrent) return;
    const formal=f.formal ? candidates.find((item) => evidenceChoiceKey(item.reference) === f.formal)?.reference : null;
    action.submit('bundle',{...selection,formal,output:f.output});
  }}><fieldset disabled={disabled || action.pending || availability?.available === false} className="wb-fieldset">
    <Field label="Selected result bundle name" name="output"/>
    <Choice label="Exact formal evidence for replay" name="formal" required={false} value={formalCurrent ? formalSelection : ''} onChange={(event) => setFormalSelection(event.target.value)} empty="Use the unique authenticated artifact, or report only if none" values={candidates.map((item) => ({value:evidenceChoiceKey(item.reference),label:item.label || item.reference.artifact.uri}))}/>
    {!formalCurrent ? <p role="alert">Selected formal evidence changed or was removed. Select current exact evidence before exporting.</p> : null}
    {!candidates.length ? <p className="panel__note">No formal evidence is recorded for this exact selection. The bundle can retain unresolved assessments; independent Lean replay is unavailable until evidence and its source closure exist.</p> : null}
    <button type="submit" className="button button--primary" disabled={!formalCurrent}>Export selected result bundle</button>
  </fieldset>{availability?.available === false ? <p className="panel__note">{availability.reason}</p> : null}<Feedback action={action}/></form>;
}

function RefereeReports({project,reports,disabled,onDone,availability}) {
  const [selection,setSelection]=useState('');
  const action=useProjectAction(project,selection,onDone);
  const selected=reports.find((item) => selector(item) === selection);
  const report=selected?.report || selected?.historical_report;
  return <section aria-label="Referee report history">
    <Choice label="Exact Referee report" name="referee_report" required={false} values={reports.map((value) => ({...value,label:`${value.receipt.request.id} · ${value.receipt.status} · ${value.current ? 'current' : 'historical'}`}))} value={selection} onChange={(event) => setSelection(event.target.value)}/>
    {selected ? <>
      <p>{selected.receipt.detail}</p>{!selected.current ? <p role="alert">Historical report: {selected.stale_reason}</p> : null}
      {(selected.receipt.layers || []).map((layer,index) => <article key={index}><strong>{layer.layer} · {layer.status}</strong><p>{layer.reason}</p><p>Actual conditions: {layer.conditions}</p></article>)}
      {(report?.targets || []).map((target,index) => <AssessmentReport key={index} report={target}/>)}
      {(report?.demonstrated_defects || []).map((defect,index) => <article key={index} className="wb-card">
        <h4>Demonstrated defect · {defect.evidence_level}</h4><RefLink value={defect.subject}/><p>{defect.reason}</p>
        {defect.span ? <><p>{defect.span.path}:{defect.span.start}–{defect.span.end} · {defect.span.digest}</p><pre className="wb-pre">{defect.span.quote}</pre></> : null}
        <pre className="wb-pre">{defect.demonstration}</pre>
      </article>)}
      <details><summary>Source mapped report, assumptions and coverage</summary><pre className="wb-pre">{JSON.stringify(report,null,2)}</pre></details>
      <form onChange={action.invalidate} onSubmit={(event) => {event.preventDefault();action.submit('referee-export',{receipt_id:selected.ref.id,receipt_digest:selected.ref.digest,output:formValues(event.currentTarget).output});}}>
        <fieldset disabled={disabled || action.pending || availability?.available === false} className="wb-fieldset"><Field label="Referee report bundle name" name="output"/><button type="submit" className="button button--primary">Export exact Referee report</button></fieldset>
      </form>{availability?.available === false ? <p className="panel__note">{availability.reason}</p> : null}<Feedback action={action}/>
    </> : null}
  </section>;
}

export function SourceNavigation({item,catalog={}}) {
  const regions=(item.semantics || []).filter(([key]) => key === 'source-region').map(([,value]) => {try{return JSON.parse(value);}catch{return null;}}).filter(Boolean);
  const sources=[...(catalog.source_history || []),...(catalog.sources || [])];
  const ownSource=item.kind === 'source' ? sources.find((value) => selector(value) === selector(item)) : null;
  return <>{ownSource ? <details><summary>Retained exact source text</summary><pre className="wb-pre">{ownSource.text ?? ownSource.error}</pre></details> : null}
    {regions.map((region,index) => {const source=sources.find((value) => selector(value) === selector(region.source));const passage=typeof source?.text === 'string' ? Array.from(source.text).slice(region.start,region.end).join('') : null;return <details key={index}><summary>Source: {region.source.id} · characters {region.start}–{region.end}</summary><RefLink value={region.source}/><pre className="wb-pre">{passage ?? source?.error ?? 'Source text is unavailable for this exact version.'}</pre></details>;})}</>;
}

export function ContextDetails({context,catalog={}}) {
  if (!context) return <p className="panel__note">Mathematical context: standalone</p>;
  const chain=[];const seen=new Set();let ref=context;
  while (ref && !seen.has(selector(ref))) {
    seen.add(selector(ref));const value=catalog.contexts?.find((item) => selector(item) === selector(ref));
    if (!value) break;chain.unshift(value);ref=value.parent;
  }
  return <details className="wb-card"><summary>Exact context, declarations and representation choices</summary>
    <RefLink value={context}/>
    {!chain.length ? <p className="panel__note">The exact context is historical or its details have not been loaded.</p> : null}
    {chain.map((value) => <section key={selector(value)}><h4>{value.label}</h4>
      {value.declarations.map((reference) => {const item=catalog.items?.find((candidate) => selector(candidate) === selector(reference));const declaration=item?.declaration;return <article key={selector(reference)}><RefLink value={reference}/>
        {declaration ? <><p>{declaration.symbol} · {declaration.role}</p><pre className="wb-pre">{declaration.semantic_type}</pre><p>Justification: <RefLink value={declaration.justification}/></p><p>Dependencies: {declaration.dependencies.map((dependency) => <span key={selector(dependency)}><RefLink value={dependency}/>{' '}</span>)}</p></> : <p>Exact declaration details unavailable.</p>}
      </article>;})}
      {value.bindings.map((reference) => {const binding=catalog.bindings?.find((candidate) => selector(candidate) === selector(reference));return <article key={selector(reference)}><RefLink value={reference}/>{binding ? <><p>{binding.kind}: {binding.symbol}</p><pre className="wb-pre">{binding.meaning}</pre><p>Exact target: <RefLink value={binding.target}/></p></> : <p>Exact convention details unavailable.</p>}</article>;})}
    </section>)}
  </details>;
}

export function AdmissionReview({project,catalog={},disabled=false,onDone}) {
  const [selected,setSelected]=useState('');
  const [faithfulness,setFaithfulness]=useState('');
  const entry=(catalog.admissions || []).find((value) => value.id === selected);
  const evidence=(catalog.evidence_refs || []).filter((value) => ['faithfulness','literature'].includes(value.reference.kind)
    && (!entry || selector(value.reference.subject) === selector(entry.subject)));
  const currentFaithfulness=evidence.some((value) => evidenceChoiceKey(value.reference) === faithfulness) ? faithfulness : '';
  const action=useProjectAction(project,`${selected}:${entry?.digest || ''}:${entry?.gate?.digest || ''}:${currentFaithfulness}`,onDone);
  return <details className="wb-card"><summary>Premise admission and exact scope decisions</summary>
    <p>Required targets and novel manuscript results remain protected. Admission requires exact source reading, correspondence, search and Lean gates.</p>
    <form onChange={action.invalidate} onSubmit={(event) => {event.preventDefault();const f=formValues(event.currentTarget);action.submit('admission-request',{subject:fromSelector(f.subject),scope:fromSelector(f.scope),interface:f.interface === 'on',formalization:optionalRef(f.formalization)});}}>
      <fieldset disabled={disabled || action.pending} className="wb-fieldset">
        <Choice label="Exact proposed premise" name="subject" values={catalog.items || []}/><Choice label="Affected exact scope" name="scope" values={catalog.scopes || []}/>
        <Choice label="Exact Lean mapping to probe" name="formalization" required={false} empty="Use the premise's own exact Lean signature" values={(catalog.items || []).filter((item) => item.semantics?.some(([key,value]) => key === 'role' && value === 'proposed_formalization'))}/>
        <Check name="interface" label="Admit as interface"/><button type="submit" className="button button--primary">Request premise admission</button>
      </fieldset>
    </form>
    <Choice label="Exact pending admission request" name="admission_selection" required={false} value={selected} onChange={(event) => setSelected(event.target.value)} values={(catalog.admissions || []).map((value) => ({value:value.id,label:`${value.subject.id} into ${value.scope.id} · ${value.digest.slice(0,8)}`}))}/>
    {entry ? <article key={selected}>
      <h4>Proposed premise</h4><RefLink value={entry.subject}/><pre className="wb-pre">{catalog.items?.find((item) => selector(item) === selector(entry.subject))?.statement}</pre>
      {entry.formalization ? <><p>Exact proposed Lean mapping: <RefLink value={entry.formalization}/></p><pre className="wb-pre">{catalog.items?.find((item) => selector(item) === selector(entry.formalization))?.statement}</pre></> : null}
      <p>Affected scope: <RefLink value={entry.scope}/></p><h4>Project targets in this scope</h4>
      {(catalog.scopes?.find((scope) => selector(scope) === selector(entry.scope))?.must_prove || []).map((ref) => <article key={selector(ref)}><RefLink value={ref}/><pre className="wb-pre">{catalog.items?.find((item) => selector(item) === selector(ref))?.statement || 'Exact target statement unavailable.'}</pre></article>)}
      <ContextDetails context={catalog.items?.find((item) => selector(item) === selector(entry.subject))?.context} catalog={catalog}/>
      <SourceNavigation item={{semantics:entry.sources.map((source) => ['source-region',JSON.stringify(source)])}} catalog={catalog}/>
      <p>Missing gates: {(entry.gate?.missing_gates || entry.missing_gates).join(', ') || 'none'}</p><p>{entry.reason}</p>
      <form onChange={action.invalidate} onSubmit={(event) => {event.preventDefault();const chosen=evidence.find((value) => evidenceChoiceKey(value.reference) === currentFaithfulness);if (chosen) action.submit('admission-gates',{request:entry.id,request_digest:entry.digest,faithfulness:chosen.reference});}}>
        <fieldset disabled={disabled || action.pending || !evidence.length} className="wb-fieldset"><Choice label="Exact correspondence or citation applicability evidence" name="faithfulness" value={currentFaithfulness} onChange={(event) => setFaithfulness(event.target.value)} values={evidence.map((value) => ({value:evidenceChoiceKey(value.reference),label:value.label}))}/><button type="submit" className="button button--primary">Run actual admission gates</button></fieldset>
      </form>
      {!evidence.length ? <p className="panel__note">Record an exact correspondence/applicability judgment before running admission gates.</p> : null}
      {entry.gate ? <details><summary>Reviewed gate: {entry.gate.id} · {entry.gate.digest.slice(0,8)}</summary><p>{entry.gate.reason}</p><pre className="wb-pre">{entry.gate.search_output}</pre>{entry.gate.probe_output.map((output,index) => <pre key={index} className="wb-pre">{output}</pre>)}</details> : null}
      <form onChange={action.invalidate} onSubmit={(event) => {event.preventDefault();const f=formValues(event.currentTarget);action.submit('admission-decide',{request:entry.id,request_digest:entry.digest,gate:entry.gate?.id || null,gate_digest:entry.gate?.digest || null,approved:f.decision === 'approve',reviewer:f.reviewer,conditions:f.conditions,reason:f.reason});}}>
        <fieldset disabled={disabled || action.pending} className="wb-fieldset">
          <Choice label="Scope decision" name="decision" required={false} empty={null} defaultValue="decline"
            values={[{value:'decline',label:'Decline'},{value:'approve',label:'Approve this exact premise and gate',disabled:!entry.can_approve}]}/>
          <ReviewerFields/><button type="submit" className="button button--primary">Record exact scope decision</button>
        </fieldset>
      </form>
      {entry.decisions.map((decision) => <article key={decision.id}><strong>{decision.approved ? 'Approved' : 'Declined'} · {decision.reviewer}</strong><p>{decision.conditions}</p><p>{decision.reason}</p><p>Request {decision.request} · reviewed gate {decision.gates || 'none'}</p></article>)}
    </article> : null}<Feedback action={action}/>
  </details>;
}

export function HumanSemanticReview({project,catalog={},disabled=false,onDone}) {
  const [dimension,setDimension]=useState('correspondence');
  const action=useProjectAction(project,dimension,onDone);
  return <details className="wb-card"><summary>Source, statement, proof step or argument review</summary>
    <form onChange={action.invalidate} onSubmit={(event) => {
      event.preventDefault();const f=formValues(event.currentTarget);const request={subject:fromSelector(f.subject),scope:fromSelector(f.scope),reviewer:f.reviewer,conditions:f.conditions,reason:f.reason};
      if (['statement','argument'].includes(dimension)) Object.assign(request,{dimension,verdict:f.assessment_verdict,coverage:lines(f.coverage),unresolved:lines(f.unresolved),assumptions:selectedValues(event.currentTarget,'assumptions').map(fromSelector)});
      else Object.assign(request,{purpose:dimension,verdict:f.verdict,relevant:selectedValues(event.currentTarget,'relevant').map(fromSelector),
        sources:f.source ? [{source:fromSelector(f.source),start:Number(f.start),end:Number(f.end)}] : []});
      action.submit(['statement','argument'].includes(dimension) ? 'assess' : 'judge',request);
    }}><fieldset disabled={disabled || action.pending} className="wb-fieldset">
      <Choice label="Review dimension" name="review_dimension" required={false} empty={null} value={dimension} onChange={(event) => setDimension(event.target.value)}
        values={plain(['correspondence','definition','representation','informal_step','critique','ambiguity','source_read','statement','argument'])}/>
      <Choice label="Exact subject under review" name="subject" values={catalog.items || []}/><Choice label="Exact review scope" name="scope" values={catalog.scopes || []}/>
      {['statement','argument'].includes(dimension) ? <>
        <Choice label="Assessment" name="assessment_verdict" required={false} empty={null} defaultValue="unresolved" values={[{value:'unresolved',label:'Unresolved'},{value:'supported',label:'Supported by review'},
          dimension === 'argument' ? {value:'defective',label:'Defective supplied argument'} : {value:'refuted',label:'Refuted statement'}]}/>
        <Field label="Coverage, one item per line" name="coverage" area required={false}/><Field label="Unresolved work, one item per line" name="unresolved" area required={false}/>
        <Choice label="Actual assumptions used" name="assumptions" values={catalog.items || []} multiple required={false}/>
      </> : <><Choice label="Review judgment" name="verdict" required={false} empty={null} defaultValue="unresolved" values={JUDGMENTS}/>
        <Choice label="Other exact review inputs" name="relevant" multiple required={false} values={catalog.items || []}/><SourceFields catalog={catalog}/>
      </>}
      <ReviewerFields/><button type="submit" className="button button--primary">Record attributed review</button>
    </fieldset></form><Feedback action={action}/>
  </details>;
}

function SourceFields({catalog}) {
  return <details className="wb-stack"><summary>Bind an exact source passage</summary>
    <Choice label="Exact source version" name="source" required={false} values={catalog.sources || []}/>
    <Field label="Passage start, Unicode character offset" name="start" type="number" min="0" initial="0" required={false}/>
    <Field label="Passage end, Unicode character offset" name="end" type="number" min="0" required={false}/>
    <p className="panel__note">The retained source and character range are authenticated by the owner. Source reading alone does not approve correspondence.</p>
  </details>;
}

export function ArgumentEditor({project,catalog={},disabled=false,onDone}) {
  const [mode,setMode]=useState('argument');
  const action=useProjectAction(project,mode,onDone);
  return <details className="wb-card"><summary>Record supplied arguments, steps, critiques and revisions</summary>
    <form onChange={action.invalidate} onSubmit={(event) => {
      event.preventDefault();const f=formValues(event.currentTarget);
      const source=f.source ? {source:fromSelector(f.source),start:Number(f.start),end:Number(f.end)} : null;
      if (mode === 'argument') action.submit('argument',{id:f.id,target:fromSelector(f.target),text:f.statement,original:optionalRef(f.original),context:optionalRef(f.context),source,reason:f.reason || null});
      else if (mode === 'revise') action.submit('revise',{subject:fromSelector(f.target),statement:f.statement,context:optionalRef(f.context)});
      else if (mode === 'formalization') action.submit('formalization',{subject:fromSelector(f.target),id:f.id,formal:f.statement,scope:fromSelector(f.scope)});
      else action.submit(mode === 'claim' ? 'item' : mode,{id:f.id,name:f.name || f.id,kind:mode === 'step' ? 'proof_step' : mode,statement:f.statement,target:optionalRef(f.target),argument:optionalRef(f.argument),context:optionalRef(f.context),source,dependencies:selectedValues(event.currentTarget,'dependencies').map(fromSelector)});
    }}><fieldset disabled={disabled || action.pending || !project} className="wb-fieldset">
      <Choice label="Record kind" name="record_kind" required={false} empty={null} value={mode} onChange={(event) => setMode(event.target.value)} values={[
        {value:'argument',label:'Original argument or distinct repair version'},{value:'step',label:'Proof step'},{value:'claim',label:'Intermediate claim'},
        {value:'critique',label:'Critique'},{value:'revise',label:'Revise ordinary claim or step'},{value:'formalization',label:'Propose exact Lean translation'}]}/>
      {mode !== 'revise' ? <Field label="New record ID" name="id"/> : null}
      <Field label={mode === 'formalization' ? 'Proposed Lean statement' : 'Statement, supplied argument or critique'} name="statement" area/>
      <Choice label={mode === 'revise' ? 'Exact item being revised' : 'Exact conclusion or subject'} name="target" required={['argument','critique','revise','formalization'].includes(mode)} values={catalog.items || []}/>
      {mode === 'argument' ? <><Choice label="Original argument being repaired" name="original" required={false} empty="This is the original supplied argument" values={(catalog.items || []).filter((item) => item.kind === 'argument')}/><Field label="Reason for this argument version" name="reason" required={false}/></> : null}
      {mode === 'step' || mode === 'claim' ? <Choice label="Exact containing argument" name="argument" required={mode === 'step'} values={(catalog.items || []).filter((item) => item.kind === 'argument')}/> : null}
      {mode === 'formalization' ? <Choice label="Exact formalization scope" name="scope" values={catalog.scopes || []}/> : <>
        <Choice label="Exact mathematical context" name="context" required={false} values={catalog.contexts || []}/>
        {mode !== 'argument' && mode !== 'revise' ? <Choice label="Actual mathematical dependencies" name="dependencies" multiple required={false} values={catalog.items || []}/> : null}
        <SourceFields catalog={catalog}/>
      </>}
      <button type="submit" className="button button--primary">Record exact version</button>
    </fieldset></form><Feedback action={action}/>
  </details>;
}
