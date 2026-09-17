// One open gate, drawn as a card at the foot of the conversation.
//
// Every card can be dismissed, and dismissing answers `null` rather than
// closing silently: the assumption gate on the other side fails closed on a
// refusal, and a card that vanished without answering would leave the handler
// awaiting a future nobody will resolve.

import {useEffect, useRef, useState} from 'react';

function Preamble({lines}) {
  if (!lines?.length) return null;
  return (
    <div className="prompt__preamble">
      {lines.map(([text, style], index) => (
        <div key={index} className={`line line--${style || 'system'}`}>
          {text}
        </div>
      ))}
    </div>
  );
}

function Choose({prompt, answer, dismiss}) {
  const rows = prompt.rows ?? [];
  const [index, setIndex] = useState(() => Math.min(Math.max(prompt.current ?? 0, 0), Math.max(rows.length - 1, 0)));
  const box = useRef(null);

  useEffect(() => {
    box.current?.focus();
  }, []);

  const onKeyDown = (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setIndex((current) => (rows.length ? (current + step + rows.length) % rows.length : 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (rows[index]) answer(rows[index].value);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      dismiss();
    } else if (/^[1-9]$/.test(event.key)) {
      // The numbers the rows are labelled with, so the card is driven the way
      // it reads. Past nine there are only the arrows, as in the terminal.
      const wanted = Number(event.key) - 1;
      if (rows[wanted]) {
        event.preventDefault();
        setIndex(wanted);
      }
    }
  };

  return (
    <div className="prompt__body" tabIndex={0} ref={box} onKeyDown={onKeyDown}>
      <ul className="prompt__rows">
        {rows.map((row, position) => (
          <li
            key={`${row.value}-${position}`}
            className={position === index ? 'prompt__row prompt__row--current' : 'prompt__row'}
            onClick={() => answer(row.value)}
            onMouseEnter={() => setIndex(position)}
          >
            <span className="prompt__number">{position + 1}</span>
            <span className="prompt__label">{row.label}</span>
            {row.note ? <span className="prompt__note">{row.note}</span> : null}
          </li>
        ))}
      </ul>
      <div className="prompt__hint">Enter to pick, arrows or numbers to move, Esc to dismiss.</div>
    </div>
  );
}

function Line({answer, dismiss}) {
  const [value, setValue] = useState('');
  const input = useRef(null);

  useEffect(() => {
    input.current?.focus();
  }, []);

  return (
    <div className="prompt__body">
      <input
        ref={input}
        className="prompt__input"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            answer(value);
          } else if (event.key === 'Escape') {
            event.preventDefault();
            dismiss();
          }
        }}
      />
      <div className="prompt__hint">Enter to answer, Esc to dismiss.</div>
    </div>
  );
}

function Confirm({answer, dismiss}) {
  const yes = useRef(null);

  useEffect(() => {
    yes.current?.focus();
  }, []);

  return (
    <div
      className="prompt__body"
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          dismiss();
        }
      }}
    >
      <div className="prompt__buttons">
        <button ref={yes} type="button" className="button button--yes" onClick={() => answer(true)}>
          Yes
        </button>
        <button type="button" className="button" onClick={() => answer(false)}>
          No
        </button>
      </div>
      <div className="prompt__hint">Esc dismisses, which is a refusal.</div>
    </div>
  );
}

export default function Prompt({prompt, onAnswer}) {
  const answer = (value) => onAnswer(prompt.id, value);
  const dismiss = () => onAnswer(prompt.id, null);

  return (
    <section className="prompt">
      <Preamble lines={prompt.preamble} />
      <header className="prompt__head">
        <div className="prompt__title">{prompt.title}</div>
        {prompt.subtitle ? <div className="prompt__subtitle">{prompt.subtitle}</div> : null}
      </header>
      {prompt.kind === 'choose' ? <Choose prompt={prompt} answer={answer} dismiss={dismiss} /> : null}
      {prompt.kind === 'line' ? <Line answer={answer} dismiss={dismiss} /> : null}
      {prompt.kind === 'confirm' ? <Confirm answer={answer} dismiss={dismiss} /> : null}
    </section>
  );
}
