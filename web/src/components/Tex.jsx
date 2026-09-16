// One TeX highlighter, the same restraint as `Lean.jsx`: commands, comments
// and math delimiters picked out of read-only source, nothing resolved and
// nothing clickable. The provenance banner, the compile log and the
// statement-vs-record check the design draws beside a TeX file are shipment
// 3's editor, not this; this component only reads a string.
//
// Reuses `.lean-block`'s box (a generic "block of highlighted monospace
// text", not actually Lean-specific) but its own `tex-token--*` colour
// classes rather than `lean-token--*`: the two highlighters share a look by
// both drawing from the same design tokens, not by one borrowing the
// other's class names for an unrelated language.

const TOKEN_RE = /%[^\n]*|\\[A-Za-z]+\*?|\\\[|\\\]|\\\(|\\\)|\$\$?/g;

function tokenClass(token) {
  if (token.startsWith('%')) return 'tex-token tex-token--comment';
  if (token.startsWith('\\[') || token.startsWith('\\]') || token.startsWith('\\(') || token.startsWith('\\)') || token.startsWith('$')) {
    return 'tex-token tex-token--math';
  }
  return 'tex-token tex-token--command';
}

export default function Tex({src}) {
  const text = src || '';
  const parts = [];
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(TOKEN_RE)) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    parts.push(
      <span key={key++} className={tokenClass(token)}>
        {token}
      </span>,
    );
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <pre className="lean-block">{parts}</pre>;
}
