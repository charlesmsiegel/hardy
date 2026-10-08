"""The command adapters `hardy.app.cli` dispatches to, one owner per command.

Each turns parsed arguments into calls on construction (`app/wiring.py`,
`app/projects.py`) and the workflows, and prints what the command reports.
None imports the parser module: `app/cli.py` builds the parser, resolves the
configuration and dispatches here, never the other way round.
"""
