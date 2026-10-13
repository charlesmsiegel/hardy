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

//: The schemes a link in a reply may carry. Anything else -- `javascript:`,
//: `data:`, `vbscript:` -- is a script the reader would run by clicking, so
//: the anchor is dropped and only its text survives. A URL with no scheme at
//: all is relative and stays a link. Control characters are stripped before
//: the scheme is read, because `java\nscript:` is one to a browser.
const SCHEMES = new Set(['http', 'https', 'mailto']);

function linkable(href) {
  // eslint-disable-next-line no-control-regex
  const cleaned = String(href ?? '').replace(/[\u0000-\u0020]/g, '');
  const scheme = /^([a-z][a-z0-9+.\-]*):/i.exec(cleaned);
  return !scheme || SCHEMES.has(scheme[1].toLowerCase());
}

const marked = new Marked({gfm: true, breaks: false});
marked.use({
  renderer: {
    html: () => '',
    link(token) {
      // `false` hands the token back to the default renderer; anything else
      // replaces it. Here the replacement is the link's own text, unlinked.
      return linkable(token.href) ? false : this.parser.parseInline(token.tokens);
    },
  },
});

// Math is recognized before HTML exists. Links, raw tags and code delimit
// source runs; their URLs and markup can never become TeX source.
function tex(source, displayMode) {
  return katex.renderToString(source, {displayMode, throwOnError: false});
}

marked.use({extensions: [{
  name: 'math',
  level: 'inline',
  start(source) { return source.indexOf('$'); },
  tokenizer(source) {
    const display = source.startsWith('$$');
    const match = display
      ? /^\$\$((?:\\[\s\S]|[^$])+?)\$\$/.exec(source)
      : /^\$((?:\\[^\n]|[^$\n])+?)\$(?!\d)/.exec(source);
    if (!match || !match[1].trim() || (!display && match[1] !== match[1].trim())) return undefined;
    // Ask the same lexer which inline constructs would occupy this span.
    // TeX backslashes remain the original source, never entity-decoded HTML.
    // Stars/underscores inside a delimited formula are TeX, even when
    // marked would treat them as emphasis. Structural markup remains a
    // boundary, including code or links nested inside emphasis.
    const textTokens = (tokens) => tokens.every((token) =>
      ['text', 'escape', 'em', 'strong', 'del'].includes(token.type)
      && (!token.tokens || textTokens(token.tokens)));
    if (!textTokens(this.lexer.inlineTokens(match[1]))) {
      return undefined;
    }
    return {type: 'math', raw: match[0], source: match[1], display};
  },
  renderer(token) { return tex(token.source, token.display); },
}]});

export function render(markdown) {
  return marked.parse(markdown ?? '');
}
