// The model picker in the header: sugar over `/model`, never a second path.
//
// The rows are the ones the terminal menu draws, read from `/api/models`;
// choosing one submits `/model <identity>` through the ordinary input path,
// so the switch is recorded, refused while a turn runs, and followed by the
// same "save as default?" card the terminal asks. The control shows the
// model the server reports and never a local guess: the header's value moves
// when the `state` event after the switch says it did.
//
// A native `<select>`, deliberately. The page has no floating menu of its
// own and this does not start one: the browser's picker is keyboard-driven,
// screen-reader-labelled and themed already. "Other…" swaps in a text field,
// which is the escape hatch the terminal's menu has for an identity the
// catalog lacks.

import {useEffect, useState} from 'react';
import {get} from './api.js';

const OTHER = '__other__';

export default function ModelPicker({model, busy, revision, onSend}) {
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState({backend: '', authentication: ''});
  const [typing, setTyping] = useState(false);
  const [typed, setTyped] = useState('');

  useEffect(() => {
    let live = true;
    get('/api/models')
      .then((found) => {
        if (!live) return;
        setRows(found.rows ?? []);
        setMeta({backend: found.backend ?? '', authentication: found.authentication ?? ''});
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [revision, model]);

  const known = rows.some((row) => row.value === model);
  const title = busy
    ? 'A turn is still running. Wait for it to finish.'
    : `Runs through: ${meta.authentication || meta.backend}. Suggestions are Hardy's bundled catalog; availability is not queried.`;

  const choose = (value) => {
    if (value === OTHER) {
      setTyping(true);
      setTyped('');
      return;
    }
    if (value && value !== model) onSend(`/model ${value}`);
  };

  if (typing) {
    return (
      <form
        className="picker"
        onSubmit={(event) => {
          event.preventDefault();
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
          onChange={(event) => setTyped(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.preventDefault();
              setTyping(false);
            }
          }}
        />
      </form>
    );
  }

  return (
    <select
      className="picker"
      aria-label="Model"
      title={title}
      value={known ? model : model || ''}
      disabled={busy}
      onChange={(event) => choose(event.target.value)}
    >
      {!known && model ? <option value={model}>{model}</option> : null}
      {rows.map((row) => (
        <option key={row.value} value={row.value} title={row.note}>
          {row.label}
        </option>
      ))}
      <option value={OTHER}>Other…</option>
    </select>
  );
}
