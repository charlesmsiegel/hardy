// Write THIRD-PARTY-NOTICES.txt beside the built bundle.
//
// The bundle is committed, so the licences of what is compiled into it have
// to be committed with it: a reader of `src/hardy/app/web/static/` should be
// able to say what is in there and under what terms without a network and
// without `node_modules/`. Every direct dependency of `web/package.json` is
// listed with its resolved version, its declared licence, and the full text
// of whatever licence file it ships.

import {createRequire} from 'node:module';
import {readFileSync, readdirSync, writeFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const out = join(here, '..', 'src', 'hardy', 'app', 'web', 'static', 'THIRD-PARTY-NOTICES.txt');

const manifest = JSON.parse(readFileSync(join(here, 'package.json'), 'utf8'));
// Only what is bundled: `devDependencies` builds the bundle and is not in it.
const names = Object.keys(manifest.dependencies ?? {}).sort();

function meta(name) {
  const root = join(here, 'node_modules', ...name.split('/'));
  const own = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
  let text = '';
  for (const entry of readdirSync(root)) {
    if (/^(LICENSE|LICENCE|COPYING)/i.test(entry)) {
      text = readFileSync(join(root, entry), 'utf8').trim();
      break;
    }
  }
  return {name, version: own.version, license: own.license ?? 'see below', homepage: own.homepage ?? '', text};
}

const rule = '='.repeat(72);
const parts = [
  'Third-party notices for the Hardy web bundle',
  '',
  'The files in this directory are built from web/, whose runtime dependencies',
  'are listed below with the licence each ships. Not every one is reachable',
  'from every build -- Vite drops what no module imports -- so this is the set',
  'the bundle may contain. Regenerate it with `npm run build` in web/.',
  '',
];

for (const name of names) {
  const found = meta(name);
  parts.push(rule, `${found.name} ${found.version}  (${found.license})`);
  if (found.homepage) parts.push(found.homepage);
  parts.push('', found.text || 'No licence file ships with this package; see its package.json.', '');
}

writeFileSync(out, `${parts.join('\n')}\n`, 'utf8');
console.log(`notices: ${names.length} packages -> ${out}`);
