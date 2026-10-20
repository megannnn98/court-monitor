"""A small world of articles, events and entity groups on PostgreSQL, built the way the
extraction and the entity rebuild leave them, for the case-resolution tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from ingestion.storage.models import ParsedArticleRecord
from processing.storage.models import (
    EntityGroupChargeRecord,
    EntityGroupMentionRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    ExtractedEventRecord,
)


@dataclass(frozen=True)
class Article:
    id: int
    run: int
    text: str


@dataclass
class Spec:
    """What an event was seeded with: enough to extract it again."""

    article: Article
    sentence: str
    people: tuple[str, ...]
    kind: str
    day: datetime | None
    charges: tuple[str, ...]
    orgs: tuple[tuple[str, str], ...]
    judges: tuple[str, ...]
    mention_ids: dict[str, int] = field(default_factory=dict)


def at(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


class World:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.factory = session_factory
        self.specs: dict[int, Spec] = {}
        self._numbers = count(1)
        with session_factory() as session:
            self.source = DatabaseSeeder(session).source("news", "https://news.example.test")
            session.commit()

    def article(self, text: str) -> Article:
        number = next(self._numbers)
        with self.factory() as session:
            article_id, run = DatabaseSeeder(session).article(
                self.source, external_id=f"a{number}", title=f"Статья {number}", text=text
            )
            session.commit()
        return Article(article_id, run, text)

    def event(
        self,
        article: Article,
        sentence: str,
        *people: str,
        kind: str = "detention",
        day: datetime | None = None,
        charges: tuple[str, ...] = (),
        orgs: tuple[tuple[str, str], ...] = (),
        judges: tuple[str, ...] = (),
    ) -> int:
        """An event of `article`; `sentence` is its words and must stand in the text, with
        the names of `people` (figurants) and `judges` (named in it, never its subject).
        `orgs` are (role, surface) of courts, bodies and places named in the text."""
        spec = Spec(article, sentence, people, kind, day, charges, orgs, judges)
        with self.factory() as session:
            seed = DatabaseSeeder(session)
            seed._texts[article.run] = article.text
            for name in (*people, *judges):
                spec.mention_ids[name] = seed.mention(article.run, name, person_id=None)
            for _, surface in orgs:
                spec.mention_ids[surface] = seed.mention(
                    article.run, surface, person_id=None, entity_type="organization"
                )
            event_id = self._seed_event(session, seed, spec)
            self._group(session, spec, event_id)
            session.commit()
        self.specs[event_id] = spec
        return event_id

    def report(
        self,
        *people: str,
        sentence: str | None = None,
        kind: str = "detention",
        day: datetime | None = None,
        charges: tuple[str, ...] = (),
        orgs: tuple[tuple[str, str], ...] = (),
        judges: tuple[str, ...] = (),
    ) -> int:
        """One article with one event; `sentence` defaults to words of its own."""
        number = next(self._numbers)
        words = sentence or f"{' и '.join(people) or 'Неизвестный'} {kind} №{number}"
        named = " ".join((*people, *judges, *(surface for _, surface in orgs)))
        article = self.article(f"Заметка {number}. {words}. {named}")
        return self.event(
            article, words, *people, kind=kind, day=day, charges=charges, orgs=orgs, judges=judges
        )

    def _seed_event(self, session: Session, seed: DatabaseSeeder, spec: Spec) -> int:
        links = [(spec.mention_ids[name], "target") for name in (*spec.people, *spec.judges)]
        links += [(spec.mention_ids[surface], role) for role, surface in spec.orgs]
        return seed.event(
            spec.article.run,
            spec.sentence,
            event_type=spec.kind,
            event_date=spec.day,
            links=[],
            entity_links=links,
        )

    def _group(self, session: Session, spec: Spec, event_id: int) -> None:
        for name in (*spec.people, *spec.judges):
            group = session.query(EntityGroupRecord).filter_by(key=name.lower()).one_or_none()
            if group is None:
                group = EntityGroupRecord(
                    key=name.lower(),
                    name=name,
                    variants=[],
                    mention_count=1,
                    article_count=1,
                    event_types={},
                )
                session.add(group)
                session.flush()
                session.add(
                    EntityGroupRoleRecord(
                        group_id=group.id,
                        role="figurant" if name in spec.people else "mentioned",
                        kind=None,
                        method="article",
                        reason="test",
                        quote="test",
                    )
                )
            session.add(
                EntityGroupMentionRecord(group_id=group.id, mention_id=spec.mention_ids[name])
            )
            if name in spec.people:
                for charge in spec.charges:
                    article, _, part = charge.partition("/")
                    session.add(
                        EntityGroupChargeRecord(
                            group_id=group.id,
                            event_id=event_id,
                            publication_id=spec.article.id,
                            article=article,
                            part=part or None,
                            clause=None,
                            event_type=spec.kind,
                            other_targets=0,
                            quote=spec.sentence,
                        )
                    )
        session.flush()

    def reextract(self, event_id: int, *, sentence: str | None = None) -> int:
        """Extraction run again: the event is replaced by a new row (a new id), the groups
        stay. A new `sentence` is an article whose words changed."""
        spec = self.specs.pop(event_id)
        with self.factory() as session:
            if sentence is not None:
                text = spec.article.text.replace(spec.sentence, sentence)
                session.get_one(ParsedArticleRecord, spec.article.id).text = text
                spec.article = Article(spec.article.id, spec.article.run, text)
                spec.sentence = sentence
            session.execute(delete(ExtractedEventRecord).where(ExtractedEventRecord.id == event_id))
            seed = DatabaseSeeder(session)
            seed._texts[spec.article.run] = spec.article.text
            new_id = self._seed_event(session, seed, spec)
            self._rebind(session, spec, new_id)
            session.commit()
        self.specs[new_id] = spec
        return new_id

    def _rebind(self, session: Session, spec: Spec, event_id: int) -> None:
        for name in spec.people:
            group_id = session.query(EntityGroupRecord).filter_by(key=name.lower()).one().id
            for charge in spec.charges:
                article, _, part = charge.partition("/")
                session.add(
                    EntityGroupChargeRecord(
                        group_id=group_id,
                        event_id=event_id,
                        publication_id=spec.article.id,
                        article=article,
                        part=part or None,
                        clause=None,
                        event_type=spec.kind,
                        other_targets=0,
                        quote=spec.sentence,
                    )
                )

    def rebuild_groups(self) -> None:
        """The entity rebuild: every group is made again, with new ids and the same keys."""
        with self.factory() as session:
            old = session.query(EntityGroupRecord).order_by(EntityGroupRecord.id).all()
            roles = {
                group.key: session.get(EntityGroupRoleRecord, group.id).role  # type: ignore[union-attr]
                for group in old
            }
            keep = [(group.key, group.name) for group in old]
            session.execute(delete(EntityGroupRecord))
            session.flush()
            ids = {}
            for key, name in reversed(keep):
                group = EntityGroupRecord(
                    key=key,
                    name=name,
                    variants=[],
                    mention_count=1,
                    article_count=1,
                    event_types={},
                )
                session.add(group)
                session.flush()
                ids[key] = group.id
                session.add(
                    EntityGroupRoleRecord(
                        group_id=group.id,
                        role=roles[key],
                        kind=None,
                        method="article",
                        reason="test",
                        quote="test",
                    )
                )
            for event_id, spec in self.specs.items():
                for name in (*spec.people, *spec.judges):
                    session.add(
                        EntityGroupMentionRecord(
                            group_id=ids[name.lower()], mention_id=spec.mention_ids[name]
                        )
                    )
                for name in spec.people:
                    for charge in spec.charges:
                        article, _, part = charge.partition("/")
                        session.add(
                            EntityGroupChargeRecord(
                                group_id=ids[name.lower()],
                                event_id=event_id,
                                publication_id=spec.article.id,
                                article=article,
                                part=part or None,
                                clause=None,
                                event_type=spec.kind,
                                other_targets=0,
                                quote=spec.sentence,
                            )
                        )
            session.commit()
