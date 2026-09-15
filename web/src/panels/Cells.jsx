// The computer algebra journal, drawn as cells rather than as JSON lines.
//
// Each cell says which file it ran from, who ran it, whether it was accepted
// into the state a rebuild replays from, and what it printed. Cells from
// before the last reset are dimmed: they are history, not live state. The
// journal itself is still a file, served whole as text, and this view is a
// reading of it, never a second record.

import usePanel from './usePanel.js';

function Text({label, shown}) {
  if (!shown || !shown.text) return null;
  return (
    <div className="cell__block">
      <div className="cell__label">{label}</div>
      <pre className="cell__text">{shown.text}</pre>
      {shown.truncated ? <div className="panel__note">cut; the whole text is in the journal</div> : null}
    </div>
  );
}

export default function Cells({revision}) {
  const {data, error} = usePanel('/api/cas/cells', revision);

  if (error) return <p className="panel__error">{error}</p>;
  if (!data) return <p className="panel__note">Reading the journal...</p>;
  if (!data.cells.length) return <p className="panel__note">no cells have run yet</p>;

  return (
    <div className="cells">
      {data.truncated ? (
        <p className="panel__note">Only the newest {data.cells.length} of {data.total} cells are shown.</p>
      ) : null}
      {data.cells.map((cell) => (
        <section
          key={`${cell.segment}-${cell.seq}`}
          className={`cell${cell.live ? '' : ' cell--old'}${cell.accepted ? '' : ' cell--refused'}`}
        >
          <div className="cell__head">
            <span className="cell__seq">[{cell.seq}]</span>
            <span className="cell__path">{cell.path || 'inline'}</span>
            <span className={`chip chip--${cell.accepted ? 'ok' : 'bad'}`}>
              {cell.status}
              {cell.accepted ? '' : ' · not accepted'}
            </span>
            <span className="cell__meta">
              {cell.author} · segment {cell.segment} · {cell.duration_ms} ms
            </span>
          </div>
          {cell.restart_note ? <div className="panel__note">{cell.restart_note}</div> : null}
          <Text label="source" shown={cell.source} />
          <Text label="stdout" shown={cell.stdout} />
          <Text label="stderr" shown={cell.stderr} />
          <Text label="value" shown={cell.value_repr} />
        </section>
      ))}
    </div>
  );
}
