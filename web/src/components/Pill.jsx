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

/** The verdict-word -> tone mapping, fixed by the design spec. This answers
 * for kernel/audit verdicts only (`kernel_verified`, `sorryAx`, `accepted`,
 * `refused`, ...) -- a caller holding one of the *state* words the design's
 * Overview names separately (`partial`, `stale`, `unknown`, `interrupted`,
 * `declined`, `checking…`) must go through `toneForState` instead of
 * guessing its way in here. `stale` is the one word both tables answer for,
 * and they agree (warning) because the prototype colours it the same way
 * in both places it appears. */
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

/** The state-word -> tone mapping, for the separate vocabulary the design's
 * Overview names (`partial`, `stale`, `unknown`, `interrupted`, `declined`,
 * `checking…`) -- turn/branch/job/project states, not kernel verdicts.
 * Checked against the prototype's own colours rather than inferred:
 * `interrupted` is `border:1px solid var(--warning);color:var(--warning)`
 * (the chat transcript's interrupted-turn chip) and the project-switcher's
 * `cyclotomic-units` row (`stateColor:'var(--warning)'`); `unknown` is
 * `color:var(--muted)` (the attention-inbox admission text and the jobs
 * tree's unknown-result chip); `checking…` carries `vc:'var(--muted)'`
 * everywhere a file's v is `'checking…'`; `partial` is an unadorned
 * `border:1px solid var(--border)` chip that inherits the surrounding
 * `color:var(--muted)` (the Hardy-initiated turn line, and the jobs tree's
 * "finished · partial" chip, both explicitly muted). `declined` has no
 * pill of its own in the prototype -- it only appears in prose ("a prompt
 * was answered no", "a restore that was then declined") -- so this maps it
 * to `muted` by the same reasoning as `partial`/`unknown`: a decision made
 * the ordinary way, not a warning or an error. That is a judgement call,
 * not a transcribed value; revisit it if a page turns up a countervailing
 * example. */
const STATE_TONE = {
  stale: 'warning',
  partial: 'muted',
  unknown: 'muted',
  interrupted: 'warning',
  declined: 'muted',
  'checking…': 'muted',
};

/** `word`'s tone as a kernel/audit verdict. Anything not in the fixed table
 * falls back to `muted` -- a decision, not an oversight: an unrecognised
 * word should read as neutral rather than borrow a verdict colour (accent,
 * warning, error) it did not earn. */
export function toneForVerdict(word) {
  return VERDICT_TONE[word] ?? 'muted';
}

/** `word`'s tone as a turn/branch/job/project state word. Same fallback
 * reasoning as `toneForVerdict`: neutral by default, never a guessed
 * accent/warning/error. */
export function toneForState(word) {
  return STATE_TONE[word] ?? 'muted';
}

/** An `/api/environment` probe's own word for its state, matching
 * `doctor.Check.line`'s own marks (`ok  ` / `FAIL` / `warn`): a required
 * check that failed is a failure, an optional one that failed is only a
 * warning. This is neither vocabulary above -- the input is the check
 * object, not a word the backend already chose -- so `/api/environment`'s
 * two consumers (Home's compact grid, the Environment page) both call this
 * pair rather than each deciding "ok" on its own. */
export function wordForCheck(check) {
  if (check.ok) return 'ok';
  return check.required ? 'fail' : 'warn';
}

/** `check`'s tone, from the same fixed set `TONE_CLASS` answers for. */
export function toneForCheck(check) {
  if (check.ok) return 'accent';
  return check.required ? 'error' : 'warning';
}

export default function Pill({tone, children}) {
  return <span className={`pill ${TONE_CLASS[tone] ?? TONE_CLASS.muted}`}>{children}</span>;
}
