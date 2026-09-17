// End-to-end smoke against the real bundle and the real server.
//
// Run `uv run python tests/unit/web_smoke_server.py --port 8765` in one shell
// and this in another. It proves six things no unit test can: the built page
// still carries the token placeholder the server stamps, every asset the page
// asks for is actually served, the event stream opens, a line posted to
// `/api/input` comes back down that stream as a `reply`, every endpoint a
// panel reads answers JSON, and a file dropped on the page is staged where the
// uploads panel will find it. On top of that sweep, `/api/environment`,
// `/api/record` and `/api/chats` are checked against their documented shapes,
// and `/api/graph` is checked for the classification tokens the drawing
// needs: `family` per node, `style` per edge, and a `tones` map covering
// every `ObligationStatus`. Task 13 adds the eight endpoints Shipment 2's
// remaining pages read: `/api/results`, `/api/ledger`, `/api/ledger/item`,
// `/api/ledger/export`, `/api/runs`, `/api/runs/item`, `/api/publications`
// and `/api/checkpoints`, each checked against the read model it comes from
// in `src/hardy/app/web/panels/`.
//
// Deliberately not a test runner. It exits 0 and prints `smoke ok`, or it
// throws with the first thing that was wrong.

const port = Number(process.env.HARDY_WEB_PORT ?? process.argv[2] ?? 8765);
const base = `http://127.0.0.1:${port}`;
const REPLY_TIMEOUT = 5000;

function check(condition, message) {
  if (!condition) throw new Error(message);
}

async function page(path = '/') {
  const response = await fetch(`${base}${path}`);
  check(response.status === 200, `GET ${path} answered ${response.status}`);
  const html = await response.text();
  const meta = /<meta name="hardy-token" content="([^"]*)"/.exec(html);
  check(meta, 'the page carries no hardy-token meta');
  check(meta[1] && meta[1] !== '__HARDY_TOKEN__', 'the token placeholder was not replaced by the server');
  check(!/<script(?![^>]*\ssrc=)/i.test(html), 'the page contains an inline <script>');
  check(!/https?:\/\/(?!127\.0\.0\.1|localhost)/i.test(html), 'the page references an off-host URL');
  return {html, token: meta[1]};
}

