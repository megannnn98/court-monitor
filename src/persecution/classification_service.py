from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonEventLinkRecord,
)
from persecution.classifier import RuleBasedPersecutionClassifier
from persecution.models import PersecutionClassification

# How far (in characters, on each side) around a person's own mention/event
# to look for political context. Text outside this window belongs to
# whatever else the article covers, not to this person — without scoping to
# a window, a political keyword next to a different person mentioned in the
# same article would leak onto everyone in it.
EVIDENCE_WINDOW_CHARS = 400

# How far (in characters) a legal_reference mention may be from a person's
# event to count as the charge behind that specific event.
LEGAL_REFERENCE_LINK_WINDOW_CHARS = 200


_SENTENCE_END = re.compile(r"[.!?…]+[»\")]*\s+")

# Boundary between clauses about different people inside one sentence: «;», «:»,
# a dash, or a comma followed by a contrasting/joining conjunction. A bare comma
# is not one («Иванова, 35 лет, задержали…» is a single clause).
_CLAUSE_BOUNDARY = re.compile(
    r"[;:]|\s[—–-]\s|,\s*(?:а|но|и|однако|тогда как|в то время как|при этом)\s",
    re.IGNORECASE,
)


def _sentence_bounds(text: str, offset: int) -> tuple[int, int]:
    """[start, end) of the sentence containing `offset` (end includes trailing space)."""
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if match.end() <= offset:
            start = match.end()
        else:
            return start, match.end()
    return start, len(text)


