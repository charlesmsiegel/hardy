// The three prompt cards the composer raises when something is about to
// happen beyond the line the user typed: a standing assumption needs
// approval, a destructive action needs a yes, or one answer is needed to
// proceed. All three share one shell -- --panel, a 1px --accent border at
// 55%, radius --r2 -- because they are one interaction wearing three faces,
// not three widgets.
//
// The rule that matters more than the shell: every card names the exact
// command it is about to run and says what pressing Esc does instead, so
// nothing here ever happens invisibly. `command`/`raisedBy` and `escNote`
// are plain props on all three rather than copy baked into the component --
// the sentence changes per action, the obligation to say it does not.

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

export function GateCard({raisedBy, facts, title = 'Approve this assumption?', statement, axiom, options = [], onPick}) {
  return (
    <Shell eyebrow={`assumption gate · raised by ${raisedBy}`} escNote="Esc dismisses = decline">
      {facts ? (
        <div className="card-shell__probes">
          <Facts rows={facts} mono />
        </div>
      ) : null}
      <div className="card-shell__title">{title}</div>
      {statement ? <div className="card-shell__statement">{statement}</div> : null}
      {axiom ? <pre className="card-shell__pre">{axiom}</pre> : null}
      <ol className="card-shell__options">
        {options.map((option, i) => (
          <li
            key={option.label}
            className={i === 0 ? 'card-shell__option card-shell__option--default' : 'card-shell__option'}
            onClick={onPick ? () => onPick(i) : undefined}
          >
            <span className="card-shell__option-number">{i + 1}</span>
            <span>{option.label}</span>
            {option.detail ? <span className="card-shell__option-detail">{option.detail}</span> : null}
          </li>
        ))}
      </ol>
      <div className="card-shell__footnote">
        Enter picks · arrows or 1–{options.length} move · Esc dismisses, which declines
      </div>
    </Shell>
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

export function LineCard({command, title, placeholder, escNote = '', onSubmit}) {
  return (
    <Shell eyebrow={`line · ${command}`}>
      <div className="card-shell__title">{title}</div>
      <input
        className="card-shell__input"
        placeholder={placeholder}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && onSubmit) onSubmit(event.currentTarget.value);
        }}
      />
      <div className="card-shell__footnote">Enter answers · {escNote}</div>
    </Shell>
  );
}