async function assets(html) {
  const urls = [
    ...[...html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g)].map((match) => match[1]),
    ...[...html.matchAll(/<link[^>]*\shref="([^"]+)"/g)].map((match) => match[1]),
  ];
  check(urls.length > 0, 'the page references no assets at all');
  // Real routing is entirely client-side (`session/useHash.js`'s `#/page/arg`
  // hash, never sent to the server) and so cannot be exercised from a
  // fetch-only script with no JS engine -- but the router's own subscription,
  // `window.addEventListener('hashchange', onChange)`, is a call site a
  // minifier has no reason to rewrite (`addEventListener` names a browser
  // API, not a local binding, and the event name is a string literal), so it
  // survives bundling verbatim. Matched as a call site rather than a bare
  // `hashchange` substring, because `react-dom` itself carries a `hashchange`
  // in an unrelated internal event-priority table regardless of whether this
  // app's own router ships -- a bare substring check passed even after the
  // subscription below was deleted outright, which is exactly the kind of
  // check that cannot fail this task exists to rule out. The call-site
  // pattern does not appear anywhere in `react-dom`; it appeared only after
  // rebuilding with the real subscription restored.
  const HASH_SUBSCRIBE = /addEventListener\([`'"]hashchange[`'"]/;
  let hashRouting = false;
  for (const url of urls) {
    // Root-absolute, not relative. The server answers every unknown route
    // with the page, so a page served at `/files/lean` would resolve a
    // relative `./assets/x.js` to `/files/assets/x.js`, be handed index.html
    // as `text/html`, and render nothing.
    check(url.startsWith('/'), `${url} is not root-absolute`);
    const resolved = new URL(url, `${base}/`);
    const response = await fetch(resolved);
    check(response.status === 200, `${url} answered ${response.status}`);
    // The static handler answers an unknown path with index.html rather than
    // a 404, so a 200 alone does not prove the asset exists.
    const type = response.headers.get('content-type') ?? '';
    check(!type.startsWith('text/html'), `${url} fell back to the page; the asset is missing`);
    if (url.endsWith('.js')) {
      const body = await response.text();
      if (HASH_SUBSCRIBE.test(body)) hashRouting = true;
    } else {
      await response.arrayBuffer();
    }
  }
  check(hashRouting, 'no shipped JS asset subscribes to "hashchange"; the hash router may not be bundled');
  return urls.length;
}

/** Read `/api/events`, collecting every event, and resolve on the first `wanted` accepts.
 *
 *  The collected list is handed back with the match, because a turn is judged
 *  by the whole run of events it emitted and not only by its last one.
 */
function stream(wanted) {
  return new Promise((resolve, reject) => {
    const seen = [];
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
      reject(new Error(`no matching event within ${REPLY_TIMEOUT} ms`));
    }, REPLY_TIMEOUT);
    const done = (value) => {
      clearTimeout(timer);
      controller.abort();
      resolve({event: value, seen});
    };
    fetch(`${base}/api/events`, {signal: controller.signal})
      .then(async (response) => {
        check(response.status === 200, `GET /api/events answered ${response.status}`);
        const type = response.headers.get('content-type') ?? '';
        check(type.startsWith('text/event-stream'), `the stream is ${type}`);
        let buffer = '';
        for await (const chunk of response.body) {
          buffer += Buffer.from(chunk).toString('utf8');
          let cut;
          while ((cut = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, cut);
            buffer = buffer.slice(cut + 2);
            const data = frame
              .split('\n')
              .filter((line) => line.startsWith('data:'))
              .map((line) => line.slice(5).trim())
              .join('\n');
            if (!data) continue; // a keepalive or the connected comment
            const event = JSON.parse(data);
            seen.push(event);
            if (wanted(event)) return done(event);
          }
        }
        throw new Error('the event stream ended before the reply arrived');
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        clearTimeout(timer);
        reject(error);
      });
  });
}

//: Every GET the page makes for a panel, in the order the tabs sit in. A
//: panel whose endpoint 404s or answers HTML is a panel that would draw the
//: server's error text and nothing else, and no unit test of `panels.py`
//: catches a route that was never wired.
const PANEL_ENDPOINTS = [
  '/api/state', '/api/commands', '/api/transcript', '/api/projects',
  '/api/summary', '/api/files', '/api/uploads', '/api/jobs',
  '/api/tree', '/api/sources', '/api/graph', '/api/models', '/api/cas/cells',
  '/api/environment', '/api/record', '/api/chats',
  '/api/results', '/api/ledger', '/api/runs', '/api/publications', '/api/checkpoints',
];

async function panels() {
  for (const path of PANEL_ENDPOINTS) {
    const response = await fetch(`${base}${path}`);
    check(response.status === 200, `GET ${path} answered ${response.status}`);
    const type = response.headers.get('content-type') ?? '';
    // The static handler answers an unknown route with the page, so a 200
    // alone does not prove the endpoint exists.
    check(type.startsWith('application/json'), `GET ${path} answered ${type}, not JSON`);
    JSON.parse(await response.text());
  }
  return PANEL_ENDPOINTS.length;
}

/** The graph panel's own answer, checked for the cases the drawing distinguishes.
 *
 *  Not a test of `panels.graph` -- `test_web_panels.py` owns that -- but of the
 *  fixture the browser check is run against. An empty ledger answers 200 and
 *  two empty lists, so a smoke that only asked for a status code would pass
 *  while the panel had never drawn a node, an edge family, or the one thing
 *  the graph says that nothing else does: a relation gone stale.
 */
