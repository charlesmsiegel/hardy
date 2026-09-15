"""The loopback HTTP boundary: what it serves, what it refuses, and to whom."""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest
from web_fakes import FakeSession, make_config, make_problem

from hardy.app.web.host import WebHost
from hardy.app.web.server import serve


class FakeOpener:
    def __init__(self, tmp_path):
        self.tmp_path, self.session = tmp_path, None

    def __call__(self, slug, confirm, current, *, chat="main"):
        import dataclasses

        self.session = FakeSession(self.tmp_path / slug, chat)
        return dataclasses.replace(current, project=slug, chat=chat), self.session

    def cancel(self):
        return False


@pytest.fixture
def server(tmp_path: Path):
    make_problem(tmp_path, "sylow")
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text(
        '<!doctype html><meta name="hardy-token" content="__HARDY_TOKEN__"><div id=root></div>',
        encoding="utf-8",
    )
    (static / "assets").mkdir()
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    host = WebHost(make_config(tmp_path), FakeOpener(tmp_path), lambda confirm, cfg: FakeSession(cfg.layout.problem))
    host.start()
    srv = serve(host, port=0, static=static, report=lambda *_: None, serve_forever=False)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        host.stop()


def _call(srv, method, path, body=None, *, token=True, headers=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
    hdrs = {"Host": f"127.0.0.1:{srv.server_port}"}
    if token:
        hdrs["X-Hardy-Token"] = srv.token
        hdrs["Origin"] = f"http://127.0.0.1:{srv.server_port}"
    hdrs.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None and "Content-Type" not in hdrs:
        hdrs["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=hdrs)
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response.status, response.getheader("Content-Type", ""), payload


def test_index_embeds_the_token_and_assets_are_served(server) -> None:
    status, ctype, body = _call(server, "GET", "/", token=False)
    assert status == 200 and "text/html" in ctype and server.token.encode() in body
    status, ctype, body = _call(server, "GET", "/assets/app.js", token=False)
    assert status == 200 and "javascript" in ctype
    status, *_ = _call(server, "GET", "/../pyproject.toml", token=False)
    assert status in {400, 404}


def test_models_and_cas_cells_are_served(server) -> None:
    status, _, body = _call(server, "GET", "/api/models", token=False)
    assert status == 200
    models = json.loads(body)
    assert models["current"] == "fake-model" and models["backend"] == "claude"
    values = [row["value"] for row in models["rows"]]
    assert "fake-model" in values and "claude-opus-5" in values and "…other" not in values
    assert [row["current"] for row in models["rows"]].count(True) == 1
    assert all("availability unverified" in row["note"] for row in models["rows"])
    status, _, body = _call(server, "GET", "/api/cas/cells", token=False)
    assert status == 200 and json.loads(body)["cells"] == []


def test_unknown_routes_fall_back_to_the_page(server) -> None:
    status, ctype, body = _call(server, "GET", "/files/lean", token=False)
    assert status == 200 and "text/html" in ctype and server.token.encode() in body
    status, _, _ = _call(server, "GET", "/api/nonsense", token=False)
    assert status == 404


def test_security_headers_and_refusals(server) -> None:
    status, _, body = _call(server, "POST", "/api/input", {"text": "hi"}, token=False)
    assert status == 403
    status, _, _ = _call(server, "POST", "/api/input", {"text": "hi"}, headers={"Origin": "http://evil.test"})
    assert status == 403
    status, _, _ = _call(server, "GET", "/api/state", token=False, headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    # A token is whatever the client sent, and `compare_digest` raises
    # `TypeError` on a non-ASCII str: the wrong answer to a wrong token.
    status, _, _ = _call(server, "POST", "/api/input", {"text": "hi"}, headers={"X-Hardy-Token": "tokén"})
    assert status == 403
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    conn.request("GET", "/api/state", headers={"Host": "evil.test"})
    assert conn.getresponse().status == 403
    conn.close()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    conn.request("GET", "/api/state", headers={"Host": f"127.0.0.1:{server.server_port}"})
    response = conn.getresponse()
    assert response.getheader("Cache-Control") == "no-store"
    assert "default-src 'self'" in response.getheader("Content-Security-Policy")
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    conn.close()


def test_state_projects_input_and_events(server) -> None:
    status, _, body = _call(server, "GET", "/api/state", token=False)
    assert status == 200 and json.loads(body)["slug"] == "sylow"
    status, _, body = _call(server, "GET", "/api/projects", token=False)
    assert json.loads(body)[0]["chats"][0]["id"] == "main"
    status, _, body = _call(server, "POST", "/api/input", {"text": "hello"})
    assert status == 200 and json.loads(body)["kind"] == "send"
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    conn.request("GET", "/api/events?after=0", headers={"Host": f"127.0.0.1:{server.server_port}"})
    response = conn.getresponse()
    assert response.getheader("Content-Type").startswith("text/event-stream")
    text = b""
    deadline = time.time() + 5
    while b'"changed"' not in text and time.time() < deadline:
        text += response.fp.readline()
    conn.close()
    assert b"id: " in text
    events = [json.loads(line[len(b"data: "):]) for line in text.splitlines() if line.startswith(b"data: ")]
    assert any(event.get("type") == "turn" and event.get("kind") == "reply" for event in events)
    assert any(event.get("type") == "changed" for event in events)


def test_refusal_is_409_with_the_dispatcher_text(server) -> None:
    from hardy.agents.contracts import TurnEvent

    server.host.session.script = [TurnEvent("text", "a")] * 40
    server.host.session.delay = 0.01
    _call(server, "POST", "/api/input", {"text": "one"})
    # A message mid-turn is queued, and says so with a 200; a command that
    # cannot run mid-turn is the refusal, with the dispatcher's own sentence.
    status, _, body = _call(server, "POST", "/api/input", {"text": "two"})
    assert status == 200 and json.loads(body)["kind"] == "queued"
    status, _, body = _call(server, "POST", "/api/input", {"text": "/goal x"})
    assert status == 409 and "cannot run" in json.loads(body)["error"]
    status, _, body = _call(server, "POST", "/api/open", {"slug": "sylow", "chat": "main"})
    assert status == 409
    # Let the scripted turn finish before the fixture stops the host: this
    # fake ignores `cancel`, and tearing the loop down under a turn it is
    # still republishing is noise this test did not mean to assert about.
    deadline = time.time() + 5
    while server.host.state()["turn_running"] and time.time() < deadline:
        time.sleep(0.02)
    assert not server.host.state()["turn_running"]


def test_chats_create_rename_open(server) -> None:
    status, _, body = _call(server, "POST", "/api/projects/sylow/chats", {"title": "Lean proof"})
    assert status == 200
    made = json.loads(body)
    status, _, body = _call(server, "PATCH", f"/api/projects/sylow/chats/{made['id']}", {"title": "Lean proof II"})
    assert status == 200 and json.loads(body)["title"] == "Lean proof II"
    status, _, body = _call(server, "PATCH", "/api/projects/sylow/chats/main", {"title": "x"})
    assert status == 400
    status, _, body = _call(server, "POST", "/api/open", {"slug": "sylow", "chat": made["id"]})
    assert status == 200 and json.loads(body)["chat"] == made["id"]


def test_projects_route_creates_one_and_refuses_the_rest(server, tmp_path: Path) -> None:
    """`POST /api/projects` is held to `/project new`'s guards, not to none.

    `prepare_layout` scatters `lean/`, `tex/`, `cas/` and a record through
    whatever directory it is pointed at, so a name that is already a project
    -- or a directory somebody else made -- has to be refused before the
    opener is ever called.
    """
    status, _, body = _call(server, "POST", "/api/projects", {"name": "frobenius"})
    assert status == 200
    assert "frobenius" in {entry["slug"] for entry in json.loads(body)}
    status, _, body = _call(server, "POST", "/api/projects", {"name": "sylow"})
    assert status == 400 and "already a project" in json.loads(body)["error"]
    stray = tmp_path / "somebody-elses"
    stray.mkdir()
    (stray / "notes.txt").write_text("mine", encoding="utf-8")
    status, _, body = _call(server, "POST", "/api/projects", {"name": "somebody-elses"})
    assert status == 400 and "not a Hardy project" in json.loads(body)["error"]
    assert (stray / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert not (stray / "lean").exists() and not (stray / ".gitignore").exists()


def test_open_route_refuses_a_slug_or_chat_nothing_made(server) -> None:
    status, _, body = _call(server, "POST", "/api/open", {"slug": "nowhere", "chat": "main"})
    assert status == 400 and "nowhere" in json.loads(body)["error"]
    status, _, body = _call(server, "POST", "/api/open", {"slug": "sylow", "chat": "never-made"})
    assert status == 400 and "never-made" in json.loads(body)["error"]


def test_library_route_imports_a_staged_document_and_refuses_mid_turn(
    server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hardy.literature.sources import tools

    monkeypatch.setattr(tools, "library_root", lambda: tmp_path / "library")
    status, _, body = _call(server, "POST", "/api/upload", raw=b"# A note\n\nTheorem 1. Fine.\n",
                            headers={"X-Hardy-Filename": "notes.md", "Content-Type": "application/octet-stream"})
    assert status == 200 and json.loads(body)["kind"] == "source"
    status, _, body = _call(server, "POST", "/api/library", {"name": "notes.md", "intent": "background"})
    assert status == 200, body
    imported = json.loads(body)
    assert len(imported["artifact"]) == 64 and imported["seed"]

    from hardy.agents.contracts import TurnEvent

    server.host.session.script = [TurnEvent("text", "a")] * 40
    server.host.session.delay = 0.01
    _call(server, "POST", "/api/input", {"text": "one"})
    status, _, body = _call(server, "POST", "/api/library", {"name": "notes.md", "intent": "background"})
    assert status == 409 and "still running" in json.loads(body)["error"]
    deadline = time.time() + 5
    while server.host.state()["turn_running"] and time.time() < deadline:
        time.sleep(0.02)
    assert not server.host.state()["turn_running"]


def test_panels_and_files(server) -> None:
    problem = server.host.config.layout.problem
    (problem / "lean" / "A.lean").write_text("theorem t : True := trivial\n", encoding="utf-8")
    for path in ("/api/summary", "/api/jobs", "/api/tree", "/api/sources", "/api/graph",
                 "/api/transcript", "/api/commands", "/api/files", "/api/uploads"):
        status, ctype, _ = _call(server, "GET", path, token=False)
        assert status == 200 and "application/json" in ctype, path
    status, _, body = _call(server, "GET", "/api/file?path=lean/A.lean", token=False)
    assert json.loads(body)["text"].startswith("theorem")
    status, *_ = _call(server, "GET", "/api/file?path=../x", token=False)
    assert status == 400


def test_upload_discard_and_bad_names(server) -> None:
    status, _, body = _call(server, "POST", "/api/upload", raw=b"theorem t : True := trivial\n",
                            headers={"X-Hardy-Filename": "A.lean", "Content-Type": "application/octet-stream"})
    assert status == 200 and json.loads(body)["kind"] == "lean"
    status, _, body = _call(server, "GET", "/api/uploads", token=False)
    assert [u["name"] for u in json.loads(body)] == ["A.lean"]
    status, *_ = _call(server, "POST", "/api/upload", raw=b"x",
                       headers={"X-Hardy-Filename": "../x", "Content-Type": "application/octet-stream"})
    assert status == 400
    status, *_ = _call(server, "POST", "/api/upload", raw=b"x", token=False,
                       headers={"X-Hardy-Filename": "B.lean", "Content-Type": "application/octet-stream"})
    assert status == 403
    status, *_ = _call(server, "DELETE", "/api/uploads/A.lean")
    assert status == 200
    status, _, body = _call(server, "GET", "/api/uploads", token=False)
    assert json.loads(body) == []


def test_answer_and_cancel(server) -> None:
    status, _, body = _call(server, "POST", "/api/answer", {"prompt_id": "nope", "value": "x"})
    assert status == 409
    status, _, body = _call(server, "POST", "/api/cancel", {})
    assert status == 200 and json.loads(body)["stopped"] == 0


def test_body_limit(server) -> None:
    status, *_ = _call(server, "POST", "/api/input", raw=b"x" * ((1 << 20) + 1),
                       headers={"Content-Type": "application/json"})
    assert status == 413


def test_a_stopped_host_answers_503(server) -> None:
    server.host.stop()
    status, _, body = _call(server, "POST", "/api/input", {"text": "hi"})
    assert status == 503 and "shutting down" in json.loads(body)["error"]


def test_cli_parses_web() -> None:
    from hardy.app.cli import build_parser

    args = build_parser().parse_args(["web", "--port", "8123", "--open", "--project", "sylow", "--chat", "x"])
    assert args.command == "web" and args.port == 8123 and args.open is True and args.chat == "x"
