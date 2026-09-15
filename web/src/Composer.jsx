// The one input. Enter sends, Shift+Enter is a newline, Tab completes a
// command name, Esc cancels whatever is running.
//
// While a turn or a command owns the session the box stays typeable -- a
// disabled textarea could never be typed `/status` into, which is the one
// thing it exists for mid-turn -- but nothing is sent. The exception is a
// slash command the registry marks `safe_in_flight`, which goes through.
//
// Not sending is the point. Posting a line the session will refuse puts it in
// the transcript for as long as the round trip takes and then takes it away
// again. The sentence shown instead is the dispatcher's own, kept from the
// last refusal, because the server is the authority on why a line was turned
// away and the page must not invent a second wording for it.

import {useEffect, useMemo, useRef} from 'react';

/** The command a draft names, if it names one at all. */
export function named(draft, commands) {
  const text = draft.trimStart();
  if (!text.startsWith('/')) return null;
  const name = text.slice(1).split(/\s/, 1)[0];
  return commands.find((command) => command.name === name) ?? null;
}

export default function Composer({draft, commands, busy, refusal, runningTool, onDraft, onSend, onBlocked, onCancel}) {
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

  const submit = () => (blocked ? onBlocked() : onSend(draft));

  const onKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
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
      {blocked && refusal ? (
        <div id="composer-refusal" className="composer__refusal" role="status">
          {refusal}
        </div>
      ) : null}
      {/* Not `disabled`, and not `aria-disabled` either: the box accepts
          typing, and telling a screen reader otherwise would be a lie about a
          control that works. What is refused is sending, and the reason is
          written beside it and pointed at from here. */}
      <textarea
        ref={box}
        className="composer__box"
        rows={3}
        value={draft}
        aria-describedby={blocked && refusal ? 'composer-refusal' : undefined}
        placeholder={blocked ? 'Only a safe-in-flight command can be sent now.' : 'Say something, or / for a command.'}
        onChange={(event) => onDraft(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="composer__row">
        <span className="composer__hint">
          Enter sends, Shift+Enter is a newline, Tab completes, Esc cancels.
        </span>
        <button type="button" className="button" disabled={blocked || !draft.trim()} onClick={submit}>
          Send
        </button>
      </div>
    </form>
  );
}