//: Every `ObligationStatus` value, exactly as `contracts.py` declares it.
//: `/api/graph`'s `tones` map is total over the enum -- a status added there
//: without a matching tone raises in `panels/record.py` rather than falling
//: back to a default colour, so this list is what would catch the map going
//: silently partial again.
const OBLIGATION_STATUSES = ['open', 'investigating', 'blocked', 'resolved', 'dismissed', 'abandoned'];

async function graph() {
  const response = await fetch(`${base}/api/graph`);
  check(response.status === 200, `GET /api/graph answered ${response.status}`);
  const {nodes, edges, tones} = JSON.parse(await response.text());
  check(nodes.length >= 4, `the ledger has ${nodes.length} nodes, fewer than four`);
  check(edges.length >= 3, `the ledger has ${edges.length} edges, fewer than three`);
  check(edges.some((edge) => edge.stale), 'no edge in the ledger is stale');
  const families = new Set(edges.map((edge) => edge.kind));
  check(families.size >= 3, `the edges are of ${families.size} kinds, fewer than three`);
  check(
    nodes.some((node) => node.evidence.includes('formal')),
    'no node carries formal evidence, so the "F" badge is never drawn',
  );

  // The classification tokens the drawing itself reads, not the ledger data
  // they are computed from -- a panel that stopped sending `family` or
  // `style` would leave every node the same colour and every edge the same
  // stroke, and nothing above this would notice.
  check(
    nodes.every((node) => typeof node.family === 'string' && node.family),
    'a ledger node carries no family, so the graph panel cannot colour it',
  );
  const nodeFamilies = new Set(nodes.map((node) => node.family));
  check(nodeFamilies.size >= 3, `the nodes carry ${nodeFamilies.size} families, fewer than the three the fixture seeds`);
  check(
    edges.every((edge) => edge.style === 'solid' || edge.style === 'dashed'),
    `an edge carries a style other than "solid"/"dashed": ${JSON.stringify(edges.map((edge) => edge.style))}`,
  );
  const edgeStyles = new Set(edges.map((edge) => edge.style));
  check(edgeStyles.size >= 2, `the edges carry ${edgeStyles.size} style(s), not both solid and dashed`);

  check(tones && typeof tones === 'object' && !Array.isArray(tones), '/api/graph carries no tones map');
  for (const status of OBLIGATION_STATUSES) {
    check(
      typeof tones[status] === 'string' && tones[status],
      `/api/graph's tones map has no entry for obligation status ${JSON.stringify(status)}`,
    );
  }
  check(
    Object.keys(tones).length === OBLIGATION_STATUSES.length,
    `/api/graph's tones map has ${Object.keys(tones).length} entries, not the ${OBLIGATION_STATUSES.length} ObligationStatus values`,
  );

  return {nodes: nodes.length, edges: edges.length, stale: edges.filter((edge) => edge.stale).length};
}

/** `/api/environment`'s own answer: every doctor check, and a failure count that agrees with it. */
async function environment() {
  const response = await fetch(`${base}/api/environment`);
  check(response.status === 200, `GET /api/environment answered ${response.status}`);
  const body = JSON.parse(await response.text());
  check(Array.isArray(body.checks), '/api/environment did not answer a checks array');
  check(body.checks.length > 0, '/api/environment reported no checks at all');
  for (const item of body.checks) {
    check(typeof item.name === 'string' && item.name, 'an /api/environment check has no name');
    check(typeof item.ok === 'boolean', `${item.name}'s ok is ${JSON.stringify(item.ok)}, not a boolean`);
    check(typeof item.detail === 'string', `${item.name}'s detail is ${JSON.stringify(item.detail)}, not a string`);
    check(typeof item.required === 'boolean', `${item.name}'s required is ${JSON.stringify(item.required)}, not a boolean`);
  }
  // `failures` is a derived count, not an independent field -- checked against
  // the list it is derived from rather than against a fixed number, since
  // which checks pass depends on the machine the smoke runs on.
  const required = body.checks.filter((item) => item.required && !item.ok).length;
  check(
    body.failures === required,
    `/api/environment reports ${body.failures} failures, but ${required} required checks are not ok`,
  );
  return body.checks.length;
}

