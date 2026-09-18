"""The loopback HTTP boundary: what it serves, what it refuses, and to whom."""

from __future__ import annotations

import http.client
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from web_fakes import FakeSession, make_config, make_problem, make_registry

from hardy.app.web.host import WebHost
from hardy.app.web.server import serve

NOW = datetime(2026, 9, 15, 14, 10, tzinfo=UTC)


class FakeOpener:
    def __init__(self, tmp_path):
        self.tmp_path, self.session = tmp_path, None

    def __call__(self, slug, confirm, current, *, chat="main", root=None):
        import dataclasses

        moved = {"root": root} if root is not None else {}
        config = dataclasses.replace(current, project=slug, chat=chat, **moved)
        self.session = FakeSession(config.layout.problem, chat)
        return config, self.session

    def cancel(self):
        return False


def _static(tmp_path: Path) -> Path:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text(
        '<!doctype html><meta name="hardy-token" content="__HARDY_TOKEN__"><div id=root></div>',
        encoding="utf-8",
    )
    (static / "assets").mkdir()
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return static


def _serve(host: WebHost, static: Path):
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


@pytest.fixture
def server(tmp_path: Path):
    make_problem(tmp_path, "sylow")
    registry = make_registry(tmp_path)
    registry.add(tmp_path / "sylow")
    host = WebHost(
        make_config(tmp_path), FakeOpener(tmp_path),
        lambda confirm, cfg: FakeSession(cfg.layout.problem), projects=registry,
    )
    yield from _serve(host, _static(tmp_path))


@pytest.fixture
def empty_server(tmp_path: Path):
    """A server with nothing open: no session, an empty registry."""
    host = WebHost(make_config(tmp_path), FakeOpener(tmp_path), None, projects=make_registry(tmp_path))
    yield from _serve(host, _static(tmp_path))


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


def test_commands_say_where_each_came_from(server) -> None:
    status, _, body = _call(server, "GET", "/api/commands", token=False)
    assert status == 200
    commands = {command["name"]: command for command in json.loads(body)}
    assert commands["model"]["kind"] == "builtin" and commands["model"]["alias_of"] is None
    assert commands["quit"]["kind"] == "builtin" and commands["quit"]["alias_of"] == "exit"
    assert commands["audit"]["kind"] == "shortcut" and commands["audit"]["template"] is True
    assert commands["status"]["safe_in_flight"] is True


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
    status, _, body = _call(server, "POST", "/api/open", {"path": str(server.host.config.layout.problem), "chat": "main"})
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
    status, _, body = _call(server, "POST", "/api/open", {"path": str(server.host.config.layout.problem), "chat": made["id"]})
    assert status == 200 and json.loads(body)["chat"] == made["id"]
    # The chat routes serve the open project only: a slug that is not it names nothing.
    status, _, body = _call(server, "POST", "/api/projects/other/chats", {"title": "x"})
    assert status == 400 and "not the open project" in json.loads(body)["error"]


