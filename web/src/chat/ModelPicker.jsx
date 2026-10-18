// The model picker in the header: sugar over `/model`, never a second path.
//
// Mounted by Chat's Header, including its docked presentation. The rows are
// the ones the terminal menu draws, read through the shared panel hook;
// choosing one submits `/model <identity>` through the ordinary input path,
// so the switch is recorded, refused while a turn runs, and followed by the
// same "save as default?" card the terminal asks. The control shows the
// model the server reports and never a local guess: the header's value moves
// when the `state` event after the switch says it did.
// A failed catalog read stays beside either control; it does not erase the
// reported model or prevent typing an identity through the same command.
//
// A native `<select>`, deliberately. The page has no floating menu of its
// own and this does not start one: the browser's picker is keyboard-driven,
// screen-reader-labelled and themed already. "Other…" swaps in a text field,
// which is the escape hatch the terminal's menu has for an identity the
// catalog lacks.

import {useId, useState} from 'react';
import usePanel from '../session/usePanel.js';

const OTHER = '__other__';

export default function ModelPicker({model, busy, revision, onSend, owner}) {
  const panel = usePanel('/api/models', revision, owner);
  const rows = panel.data?.rows ?? [];
  const meta = panel.data ?? {};
  const [typing, setTyping] = useState(false);
  const [typed, setTyped] = useState('');
  const errorId = useId();
  const failure = panel.error ? <p id={errorId} role="alert" className="panel__error wb-model-error">{panel.error}</p> : null;

  const known = rows.some((row) => row.value === model);
  const title = busy
    ? 'The session is busy. Wait for the active operation to stop.'
    : meta.authentication || meta.backend
      ? `Runs through: ${meta.authentication || meta.backend}. Suggestions are Hardy's bundled catalog; availability is not queried.`
      : `${panel.error ? 'Model catalog could not be read.' : 'Reading the model catalog.'} Other… accepts a typed identity; availability is not queried.`;

  const choose = (value) => {
    if (busy) return;
    if (value === OTHER) {
      setTyping(true);
      setTyped('');
      return;
    }
    if (value && value !== model) onSend(`/model ${value}`);
  };

  const control = typing ? (
    <form
      className="picker picker--typed"
      onSubmit={(event) => {
        event.preventDefault();
        if (busy) return;
        const identity = typed.trim();
        setTyping(false);
        if (identity) onSend(`/model ${identity}`);
      }}
    >
      <input
        className="picker__input"
        autoFocus
        placeholder="model identity"
        value={typed}
        aria-label="Model identity"
        aria-describedby={panel.error ? errorId : undefined}
        disabled={busy}
        onChange={(event) => setTyped(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault();
            setTyping(false);
          }
        }}
      />
      <button className="button button--small" type="submit" disabled={busy || !typed.trim()}>Use model</button>
    </form>
  ) : (
    <select
      className="picker"
      aria-label="Model"
      aria-describedby={panel.error ? errorId : undefined}
      title={title}
      value={known ? model : model || ''}
      disabled={busy}
      onChange={(event) => choose(event.target.value)}
    >
      {!model ? <option value="" disabled>Choose model</option> : null}
      {!known && model ? <option value={model}>{model}</option> : null}
      {rows.map((row) => (
        <option key={row.value} value={row.value} title={row.note}>
          {row.label}
        </option>
      ))}
      <option value={OTHER}>Other…</option>
    </select>
  );

  return <>{control}{failure}</>;
}