/** `/api/record`'s counts, cross-checked against each other and against the fixture's four items. */
async function record() {
  const response = await fetch(`${base}/api/record`);
  check(response.status === 200, `GET /api/record answered ${response.status}`);
  const body = JSON.parse(await response.text());
  check(Number.isInteger(body.items) && body.items >= 4, `/api/record reports ${body.items} items, fewer than the four the fixture seeds`);
  for (const key of ['by_family', 'by_kind', 'evidence', 'obligations']) {
    check(
      body[key] !== null && typeof body[key] === 'object' && !Array.isArray(body[key]),
      `/api/record's ${key} is ${JSON.stringify(body[key])}, not an object`,
    );
  }
  const byFamilyTotal = Object.values(body.by_family).reduce((sum, count) => sum + count, 0);
  check(
    byFamilyTotal === body.items,
    `/api/record's by_family sums to ${byFamilyTotal}, not the ${body.items} items it reports`,
  );
  const byKindTotal = Object.values(body.by_kind).reduce((sum, count) => sum + count, 0);
  check(
    byKindTotal === body.items,
    `/api/record's by_kind sums to ${byKindTotal}, not the ${body.items} items it reports`,
  );
  check(
    Object.keys(body.by_family).length >= 3,
    `/api/record's by_family has ${Object.keys(body.by_family).length} families, fewer than the three the fixture seeds`,
  );
  check(
    (body.evidence.formal ?? 0) >= 1,
    '/api/record reports no formal evidence, though the fixture seeds one theorem with a formal artifact',
  );
  check(Number.isInteger(body.revision), `/api/record's revision is ${JSON.stringify(body.revision)}, not an integer`);
  return body.items;
}

/** `/api/chats`'s rows, checked for shape rather than for turn counts the fake session never persists. */
async function chats() {
  const response = await fetch(`${base}/api/chats`);
  check(response.status === 200, `GET /api/chats answered ${response.status}`);
  const body = JSON.parse(await response.text());
  check(Array.isArray(body), '/api/chats did not answer an array');
  check(body.length >= 1, '/api/chats answered no chats at all');
  const main = body.find((chat) => chat.id === 'main');
  check(main, '/api/chats has no "main" chat, though every project starts with one');
  for (const chat of body) {
    check(typeof chat.id === 'string' && chat.id, 'a chat in /api/chats has no id');
    check(typeof chat.title === 'string' && chat.title, `${chat.id}'s title is ${JSON.stringify(chat.title)}`);
    check(typeof chat.created === 'number', `${chat.id}'s created is ${JSON.stringify(chat.created)}, not a number`);
    // `turns`/`last_activity` are `null`, not `0`, when nothing could be read
    // -- the fake session never writes a transcript file to disk, so `main`
    // legitimately reports `null` here rather than a count.
    check(
      chat.turns === null || Number.isInteger(chat.turns),
      `${chat.id}'s turns is ${JSON.stringify(chat.turns)}, neither null nor an integer`,
    );
    check(
      chat.last_activity === null || typeof chat.last_activity === 'number',
      `${chat.id}'s last_activity is ${JSON.stringify(chat.last_activity)}, neither null nor a number`,
    );
  }
  return body.length;
}

/** `/api/results`'s theorem table on a fixture that records a ledger claim but never writes `lean/`.
 *
 *  `panels/record.py`'s `results()` builds one row per Lean `theorem`/`lemma`
 *  the tree on disk currently declares (`_lean_declarations`, which scans
 *  `problem/lean` for `.lean` files) -- `web_smoke_server.py`'s `seed_ledger`
 *  records a `FORMAL` evidence ref naming `lean/Sylow.lean`, but never writes
 *  that file, so the honest answer here is no rows at all, not a row guessed
 *  from the ledger claim. `revision` is checked against `/api/ledger`'s own
 *  answer for the same snapshot rather than against a literal number, the
 *  same reason `record()` above checks `failures` against the list it is
 *  derived from: both read `LedgerStore(problem).read().revision` off the
 *  same store (`panels/record.py:150,184,508`).
 */
