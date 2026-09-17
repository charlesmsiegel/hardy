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

/** `DelegationState`'s tone (`src/hardy/workflows/delegation/contracts.py:
 * 25-35` -- `queued|active|waiting|paused|completed|partial|failed|
 * cancelled|exhausted|unknown`). A THIRD vocabulary, not `STATE_TONE`: the
 * two share the words `partial` and `unknown`, which is exactly why routing
 * a `DelegationState` through `toneForState` (as an earlier pass of this
 * page did) read as correct while quietly flattening every other real value
 * to the neutral default -- the same class of mistake `toneForVerdict` vs.
 * `toneForState` already exists to prevent, one level down.
 *
 * Where the prototype (Hardy Workbench.dc.html) draws a delegation's own
 * state, that colour is used directly:
 *   - `active`: the Jobs tree's `d-08` row and its own detail pane both
 *     draw `● running` in `color:var(--accent)` (lines ~483, ~491) -- the
 *     one delegation state the prototype gives a colour of its own.
 *   - `completed`: the Jobs tree's `d-04` row ("4/4 · 310 s", a clean full
 *     finish) draws its state as plain `color:var(--muted)` text with no
 *     border or accent (line ~480); `c-03`'s computation row draws
 *     `done · exit 0` the same muted way (line ~484) -- a successful
 *     terminal state reads as unremarkable, not celebratory.
 *   - `partial`: the Jobs tree's `d-07` row draws `finished · partial` as a
 *     `border:1px solid var(--border)` chip with no colour override, so it
 *     inherits the row's `color:var(--muted)` (line ~482); the Chat
 *     transcript's Hardy-initiated notice line draws the same word the same
 *     way (line ~194). Kept from the existing `STATE_TONE` entry, which
 *     this same evidence already grounded.
 *   - `unknown`: the Jobs tree's `d-05` row draws `unknown` as the same
 *     unadorned muted chip (line ~481). Also kept from `STATE_TONE`.
 *
 * The prototype never draws a delegation's own state pill for `queued`,
 * `waiting`, `paused`, `failed`, `cancelled` or `exhausted` -- the only
 * near-hits are a different vocabulary entirely (`1 queued` in the
 * composer/dock strip counts queued *input lines*, not a delegation; a
 * checkpoint-restore confirm card colours "delegation d-08 (running)" in
 * `--warning`, line ~974, but that is the colour of *what restoring would
 * lose*, the same idiom that colours the theorems it would lose too -- not
 * the delegation's own state colour, which the Jobs page itself already
 * gives as accent). For these six, rather than let them fall through to a
 * silent default, each gets its own considered (not scavenged) choice,
 * recorded here so a later page with real evidence can correct it:
 *   - `queued`: muted -- nothing is happening yet, the same "neutral,
 *     pre-work" reading `checking…` and `unknown` already have.
 *   - `waiting`, `paused`: warning -- each is a delegation not making
 *     progress on its own and pending something (a dependency, a human
 *     resume), the same bucket `stale`/`interrupted` occupy in `STATE_TONE`
 *     for the same reason: recorded, not resolved.
 *   - `exhausted`: warning -- stopped by its own lease ceiling without a
 *     verdict either way; worth a human's attention without asserting the
 *     work failed.
 *   - `failed`: error -- the one definitively bad terminal outcome, the
 *     same bucket `VERDICT_TONE` reserves for `sorryAx`/`refused`/`not
 *     accepted`; letting it default to muted would draw a failure exactly
 *     like `completed`.
 *   - `cancelled`: muted -- a deliberate stop, not a failure of the work;
 *     the same reasoning `STATE_TONE`'s own `declined` entry already
 *     documents for "a decision made the ordinary way". */
const DELEGATION_TONE = {
  queued: 'muted',
  active: 'accent',
  waiting: 'warning',
  paused: 'warning',
  completed: 'muted',
  partial: 'muted',
  failed: 'error',
  cancelled: 'muted',
  exhausted: 'warning',
  unknown: 'muted',
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

/** A `DelegationState` value's tone -- see `DELEGATION_TONE` above for the
 * evidence and reasoning behind each of the ten. The smoke server's
 * `FakeDelegations` fixture (`tests/unit/web_fakes.py`) briefly answered
 * `"running"`, which is not a `DelegationState` value -- it fell through to
 * `muted` here like anything else this table does not name, which was
 * correct (the fixture was wrong, not this function) but was mistaken for
 * evidence of the real vocabulary while this page was built (issue #166).
 * The fixture now constructs its state from `DelegationState.ACTIVE`
 * itself, so `active`'s real `accent` tone is what a reader now sees. */
export function toneForDelegation(word) {
  return DELEGATION_TONE[word] ?? 'muted';
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