def test_projects_route_creates_one_and_refuses_the_rest(server, tmp_path: Path) -> None:
    """`POST /api/projects` is held to `/project new`'s guards, not to none.

    `prepare_layout` scatters `lean/`, `tex/`, `cas/` and a record through
    whatever directory it is pointed at, so a name that is already a project
    -- or a directory somebody else made -- has to be refused before the
    opener is ever called.
    """
    status, _, body = _call(server, "POST", "/api/projects", {"name": "frobenius"})
    assert status == 200
    rows = {entry["slug"]: entry for entry in json.loads(body)}
    assert rows["frobenius"]["active"] is True and rows["frobenius"]["path"] == str(tmp_path / "projects" / "frobenius")
    status, _, body = _call(server, "POST", "/api/projects", {"name": "sylow", "location": str(tmp_path)})
    assert status == 400 and "already a project" in json.loads(body)["error"]
    stray = tmp_path / "projects" / "somebody-elses"
    stray.mkdir(parents=True)
    (stray / "notes.txt").write_text("mine", encoding="utf-8")
    status, _, body = _call(server, "POST", "/api/projects", {"name": "somebody-elses"})
    assert status == 400 and "not a Hardy project" in json.loads(body)["error"]
    assert (stray / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert not (stray / "lean").exists() and not (stray / ".gitignore").exists()


def test_open_route_refuses_a_path_or_chat_nothing_made(server, tmp_path: Path) -> None:
    status, _, body = _call(server, "POST", "/api/open", {"path": str(tmp_path / "nowhere"), "chat": "main"})
    assert status == 400 and "not a registered project" in json.loads(body)["error"]
    status, _, body = _call(server, "POST", "/api/open", {"path": str(tmp_path / "sylow"), "chat": "never-made"})
    assert status == 400 and "never-made" in json.loads(body)["error"]
    assert not (tmp_path / "nowhere").exists()


def test_nothing_open_answers_the_menu_and_refuses_the_rest(empty_server, tmp_path: Path) -> None:
    """With no session the page draws the project menu from `state` and
    `projects`; everything about a problem is one 409 with one sentence."""
    status, _, body = _call(empty_server, "GET", "/api/state", token=False)
    state = json.loads(body)
    assert status == 200 and state["open"] is False and state["slug"] is None
    assert state["default_root"] == str(tmp_path / "projects")
    status, _, body = _call(empty_server, "GET", "/api/projects", token=False)
    assert status == 200 and json.loads(body) == []
    for name in ("summary", "files", "chats", "transcript", "record", "checkpoints"):
        status, _, body = _call(empty_server, "GET", f"/api/{name}", token=False)
        assert status == 409 and json.loads(body)["error"] == "No project is open. Open one from the project menu.", name
    # Not any one problem's: still answered.
    for name in ("commands", "models", "environment", "runs"):
        status, *_ = _call(empty_server, "GET", f"/api/{name}", token=False)
        assert status == 200, name
    status, _, body = _call(empty_server, "POST", "/api/input", {"text": "hello"})
    assert status == 409 and "No project is open" in json.loads(body)["error"]
    status, *_ = _call(empty_server, "POST", "/api/upload", raw=b"x", headers={"X-Hardy-Filename": "a.txt", "Content-Type": "application/octet-stream"})
    assert status == 409
    status, *_ = _call(empty_server, "PUT", "/api/file", {"path": "lean/A.lean", "source": ""})
    assert status == 409
    status, *_ = _call(empty_server, "PATCH", "/api/projects/sylow/chats/main", {"title": "x"})
    assert status == 409
    status, *_ = _call(empty_server, "POST", "/api/close")
    assert status == 200


def test_add_open_close_and_forget_routes(empty_server, tmp_path: Path) -> None:
    problem = make_problem(tmp_path / "math", "sylow")
    status, _, body = _call(empty_server, "POST", "/api/projects/add", {"path": str(tmp_path / "math")})
    assert status == 200 and [row["slug"] for row in json.loads(body)] == ["sylow"]
    status, _, body = _call(empty_server, "POST", "/api/open", {"path": str(problem)})
    state = json.loads(body)
    assert status == 200 and state["open"] is True and state["slug"] == "sylow" and state["root"] == str(tmp_path / "math")
    status, _, body = _call(empty_server, "GET", "/api/chats", token=False)
    assert status == 200
    status, _, body = _call(empty_server, "POST", "/api/projects/forget", {"path": str(problem)})
    assert status == 400 and "Close it first" in json.loads(body)["error"]
    status, _, body = _call(empty_server, "POST", "/api/close")
    assert status == 200 and json.loads(body)["open"] is False
    status, _, body = _call(empty_server, "POST", "/api/projects/forget", {"path": str(problem)})
    assert status == 200 and json.loads(body) == []
    assert problem.is_dir()
    status, _, body = _call(empty_server, "POST", "/api/projects/add", {"path": str(tmp_path / "nowhere")})
    assert status == 400 and "does not exist" in json.loads(body)["error"]


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
                 "/api/transcript", "/api/commands", "/api/files", "/api/uploads",
                 "/api/record", "/api/ledger", "/api/results", "/api/chats", "/api/runs",
                 "/api/publications", "/api/checkpoints"):
        # `/api/environment` is deliberately not in this list: it is the one
        # route that reaches `doctor.run_checks`, and this fixture's `server`
        # runs a real, unmocked `WebHost` -- exercising it here would shell
        # out to `lean --version`/`elan`/backend CLIs with real timeouts.
        # `test_environment_route_is_wired_without_probing_the_host` below
        # exercises that route's wiring with `doctor.run_checks` monkeypatched.
        status, ctype, _ = _call(server, "GET", path, token=False)
        assert status == 200 and "application/json" in ctype, path
    status, _, body = _call(server, "GET", "/api/file?path=lean/A.lean", token=False)
    assert json.loads(body)["text"].startswith("theorem")
    status, *_ = _call(server, "GET", "/api/file?path=../x", token=False)
    assert status == 400


