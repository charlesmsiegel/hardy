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

import {useEffect, useRef} from 'react';
import Facts from './Facts.jsx';

// `Shell` took an `escNote` and rendered it in the eyebrow. `ConfirmCard` is
// its only caller and has never passed one -- it puts the note in its own
// footnote instead -- so the branch could not fire and `.card-shell__esc` had
// no rule left in `styles.css` to style it with. Issue #170's first item.
function Shell({eyebrow, onKeyDown, children}) {
  return (
    <section className="card-shell" onKeyDown={onKeyDown}>
      <div className="card-shell__eyebrow">
        <span>{eyebrow}</span>
      </div>
      {children}
    </section>
  );
}

export function ConfirmCard({command, title, facts, escNote = '', onYes, onNo}) {
  // The footnote says "No is focused" and Help.jsx repeats the claim, so
  // both have to be true here, not just written: focus No on mount --
  // dismissing without answering is the safe default -- and treat Esc as No,
  // the same "dismiss answers, it does not vanish silently" rule
  // `chat/Prompt.jsx`'s own Confirm already applies. The handler lives on
  // the section rather than the button: a keydown on the focused No or Yes
  // button still bubbles up to it.
  const no = useRef(null);

  useEffect(() => {
    no.current?.focus();
  }, []);

  const onKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onNo();
    }
  };

  return (
    <Shell eyebrow={`confirm · ${command}`} onKeyDown={onKeyDown}>
      <div className="card-shell__title">{title}</div>
      {facts ? <Facts rows={facts} mono /> : null}
      <div className="card-shell__actions">
        <button ref={no} type="button" className="button" onClick={onNo}>No</button>
        <button type="button" className="button button--yes" onClick={onYes}>Yes</button>
      </div>
      <div className="card-shell__footnote">No is focused. {escNote}</div>
    </Shell>
  );
}
