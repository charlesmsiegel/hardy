// Pulling ```lean fences out of an assistant reply's markdown so each one can
// be handed to `<Lean/>` as a live component instead of a `<pre><code>` the
// markdown renderer would otherwise draw. `render()` (markdown.js) still owns
// every other code fence and all of the prose around these -- this only
// slices the text into the pieces the two renderers each own.

const FENCE = /```lean\r?\n([\s\S]*?)```/gi;

/** `text` split into `{kind: 'prose', text}` and `{kind: 'lean', text}` runs,
 *  in order, prose segments included even when empty so the caller need not
 *  special-case the ends. */
export function splitLean(text) {
  const source = text ?? '';
  const segments = [];
  let last = 0;
  for (const match of source.matchAll(FENCE)) {
    segments.push({kind: 'prose', text: source.slice(last, match.index)});
    segments.push({kind: 'lean', text: match[1]});
    last = match.index + match[0].length;
  }
  segments.push({kind: 'prose', text: source.slice(last)});
  return segments;
}
