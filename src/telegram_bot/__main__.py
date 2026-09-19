"""`uv run python -m telegram_bot`: the bot as its own process, never inside the CLI."""

from telegram_bot.app import main

if __name__ == "__main__":
    main()
