"""`hardy check`: a root's problems against each other, the files, and the status rules."""

from __future__ import annotations

import argparse

from hardy.app.config import Config
from hardy.workflows.root_check import check_root


def main(args: argparse.Namespace, config: Config) -> int:
    report = check_root(config.root)
    for line in report.lines(mermaid=args.mermaid):
        print(line)
    return 0 if report.ok else 1