def test_ledger_item_route_serves_a_real_item_and_refuses_an_unknown_id(server) -> None:
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin
    from hardy.workflows.ledger.store import LedgerStore

    problem = server.host.config.layout.problem
    store = LedgerStore(problem)
    item = ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.TARGET_PAPER)
    store.append([item], expected_revision=store.read().revision)

    status, ctype, body = _call(server, "GET", "/api/ledger/item?id=thm-1", token=False)
    assert status == 200 and "application/json" in ctype
    payload = json.loads(body)
    assert payload["id"] == "thm-1" and payload["kind"] == "theorem" and payload["version"] == 1
    assert [v["version"] for v in payload["versions"]] == [1]

    # An unknown id is `snapshot.head`'s own `ValueError`, mapped to a clean
    # 400 by `do_GET` -- not a 500 and not a traceback.
    status, _, body = _call(server, "GET", "/api/ledger/item?id=nope", token=False)
    assert status == 400 and "unknown record identity: nope" in json.loads(body)["error"]


def test_ledger_export_route_serves_a_real_item_and_refuses_an_unknown_id(server) -> None:
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin
    from hardy.workflows.ledger.store import LedgerStore

    problem = server.host.config.layout.problem
    store = LedgerStore(problem)
    item = ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.TARGET_PAPER)
    store.append([item], expected_revision=store.read().revision)

    status, ctype, body = _call(server, "GET", "/api/ledger/export?id=thm-1", token=False)
    assert status == 200 and "application/json" in ctype
    payload = json.loads(body)
    assert payload["root"] == "thm-1" and payload["scope"] is None and payload["ready"] is None
    assert payload["export_command"]["available"] is False

    status, _, body = _call(server, "GET", "/api/ledger/export?id=nope", token=False)
    assert status == 400 and "unknown record identity: nope" in json.loads(body)["error"]


def test_runs_route_serves_a_real_run_and_refuses_an_unknown_id(server) -> None:
    """`/api/runs` and `/api/runs/item` reach `panels.runs`/`panels.run_item(host.config, ...)`.

    `host.config`, not `problem`: runs live under `config.runs_root`, outside
    the problem tree `self._problem()` names -- the one thing that makes this
    endpoint different from every other panel wired above.
    """
    from uuid import uuid4

    from hardy.workflows.contracts import RunManifest, RunPhase
    from hardy.workflows.storage import RunStore

    run_id = uuid4()
    store = RunStore.create(server.host.config.runs_root, "order-30", now=NOW, run_id=run_id)
    store.finalize(RunManifest(
        run_id=run_id, created_at=NOW, phase=RunPhase.COMPLETED, model="claude-opus-4-1",
        prompt_set_sha256="a" * 64, claim_sha256="b" * 64,
    ))

    status, ctype, body = _call(server, "GET", "/api/runs", token=False)
    assert status == 200 and "application/json" in ctype
    rows = json.loads(body)["runs"]
    assert len(rows) == 1 and rows[0]["run_id"] == str(run_id) and rows[0]["readable"] is True

    status, ctype, body = _call(server, "GET", f"/api/runs/item?id={run_id}", token=False)
    assert status == 200 and "application/json" in ctype
    payload = json.loads(body)
    assert payload["run_id"] == str(run_id) and payload["claim_sha256"] == "b" * 64
    assert payload["trajectory"] == []
    # The manifest names a hash but this run wrote no `formalization.json`:
    # the statement is reported missing for *this run*, not silently absent
    # (issue #174).
    assert payload["claim"] is None and "formalization.json" in payload["claim_error"]

    # An unknown run_id is `run_item`'s own `ValueError`, mapped to a clean
    # 400 by `do_GET` -- the same refusal path `ledger_item`/`ledger_export`
    # already exercise for an unknown ledger id.
    status, _, body = _call(server, "GET", f"/api/runs/item?id={uuid4()}", token=False)
    assert status == 400 and "no run found" in json.loads(body)["error"]

    # A malformed run_id (not even a UUID) refuses the same clean way.
    status, _, body = _call(server, "GET", "/api/runs/item?id=not-a-uuid", token=False)
    assert status == 400


