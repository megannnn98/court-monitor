"""`PYTHONPATH=src uv run python -m telegram_bot`: its own process, never inside the CLI.

The image sets PYTHONPATH=/app/src, so there the command is `python -m telegram_bot`.
"""

from telegram_bot.app import main

if __name__ == "__main__":
    main()
