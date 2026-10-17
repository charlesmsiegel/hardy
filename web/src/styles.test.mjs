// The palette has one rule that nothing else checks: every colour is a
// variable, and every colour variable is declared twice -- in `:root` for
// light and again in the `:root` under `prefers-color-scheme: dark`. A token
// added to the light block alone looks right in a light browser and, in a
// dark one, falls back to its light value, which is usually unreadable on the
// dark background. This reads `styles.css` as text, so it needs no browser
// and no build.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const css = readFileSync(new URL('./styles.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

//: the custom properties of the first `:root {` block at or after `from`,
//: name -> value; null when there is no such block.
function rootBlock(from) {
  const start = css.indexOf(':root {', from);
  if (start < 0) return null;
  const body = css.slice(start + ':root {'.length, css.indexOf('}', start));
  return new Map([...body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map(([, name, value]) => [name, value.trim()]));
}

const light = rootBlock(0);
const darkMedia = css.indexOf('@media (prefers-color-scheme: dark)');
const dark = darkMedia < 0 ? null : rootBlock(darkMedia);
const isColour = (value) => /^(#|rgba?\(|hsla?\(|color-mix\()/.test(value);

test('every colour token has a dark value', () => {
  assert.ok(light, 'styles.css declares a :root block');
  assert.ok(dark, 'styles.css declares a :root block under prefers-color-scheme: dark');
  const colours = [...light].filter(([, value]) => isColour(value)).map(([name]) => name);
  assert.ok(colours.length > 0, 'the light :root declares colour tokens');
  const missing = colours.filter((name) => !dark.has(name));
  assert.deepEqual(missing, [], `colour tokens with no dark value: ${missing.join(', ')}`);
});

test('the ui stack and focus ring exist', () => {
  assert.ok(light, 'styles.css declares a :root block');
  assert.ok(light.has('--ui'), ':root declares --ui, the system sans stack for chrome');
  assert.ok(light.has('--focus'), ':root declares --focus, the one focus-ring colour');
});

//: the declarations of the first rule whose selector list contains `selector`.
function rule(selector) {
  const match = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].find(([, selectors]) => selectors.split(',').map((value) => value.trim()).includes(selector));
  assert.ok(match, `styles.css has a rule for ${selector}`);
  return match[2];
}

const channels = (hex) => [1, 3, 5].map((at) => parseInt(hex.slice(at, at + 2), 16) / 255);
const luminance = (hex) => {
  const [r, g, b] = channels(hex).map((value) => value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a, b) => {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (high + 0.05) / (low + 0.05);
};
const blend = (top, bottom, share) => '#' + channels(top).map((value, at) => Math.round((value * share + channels(bottom)[at] * (1 - share)) * 255).toString(16).padStart(2, '0')).join('');

test('a quiet button\'s hover tint keeps its accent text at 4.5:1 on either surface', () => {
  const share = Number(/color-mix\(in srgb, var\(--accent\) (\d+)%, transparent\)/.exec(rule('.button--quiet:hover:enabled'))?.[1]) / 100;
  assert.ok(share > 0, 'the hover is the accent at some share over transparent');
  for (const [palette, tokens] of [['light', light], ['dark', dark]]) {
    for (const surface of ['--bg', '--panel']) {
      const ratio = contrast(tokens.get('--accent'), blend(tokens.get('--accent'), tokens.get(surface), share));
      assert.ok(ratio >= 4.5, `${palette} accent on its hover tint over ${surface} is ${ratio.toFixed(2)}:1`);
    }
  }
});

test('the picker and the composer box edge themselves with the strong border', () => {
  for (const selector of ['.picker', '.composer__box']) assert.match(rule(selector), /border:\s*1px solid var\(--border-strong\)/, selector);
});

//: A declaration's value in a rule's body, with `var(--token)` resolved
//: against one palette; only the colour tokens are variables here.
function resolved(declarations, property, tokens) {
  const raw = new RegExp(`(?:^|[;\\s])${property}:\\s*([^;]+)`).exec(declarations)?.[1].trim();
  const token = /^var\((--[\w-]+)\)$/.exec(raw ?? '')?.[1];
  assert.ok(token && tokens.has(token), `${property} is a colour token (${raw})`);
  return tokens.get(token);
}

test('a sidebar count keeps its digits at 4.5:1 on its fill, in both palettes', () => {
  for (const selector of ['.wb-nav__badge', '.wb-nav__badge--attention']) {
    // The attention badge only changes the fill; the digits' colour is the base rule's.
    const declarations = rule(selector) + (selector === '.wb-nav__badge' ? '' : rule('.wb-nav__badge'));
    for (const [palette, tokens] of [['light', light], ['dark', dark]]) {
      const ratio = contrast(resolved(declarations, 'color', tokens), resolved(declarations, 'background', tokens));
      assert.ok(ratio >= 4.5, `${selector} digits on their fill are ${ratio.toFixed(2)}:1 in ${palette}`);
    }
  }
  assert.match(rule('.wb-nav__badge--attention'), /background:\s*var\(--error\)/, 'attention is the error tone');
});

//: the `max-width` of every `@media (max-width: Npx)` block holding a rule for
//: `selector` with `declaration` in it.
function narrowedAt(selector, declaration) {
  const widths = [];
  for (const media of css.matchAll(/@media \(max-width: (\d+)px\) \{/g)) {
    const from = media.index + media[0].length;
    let depth = 1;
    let at = from;
    while (depth > 0 && at < css.length) {
      depth += css[at] === '{' ? 1 : css[at] === '}' ? -1 : 0;
      at += 1;
    }
    const hit = [...css.slice(from, at - 1).matchAll(/([^{}]+)\{([^{}]*)\}/g)].some(
      ([, selectors, body]) => selectors.split(',').map((value) => value.trim()).includes(selector) && body.includes(declaration),
    );
    if (hit) widths.push(Number(media[1]));
  }
  return widths;
}

test('the sidebar is a rail before the pinned dock squeezes the page', () => {
  // An open sidebar (184px) beside the dock (380px) leaves a page 337px wide at
  // 901px; the rail has to be back before the dock is.
  const [rail] = narrowedAt('.wb-sidebar', 'width: 48px');
  const [dock] = narrowedAt('.wb-dock', 'display: none');
  assert.ok(rail && dock, 'both breakpoints are declared');
  assert.ok(rail > dock, `the rail (${rail}px) comes before the dock goes (${dock}px)`);
});

test('the current entry keeps its cue when colours are forced, and hover does not dim it', () => {
  assert.match(rule('.wb-nav__item[aria-current="page"]'), /border-inline-start:\s*3px solid var\(--accent\)/, 'a border survives forced colours; a box-shadow does not');
  assert.doesNotMatch(rule('.wb-nav__item[aria-current="page"]'), /box-shadow/);
  assert.match(rule('.wb-nav__item:hover:enabled:not([aria-current])'), /background/, 'hover tints only entries that are not current');
});

test('a three-card row wraps by the width its page has, not the window\'s', () => {
  // Beside the page sit a sidebar (open or a rail) and a dock (pinned or not),
  // so the same window gives pages of very different widths.
  assert.match(rule('.wb-cols3'), /repeat\(auto-fit,\s*minmax\(min\(100%,\s*\d+px\),\s*1fr\)\)/);
  assert.deepEqual(narrowedAt('.wb-cols3', 'grid-template-columns'), [], 'no window query forces it to one column');
});
