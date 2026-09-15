// Markdown and TeX for one assistant reply.
//
// What is rendered here is a local model's output and the user's own text, on
// a loopback page under a CSP that runs no inline script. That is not a reason
// to hand raw HTML through: a reply that contains `<div onclick=...>` would
// still draw a clickable thing the user did not write, and an `<img>` pointing
// anywhere would still be an attempt to fetch. The `html` renderer returns
// nothing, so every raw tag in a reply is dropped and only its text survives.

import {Marked} from 'marked';
import katex from 'katex';
import 'katex/dist/katex.min.css';

const marked = new Marked({gfm: true, breaks: false});
marked.use({renderer: {html: () => ''}});

//: Code is the one place a `$` means a dollar. Math is applied between these
//: spans and never inside them, so `` `$5` `` in a reply stays five dollars.
const CODE = /(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>)/;

const ENTITIES = {'&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&amp;': '&'};

/** Undo the escaping `marked` applied, so TeX reaches KaTeX as it was written.
 *
 *  `\le` arrives from the parser as `&lt;`-escaped text because the parser
 *  could not know it was TeX; handing that to KaTeX would typeset the entity.
 *  `&amp;` is last, or `&amp;lt;` would decode twice.
 */
function unescape(text) {
  return text
    .replace(/&(lt|gt|quot|#39);/g, (match) => ENTITIES[match])
    .replace(/&amp;/g, '&');
}

function tex(source, displayMode) {
  // `throwOnError: false` renders the offending source in red rather than
  // throwing: one malformed formula must not blank the whole reply.
  return katex.renderToString(unescape(source), {displayMode, throwOnError: false});
}

function renderMath(html) {
  return html
    .split(CODE)
    .map((part, index) =>
      index % 2
        ? part
        : part
            .replace(/\$\$([\s\S]+?)\$\$/g, (_, source) => tex(source, true))
            .replace(/(^|[^\\$])\$([^\n$]+?)\$/g, (_, before, source) => before + tex(source, false)),
    )
    .join('');
}

export function render(markdown) {
  return renderMath(marked.parse(markdown ?? ''));
}
