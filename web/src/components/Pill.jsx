// One pill shape, one colour law. The backend hands a page a verdict word
// (a kernel verdict, an obligation status, a file's state...); this is the
// only place that turns that word into a colour, so the same word always
// reads the same way no matter which page printed it.

const TONE_CLASS = {
  accent: 'pill--accent',
  warning: 'pill--warning',
  error: 'pill--error',
  muted: 'pill--muted',
};

/** The verdict-word -> tone mapping, fixed by the design spec. Anything not
 * listed here falls back to `muted` -- an unrecognised word should read as
 * neutral, never borrow a verdict colour it did not earn. */
const VERDICT_TONE = {
  kernel_verified: 'accent',
  accepted: 'accent',
  ok: 'accent',
  'modulo-assumption': 'warning',
  warning: 'warning',
  stale: 'warning',
  waiting: 'warning',
  sorryAx: 'error',
  error: 'error',
  'not accepted': 'error',
  refused: 'error',
  'not applicable': 'muted',
  history: 'muted',
  imported: 'muted',
};

export function toneFor(word) {
  return VERDICT_TONE[word] ?? 'muted';
}

export default function Pill({tone, children}) {
  return <span className={`pill ${TONE_CLASS[tone] ?? TONE_CLASS.muted}`}>{children}</span>;
}
