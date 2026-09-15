// The one input. Enter sends, Shift+Enter is a newline, Tab completes a
// command name, Esc closes the completion list or cancels whatever is running.
//
// While a turn or a command owns the session the box stays typeable, and a
// plain line is still sent: the server queues it and sends it the moment the
// session is free, in the order typed. What is not sent is a slash command
// the registry does not mark `safe_in_flight` -- a command takes the session
// over and cannot wait in a queue without changing what it means. The
// sentence shown for that is the dispatcher's own, kept from the last
// refusal, because the server is the authority on why a line was turned away
// and the page must not invent a second wording for it.
//
// Typing `/mo` opens a list of the commands it could be, drawn from the same
// registry the server dispatches against, so the suggestion is never a name
// the server would then refuse as unknown.

import {useEffect, useMemo, useRef, useState} from 'react';

/** The command a draft names, if it names one at all. */
export function named(draft, commands) {
  const text = draft.trimStart();
  if (!text.startsWith('/')) return null;
  const name = text.slice(1).split(/\s/, 1)[0];
  return commands.find((command) => command.name === name) ?? null;
}

/** The commands a draft could still become: a `/` prefix with no space after it yet. */
export function completions(draft, commands) {
  const text = draft.trimStart();
  if (!text.startsWith('/') || /\s/.test(text)) return [];
  const prefix = text.slice(1);
  return commands.filter((command) => command.name.startsWith(prefix) && command.name !== prefix);
}

export default function Composer({draft, commands, busy, queued, refusal, runningTool, onDraft, onSend, onBlocked, onCancel}) {
  const box = useRef(null);
  const command = useMemo(() => named(draft, commands), [draft, commands]);
  const matches = useMemo(() => completions(draft, commands), [draft, commands]);
  //: Which suggestion the arrows have moved to, reset whenever the list changes.
  const [chosen, setChosen] = useState(0);
  const [closed, setClosed] = useState(false);
  const open = matches.length > 0 && !closed;
  const blocked = busy && command !== null && !command.safe_in_flight;

  useEffect(() => {
    box.current?.focus();
  }, []);

  useEffect(() => {
    setChosen(0);
    setClosed(false);
  }, [draft]);

  const accept = (pick) => {
    const match = pick ?? matches[Math.min(chosen, matches.length - 1)];
    if (!match) return false;
    onDraft(`/${match.name} `);
    return true;
  };

  const submit = () => (blocked ? onBlocked() : onSend(draft));

  const onKeyDown = (event) => {
    if (open && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setChosen((current) => (current + step + matches.length) % matches.length);
    } else if (open && event.key === 'Tab') {
      event.preventDefault();
      accept();
    } else if (open && event.key === 'Enter' && !event.shiftKey) {
      // The draft is a prefix, not a command: Enter takes the suggestion
      // rather than sending `/mo` for the server to refuse as unknown.
      event.preventDefault();
      accept();
    } else if (open && event.key === 'Escape') {
      event.preventDefault();
      setClosed(true);
    } else if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    } else if (event.key === 'Tab') {
      if (accept()) event.preventDefault();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      onCancel();
    }
  };

  const placeholder = blocked
    ? 'Only a safe-in-flight command can run now; a message is queued until the turn ends.'
    : busy
      ? 'A message sent now is queued until the turn ends.'
      : 'Say something, or / for a command.';

  return (
    <form className={blocked ? 'composer composer--blocked' : 'composer'} onSubmit={(event) => event.preventDefault()}>
      {busy ? (
        <div className="composer__running">
          <span className="dot" />
          {runningTool ? `running ${runningTool}` : 'working'}
          {queued ? ` · ${queued} queued` : ''}
        </div>
      ) : null}
      {blocked && refusal ? (
        <div id="composer-refusal" className="composer__refusal" role="status">
          {refusal}
        </div>
      ) : null}
      {open ? (
        <ul className="complete" role="listbox" aria-label="Commands">
          {matches.map((match, index) => (
            <li
              key={match.name}
              role="option"
              aria-selected={index === chosen}
              className={index === chosen ? 'complete__row complete__row--on' : 'complete__row'}
              onMouseDown={(event) => {
                // Before the textarea loses focus, so the click completes
                // rather than only blurring.
                event.preventDefault();
                accept(match);
              }}
              onMouseEnter={() => setChosen(index)}
            >
              <span className="complete__name">/{match.name}</span>
              {match.argument_hint ? <span className="complete__hint">{match.argument_hint}</span> : null}
              <span className="complete__summary">{match.summary}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {/* Not `disabled`, and not `aria-disabled` either: the box accepts
          typing, and telling a screen reader otherwise would be a lie about a
          control that works. What is refused is sending a command that cannot
          run now, and the reason is written beside it and pointed at from here. */}
      <textarea
        ref={box}
        className="composer__box"
        rows={3}
        value={draft}
        aria-describedby={blocked && refusal ? 'composer-refusal' : undefined}
        placeholder={placeholder}
        onChange={(event) => onDraft(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="composer__row">
        <span className="composer__hint">
          Enter sends, Shift+Enter is a newline, Tab completes, Esc cancels.
        </span>
        <button type="button" className="button" disabled={blocked || !draft.trim()} onClick={submit}>
          {busy && !blocked ? 'Queue' : 'Send'}
        </button>
      </div>
    </form>
  );
}
