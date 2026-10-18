// Workflow selection is a view over mounted forms, not a new action engine.
// Each form owns its draft and pending request and calls the existing typed
// project adapter. Hiding preserves that state; leaving the page unmounts it,
// and Shell's project key prevents a new project inheriting old action state.
import useHash from '../session/useHash.js';
import useSession from '../session/useSession.js';
import useProjectState from '../workbench/useProjectState.js';
import {WORKFLOW_FORMS, WORKFLOW_GROUPS} from '../workbench/workflowForms.js';
import {NewTarget} from '../workbench/SemanticTarget.jsx';
import {CapabilityStatus, ContextForm, ScopeForm, ManuscriptIntake, LiveWorkflow, ObligationWork, ObligationForm} from '../workbench/ProjectWorkflow.jsx';
import {IntakeReview, RefereeControl, AdmissionReview, HumanSemanticReview, ArgumentEditor} from '../workbench/ProjectReview.jsx';

const FORMS = {
  target: NewTarget, context: ContextForm, scope: ScopeForm, intake: ManuscriptIntake,
  mappings: IntakeReview, live: LiveWorkflow, referee: RefereeControl,
  admission: AdmissionReview, review: HumanSemanticReview, arguments: ArgumentEditor,
  obligations: ObligationWork, 'obligation-new': ObligationForm,
};

export default function Workflows({arg}) {
  const [, go] = useHash();
  const {cancel} = useSession();
  const {project, state, catalog, disabled, error, reload} = useProjectState();
  const selected = arg || 'target';
  if (!state.project) return <p className={error ? 'panel__error' : 'panel__note'}>{error || 'Reading project workflow choices…'}</p>;

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Workflows</span>
        <span className="wb-page-subtitle">Define, investigate, judge and record</span>
      </div>
      {error ? <p role="alert" className="panel__error">{error}</p> : null}
      {(state.errors || []).map((message) => <p key={message} role="alert" className="panel__error">{message}</p>)}
      <div className="wb-workflows__layout">
        <nav className="wb-workflows__nav" aria-label="Workflow forms">
          {WORKFLOW_GROUPS.map((group) => (
            <div key={group.id} className="wb-workflows__group">
              <h2 className="section-label">{group.label}</h2>
              {WORKFLOW_FORMS.filter((form) => form.group === group.id).map((form) => (
                <button key={form.id} type="button" data-workflow={form.id} className="wb-workflows__choice"
                  aria-current={selected === form.id ? 'page' : undefined} title={form.summary}
                  onClick={() => go({page: 'workflows', arg: form.id})}>
                  {form.title}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="wb-workflows__body">
          <CapabilityStatus workflow={state.workflow}/>
          {!FORMS[selected] ? <p className="panel__note">No workflow form named {selected}. Choose a form from the list.</p> : null}
          {WORKFLOW_FORMS.map(({id}) => {
            const Form = FORMS[id];
            return (
              <div key={id} id={`workflow-${id}`} className="wb-workflows__form" hidden={id !== selected}>
                <Form project={project} catalog={catalog} workflow={state.workflow} intake={state.intake} referee={state.referee}
                  disabled={disabled} onDone={reload} onCancel={cancel} go={go}/>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
