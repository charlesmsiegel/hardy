// One Lean highlighter, so a block reads the same wherever it appears -- the
// chat transcript, a theorem's Results panel, an axiom in a gate card:
// keywords bold, tactics --accent, comments muted italic, strings --warning,
// `sorry` --error bold, `⊢`/`#print` etc. --accent bold.
//
// It does not know which identifiers are real declarations -- that lookup
// (mathlib vs this project vs unknown) belongs to whoever holds the session,
// which is a page, not this component. Pass the names you can resolve as
// `known` and this dots the underline and wires the click only for those;
// leave it out and the block still highlights correctly, just with nothing
// clickable, which is the honest state before that lookup exists.
//
// `onName` receives the token and the click event: Chat is the first live
// caller, and it needs the pointer position to place a `Peek` popover near
// the name that was clicked, not just the name itself.

const STRUCTURAL = 'theorem|lemma|def|example|import|namespace|end|variable|noncomputable|instance|structure|class|open|section|universe|abbrev|axiom|where|deriving';
const TACTICS = "by|intro|intros|obtain|have|exact|rcases|rw|simp|omega|norm_num|apply|refine|constructor|cases|induction|calc|show|use|decide|ring|linarith|aesop|trivial|rfl|fun|match|with|let|at|do|then|else|if|exists";
const STRUCTURAL_RE = new RegExp(`^(?:${STRUCTURAL})$`);

function tokenPattern(known) {
  const names = known && known.length
    ? `(?:${[...known].sort((a, b) => b.length - a.length).map((k) => k.replace(/[.'\\]/g, '\\$&')).join('|')})(?![A-Za-z0-9_.'])|`
    : '';
  return new RegExp(
    `${names}\\/-[\\s\\S]*?-\\/|--[^\\n]*|"(?:[^"\\\\]|\\\\.)*"|\\bsorry\\b|#(?:print|check|eval|reduce)\\b|⊢|\\b(?:${STRUCTURAL})\\b|\\b(?:${TACTICS})\\b`,
    'g',
  );
}

function tokenClass(token, known) {
  if (token.startsWith('/-') || token.startsWith('--')) return 'lean-token lean-token--comment';
  if (token.startsWith('"')) return 'lean-token lean-token--string';
  if (token === 'sorry') return 'lean-token lean-token--sorry';
  if (token === '⊢' || token.startsWith('#')) return 'lean-token lean-token--directive';
  if (known && known.includes(token)) return 'lean-token lean-token--name';
  if (STRUCTURAL_RE.test(token)) return 'lean-token lean-token--keyword';
  return 'lean-token lean-token--tactic';
}

export default function Lean({src, onName, known}) {
  const text = src || '';
  const re = tokenPattern(known);
  const parts = [];
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(re)) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    const clickable = Boolean(known && known.includes(token));
    parts.push(
      <span
        key={key++}
        className={tokenClass(token, known)}
        onClick={clickable ? (event) => onName?.(token, event) : undefined}
      >
        {token}
      </span>,
    );
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <pre className="lean-block">{parts}</pre>;
}
