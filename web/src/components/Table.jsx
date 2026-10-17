// One row-picking table for every list in the workbench -- theorems, chats,
// ledger entries, jobs. It draws headers and cells and, if given `onPick`,
// tints and clicks a row; it does not know what a "theorem" or a "ledger
// entry" is, only that some rows are pickable and one of them may be the
// current selection. `rows` is `[{key, cells: [node, ...]}, ...]` so a page
// can put whatever it wants in a cell -- a <Pill/>, an <Absent/>, a link --
// without this component ever inspecting it.
//
// A pickable row is a tab stop that Enter or Space picks, the way a click
// does: the row is the control, so a keyboard has to be able to reach and
// operate it. It stays a <tr> -- a role on it would take it out of the table
// -- and says whether it is the current selection with `aria-selected`.

/** Enter and Space pick the row, but only when the row itself has focus: a key
 *  pressed in a link or button inside a cell belongs to that control, and
 *  picking the row as well would act twice on one keystroke. */
function pickOnKey(event, pick) {
  if (event.target !== event.currentTarget) return;
  if (event.key !== 'Enter' && event.key !== ' ') return;
  event.preventDefault();
  pick();
}

export default function Table({head, rows, onPick, selected}) {
  return (
    <table className="vtable">
      <thead>
        <tr>
          {head.map((label) => (
            <th key={label}>{label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const classes = ['vtable__row'];
          if (onPick) classes.push('vtable__row--pick');
          if (onPick && row.key === selected) classes.push('vtable__row--selected');
          return (
            <tr
              key={row.key}
              className={classes.join(' ')}
              tabIndex={onPick ? 0 : undefined}
              aria-selected={onPick ? row.key === selected : undefined}
              onClick={onPick ? () => onPick(row.key) : undefined}
              onKeyDown={onPick ? (event) => pickOnKey(event, () => onPick(row.key)) : undefined}
            >
              {row.cells.map((cell, i) => (
                <td key={i}>{cell}</td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
