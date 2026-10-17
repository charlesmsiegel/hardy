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
