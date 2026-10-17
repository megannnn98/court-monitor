"""The command line: `python src/main.py <command>`.

`cli.app` builds the parser from each area's `register` and dispatches to the handler
the chosen command set; `cli.context.CliContext` is the one composition root, which
opens the database only for commands that ask for it.
"""