async function results(ledgerRevision) {
  const response = await fetch(`${base}/api/results`);
  check(response.status === 200, `GET /api/results answered ${response.status}`);
  const body = JSON.parse(await response.text());
  check(Array.isArray(body.theorems), '/api/results did not answer a theorems array');
  check(
    body.theorems.length === 0,
    `/api/results reports ${body.theorems.length} theorems, though the fixture writes no lean/ tree`,
  );
  check(Number.isInteger(body.revision), `/api/results's revision is ${JSON.stringify(body.revision)}, not an integer`);
  check(
    body.revision === ledgerRevision,
    `/api/results reports revision ${body.revision}, but /api/ledger reported ${ledgerRevision} for the same snapshot`,
  );
  return body.theorems.length;
}

/** `/api/ledger`'s rows, checked against the fixture's four items and the one it revises twice.
 *
 *  Named `ledgerList`, not `ledger`, because `main()` already binds a local
 *  `ledger` to `graph()`'s return value -- the project ledger's graph view,
 *  not this endpoint's list view. Two things sharing the word "ledger" is the
 *  domain, not a naming accident to paper over.
 */
async function ledgerList() {
  const response = await fetch(`${base}/api/ledger`);
  check(response.status === 200, `GET /api/ledger answered ${response.status}`);
  const body = JSON.parse(await response.text());
  check(Array.isArray(body.items), '/api/ledger did not answer an items array');
  check(body.items.length >= 4, `/api/ledger reports ${body.items.length} items, fewer than the four the fixture seeds`);
  for (const item of body.items) {
    check(typeof item.id === 'string' && item.id, 'a /api/ledger item has no id');
    check(typeof item.family === 'string' && item.family, `${item.id}'s family is ${JSON.stringify(item.family)}`);
    check(Array.isArray(item.evidence), `${item.id}'s evidence is not an array`);
    check(
      item.obligations !== null && typeof item.obligations === 'object' && !Array.isArray(item.obligations),
      `${item.id}'s obligations is ${JSON.stringify(item.obligations)}, not an object`,
    );
    check(Number.isInteger(item.version) && item.version >= 1, `${item.id}'s version is ${JSON.stringify(item.version)}`);
  }
  const lemma = body.items.find((item) => item.id === 'lemma-conjugacy');
  check(lemma, '/api/ledger has no "lemma-conjugacy" row, though the fixture seeds one');
  check(
    lemma.version >= 2,
    `"lemma-conjugacy" carries version ${lemma.version}, fewer than the two revisions the fixture writes`,
  );
  return {count: body.items.length, ids: body.items.map((item) => item.id), revision: body.revision};
}

/** `/api/ledger/item`'s full history and relations for the one item the fixture revises. */
async function ledgerItem() {
  const response = await fetch(`${base}/api/ledger/item?id=lemma-conjugacy`);
  check(response.status === 200, `GET /api/ledger/item answered ${response.status}`);
  const body = JSON.parse(await response.text());
  check(body.id === 'lemma-conjugacy', `/api/ledger/item answered about ${JSON.stringify(body.id)}`);
  check(
    Array.isArray(body.versions) && body.versions.length === 2,
    `/api/ledger/item reports ${JSON.stringify(body.versions)} versions, not the fixture's two`,
  );
  check(
    body.versions[0].version === 1 && body.versions[1].version === 2,
    `/api/ledger/item's versions are numbered ${JSON.stringify(body.versions.map((v) => v.version))}, not 1, 2`,
  );
  // Both relations the fixture pins to this exact lemma revision: `rel-depends`
  // (`depends_on`, the generic stale path) and `rel-formalizes` (`formalizes`,
  // the real-versions path) -- see `seed_ledger`'s own docstring in
  // `tests/unit/web_smoke_server.py`.
  const kinds = body.relations.map((relation) => relation.kind);
  check(kinds.includes('depends_on'), '/api/ledger/item has no "depends_on" relation touching lemma-conjugacy');
  check(kinds.includes('formalizes'), '/api/ledger/item has no "formalizes" relation touching lemma-conjugacy');
  return body.versions.length;
}

