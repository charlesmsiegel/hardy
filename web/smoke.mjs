// End-to-end smoke against the real bundle and the real server.
//
// Run `uv run python tests/unit/web_smoke_server.py --port 8765` in one shell
// and this in another. It proves six things no unit test can: the built page
// still carries the token placeholder the server stamps, every asset the page
// asks for is actually served, the event stream opens, a line posted to
// `/api/input` comes back down that stream as a `reply`, every endpoint a
// panel reads answers JSON, and a file dropped on the page is staged where the
// uploads panel will find it.
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
    await response.arrayBuffer();
  }
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
async function graph() {
  const response = await fetch(`${base}/api/graph`);
  check(response.status === 200, `GET /api/graph answered ${response.status}`);
  const {nodes, edges} = JSON.parse(await response.text());
  check(nodes.length >= 4, `the ledger has ${nodes.length} nodes, fewer than four`);
  check(edges.length >= 3, `the ledger has ${edges.length} edges, fewer than three`);
  check(edges.some((edge) => edge.stale), 'no edge in the ledger is stale');
  const families = new Set(edges.map((edge) => edge.kind));
  check(families.size >= 3, `the edges are of ${families.size} kinds, fewer than three`);
  check(
    nodes.some((node) => node.evidence.includes('formal')),
    'no node carries formal evidence, so the "F" badge is never drawn',
  );
  return {nodes: nodes.length, edges: edges.length};
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
  const name = await staged(token);

  console.log(
    `smoke ok (${count} assets, ${endpoints} panel endpoints, ` +
      `${ledger.nodes} ledger nodes and ${ledger.edges} edges with one stale, staged ${name}, ` +
      'reply "hello", interrupted turn replies "one two" once)',
  );
}

main().catch((error) => {
  console.error(`smoke failed: ${error.message}`);
  process.exit(1);
});
