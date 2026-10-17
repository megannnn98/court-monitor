"""Monitored public Telegram channels, loaded from channels.csv next to this module."""

import csv
from dataclasses import dataclass
from pathlib import Path

CHANNELS_PATH = Path(__file__).with_name("channels.csv")


@dataclass(frozen=True)
class TelegramChannel:
    username: str
    title: str
    topics: tuple[str, ...]

    @property
    def source_name(self) -> str:
        """Registry name: `tg-<username>` (Telegram usernames are case-insensitive)."""
        return f"tg-{self.username.lower()}"

    @property
    def base_url(self) -> str:
        return f"https://t.me/{self.username}"


def load_telegram_channels(path: Path = CHANNELS_PATH) -> list[TelegramChannel]:
    with path.open(encoding="utf-8", newline="") as file:
        return [
            TelegramChannel(
                username=row["username"],
                title=row["title"],
                topics=tuple(topic for topic in row["topics"].split(";") if topic),
            )
            for row in csv.DictReader(file)
        ]