/** `/api/ledger/export`'s closure for the theorem, checked against both the stale rows and the documented one. */
async function ledgerExport() {
  const response = await fetch(`${base}/api/ledger/export?id=thm-sylow-three`);
  check(response.status === 200, `GET /api/ledger/export answered ${response.status}`);
  const body = JSON.parse(await response.text());
  check(body.root === 'thm-sylow-three', `/api/ledger/export answered about ${JSON.stringify(body.root)}`);
  check(
    Array.isArray(body.rows) && body.rows.length >= 3,
    `/api/ledger/export reports ${JSON.stringify(body.rows)} rows, fewer than the closure's three`,
  );
  const byId = Object.fromEntries(body.rows.map((row) => [row.id, row]));
  check(
    byId['lemma-conjugacy']?.stale === true,
    'the closure row for lemma-conjugacy is not stale, though its formalizes relation was pinned to a superseded version',
  );
  check(byId['thm-sylow-three']?.stale === true, 'the closure row for thm-sylow-three is not stale');
  check(
    byId['sec-counting']?.stale === false,
    'the closure row for sec-counting is stale, though nothing pinned it to a moved version',
  );
  check(
    byId['thm-sylow-three']?.writeup?.documented === true,
    "thm-sylow-three's writeup is not documented, though sec-counting documents it",
  );
  check(
    body.export_command && body.export_command.available === false,
    '/api/ledger/export reports export_command.available as true, though no /export proof subcommand exists',
  );
  return body.rows.length;
}

/** `/api/runs` for a project that has never run `/prove` -- an honest empty list, not an error. */
async function runs() {
  const response = await fetch(`${base}/api/runs`);
  check(response.status === 200, `GET /api/runs answered ${response.status}`);
  const body = JSON.parse(await response.text());
  check(Array.isArray(body.runs), '/api/runs did not answer a runs array');
  check(body.runs.length === 0, `/api/runs reports ${body.runs.length} runs, though the smoke server sets no runs_root`);
  return body.runs.length;
}

/** `/api/runs/item` for a run that does not exist -- the same clean 400 an unknown ledger id already gets. */
async function runItem() {
  const response = await fetch(`${base}/api/runs/item?id=12345678-1234-5678-1234-567812345678`);
  check(response.status === 400, `GET /api/runs/item for an unknown run answered ${response.status}`);
  const body = JSON.parse(await response.text());
  check(typeof body.error === 'string' && body.error.includes('runs'), `/api/runs/item's error is ${JSON.stringify(body.error)}`);
}

/** `/api/publications`'s item list, cross-checked against `/api/ledger`'s -- the same ledger, read two ways. */
async function publications(ledgerIds) {
  const response = await fetch(`${base}/api/publications`);
  check(response.status === 200, `GET /api/publications answered ${response.status}`);
  const body = JSON.parse(await response.text());
  check(Array.isArray(body.items), '/api/publications did not answer an items array');
  check(Array.isArray(body.candidates), '/api/publications did not answer a candidates array');
  for (const item of body.items) {
    check(typeof item.visibility === 'string' && item.visibility, `${item.id}'s visibility is ${JSON.stringify(item.visibility)}`);
    check(
      typeof item.visibility_tone === 'string' && item.visibility_tone,
      `${item.id}'s visibility_tone is ${JSON.stringify(item.visibility_tone)}`,
    );
    check(Array.isArray(item.link_source_kinds), `${item.id}'s link_source_kinds is not an array`);
  }
  const ids = body.items.map((item) => item.id).sort();
  const expected = [...ledgerIds].sort();
  check(
    JSON.stringify(ids) === JSON.stringify(expected),
    `/api/publications names ${JSON.stringify(ids)}, not the same items /api/ledger reported (${JSON.stringify(expected)})`,
  );
  return body.items.length;
}

