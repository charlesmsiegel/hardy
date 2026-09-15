"""The loopback HTTP boundary: JSON in, JSON and Server-Sent Events out, one static bundle.

This module is the only part of the browser client that knows about HTTP, and
it knows nothing else: it parses a request, proves the request may act, routes
it to `host`, `panels`, `uploads` or `chats`, and serializes what comes back.
Every rule about what a panel may read, what an upload may be called, or what
may run while a turn is in flight belongs to those modules and is not repeated
here -- a second copy of a safety rule is a second chance to get it wrong.

Two things the boundary does own. The first is who may act: the server binds
to loopback, refuses any request whose `Host` or `Origin` is not its own
address, and requires every mutation to echo a token that exists only in the
page it served, so another site open in the same browser can neither read the
session nor drive it. The second is the stream: one `GET /api/events` per tab,
resumable by sequence number, because a page reload must be able to say what
it already drew rather than start the transcript again.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from hardy.app.web import chats, panels, uploads
from hardy.app.web.host import Busy, WebHost
from hardy.foundation.files import LayoutError
from hardy.workflows.layout import validate_slug

#: A JSON request body past this is refused; the page sends lines of text and
#: chat titles, never documents. Files arrive at `/api/upload` instead, under
#: `uploads.MAX_UPLOAD`.
MAX_BODY = 1 << 20
#: An oversized body is still read and discarded up to this much before the
#: refusal is sent. Closing the connection on a client still writing resets it
#: on Windows, and the browser would then report a dropped connection rather
#: than the 413 that says what was wrong.
DRAIN_LIMIT = 8 << 20
#: How long a read on a request body may stall before the connection is given
#: up on; a browser that opened a request and stopped writing must not hold a
#: server thread for the rest of the process's life.
BODY_TIMEOUT = 30
#: The gap between keepalive comments on an idle event stream. Long enough to
#: cost nothing, short enough that a proxy or a sleeping laptop's NAT does not
#: decide the connection is dead.
KEEPALIVE = 15
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
       "img-src 'self' data:; object-src 'self'; frame-ancestors 'none'; connect-src 'self'")
TOKEN_PLACEHOLDER = "__HARDY_TOKEN__"
GONE = "the session is shutting down"


class WebServer(ThreadingHTTPServer):
    """One host, one token, one static directory, bound to loopback only.

    `daemon_threads`, because a tab parked on `/api/events` holds its thread
    until something arrives on it: joining those on the way out would make
    Ctrl+C wait for a keepalive on every open tab.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host: WebHost, port: int, static: Path) -> None:
        self.host = host
        self.static = static
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server: WebServer

    def log_message(self, *args: Any) -> None:
        """Silent: the useful output is the URL, printed once by `serve`."""

    # -- plumbing --------------------------------------------------------

    def _headers(self, code: int, ctype: str, length: int | None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        # Nothing here is cacheable: every answer describes a session that is
        # changing while it is read, and a stale panel is worse than a slow one.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()

    def _bytes(self, code: int, data: bytes, ctype: str) -> None:
        self._headers(code, ctype, len(data))
        self.wfile.write(data)

    def _json(self, code: int, value: Any) -> None:
        self._bytes(code, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8")

    def _allowed(self, *, mutation: bool) -> bool:
        """Whether this request may be answered at all, and mutations at all.

        Three independent checks, because each closes a different door. `Host`
        being our own loopback address defeats DNS rebinding, where a name the
        attacker controls resolves to 127.0.0.1 after the page has loaded.
        `Origin` being ours defeats an ordinary cross-site form post. The token
        defeats the case both of those still allow -- a page served from this
        very origin by something else -- and is the one check a mutation cannot
        pass without having read the page Hardy served.
        """
        port = self.server.server_port
        host = self.headers.get("Host", "")
        ok = host in {f"127.0.0.1:{port}", f"localhost:{port}"}
        ok = ok and self.headers.get("Sec-Fetch-Site") != "cross-site"
        origin = self.headers.get("Origin")
        if origin is not None:
            ok = ok and origin == f"http://{host}"
        if mutation:
            ok = ok and origin == f"http://{host}"
            # `isascii` first: `compare_digest` raises `TypeError` on a str
            # holding a codepoint above 127, and a header is whatever the
            # client sent. A traceback out of the handler thread is a worse
            # answer to a wrong token than the 403 a wrong token gets.
            token = self.headers.get("X-Hardy-Token", "")
            ok = ok and token.isascii() and secrets.compare_digest(token, self.server.token)
        if not ok:
            self._discard_body()
            self._json(403, {"error": "Open this action from the local Hardy page."})
        return ok

    def _discard_body(self) -> None:
        """Read and drop the body of a request that is about to be refused.

        Not politeness: closing a connection whose peer is still writing
        resets it rather than ending it, on Windows in particular, and the
        client is then told the connection failed instead of being handed the
        403 or the 413 that says what was actually wrong. Bounded, because an
        absurd body is not worth reading to be polite about.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        self.connection.settimeout(BODY_TIMEOUT)
        remaining = min(max(length, 0), DRAIN_LIMIT)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1 << 16))
            if not chunk:
                break
            remaining -= len(chunk)

    def _body(self, limit: int) -> bytes | None:
        """The request body, or None once the refusal has already been sent."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        self.connection.settimeout(BODY_TIMEOUT)
        if not 0 <= length <= limit:
            self._discard_body()
            self._json(413, {"error": f"Request body is larger than {limit} bytes"})
            return None
        return self.rfile.read(length)

    def _json_body(self) -> dict | None:
        raw = self._body(MAX_BODY)
        if raw is None:
            return None
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            self._json(400, {"error": "Body is not JSON"})
            return None
        if not isinstance(data, dict):
            self._json(400, {"error": "Body must be a JSON object"})
            return None
        return data

    def _problem(self) -> Path:
        return self.server.host.config.layout.problem

    # -- GET ---------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._allowed(mutation=False):
            return
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        query = parse_qs(parts.query)
        try:
            if path == "/api/events":
                # `Last-Event-ID` is what the browser's own `EventSource`
                # resends on a reconnect; `?after=` is for a reload, which
                # starts a new stream and has to say where it got to itself.
                after = query.get("after", [None])[0] or self.headers.get("Last-Event-ID")
                self._events(int(after) if after else None)
            elif path.startswith("/api/"):
                self._api_get(path[len("/api/"):], query)
            else:
                self._static(path)
        except RuntimeError:
            self._json(503, {"error": GONE})
        except (ValueError, LayoutError, KeyError) as error:
            self._json(400, {"error": str(error) or "bad request"})
        except OSError as error:
            self._json(404, {"error": str(error)})

    def _api_get(self, name: str, query: dict[str, list[str]]) -> None:
        host = self.server.host
        session, problem = host.session, self._problem()
        if name == "state":
            self._json(200, host.state())
        elif name == "projects":
            self._json(200, host.projects())
        elif name == "commands":
            self._json(200, [{"name": c.name, "summary": c.summary, "argument_hint": c.argument_hint,
                              "safe_in_flight": c.safe_in_flight, "template": c.template is not None}
                             for c in host.registry])
        elif name == "transcript":
            self._json(200, panels.transcript(session))
        elif name == "summary":
            self._json(200, panels.summary(session))
        elif name == "tree":
            self._json(200, panels.tree(session))
        elif name == "jobs":
            self._json(200, panels.jobs(session))
        elif name == "files":
            self._json(200, panels.files(problem))
        elif name == "file":
            self._json(200, panels.file_text(problem, query.get("path", [""])[0]))
        elif name == "pdf":
            self._bytes(200, panels.pdf_bytes(problem, query.get("path", [""])[0]), "application/pdf")
        elif name == "sources":
            self._json(200, panels.sources(problem))
        elif name == "graph":
            self._json(200, panels.graph(problem))
        elif name == "uploads":
            self._json(200, uploads.staged(problem))
        else:
            self._json(404, {"error": "unknown endpoint"})

    def _events(self, after: int | None) -> None:
        """One tab's view of the stream, resumable and kept alive.

        The subscription is taken before a byte is written, so nothing emitted
        between the request arriving and the headers going out can fall
        between the replay and the live stream. It is closed on the way out
        however this ends -- a closed tab is a write that raises, and a
        subscriber nobody reads would otherwise grow a queue forever.
        """
        sub = self.server.host.subscribe(after)
        self._headers(200, "text/event-stream; charset=utf-8", None)
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = sub.queue.get(timeout=KEEPALIVE)
                except Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps(event, ensure_ascii=False)
                self.wfile.write(f"id: {event['seq']}\ndata: {payload}\n\n".encode())
                self.wfile.flush()
        except OSError:
            # The tab went away: `BrokenPipeError`, `ConnectionResetError` and
            # a closed socket are all this, and none of them is a failure.
            pass
        finally:
            sub.close()

    def _static(self, path: str) -> None:
        """The bundle, and nothing else on the machine.

        `..` is refused before resolution rather than after, and the resolved
        target is proven to be under the resolved bundle directory, so neither
        a traversal nor a symlink planted in `static/` can name a file outside
        it. Anything else that does not exist is an app route -- the client
        routes `/files/lean` itself -- so it is answered with the page.
        """
        root = self.server.static.resolve()
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        if ".." in Path(relative).parts:
            self._json(404, {"error": "unknown file"})
            return
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            target = root / "index.html"
            if not target.is_file():
                self._json(404, {"error": "unknown file"})
                return
        if target.name == "index.html":
            # Stamped on every serve, not once at build time: the token is per
            # process, and a bundle carrying yesterday's token is a page that
            # can read the session but never act on it.
            page = target.read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, self.server.token)
            self._bytes(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            # Windows registries have been known to map `.js` to
            # `text/plain`, and a module served as that never executes.
            mime = "text/javascript"
        self._bytes(200, target.read_bytes(), mime)

    # -- mutations -------------------------------------------------------

    def do_POST(self) -> None:
        if not self._allowed(mutation=True):
            return
        path = unquote(urlsplit(self.path).path)
        host = self.server.host
        try:
            if path == "/api/upload":
                raw = self._body(uploads.MAX_UPLOAD)
                if raw is None:
                    return
                self._json(200, uploads.stage(self._problem(), self.headers.get("X-Hardy-Filename", ""), raw))
                return
            data = self._json_body()
            if data is None:
                return
            if path == "/api/input":
                # A refusal is the dispatcher's own sentence, and the page
                # prints it rather than inventing one; everything else the
                # submission does reports itself on the stream.
                result = host.submit(str(data.get("text", "")))
                if result["kind"] == "refused":
                    self._json(409, {**result, "error": result["message"]})
                else:
                    self._json(200, result)
            elif path == "/api/answer":
                if host.answer(str(data.get("prompt_id", "")), data.get("value")):
                    self._json(200, {"ok": True})
                else:
                    self._json(409, {"error": "no prompt is waiting for that answer"})
            elif path == "/api/cancel":
                self._json(200, host.cancel())
            elif path == "/api/open":
                self._json(200, host.open_chat(str(data.get("slug", "")), str(data.get("chat", "main"))))
            elif path == "/api/projects":
                self._json(200, host.create_project(str(data.get("name", ""))))
            elif path.startswith("/api/projects/") and path.endswith("/chats"):
                slug = path[len("/api/projects/"):-len("/chats")]
                self._json(200, chats.create_chat(self._project(slug), str(data.get("title", ""))).as_dict())
            elif path == "/api/library":
                problem = self._problem()
                result = host.run_exclusive(lambda: uploads.library_import(
                    problem, str(data.get("name", "")), title=str(data.get("title", "")),
                    author=str(data.get("author", "")), intent=str(data.get("intent", ""))))
                self._json(200, result)
            else:
                self._json(404, {"error": "unknown action"})
        except Busy as error:
            self._json(409, {"error": str(error)})
        except RuntimeError:
            self._json(503, {"error": GONE})
        except (ValueError, LayoutError, KeyError, TypeError) as error:
            self._json(400, {"error": str(error) or "bad request"})
        except OSError as error:
            self._json(500, {"error": str(error)})

    def do_PATCH(self) -> None:
        if not self._allowed(mutation=True):
            return
        path = unquote(urlsplit(self.path).path)
        data = self._json_body()
        if data is None:
            return
        try:
            prefix = "/api/projects/"
            if path.startswith(prefix) and "/chats/" in path:
                slug, chat_id = path[len(prefix):].split("/chats/", 1)
                renamed = chats.rename_chat(self._project(slug), chat_id, str(data.get("title", "")))
                self._json(200, renamed.as_dict())
            else:
                self._json(404, {"error": "unknown action"})
        except RuntimeError:
            self._json(503, {"error": GONE})
        except (ValueError, LayoutError) as error:
            self._json(400, {"error": str(error) or "bad request"})
        except OSError as error:
            self._json(500, {"error": str(error)})

    def do_DELETE(self) -> None:
        if not self._allowed(mutation=True):
            return
        path = unquote(urlsplit(self.path).path)
        try:
            prefix = "/api/uploads/"
            if path.startswith(prefix):
                uploads.discard(self._problem(), path[len(prefix):])
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "unknown action"})
        except RuntimeError:
            self._json(503, {"error": GONE})
        except (ValueError, LayoutError) as error:
            self._json(400, {"error": str(error) or "bad request"})
        except OSError as error:
            self._json(500, {"error": str(error)})

    def _project(self, slug: str) -> Path:
        """`slug`'s directory under the root, proven to be one project name.

        A slug arrives here as a path segment of a URL, so it is held to the
        rule every other slug is held to before it is joined to anything --
        `/api/projects/..%2F..%2Fetc/chats` names no project. An existing
        directory, too: `create_chat` would otherwise conjure a problem
        directory out of a typo and leave it there with one chat in it.
        """
        problem = self.server.host.config.root / validate_slug(slug)
        if not problem.is_dir():
            raise ValueError(f"no project {slug!r}")
        return problem


def serve(host: WebHost, *, port: int = 0, static: Path | None = None, open_browser: bool = False,
          report: Any = print, serve_forever: bool = True) -> WebServer:
    """Bound to loopback; the page carries the token every mutation must echo.

    `serve_forever=False` hands the caller a bound, unserved server -- what a
    test drives on a thread of its own. Stopping it is the caller's: this
    function only closes the socket of a server it ran itself, on its way out
    of the Ctrl+C that ended it.
    """
    server = WebServer(host, port, static or Path(__file__).with_name("static"))
    url = f"http://127.0.0.1:{server.server_port}/"
    report(f"Hardy web on {url}  (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    if serve_forever:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            report("")
        finally:
            server.server_close()
    return server
