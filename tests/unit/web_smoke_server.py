"""Serve the real browser bundle over a scripted fake session.

A script, not a test: pytest collects `test_*.py` and this is not one. It
exists so `web/smoke.mjs` -- and a person with a browser -- can drive the
actual page without a model, a provider, or a Lean toolchain. The session is
`FakeSession` from `web_fakes`, whose scripted turn streams "hel", "lo" and
then a `reply` of "hello", so a smoke can assert the whole streaming path
end to end and finish in milliseconds.

    uv run python tests/unit/web_smoke_server.py --port 8765

Everything it writes goes in a temporary directory that is removed on the way
out; nothing here touches a real project.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from web_fakes import FakeSession, make_config, make_problem  # noqa: E402

from hardy.app.web.host import WebHost  # noqa: E402
from hardy.app.web.server import serve  # noqa: E402

SLUG = "sylow"


class FakeOpener:
    """The opener `test_web_server.py` uses: a new fake session per open."""

    def __init__(self, root: Path) -> None:
        self.root, self.session = root, None

    def __call__(self, slug, confirm, current, *, chat="main"):
        self.session = FakeSession(self.root / slug, chat)
        return dataclasses.replace(current, project=slug, chat=chat), self.session

    def cancel(self) -> bool:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--static",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "src" / "hardy" / "app" / "web" / "static",
        help="the built bundle to serve; the packaged one by default",
    )
    parser.add_argument("--open", action="store_true", help="open a browser on the page")
    options = parser.parse_args(argv)

    if not (options.static / "index.html").is_file():
        parser.error(f"no bundle at {options.static}; run `npm run build` in web/ first")

    with tempfile.TemporaryDirectory(prefix="hardy-web-smoke-") as directory:
        root = Path(directory)
        make_problem(root, SLUG)
        config = make_config(root, SLUG)
        host = WebHost(config, FakeOpener(root), lambda confirm, cfg: FakeSession(cfg.layout.problem))
        host.start()
        try:
            serve(host, port=options.port, static=options.static, open_browser=options.open)
        finally:
            host.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
