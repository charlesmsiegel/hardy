// Help: reading the marks, the live command list, and the keys -- the one
// page that is almost entirely static copy, quoted verbatim from the
// prototype (Reading the marks), with one live section (Commands) drawn from
// the same registry `/help` in the composer prints.
//
// The Keys block follows the real handlers in Shell, Composer, Prompt and
// Cards: Ctrl/Cmd+K opens the palette even in a text field; the prompt's
// number keys move selection and Enter answers. Copying the prototype's
// shortcuts without checking their handlers would promise controls absent
// from the page.
//
// The prototype's subtitle names a Hardy version ("hardy 0.9.3"); nothing
// this shipment serves carries that figure anywhere a page can read it, so
// it is left off rather than typed as a fact nobody asked the host for. The
// rest of the subtitle is kept.

import Absent from '../components/Absent.jsx';
import Label from '../components/Label.jsx';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

export default function Help() {
  const {revision, status, compose} = useSession();
  const {data, error} = usePanel('/api/commands', revision, status?.path);

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Help</span>
        <span className="wb-page-subtitle">also /help in the composer</span>
      </div>

      <div className="wb-section">
        <Label>Reading the marks</Label>
        <div className="wb-help-block">
          <span style={{fontFamily: 'var(--mono)', fontWeight: 600}}>⊢</span>
          <span>
            What the Lean kernel checked: axioms used, presence of{' '}
            <span style={{fontFamily: 'var(--mono)'}}>sorryAx</span>, signature hash. Never edited
            by the model.
          </span>
          <span style={{fontFamily: 'var(--mono)', fontWeight: 600}}>§</span>
          <span>
            What the record says: the ledger, saved theorems, approved assumptions, publication
            bundles. Written by Hardy at the user's or model's request.
          </span>
          <span style={{fontFamily: 'var(--mono)', fontWeight: 600}}>&ldquo; &rdquo;</span>
          <span>What the model said. Shown beside the other two, never merged with them.</span>
          <span>
            <Absent kind="unreported" />
          </span>
          <span>
            The backend gave no figure. Distinct from <Absent kind="zero" />, and from{' '}
            <Absent kind="na" />, which means the field does not apply.
          </span>
          <span style={{fontFamily: 'var(--mono)'}}>stale · partial · unknown · interrupted · declined</span>
          <span>
            States are words. Stale points at an older version; partial means holes remain;
            declined means a prompt was answered no.
          </span>
        </div>
      </div>

      <div className="wb-section">
        <Label>Commands</Label>
        {error ? (
          <p className="panel__error">{error}</p>
        ) : !data ? (
          <p className="panel__note">Reading the commands...</p>
        ) : (
          [['builtin', 'Built-ins'], ['shortcut', 'Prompt shortcuts'], ['project', 'Project commands']].map(([kind, title]) => (
            <section key={kind} data-command-kind={kind}>
              <Label>{title}</Label>
              <div className="wb-help-block wb-help-block--tight">
                {data.filter(command => command.kind === kind).map(command => (
                  <span key={command.name} style={{display: 'contents'}}>
                    <button type="button" className="button button--quiet wb-help__command" onClick={() => compose(`/${command.name} `)}>
                      /{command.name}{command.argument_hint ? ` ${command.argument_hint}` : ''}
                    </button>
                    <span>{command.summary}</span>
                  </span>
                ))}
              </div>
            </section>
          ))
        )}
      </div>

      <div className="wb-section">
        <Label>Keys</Label>
        <div className="wb-help-block wb-help-block--tight">
          <span style={{fontFamily: 'var(--mono)'}}>⌘K / Ctrl+K</span>
          <span>Open or close the page, chat and command palette, including while typing. Escape closes it and restores focus.</span>
          <span style={{fontFamily: 'var(--mono)'}}>Enter</span>
          <span>Send, or queue if a turn is running.</span>
          <span style={{fontFamily: 'var(--mono)'}}>Esc</span>
          <span>Dismisses a focused card first; otherwise closes an open command list, or cancels the running turn.</span>
          <span style={{fontFamily: 'var(--mono)'}}>Tab</span>
          <span>Completes the command being typed when its command list is open.</span>
          <span style={{fontFamily: 'var(--mono)'}}>Up Down</span>
          <span>Moves through the open command list, or through a focused card's options.</span>
          <span style={{fontFamily: 'var(--mono)'}}>1 2 3</span>
          <span>Move the focused card's selection to that option; Enter answers it.</span>
        </div>
      </div>

      <div className="panel__note">
        Session commands: <span style={{fontFamily: 'var(--mono)'}}>docs/reference/session-commands.md</span> in the Hardy
        install · issues at the source repository.
      </div>
    </div>
  );
}
