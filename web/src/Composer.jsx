// The one input. Enter sends, Shift+Enter is a newline, Tab completes a
// command name, Esc cancels whatever is running.
//
// While a turn or a command owns the session the box is marked blocked and
// dimmed, with one exception: a slash command the registry marks
// `safe_in_flight` may still be sent, which is how `/status` answers the
// question "what is it doing" while it is doing it. The sentence shown is the
// dispatcher's own, kept from the last refusal, because the server is the
// authority on why a line was turned away and the page must not invent a
// second wording for it.

import {useEffect, useMemo, useRef} from 'react';

/** The command a draft names, if it names one at all. */
export function named(draft, commands) {
  const text = draft.trimStart();
  if (!text.startsWith('/')) return null;
  const name = text.slice(1).split(/\s/, 1)[0];
  return commands.find((command) => command.name === name) ?? null;
}

export default function Composer({draft, commands, busy, refusal, runningTool, onDraft, onSend, onCancel}) {
  const box = useRef(null);
  const command = useMemo(() => named(draft, commands), [draft, commands]);
  const blocked = busy && !(command && command.safe_in_flight);

  useEffect(() => {
    box.current?.focus();
  }, []);

  const complete = () => {
    const text = draft.trimStart();
    if (!text.startsWith('/') || /\s/.test(text)) return false;
    const prefix = text.slice(1);
    const match = commands.find((candidate) => candidate.name.startsWith(prefix));
    if (!match || match.name === prefix) return false;
    onDraft(`/${match.name} `);
    return true;
  };

  const onKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      onSend(draft);
    } else if (event.key === 'Tab') {
      if (complete()) event.preventDefault();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      onCancel();
    }
  };

  return (
    <form className={blocked ? 'composer composer--blocked' : 'composer'} onSubmit={(event) => event.preventDefault()}>
      {busy ? (
        <div className="composer__running">
          <span className="dot" />
          {runningTool ? `running ${runningTool}` : 'working'}
        </div>
      ) : null}
      {blocked && refusal ? <div className="composer__refusal">{refusal}</div> : null}
      <textarea
        ref={box}
        className="composer__box"
        rows={3}
        value={draft}
        aria-disabled={blocked}
        placeholder={blocked ? 'Only a safe-in-flight command can be sent now.' : 'Say something, or / for a command.'}
        onChange={(event) => onDraft(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="composer__row">
        <span className="composer__hint">
          Enter sends, Shift+Enter is a newline, Tab completes, Esc cancels.
        </span>
        <button type="button" className="button" disabled={blocked || !draft.trim()} onClick={() => onSend(draft)}>
          Send
        </button>
      </div>
    </form>
  );
}
