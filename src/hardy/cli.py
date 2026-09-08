"""Compatibility entry point; implementation lives in hardy.app.cli."""
from hardy.app.cli import build_parser as build_parser
from hardy.app.cli import choose_project as choose_project
from hardy.app.cli import main
from hardy.app.cli import run_accept as run_accept
from hardy.app.cli import run_latency as run_latency
from hardy.app.cli import run_prove as run_prove

if __name__ == "__main__":
    raise SystemExit(main())
