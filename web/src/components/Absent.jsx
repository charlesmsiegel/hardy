// The only place in the client that renders an absence.
//
// Three absences exist and they are three different claims. *not reported*
// means the backend was asked and gave no figure. `—` means the field does
// not apply to this thing at all. `0` means zero, which is a measurement and
// not an absence. A page that wrote these itself would eventually write one
// where it meant another, and the whole design turns on them staying
// distinct -- so pages import this and never type the strings.
//
// Which kind, for which situation (issue #168 found five places that had
// each guessed independently, and disagreed):
//
// - `unreported` -- a field the backend was asked for and did not supply:
//   a panel that is still loading or has errored, a figure no endpoint
//   carries yet (a timestamp, a token count, a thought's duration), or a
//   collection this page cannot tell "empty" from "not fetched" for. This
//   is also the default a *loading/errored* panel must show instead of
//   quietly rendering an empty collection as if it were a real, counted
//   zero -- `attention` on `pages/Chat.jsx`'s rail is the example that
//   went the other way.
// - `zero` -- a collection or count the backend *did* answer, and the
//   answer is none: an empty `evidence`/`artifacts`/`obligations` list on a
//   ledger item, `0` theorems on a fresh project. If the field applies to
//   this thing and the backend read it successfully, an empty result is
//   `zero`, never `na` -- `na` would claim the field does not apply at all,
//   which is a different, stronger claim than "applies, and there are
//   none".
// - `na` -- the field does not apply to this particular thing, independent
//   of whether the backend was even asked: a single optional link (like a
//   ledger item's `research` reference) that most items simply don't have,
//   or a control that is disabled because the action it would take does
//   not exist in this shipment.
//
// The shape issue #173 was filed about, because the three above do not
// settle it on their own: a *settled lookup that found nothing*. The backend
// performed a real, complete lookup -- `results()` matching a Lean
// declaration against the ledger, `Editor`'s buffer against the verdict of
// the file on disk -- and the answer is that there is no such thing to
// report. That is `na`, not `zero` and not `unreported`:
//
// - `zero` renders the glyph `0`, which is a count, and the missing thing
//   here is a statement, a reference or a verdict -- not a quantity. `0`
//   beside "§ says" would be mechanically wrong.
// - `unreported` says the backend was asked and gave no figure, which
//   implies the answer is indeterminate and might arrive later. It will not:
//   the lookup ran to completion and the answer is that nothing matches.
//
// So: no ledger entry for a theorem, no recorded LaTeX correspondence for a
// declaration, a saved verdict that does not describe the text now in the
// editor. All `na`, all with the reason in words beside them -- the glyph
// alone says "does not apply" without saying why, and why is the useful half.
//
// The second shape #173 recorded, found on the Ledger item page the day
// after the first: an absence with *no recorded cause*. A ledger item's
// `statement` is `Text | None` on every kind
// (`workflows/ledger/contracts.py`), and nothing in the schema says whether
// a null one means "not written yet" or "this kind does not carry one". So
// no kind fits: `na` claims the field does not apply, which the data does
// not support; `unreported` claims the backend was asked and gave no figure,
// when it answered and the answer was null; `zero` is a count. The ruling is
// prose, not an `Absent` kind -- a short note in the page's own voice ("No
// statement recorded.") that says what is known and no more. This is the
// note's answer, not each page's: Ledger, Results, Chat and the editor rail
// all render a missing `statement` this one way, and
// `tests/unit/test_web_absent.py` fails a page that renders it any other
// way. What separates this from the settled-lookup shape above is whether
// the *reason* for the absence is on record: there it is (the lookup ran;
// nothing matched), so `na` with the reason beside it is honest; here it is
// not, so no glyph is.
//
// Two more rules that keep pages consistent with each other, not just with
// themselves:
//
// - `??` vs `||` on a value that can honestly return `''`: pick `||` unless
//   the empty string is itself a meaningful, distinct answer from "nothing
//   found" -- most callers here mean the same "nothing to show" either way,
//   so `??` alone (letting `''` print as a blank line) is very rarely right.
// - a refusal or a busy/blocked state renders unconditionally on the value
//   that already scopes its own lifetime (e.g. `useSession()`'s `refusal`,
//   which the reducer clears the moment nothing is running) -- do not add a
//   second, page-local guard like `busy && refusal`, which can only ever
//   agree with or lag the value it is guarding.

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