/** `/api/checkpoints` for a project nothing has ever checkpointed -- an honest empty list. */
async function checkpoints() {
  const response = await fetch(`${base}/api/checkpoints`);
  check(response.status === 200, `GET /api/checkpoints answered ${response.status}`);
  const body = JSON.parse(await response.text());
  check(Array.isArray(body.checkpoints), '/api/checkpoints did not answer a checkpoints array');
  check(
    body.checkpoints.length === 0,
    `/api/checkpoints reports ${body.checkpoints.length} checkpoints, though nothing checkpointed this fixture`,
  );
  return body.checkpoints.length;
}

/** `/api/files`'s four trees (Task 1: `panels.files` -> `{lean, tex, cas, pdf}`
 *  of `Row`s, not bare path strings).
 *
 *  The fixture writes no `lean/`, `tex/` or `cas/` file to disk and no
 *  `writeup.pdf` -- the same reason `results()` above expects zero theorem
 *  rows -- so every tree here is an honest empty list too, and there is no
 *  row to inspect `bytes`/`modified`/`verdict` on (`test_web_panels.py`
 *  covers those against a real file). What this does check end to end: the
 *  documented `{lean, tex, cas, pdf}` shape still answers, and computing
 *  Lean verdicts (`record.file_verdicts`) over a problem with no `lean/`
 *  directory at all does not throw.
 */
async function filesTree() {
  const response = await fetch(`${base}/api/files`);
  check(response.status === 200, `GET /api/files answered ${response.status}`);
  const body = JSON.parse(await response.text());
  for (const tree of ['lean', 'tex', 'cas', 'pdf']) {
    check(Array.isArray(body[tree]), `/api/files's ${tree} is ${JSON.stringify(body[tree])}, not an array`);
    check(
      body[tree].length === 0,
      `/api/files reports ${body[tree].length} ${tree} rows, though the fixture writes none to disk`,
    );
  }
  return Object.values(body).reduce((sum, rows) => sum + rows.length, 0);
}

/** Stage one `.lean` file the way the drop zone does, and see the panel list it. */
async function staged(token) {
  const name = `smoke-${Date.now()}.lean`;
  const put = await fetch(`${base}/api/upload`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/octet-stream',
      'X-Hardy-Token': token,
      'X-Hardy-Filename': name,
      Origin: base,
    },
    body: 'theorem smoke : True := trivial\n',
  });
  const body = await put.text();
  check(put.status === 200, `POST /api/upload answered ${put.status}: ${body}`);
  const described = JSON.parse(body);
  check(described.name === name, `the upload was staged as ${JSON.stringify(described.name)}`);
  check(described.kind === 'lean', `a .lean file was staged as ${JSON.stringify(described.kind)}`);

  const listed = await fetch(`${base}/api/uploads`);
  check(listed.status === 200, `GET /api/uploads answered ${listed.status}`);
  const files = JSON.parse(await listed.text());
  const found = files.find((file) => file.name === name);
  check(found, `${name} is not in /api/uploads`);
  check(found.path.includes(name), `the staged path ${JSON.stringify(found.path)} does not name the file`);
  return name;
}

async function send(token, text) {
  const sent = await fetch(`${base}/api/input`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Hardy-Token': token, Origin: base},
    body: JSON.stringify({text}),
  });
  // Read once: the body is a stream, and reading it to build a message that
  // is only used on failure consumes it before the success path parses it.
  const body = await sent.text();
  check(sent.status === 200, `POST /api/input answered ${sent.status}: ${body}`);
  const outcome = JSON.parse(body);
  check(outcome.kind === 'send', `${JSON.stringify(text)} was classified ${outcome.kind}, not send`);
}

