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