def test_publications_route_serves_a_real_candidate(server) -> None:
    from hardy.workflows.ledger.contracts import ProjectItem, ProjectItemKind, ProjectOrigin, Scope
    from hardy.workflows.ledger.store import LedgerStore

    problem = server.host.config.layout.problem
    store = LedgerStore(problem)
    item = ProjectItem(id="thm-1", kind=ProjectItemKind.THEOREM, name="Thm", origin=ProjectOrigin.TARGET_PAPER)
    scope = Scope(id="scope-1", must_prove=(item.ref,))
    store.append([item, scope], expected_revision=store.read().revision)

    status, ctype, body = _call(server, "GET", "/api/publications", token=False)
    assert status == 200 and "application/json" in ctype
    payload = json.loads(body)
    assert payload["items"][0]["id"] == "thm-1" and payload["items"][0]["visibility"] == "internal"
    assert payload["candidates"][0]["item"] == "thm-1" and payload["candidates"][0]["ready"] is False


def test_checkpoints_route_serves_a_real_checkpoint(server) -> None:
    from hardy.workflows import checkpoints as checkpoint_module

    paths = server.host.config.layout
    saved = checkpoint_module.save(paths, name="before refactor")

    status, ctype, body = _call(server, "GET", "/api/checkpoints", token=False)
    assert status == 200 and "application/json" in ctype
    rows = json.loads(body)["checkpoints"]
    assert len(rows) == 1 and rows[0]["id"] == saved.id and rows[0]["name"] == "before refactor"