/** One turn, driven end to end: subscribe, send, wait for `turn_end`. */
async function turn(token, text) {
  // Subscribed before the line is sent: the stream is live-only, so an event
  // emitted before this connects is one nothing would ever see.
  const running = stream((event) => event.type === 'turn_end');
  await new Promise((resolve) => setTimeout(resolve, 200));
  await send(token, text);
  return running;
}

async function main() {
  const {html, token} = await page();
  const count = await assets(html);

  // The same page from a nested client route, with the same assets: this is
  // the path `base: './'` used to break.
  const nested = await page('/files/lean');
  check(nested.token === token, 'the nested route served a different token');
  await assets(nested.html);

  const plain = await turn(token, 'hello');
  const replies = plain.seen.filter((event) => event.type === 'turn' && event.kind === 'reply');
  check(replies.length === 1, `${replies.length} reply events for one turn`);
  check(replies[0].text === 'hello', `the reply said ${JSON.stringify(replies[0].text)}`);

  // A turn that interrupts itself with a session notice, which is what a
  // delegation finishing mid-turn does. The client identifies the streaming
  // message by an id held for the whole turn, so the interruption must not
  // split it: one reply, carrying the turn's whole text, arriving after the
  // notice it was interrupted by.
  const split = await turn(token, 'interleave');
  const notices = split.seen.filter((event) => event.type === 'notice');
  const spoken = split.seen.filter((event) => event.type === 'turn' && event.kind === 'text');
  const finals = split.seen.filter((event) => event.type === 'turn' && event.kind === 'reply');
  check(notices.length === 1, `${notices.length} notices in the interrupted turn`);
  check(spoken.length === 2, `${spoken.length} text events in the interrupted turn`);
  check(finals.length === 1, `${finals.length} reply events in the interrupted turn`);
  check(finals[0].text === 'one two', `the reply said ${JSON.stringify(finals[0].text)}`);
  const order = split.seen.map((event) => (event.type === 'notice' ? 'notice' : `${event.type}:${event.kind ?? ''}`));
  check(
    order.indexOf('notice') > order.indexOf('turn:text') &&
      order.indexOf('notice') < order.lastIndexOf('turn:text'),
    `the notice did not land between the two text events: ${order.join(' ')}`,
  );

  const endpoints = await panels();
  const ledger = await graph();
  const envChecks = await environment();
  const items = await record();
  const chatCount = await chats();

  // The eight endpoints Task 13 adds. `ledgerList` runs first because
  // `results` cross-checks its `revision` against the same snapshot, and
  // `publications` cross-checks its item ids against the same list.
  const ledgerRows = await ledgerList();
  const resultRows = await results(ledgerRows.revision);
  const itemVersions = await ledgerItem();
  const exportRows = await ledgerExport();
  const runCount = await runs();
  await runItem();
  const pubItems = await publications(ledgerRows.ids);
  const checkpointCount = await checkpoints();
  const fileRows = await filesTree();

  const name = await staged(token);

  console.log(
    `smoke ok (${count} assets, ${endpoints} panel endpoints, ` +
      `${ledger.nodes} ledger nodes and ${ledger.edges} edges with ${ledger.stale} stale, ` +
      `${envChecks} environment checks, ${items} record items, ${chatCount} chats, ` +
      `${ledgerRows.count} ledger rows, ${resultRows} result theorems, ` +
      `${itemVersions} versions on lemma-conjugacy, ${exportRows} export rows, ` +
      `${runCount} runs, ${pubItems} publication items, ${checkpointCount} checkpoints, ` +
      `${fileRows} file rows, ` +
      `staged ${name}, reply "hello", interrupted turn replies "one two" once)`,
  );
}

main().catch((error) => {
  console.error(`smoke failed: ${error.message}`);
  process.exit(1);
});
