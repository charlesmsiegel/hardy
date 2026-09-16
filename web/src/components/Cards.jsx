// The confirm card the composer raises when a destructive command needs a
// yes before it runs -- --panel, a 1px --accent border at 55%, radius --r2.
//
// The rule that matters more than the shell: it names the exact command it
// is about to run and says what pressing Esc does instead, so nothing here
// ever happens invisibly. `command` and `escNote` are plain props rather
// than copy baked into the component -- the sentence changes per action, the
// obligation to say it does not.
//
// This used to be one of three cards (`GateCard`, `LineCard`, `ConfirmCard`)
// sharing this shell. The other two were deleted in Task 14: they duplicated
// what `chat/Prompt.jsx` already does for the real prompt payload
// (`kind`/`title`/`subtitle`/`rows`/`preamble`), and their prop shape
// (`raisedBy`/`facts`/`statement`/`axiom`/`options`) came from the
// prototype's mock rather than any payload this shipment serves. `ConfirmCard`
// survives because `pages/Jobs.jsx` (Task 11) uses it for five real commands.

import Facts from './Facts.jsx';

function Shell({eyebrow, escNote, children}) {
  return (
    <section className="card-shell">
      <div className="card-shell__eyebrow">
        <span>{eyebrow}</span>
        {escNote ? <span className="card-shell__esc">{escNote}</span> : null}
      </div>
      {children}
    </section>
  );
}

export function ConfirmCard({command, title, facts, escNote = '', onYes, onNo}) {
  return (
    <Shell eyebrow={`confirm · ${command}`}>
      <div className="card-shell__title">{title}</div>
      {facts ? <Facts rows={facts} mono /> : null}
      <div className="card-shell__actions">
        <button type="button" className="button" onClick={onNo}>No</button>
        <button type="button" className="button button--yes" onClick={onYes}>Yes</button>
      </div>
      <div className="card-shell__footnote">No is focused. {escNote}</div>
    </Shell>
  );
}
