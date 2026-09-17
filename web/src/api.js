// The only part of the page that knows about HTTP.
//
// The token is read once from the meta the server stamps on every serve, and
// every mutation echoes it; the server refuses a mutation that cannot. A GET
// needs no token -- loopback, `Host` and `Origin` already answer for those --
// but sending it costs nothing and keeps one code path.

export const token = document.querySelector('meta[name="hardy-token"]')?.content ?? '';

/** An error carrying the server's status, so a 409 can be told from a 500. */
export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

async function call(method, path, body, headers = {}) {
  const init = {method, headers: {'X-Hardy-Token': token, ...headers}};
  if (body !== undefined && !(body instanceof Blob) && !(body instanceof ArrayBuffer)) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  } else if (body !== undefined) {
    init.body = body;
  }
  const response = await fetch(path, init);
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = {error: text};
  }
  if (!response.ok) {
    throw new ApiError(data?.error || `${method} ${path} failed (${response.status})`, response.status, data);
  }
  return data;
}

export const get = (path) => call('GET', path);
export const post = (path, body) => call('POST', path, body ?? {});
export const put = (path, body) => call('PUT', path, body);
export const patch = (path, body) => call('PATCH', path, body);
export const del = (path) => call('DELETE', path);
export const upload = (file) =>
  call('POST', '/api/upload', file, {'X-Hardy-Filename': file.name, 'Content-Type': 'application/octet-stream'});

/**
 * Subscribe to the session's event stream.
 *
 * `after` is where this page got to. It is null on a first open on purpose:
 * the transcript, fetched beside this, already says everything that happened
 * before now, and asking for `after=0` as well would draw the ring's copy of
 * it a second time. Once an event has arrived its sequence is what a
 * reconnect resumes from -- `EventSource` resends `Last-Event-ID` itself, but
 * a stream this closes and reopens has to say where it stopped.
 *
 * Returns a function that closes the stream and stops reconnecting.
 */
export function events(onEvent, after = null) {
  let last = after;
  let source = null;
  let timer = null;
  let closed = false;
  const open = () => {
    if (closed) return;
    source = new EventSource(last === null ? '/api/events' : `/api/events?after=${last}`);
    source.onmessage = (message) => {
      let event;
      try {
        event = JSON.parse(message.data);
      } catch {
        return;
      }
      if (typeof event.seq === 'number') last = event.seq;
      onEvent(event);
    };
    source.onerror = () => {
      source.close();
      if (closed) return;
      timer = setTimeout(open, 1000);
    };
  };
  open();
  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    if (source) source.close();
  };
}
