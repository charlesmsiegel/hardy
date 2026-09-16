// One row-picking table for every list in the workbench -- theorems, chats,
// ledger entries, jobs. It draws headers and cells and, if given `onPick`,
// tints and clicks a row; it does not know what a "theorem" or a "ledger
// entry" is, only that some rows are pickable and one of them may be the
// current selection. `rows` is `[{key, cells: [node, ...]}, ...]` so a page
// can put whatever it wants in a cell -- a <Pill/>, an <Absent/>, a link --
// without this component ever inspecting it.

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
              onClick={onPick ? () => onPick(row.key) : undefined}
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
