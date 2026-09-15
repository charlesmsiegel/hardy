// End-to-end smoke against the real bundle and the real server.
//
// Run `uv run python tests/unit/web_smoke_server.py --port 8765` in one shell
// and this in another. It proves four things no unit test can: the built page
// still carries the token placeholder the server stamps, every asset the page
// asks for is actually served, the event stream opens, and a line posted to
// `/api/input` comes back down that stream as a `reply`.
//
// Deliberately not a test runner. It exits 0 and prints `smoke ok`, or it
// throws with the first thing that was wrong.

const port = Number(process.env.HARDY_WEB_PORT ?? process.argv[2] ?? 8765);
const base = `http://127.0.0.1:${port}`;
const REPLY_TIMEOUT = 5000;

function check(condition, message) {
  if (!condition) throw new Error(message);
}

async function page() {
  const response = await fetch(`${base}/`);
  check(response.status === 200, `GET / answered ${response.status}`);
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

/** Read `/api/events` and resolve on the first event `wanted` accepts. */
function stream(wanted) {
  return new Promise((resolve, reject) => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
      reject(new Error(`no matching event within ${REPLY_TIMEOUT} ms`));
    }, REPLY_TIMEOUT);
    const done = (value) => {
      clearTimeout(timer);
      controller.abort();
      resolve(value);
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

async function main() {
  const {html, token} = await page();
  const count = await assets(html);

  // Subscribed before the line is sent: the stream is live-only, so an event
  // emitted before this connects is one nothing would ever see.
  const reply = stream((event) => event.type === 'turn' && event.kind === 'reply');
  await new Promise((resolve) => setTimeout(resolve, 200));

  const sent = await fetch(`${base}/api/input`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Hardy-Token': token, Origin: base},
    body: JSON.stringify({text: 'hello'}),
  });
  // Read once: the body is a stream, and reading it to build a message that
  // is only used on failure consumes it before the success path parses it.
  const body = await sent.text();
  check(sent.status === 200, `POST /api/input answered ${sent.status}: ${body}`);
  const outcome = JSON.parse(body);
  check(outcome.kind === 'send', `the line was classified ${outcome.kind}, not send`);

  const event = await reply;
  check(event.text === 'hello', `the reply said ${JSON.stringify(event.text)}`);

  console.log(`smoke ok (${count} assets, reply ${JSON.stringify(event.text)} at seq ${event.seq})`);
}

main().catch((error) => {
  console.error(`smoke failed: ${error.message}`);
  process.exit(1);
});
