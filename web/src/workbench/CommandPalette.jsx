// A modal chooser over navigation and drafts. It owns search, selection and
// focus, while Shell owns what a choice does. Keeping sending out of this
// interface means adding another command cannot silently execute it.
import {useEffect, useId, useMemo, useRef, useState} from 'react';
import {rankEntries} from './palette.js';

export default function CommandPalette({open, onClose, entries, onRun}) {
  const id = useId();
  const search = useRef(null);
  const close = useRef(null);
  const [query, setQuery] = useState('');
  const [chosen, setChosen] = useState(0);
  const ranked = useMemo(() => rankEntries(query, entries), [query, entries]);
  const selected = Math.min(chosen, ranked.length - 1);
  const activeId = selected < 0 ? undefined : `${id}-option-${selected}`;

  useEffect(() => {
    if (!open) return undefined;
    const previous = document.activeElement;
    setQuery('');
    setChosen(0);
    search.current?.focus();
    return () => {
      if (previous?.isConnected) previous.focus();
    };
  }, [open]);

  useEffect(() => {
    if (open && activeId) document.getElementById(activeId)?.scrollIntoView?.({block: 'nearest'});
  }, [open, activeId, query]);

  if (!open) return null;
  const onKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      onClose();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (ranked.length) setChosen((selected + (event.key === 'ArrowDown' ? 1 : -1) + ranked.length) % ranked.length);
    } else if (event.key === 'Enter' && event.target === search.current) {
      event.preventDefault();
      if (selected >= 0) onRun(ranked[selected].run);
    } else if (event.key === 'Tab') {
      // The rows use the search's active descendant, not additional tab stops.
      event.preventDefault();
      (event.target === search.current ? close : search).current?.focus();
    }
  };

  return (
    <div className="wb-palette-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="wb-palette" role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} aria-describedby={`${id}-hint`} onKeyDown={onKeyDown}>
        <div className="wb-palette__head">
          <h2 id={`${id}-title`}>Go to or compose</h2>
          <button ref={close} type="button" className="button button--quiet button--small" onClick={onClose}>Close</button>
        </div>
        <label className="field__label" htmlFor={`${id}-search`}>Search pages, chats, workflow forms and commands</label>
        <input ref={search} id={`${id}-search`} type="search" autoComplete="off" value={query}
          aria-controls={`${id}-results`} aria-activedescendant={activeId}
          onChange={(event) => { setQuery(event.target.value); setChosen(0); }} />
        <p id={`${id}-hint`} className="panel__note">Forms open on Workflows. Commands fill the composer for review. Enter chooses; Escape closes.</p>
        <ul id={`${id}-results`} className="wb-palette__list" role="listbox" aria-label="Matches">
          {ranked.map((entry, index) => (
            <li id={`${id}-option-${index}`} key={entry.id} role="option" aria-selected={index === selected}
              className="wb-palette__option" onMouseEnter={() => setChosen(index)} onClick={() => onRun(entry.run)}>
              <span className="wb-palette__label">{entry.label}</span>
              <span className="wb-palette__kind">{entry.kind}</span>
              <span className="wb-palette__detail">{entry.detail}</span>
            </li>
          ))}
        </ul>
        {!ranked.length ? <p className="panel__note" role="status">{entries.length ? 'No matches.' : 'Open a project to browse pages and commands.'}</p> : null}
      </section>
    </div>
  );
}