def _narrow_shared_spans(
    spans: Sequence[tuple[int, int]],
    own_mention_spans: Sequence[tuple[int, int]],
    other_person_spans: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    """An event span that also contains another person's mention (one detention event
    over «X — for theft, and Y at an anti-war picket») is replaced by this person's own
    mentions inside it, so the clause cut in `_merge_windows` has an anchor. Without
    an own mention inside, the span is kept as it is."""
    narrowed: list[tuple[int, int]] = []
    for start, end in spans:
        if any(
            start <= other_start and other_end <= end
            for other_start, other_end in other_person_spans
        ):
            inside = [
                (mention_start, mention_end)
                for mention_start, mention_end in own_mention_spans
                if start <= mention_start and mention_end <= end
            ]
            if inside:
                narrowed.extend(inside)
                continue
        narrowed.append((start, end))
    return narrowed


class PersecutionClassificationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        classifier: RuleBasedPersecutionClassifier | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.classifier = classifier or RuleBasedPersecutionClassifier()

    def classify_person(self, person_id: int) -> PersecutionClassification:
        """Classify a person from evidence scoped to them specifically.

        Only this person's own mentions/events and the text immediately
        around each are used — not the whole article, which may also cover
        other people with unrelated (e.g. political vs. non-political)
        context.
        """
        with self.session_factory() as session:
            person_mentions = self._get_person_mentions(session, person_id)
            person_events = self._get_person_events(session, person_id)

            events = self._events_with_charge_evidence(session, person_events)
            articles = self._windowed_articles_for_person(
                session, person_mentions, person_events, person_id=person_id
            )

            classification = self.classifier.classify(
                person_id=person_id,
                events=events,
                articles=articles,
            )

            self._save_classification(session, classification)
            session.commit()

            return classification

    def classify_all_persons(self) -> list[PersecutionClassification]:
        """Classify all persons in the database."""
        with self.session_factory() as session:
            person_ids = session.scalars(select(PersonEventLinkRecord.person_id).distinct()).all()

            classifications = []
            for person_id in person_ids:
                classification = self.classify_person(person_id)
                classifications.append(classification)

            return classifications

    def get_person_classification(
        self,
        person_id: int,
        classifier_name: str | None = None,
    ) -> PersecutionClassificationRecord | None:
        """Get the latest classification for a person."""
        with self.session_factory() as session:
            query = select(PersecutionClassificationRecord).where(
                PersecutionClassificationRecord.person_id == person_id
            )

            if classifier_name:
                query = query.where(
                    PersecutionClassificationRecord.classifier_name == classifier_name
                )

            query = query.order_by(PersecutionClassificationRecord.classified_at.desc()).limit(1)

            return session.scalar(query)

    def _get_person_mentions(
        self,
        session: Session,
        person_id: int,
    ) -> Sequence[EntityMentionRecord]:
        """Get this person's own resolved mentions (not other people's)."""
        return session.scalars(
            select(EntityMentionRecord).where(EntityMentionRecord.person_id == person_id)
        ).all()

    def _get_person_events(
        self,
        session: Session,
        person_id: int,
    ) -> Sequence[ExtractedEventRecord]:
        """Get all events linked to a person."""
        event_ids = session.scalars(
            select(PersonEventLinkRecord.event_id).where(
                PersonEventLinkRecord.person_id == person_id
            )
        ).all()

        if not event_ids:
            return []

        return session.scalars(
            select(ExtractedEventRecord).where(ExtractedEventRecord.id.in_(event_ids))
        ).all()

    def _events_with_charge_evidence(
        self,
        session: Session,
        events: Sequence[ExtractedEventRecord],
    ) -> list[dict[str, Any]]:
        """Build classifier-facing event dicts, filling in `attributes["charge"]`
        from a nearby legal_reference mention in the same extraction run when
        the extractor didn't already populate one.
        """
        run_ids = {event.extraction_run_id for event in events}
        legal_mentions_by_run: dict[int, list[EntityMentionRecord]] = {}
        if run_ids:
            legal_mentions = session.scalars(
                select(EntityMentionRecord).where(
                    EntityMentionRecord.extraction_run_id.in_(run_ids),
                    EntityMentionRecord.entity_type == "legal_reference",
                )
            ).all()
            for mention in legal_mentions:
                legal_mentions_by_run.setdefault(mention.extraction_run_id, []).append(mention)

        result: list[dict[str, Any]] = []
        for event in events:
            attributes = dict(event.attributes)
            if not attributes.get("charge"):
                nearest = self._nearest_legal_reference(
                    legal_mentions_by_run.get(event.extraction_run_id, []),
                    event.start_offset,
                    event.end_offset,
                )
                if nearest is not None:
                    attributes["charge"] = nearest.normalized_text

            result.append(
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "event_date": event.event_date,
                    "confidence": event.confidence,
                    "attributes": attributes,
                }
            )
        return result

    @staticmethod
    def _nearest_legal_reference(
        candidates: list[EntityMentionRecord],
        start_offset: int,
        end_offset: int,
    ) -> EntityMentionRecord | None:
        best: EntityMentionRecord | None = None
        best_distance = LEGAL_REFERENCE_LINK_WINDOW_CHARS + 1
        for mention in candidates:
            if mention.end_offset <= start_offset:
                distance = start_offset - mention.end_offset
            elif mention.start_offset >= end_offset:
                distance = mention.start_offset - end_offset
            else:
                distance = 0  # overlapping spans
            if distance <= LEGAL_REFERENCE_LINK_WINDOW_CHARS and distance < best_distance:
                best = mention
                best_distance = distance
        return best

    def _windowed_articles_for_person(
        self,
        session: Session,
        mentions: Sequence[EntityMentionRecord],
        events: Sequence[ExtractedEventRecord],
        *,
        person_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Build per-article text excerpts scoped to this person: only the
        text around their own mentions/events, not the whole article.
        """
        spans_by_run: dict[int, list[tuple[int, int]]] = {}
        for mention in mentions:
            spans_by_run.setdefault(mention.extraction_run_id, []).append(
                (mention.start_offset, mention.end_offset)
            )
        for event in events:
            spans_by_run.setdefault(event.extraction_run_id, []).append(
                (event.start_offset, event.end_offset)
            )

        if not spans_by_run:
            return []

        runs = session.scalars(
            select(ArticleExtractionRunRecord).where(
                ArticleExtractionRunRecord.id.in_(spans_by_run)
            )
        ).all()
        article_id_by_run = {run.id: run.article_id for run in runs}

        spans_by_article: dict[int, list[tuple[int, int]]] = {}
        for run_id, spans in spans_by_run.items():
            article_id = article_id_by_run.get(run_id)
            if article_id is None:
                continue
            spans_by_article.setdefault(article_id, []).extend(spans)

        if not spans_by_article:
            return []

        articles = session.scalars(
            select(ParsedArticleRecord).where(ParsedArticleRecord.id.in_(spans_by_article))
        ).all()

        # Mentions of other people in the same runs: their sentences belong to them.
        others_by_article: dict[int, list[tuple[int, int]]] = {}
        other_mentions = session.execute(
            select(
                EntityMentionRecord.extraction_run_id,
                EntityMentionRecord.start_offset,
                EntityMentionRecord.end_offset,
            ).where(
                EntityMentionRecord.extraction_run_id.in_(spans_by_run),
                EntityMentionRecord.entity_type == "person",
                or_(
                    EntityMentionRecord.person_id.is_(None),
                    EntityMentionRecord.person_id != person_id,
                )
                if person_id is not None
                else EntityMentionRecord.person_id.is_(None),
            )
        ).all()
        for run_id, start, end in other_mentions:
            article_id = article_id_by_run.get(run_id)
            if article_id is not None:
                others_by_article.setdefault(article_id, []).append((start, end))

        own_mentions_by_article: dict[int, list[tuple[int, int]]] = {}
        for mention in mentions:
            article_id = article_id_by_run.get(mention.extraction_run_id)
            if article_id is not None:
                own_mentions_by_article.setdefault(article_id, []).append(
                    (mention.start_offset, mention.end_offset)
                )

        result: list[dict[str, Any]] = []
        for article in articles:
            others = others_by_article.get(article.id, [])
            windows = self._merge_windows(
                _narrow_shared_spans(
                    spans_by_article[article.id],
                    own_mentions_by_article.get(article.id, []),
                    others,
                ),
                len(article.text),
                EVIDENCE_WINDOW_CHARS,
                text=article.text,
                other_person_spans=others,
            )
            excerpt = "\n[...]\n".join(article.text[start:end] for start, end in windows)
            result.append(
                {
                    "id": article.id,
                    "title": article.title,
                    "text": excerpt,
                    "published_at": article.published_at,
                }
            )
        return result

    @staticmethod
    def _merge_windows(
        spans: list[tuple[int, int]],
        text_length: int,
        window: int,
        *,
        text: str | None = None,
        other_person_spans: Sequence[tuple[int, int]] = (),
    ) -> list[tuple[int, int]]:
        """Expand each span by `window` chars on both sides (clipped to the
        text bounds) and merge overlapping/adjacent results.

        A window never reaches into a sentence that mentions another person
        (unless it is the span's own sentence): in a short article, «X was
        detained for theft. Y was detained at an anti-war picket.» must not give
        X the political context of Y. Inside the span's own sentence it stops at
        the clause boundary nearest to the span («X — for theft, and Y at an
        anti-war picket»); without a boundary the clause is shared («X and Y were
        detained at a picket») and its context belongs to both.
        """
        expanded = []
        for start, end in spans:
            window_start = max(0, start - window)
            window_end = min(text_length, end + window)
            if text is not None and other_person_spans:
                own_sentence = _sentence_bounds(text, start)
                for other_start, other_end in other_person_spans:
                    other_sentence = _sentence_bounds(text, other_start)
                    if other_sentence == own_sentence:
                        if other_start >= end:
                            boundary = _CLAUSE_BOUNDARY.search(text, end, other_start)
                            if boundary is not None:
                                window_end = min(window_end, boundary.start())
                        elif other_end <= start:
                            boundaries = list(_CLAUSE_BOUNDARY.finditer(text, other_end, start))
                            if boundaries:
                                window_start = max(window_start, boundaries[-1].end())
                        continue
                    if other_start < start:
                        window_start = max(window_start, other_sentence[1])
                    else:
                        window_end = min(window_end, other_sentence[0])
            if window_start < window_end:
                expanded.append((window_start, window_end))
        expanded.sort()
        merged: list[list[int]] = []
        for start, end in expanded:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return [(start, end) for start, end in merged]

    def _save_classification(
        self,
        session: Session,
        classification: PersecutionClassification,
    ) -> None:
        """Save or update a classification in the database."""
        existing = session.scalar(
            select(PersecutionClassificationRecord).where(
                PersecutionClassificationRecord.person_id == classification.person_id,
                PersecutionClassificationRecord.classifier_name == classification.classifier_name,
                PersecutionClassificationRecord.classifier_version
                == classification.classifier_version,
            )
        )

        if existing:
            existing.status = classification.status
            existing.confidence = classification.confidence
            existing.reasons = classification.reasons
            existing.evidence_types = [et for et in classification.evidence_types]
            existing.classified_at = classification.classified_at or datetime.now(UTC)
        else:
            record = PersecutionClassificationRecord(
                person_id=classification.person_id,
                status=classification.status,
                confidence=classification.confidence,
                reasons=classification.reasons,
                evidence_types=[et for et in classification.evidence_types],
                classifier_name=classification.classifier_name,
                classifier_version=classification.classifier_version,
                classified_at=classification.classified_at or datetime.now(UTC),
            )
            session.add(record)
