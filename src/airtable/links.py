"""The four lists, and where each of them may be read from.

Three sources exist for one list, and they are not equal. The API is best: it is
automatic and carries each record's id, so a renamed person is an update rather than a
second row. But it needs a token with rights to the base, and a base owned by someone
else does not grant those to a visitor.

A public share link comes next, and it is the one that makes the button work with nobody
at the keyboard: the link opens for any visitor, and `airtable.share` reads the rows
from it with no token at all. Exported files come last — they work, but only after the
operator saves one by hand, which is the thing this whole change exists to remove.

A list with no source configured is *skipped*, never read as an empty one. Reading
"nothing to read" as "the list is now empty" is how a snapshot of 23 000 people
disappears.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from airtable.client import AirtableClient, AirtableError, AirtableRecord
from airtable.models import TABLES

logger = logging.getLogger("airtable")

# The environment variable naming the public link of each list. Sources live in a
# *different* base from the rest — that is how the base is set up, and it was found by
# reading the lists rather than assumed.
# What makes a row the row it is, per list. Without this the identity would be the
# row's number in the export, and one person inserted in the middle of a list of 13 765
# would rename every id after it — the sync would then rewrite the whole tail and report
# fourteen thousand updates for a list nobody had touched.
IDENTITY_FIELDS = {
    "sources": ("Ссылка", "Ссылка на источник", "base_url", "url", "URL"),
    "known_persons": ("Преследуемый", "ФИО", "Дата рождения", "full_name", "birth_date"),
    "articles": ("Полная статья", "Статья", "article", "full_article"),
    "officials": ("ФИО", "full_name"),
}

SHARE_URL_ENV = {
    "sources": "AIRTABLE_SHARE_URL_SOURCES",
    "known_persons": "AIRTABLE_SHARE_URL_KNOWN",
    "officials": "AIRTABLE_SHARE_URL_OFFICIALS",
    "articles": "AIRTABLE_SHARE_URL_ARTICLES",
}


# The list of people we already have on file used to be called the Rosfinmonitoring list
# and read from `AIRTABLE_SHARE_URL_RFM`. It is not that list: the Rosfinmonitoring list is
# published by fedsfm.ru and is the only thing that may say a person is on it. The name is
# still accepted so that an environment already filled in keeps working.
LEGACY_SHARE_URL = "AIRTABLE_SHARE_URL_RFM"


def share_links(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The public link of each list that has one, by list name."""
    env = os.environ if env is None else env
    found = {name: (env.get(var) or "").strip() for name, var in SHARE_URL_ENV.items()}
    legacy = (env.get(LEGACY_SHARE_URL) or "").strip()
    if legacy and not found.get("known_persons"):
        found["known_persons"] = legacy
    return {name: link for name, link in found.items() if link}


class ShareTableClient:
    """Reads a list from its public link, the same way the sync reads a table.

    The link is the table name this client is asked for, so the sync needs no idea that
    the sources live in another base.
    """

    def __init__(self, links: Mapping[str, str], *, session: object | None = None) -> None:
        self._links = dict(links)
        self._session = session

    #: The lists this source is a copy of, and may therefore replace whole. A list the
    #: operator keeps by hand is not a copy of anything, and a row they wrote is not a row
    #: an export can revoke.
    REPLACES_WHOLE = frozenset({"known_persons", "articles"})

    def replaces_whole(self, table: str) -> bool:
        """Whether a list read from this source is a copy that may be replaced whole."""
        return table in self.REPLACES_WHOLE

    @property
    def links(self) -> dict[str, str]:
        return dict(self._links)

    def list_records(self, table: str) -> list[AirtableRecord]:
        link = self._links.get(table, "")
        if not link:
            raise FileNotFoundError(f"для списка {table} ссылка не настроена")
        from airtable.share import read_records

        records = read_records(
            link,
            session=self._session,
            table=table,
            identity=IDENTITY_FIELDS.get(table, ()),
        )
        if not records:
            # An empty read here would be a list emptied. The reader raises rather than
            # returning nothing, so reaching this means the view genuinely has no rows —
            # which for these lists means a mistake worth reporting, not applying.
            raise AirtableError(
                f"ссылка {link} вернула ни одной строки: список не тронут, "
                "возможно представление пустое или ссылка отозвана"
            )
        return records


class FallbackTableClient:
    """The API where it answers, the public link where it does not.

    A token can be valid and still have no rights to a base owned by someone else: the
    read then fails with `INVALID_PERMISSIONS`, every time, while the same list stays
    readable through its public link. Choosing one mode for the whole sync would then
    leave every list broken because of one bad token, so the fallback is per list.

    The token is tried first because it is the better source where it works — it carries
    each record's id, so a renamed person is an update rather than a second row.
    """

    def __init__(
        self,
        primary: AirtableClient,
        links: Mapping[str, str],
        tables: Mapping[str, str] | None = None,
    ) -> None:
        self._primary = primary
        self._links = ShareTableClient(links)
        self.used_links: set[str] = set()
        # The sync asks for an Airtable table name; the links are keyed by list name.
        # In API mode those differ — «Sources» in the base is the «sources» list — so
        # the way back is needed to know which link could stand in.
        #
        # Two lists may share one table: «Росфинмониторинг» and «Найденные люди» are
        # the same 13 765 people in one table. The mapping is then ambiguous, and the
        # first list in reading order wins rather than the last, so which one is chosen
        # is decided here and not by the order a dict happens to keep. Both lists read
        # the same link, so the rows are the same either way — but the choice is made
        # on purpose, not by accident.
        self._by_api_name: dict[str, str] = {}
        for name in TABLES:
            api = (tables or {}).get(name)
            if api:
                self._by_api_name.setdefault(api, name)

    def list_records(self, table: str) -> list[AirtableRecord]:
        name = self._by_api_name.get(table, table)
        if not self._links.links.get(name):
            return self._primary.list_records(table)
        try:
            return self._primary.list_records(table)
        except AirtableError as exc:
            logger.warning(
                "event=airtable_api_unreadable list=%s table=%s falling_back=share error=%s",
                name,
                table,
                exc,
            )
        try:
            records = self._links.list_records(name)
        except (AirtableError, FileNotFoundError) as exc:
            raise AirtableError(
                f"ни API, ни публичная ссылка не дали список «{name}»: {exc}"
            ) from exc
        self.used_links.add(name)
        return records
