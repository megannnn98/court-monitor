"""Who may talk to the bot: a numeric allowlist, closed by default."""

from __future__ import annotations

import logging

from telegram_bot.config import TelegramBotSettings

logger = logging.getLogger(__name__)


class Authorization:
    """Every command is closed; a user is allowed only by numeric id (never by username)."""

    def __init__(self, settings: TelegramBotSettings) -> None:
        self._allowed = settings.allowed_user_ids

    def is_allowed(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self._allowed

    def check(self, command: str, user_id: int | None) -> bool:
        allowed = self.is_allowed(user_id)
        # The decision is logged, the message text never is.
        logger.info(
            "event=telegram_authorization command=%s telegram_user_id=%s result=%s",
            command,
            "unknown" if user_id is None else user_id,
            "allowed" if allowed else "denied",
        )
        return allowed
