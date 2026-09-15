// Everything the page answers to: every command, the prompt shortcuts, a
// project's own commands, and the keys.
//
// The command list is `/api/commands`, the same registry the server
// dispatches against, so nothing here can name a command the server would
// refuse as unknown, and a project's own `.hardy/prompts/` entries appear
// the moment the session has them. Clicking a command puts it in the
// composer rather than sending it: a page that ran `/checkpoint restore`
// on a click would be a page that acts on a misclick.

import usePanel from './usePanel.js';

//: What the browser's own keys do, stated once. The terminal's are in the
//: session-commands reference; they differ where a terminal has no mouse.
const KEYS = [
  ['Enter', 'Sends the line, or takes the completion chosen when a / prefix is open.'],
  ['Shift+Enter', 'Starts a new line without sending.'],
  ['Tab', 'Completes the command a / prefix names.'],
  ['Up / Down', 'Moves through the completion list, or through a card\'s rows.'],
  ['1 to 9', 'Picks a numbered row on a selector card.'],
  ['Esc', 'Closes the completion list; otherwise cancels the running turn, and a second Esc kills what has not stopped. On a card, dismisses it, which is a decline.'],
];

function Row({command, onDraft}) {
  const line = command.argument_hint ? `/${command.name} ${command.argument_hint}` : `/${command.name}`;
  return (
    <tr>
      <td>
        <button type="button" className="help__command" title="Put this in the composer" onClick={() => onDraft(`/${command.name} `)}>
          {line}
        </button>
      </td>
      <td>
        {command.summary}
        {command.alias_of ? <span className="help__note"> (same as /{command.alias_of})</span> : null}
        {command.safe_in_flight ? <span className="help__flag">works during a turn</span> : null}
      </td>
    </tr>
  );
}

function Table({title, note, commands, onDraft}) {
  if (!commands.length) return null;
  return (
    <section>
      <h3 className="panel__subheading">{title}</h3>
      {note ? <p className="panel__note">{note}</p> : null}
      <table className="help">
        <tbody>
          {commands.map((command) => (
            <Row key={command.name} command={command} onDraft={onDraft} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function Help({revision, onDraft}) {
  const {data, error} = usePanel('/api/commands', revision);

  if (error) return <p className="panel__error">{error}</p>;
  if (!data) return <p className="panel__note">Reading the commands...</p>;

  const builtin = data.filter((command) => command.kind === 'builtin');
  const shortcuts = data.filter((command) => command.kind === 'shortcut');
  const own = data.filter((command) => command.kind === 'project');

  return (
    <div className="panel__body">
      <h2 className="panel__heading">Help</h2>
      <p className="panel__note">
        Type a message to talk to the model, or a / command. While a turn runs, a message is queued
        and sent when the turn ends; only the commands marked below run alongside one.
      </p>
      <Table title="Commands" commands={builtin} onDraft={onDraft} />
      <Table
        title="Prompt shortcuts"
        note="Each sends and records the expanded text as a request, not the /name, and none is verification evidence."
        commands={shortcuts}
        onDraft={onDraft}
      />
      <Table
        title="Your own commands"
        note="Read from this root's .hardy/prompts/. Sending one records the expanded text, not the /name."
        commands={own}
        onDraft={onDraft}
      />
      <section>
        <h3 className="panel__subheading">Keys</h3>
        <table className="help">
          <tbody>
            {KEYS.map(([key, what]) => (
              <tr key={key}>
                <td><span className="help__key">{key}</span></td>
                <td>{what}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="panel__note">
          The header's model picker submits /model for you. The terminal's own keys are in the
          session-commands reference under docs/.
        </p>
      </section>
    </div>
  );
}
