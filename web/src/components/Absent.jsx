// The only place in the client that renders an absence.
//
// Three absences exist and they are three different claims. *not reported*
// means the backend was asked and gave no figure. `—` means the field does
// not apply to this thing at all. `0` means zero, which is a measurement and
// not an absence. A page that wrote these itself would eventually write one
// where it meant another, and the whole design turns on them staying
// distinct -- so pages import this and never type the strings.

export default function Absent({kind}) {
  if (kind === 'na') return <span className="absent absent--na">—</span>;
  if (kind === 'zero') return <span className="absent absent--zero">0</span>;
  return <em className="absent absent--unreported">not reported</em>;
}

/** `value` when it is a real figure, the right absence when it is not. */
export function orAbsent(value, {na = false} = {}) {
  if (value === 0) return <Absent kind="zero" />;
  if (value === null || value === undefined || value === '') {
    return <Absent kind={na ? 'na' : 'unreported'} />;
  }
  return value;
}
