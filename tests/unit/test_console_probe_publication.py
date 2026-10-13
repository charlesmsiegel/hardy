"""Readiness must not become visible while its JSON is still being written."""

import json
import runpy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event


def test_readiness_is_published_only_after_complete_write(tmp_path, monkeypatch):
    probe = Path(__file__).parents[1] / "fixtures" / "console" / "close_probe.py"
    write = runpy.run_path(str(probe))["write"]
    opened, release = Event(), Event()
    payload = {"hwnd": 123, "window_class": "ConsoleWindowClass", "session_id": 2}

    def paused_write(path, data, *, encoding):
        with path.open("w", encoding=encoding) as stream:
            opened.set()
            assert release.wait(timeout=5), "test did not release the writer"
            return stream.write(data)

    monkeypatch.setattr(Path, "write_text", paused_write)
    ready = tmp_path / "ready.json"
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(write, tmp_path, "ready.json", payload)
        try:
            assert opened.wait(timeout=5), "writer did not reach publication barrier"
            assert not ready.exists(), "readiness exposed before the JSON was written and closed"
        finally:
            release.set()
        pending.result(timeout=5)
    assert json.loads(ready.read_text(encoding="utf-8")) == payload