def test_environment_route_is_wired_without_probing_the_host(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/api/environment` reaches `panels.environment(host.config)` and back out as JSON.

    `doctor.run_checks` is monkeypatched so this exercises the HTTP wiring --
    the route string, the argument (`host.config`, not `problem`), and the
    JSON shape -- without shelling out to probe the real host, which is what
    `doctor.run_checks` does unmocked.
    """
    from hardy.app import doctor

    monkeypatch.setattr(doctor, "run_checks",
                        lambda config, **kwargs: [doctor.Check("python", True, "3.12.1", required=True)])
    status, ctype, body = _call(server, "GET", "/api/environment", token=False)
    assert status == 200 and "application/json" in ctype
    assert json.loads(body) == {
        "checks": [{"name": "python", "ok": True, "detail": "3.12.1", "required": True}],
        "failures": 0,
    }


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


def test_put_file_saves_through_the_session(server) -> None:
    """The bytes do not reach disk from the boundary.

    `PUT /api/file` hands them to the session's own save, which is what
    checks them, rebuilds their importers, gates them and audits them. The
    fake records the call; what matters here is that the boundary routed it
    there rather than writing the file itself.
    """
    status, _, body = _call(server, "PUT", "/api/file",
                            {"path": "lean/Main.lean", "source": "import Mathlib\n"})
    assert status == 200
    answer = json.loads(body)
    assert answer["ok"] is True and answer["path"] == "lean/Main.lean"
    assert server.host.session.saved == [("lean/Main.lean", "import Mathlib\n")]


def test_a_refused_save_comes_back_with_the_session_s_own_sentence(server) -> None:
    """A save is refused for a dozen reasons, each with a sentence written
    for a reader. The boundary must not replace one with a status code."""
    refusal = "declaration uses 'sorry'; completed saved work must contain no holes"
    server.host.session.refuse_saves = refusal
    status, _, body = _call(server, "PUT", "/api/file",
                            {"path": "lean/Main.lean", "source": "theorem t : True := by sorry\n"})
    # 200, not 4xx: the request was well formed and the session answered it.
    # The refusal is the answer, not an error about the request.
    assert status == 200
    answer = json.loads(body)
    assert answer["ok"] is False
    assert answer["output"] == refusal


def test_put_file_refuses_a_path_outside_the_problem(server) -> None:
    status, _, body = _call(server, "PUT", "/api/file",
                            {"path": "../../etc/passwd", "source": "x"})
    assert status == 400
    assert server.host.session.saved == []


def test_put_file_refuses_a_tree_it_does_not_serve(server) -> None:
    """`.lean` and `.tex` only -- the session's own rule, reported as the
    session words it."""
    status, _, body = _call(server, "PUT", "/api/file",
                            {"path": "notes.txt", "source": "x"})
    assert status == 200 and json.loads(body)["ok"] is False


def test_put_file_needs_the_token(server) -> None:
    status, *_ = _call(server, "PUT", "/api/file",
                       {"path": "lean/Main.lean", "source": "x"}, token=False)
    assert status == 403


def test_an_unknown_put_is_404(server) -> None:
    status, *_ = _call(server, "PUT", "/api/nothing", {})
    assert status == 404


def test_post_check_runs_without_saving(server) -> None:
    status, _, body = _call(server, "POST", "/api/check",
                            {"path": "lean/Scratch.lean", "source": "#check Nat.succ\n"})
    assert status == 200 and json.loads(body)["ok"] is True
    assert server.host.session.checked == [("lean/Scratch.lean", "#check Nat.succ\n")]
    # The whole contract of a check: nothing was saved.
    assert server.host.session.saved == []


def test_a_save_writes_one_transcript_line_attributed_to_hardy(server) -> None:
    """The record should be able to say the file changed, without the line
    reading as something the person typed into the composer."""
    _call(server, "PUT", "/api/file", {"path": "lean/Main.lean", "source": "import Mathlib\n"})
    status, _, body = _call(server, "GET", "/api/transcript", token=False)
    assert status == 200
    entries = json.loads(body)
    note = [entry for entry in entries if "Edited lean/Main.lean" in json.dumps(entry)]
    assert len(note) == 1


def test_declarations_endpoints_answer_without_a_lean_project(server) -> None:
    """`lean_project` is None in the web test config, which is a real
    configuration -- a session with no Lean installed. The endpoints answer
    rather than raising, and never claim an index they did not build."""
    status, _, body = _call(server, "GET", "/api/declarations?q=Sylow", token=False)
    assert status == 200 and json.loads(body)["results"] == []
    status, _, body = _call(server, "GET", "/api/declaration?name=Sylow.card", token=False)
    assert status == 200 and json.loads(body)["found"] is False


def test_cli_parses_web() -> None:
    from hardy.app.cli import build_parser

    args = build_parser().parse_args(["web", "--port", "8123", "--open", "--project", "sylow", "--chat", "x"])
    assert args.command == "web" and args.port == 8123 and args.open is True and args.chat == "x"


def test_an_editor_save_may_exceed_the_ordinary_body_limit(server) -> None:
    """`/api/file` serves a file up to `TEXT_LIMIT` (1 MiB) and the editor
    presents it as complete, so the save of that same text -- larger once
    JSON-escaped -- must not be refused by a body limit equal to the read
    limit. It was: a near-limit TeX file got a 413 and could be neither
    checked nor saved."""
    source = "\n".join([r"\section{x}"] * 60000)   # ~1 MiB, newline-dense
    assert len(source) > (1 << 20) * 0.6
    status, _, body = _call(server, "PUT", "/api/file",
                            {"path": "tex/big.tex", "source": source})
    assert status == 200, f"a near-limit editor save was refused with {status}"
    assert json.loads(body)["ok"] is True


def test_an_ordinary_mutation_keeps_the_smaller_body_limit(server) -> None:
    """The larger allowance is for the two editor routes only. `/api/input`
    sends lines of text; a megabyte of it is still not a line."""
    status, *_ = _call(server, "POST", "/api/input", raw=b"x" * ((1 << 20) + 1),
                       headers={"Content-Type": "application/json"})
    assert status == 413


def test_an_editor_body_past_the_editor_limit_is_still_refused(server) -> None:
    """The larger limit is a limit, not its absence."""
    status, *_ = _call(server, "PUT", "/api/file", raw=b"x" * ((4 << 20) + 1),
                       headers={"Content-Type": "application/json"})
    assert status == 413
