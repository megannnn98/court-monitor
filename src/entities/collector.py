"""Rebuild the person entities from the articles with a criminal case.

The whole table at once, in one transaction: grouping reads every mention anyway, and a
reader never sees a half-built list. What a rebuild keeps stable is the entity key, not
its id.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, distinct, func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupChargeRecord,
    EntityGroupMentionRecord,
    EntityGroupRecord,
    EntityNameNormalizationRecord,
)
from entities.answers import input_hash
from entities.disputes import merge_decided, same_pairs
from entities.grouping import (
    Entity,
    GivenName,
    PersonMention,
    apply_names,
    attach_aliases,
    attach_bare,
    group_mentions,
    kinship,
    merge_swapped,
    nominative_form,
)
from entities.llm import BudgetExceededError, Spend, ask_in_batches
from entities.normalizer import (
    BATCH_SIZE,
    PROMPT_VERSION,
    NameItem,
    NameNormalizer,
    NameNormalizerError,
    NormalizedName,
)
from entities.overrides import apply_overrides, name_overrides
from monitoring.junk_purge import CRIMINAL_EVENT_TYPES

logger = logging.getLogger("entities")

# Link rows per INSERT statement.
INSERT_CHUNK = 5_000
# Name forms of an entity sent to the model, most frequent first.
MAX_FORMS = 8
# Model calls in flight at once; one batch of 50 takes about half a minute.
NORMALIZE_CONCURRENCY = 8

# Person mentions of the latest successful extraction of every article that has a
# criminal-case event: the articles a junk purge keeps.
_MENTIONS = text(
    """
    WITH latest AS (
        SELECT DISTINCT ON (article_id) id, article_id FROM article_extraction_runs
        WHERE status = 'succeeded' ORDER BY article_id, id DESC
    ),
    criminal AS (
        SELECT DISTINCT l.id AS run_id, l.article_id FROM latest l
        JOIN extracted_events e ON e.extraction_run_id = l.id
        WHERE e.event_type = ANY(:criminal)
    )
    SELECT m.id, c.article_id, a.published_at,
           m.normalized_data->>'first_name',
           m.normalized_data->>'last_name',
           m.normalized_data->>'patronymic',
           m.surface_text,
           substr(a.text, greatest(m.start_offset - :context, 0) + 1,
                  m.end_offset - greatest(m.start_offset - :context, 0) + :context),
           -- A registry card (memopzk) names its person's region on a line of its own.
           substring(a.text from '(?:^|\n)Регион: ([^\n]+?)\\.?(?:\n|$)')
    FROM entity_mentions m
    JOIN criminal c ON c.run_id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = c.article_id
    WHERE m.entity_type = 'person'
    """
)
# A person's mention in brackets right after another's: «Дмитрия Пуркина (Дед Архимед)».
# Offsets are Python's, from 0; `substr` counts from 1.
_ALIASES = text(
    r"""
    SELECT a.id, n.id
    FROM entity_mentions a
    JOIN article_extraction_runs r ON r.id = a.extraction_run_id
    JOIN parsed_articles p ON p.id = r.article_id
    JOIN entity_mentions n
      ON n.extraction_run_id = a.extraction_run_id AND n.entity_type = 'person'
     AND n.end_offset < a.start_offset
    WHERE a.entity_type = 'person' AND a.start_offset > 0
      AND substr(p.text, a.start_offset, 1) = '('
      AND substr(p.text, a.end_offset + 1, 1) = ')'
      AND substr(p.text, n.end_offset + 1, a.start_offset - n.end_offset) ~ '^[ \t]*\($'
    """
)

_EVENTS = text(
    """
    SELECT em.mention_id, e.event_type FROM event_entity_mentions em
    JOIN extracted_events e ON e.id = em.event_id
    WHERE em.mention_id = ANY(:mentions)
    """
)


# Characters of article text on each side of a mention, for the model's quote.
QUOTE_CONTEXT = 120
# Characters of an event's text kept as the evidence of a charge.
CHARGE_QUOTE_LIMIT = 400

# The Criminal Code articles the events tie to the entities just written: the entity's
# mention is the event's target and the article its legal basis. КоАП is left out (a
# fine is not a criminal case). `other_targets` counts the event's targets outside this
# entity: the extractor makes every person of the sentence a target.
_CHARGES = text(
    """
    INSERT INTO entity_group_charges
        (group_id, event_id, publication_id, article, part, clause, event_type,
         other_targets, quote)
    SELECT DISTINCT ON (g.group_id, e.id, l.normalized_data->>'article',
                        l.normalized_data->>'part', l.normalized_data->>'clause')
           g.group_id, e.id, r.article_id, l.normalized_data->>'article',
           l.normalized_data->>'part', l.normalized_data->>'clause', e.event_type,
           (SELECT count(*) FROM event_entity_mentions o
            WHERE o.event_id = e.id AND o.role = 'target'
              AND NOT EXISTS (SELECT 1 FROM entity_group_mentions s
                              WHERE s.mention_id = o.mention_id AND s.group_id = g.group_id)),
           substr(a.text, e.start_offset + 1, least(e.end_offset - e.start_offset, :limit))
    FROM entity_group_mentions g
    JOIN event_entity_mentions t ON t.mention_id = g.mention_id AND t.role = 'target'
    JOIN extracted_events e ON e.id = t.event_id
    JOIN event_entity_mentions b ON b.event_id = e.id AND b.role = 'legal_basis'
    JOIN entity_mentions l ON l.id = b.mention_id
    JOIN article_extraction_runs r ON r.id = e.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    WHERE l.normalized_data->>'code' IN ('УК РФ', 'УК')
      AND coalesce(l.normalized_data->>'article', '') <> ''
    ORDER BY g.group_id, e.id, l.normalized_data->>'article', l.normalized_data->>'part',
             l.normalized_data->>'clause'
    """
)


@dataclass(frozen=True)
class CollectResult:
    mentions: int
    entities: int
    grouped: int
    # Entities a model named in this rebuild (new answers) and from earlier answers.
    normalized_now: int = 0
    normalized_cached: int = 0
    normalize_failures: int = 0
    # Left unasked (the run's budget was spent), and what the model's calls cost.
    normalize_unasked: int = 0
    model_cost_usd: float = 0.0
    # Criminal Code articles tied to the entities, and the entities that have any.
    charges: int = 0
    charged_entities: int = 0


class EntityCollector:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        on_stage: Callable[[str], None] = lambda _stage: None,
        normalizer: NameNormalizer | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._on_stage = on_stage
        self._normalizer = normalizer

    def run(self) -> CollectResult:
        with self._session_factory() as session:
            self._on_stage("reading")
            rows = session.execute(
                _MENTIONS, {"criminal": list(CRIMINAL_EVENT_TYPES), "context": QUOTE_CONTEXT}
            ).all()
            published: dict[int, datetime | None] = {}
            quotes: dict[int, str] = {}
            mentions: list[PersonMention] = []
            for (
                mention_id,
                article_id,
                published_at,
                first,
                last,
                patronymic,
                surface,
                quote,
                region,
            ) in rows:
                published[mention_id] = published_at
                quotes[mention_id] = " ".join((quote or "").split())
                mentions.append(
                    PersonMention(mention_id, article_id, first, last, patronymic, region, surface)
                )
            events: dict[int, list[str]] = defaultdict(list)
            for mention_id, event_type in session.execute(
                _EVENTS, {"mentions": list(published)}
            ).all():
                events[mention_id].append(event_type)
        articles = {mention.mention_id: mention.article_id for mention in mentions}

        self._on_stage("grouping")
        # «Жене Владимира» names a relative, not a person.
        entities = group_mentions([mention for mention in mentions if not kinship(mention)])
        names, asked, cached, failures, unasked, cost = self._names(entities, quotes)
        entities = merge_swapped(apply_names(entities, names))
        # A person's «one person» decisions, kept by key, merge again at every rebuild.
        with self._session_factory() as session:
            entities = merge_decided(entities, same_pairs(session))
            # And a person's corrections of names.
            entities = apply_overrides(entities, name_overrides(session))
        # A pseudonym in brackets after a name is that person («Пуркина (Дед Архимед)»).
        with self._session_factory() as session:
            aliases = [(alias, named) for alias, named in session.execute(_ALIASES).all()]
        entities = attach_aliases(entities, aliases)
        # A surname alone joins the full name of that surname its article names.
        entities = attach_bare(entities, articles)

        with self._session_factory.begin() as session:
            self._on_stage("writing")
            session.execute(delete(EntityGroupRecord))
            links: list[dict[str, int]] = []
            for entity in entities:
                dates = [d for m in entity.mention_ids if (d := published[m]) is not None]
                group_id = session.execute(
                    insert(EntityGroupRecord)
                    .values(
                        key=entity.key,
                        name=entity.name,
                        variants=[list(item) for item in entity.variants.most_common()],
                        mention_count=len(entity.mention_ids),
                        article_count=len({articles[m] for m in entity.mention_ids}),
                        event_types=dict(
                            Counter(kind for m in entity.mention_ids for kind in events[m])
                        ),
                        last_published_at=max(dates) if dates else None,
                        gender=entity.gender,
                        name_source=entity.name_source,
                        regions=[list(item) for item in entity.regions.most_common()],
                    )
                    .returning(EntityGroupRecord.id)
                ).scalar_one()
                links += [{"group_id": group_id, "mention_id": m} for m in entity.mention_ids]
            for start in range(0, len(links), INSERT_CHUNK):
                session.execute(
                    insert(EntityGroupMentionRecord), links[start : start + INSERT_CHUNK]
                )
            self._on_stage("charges")
            session.execute(_CHARGES, {"limit": CHARGE_QUOTE_LIMIT})
            charges, charged = session.execute(
                select(
                    func.count(), func.count(distinct(EntityGroupChargeRecord.group_id))
                ).select_from(EntityGroupChargeRecord)
            ).one()
        result = CollectResult(
            mentions=len(mentions),
            entities=len(entities),
            grouped=len(links),
            normalized_now=asked,
            normalized_cached=cached,
            normalize_failures=failures,
            normalize_unasked=unasked,
            model_cost_usd=round(cost, 6),
            charges=charges,
            charged_entities=charged,
        )
        logger.info(
            "event=entities_collected mentions=%d entities=%d grouped=%d normalized_now=%d "
            "normalized_cached=%d normalize_failures=%d charges=%d charged_entities=%d",
            result.mentions,
            result.entities,
            result.grouped,
            result.normalized_now,
            result.normalized_cached,
            result.normalize_failures,
            result.charges,
            result.charged_entities,
        )
        return result

    def _names(
        self, entities: Sequence[Entity], quotes: Mapping[int, str]
    ) -> tuple[dict[str, GivenName], int, int, int, int, float]:
        """Earlier answers from the cache; the rest asked in batches, each batch kept.

        An answer is found by the forms it named (`forms:` + their hash), so a rebuild
        that changes the keys asks nothing again; an older answer kept by the rule key
        still counts. A failed batch leaves its entities with their rule names: the list
        is still built, and the next rebuild asks about them again. Past the run's budget
        nothing more is asked."""
        forms = {
            entity.key: tuple(form for form, _ in entity.variants.most_common(MAX_FORMS))
            for entity in entities
        }
        by_forms = {key: f"forms:{input_hash(*found)}" for key, found in forms.items()}
        with self._session_factory() as session:
            records = {
                record.key: GivenName(record.nominative, record.gender, record.is_person)
                for record in session.scalars(
                    select(EntityNameNormalizationRecord).where(
                        EntityNameNormalizationRecord.prompt_version == PROMPT_VERSION,
                        EntityNameNormalizationRecord.key.in_(
                            set(by_forms.values()) | set(by_forms)
                        ),
                    )
                )
            }
        names: dict[str, GivenName] = {}
        for entity in entities:
            given = records.get(by_forms[entity.key]) or records.get(entity.key)
            if given is not None:
                names[entity.key] = given
        cached = len(names)
        # A single form already in the nominative needs no model (`nominative_form`).
        for entity in entities:
            if entity.key not in names and len(forms[entity.key]) == 1:
                ruled = nominative_form(forms[entity.key][0])
                if ruled is not None:
                    names[entity.key] = ruled
        missing = [entity for entity in entities if entity.key not in names]
        if self._normalizer is None or not missing:
            if missing:
                logger.warning(
                    "event=entity_names_not_normalized entities=%d reason=no_model", len(missing)
                )
            return names, 0, cached, 0, 0, 0.0
        normalizer = self._normalizer
        spend: Spend | None = getattr(normalizer, "spend", None)
        batches = [
            missing[start : start + BATCH_SIZE] for start in range(0, len(missing), BATCH_SIZE)
        ]

        def ask(batch: list[Entity]) -> dict[int, NormalizedName]:
            items = [
                NameItem(
                    id=position,
                    forms=forms[entity.key],
                    quote=quotes.get(entity.mention_ids[0], ""),
                )
                for position, entity in enumerate(batch)
            ]
            return normalizer.normalize(items)

        asked = failures = unasked = done = 0
        # Batches go out in parallel: the calls wait on the provider, not on this process.
        # Answers are written here, one batch at a time, as they come.
        for batch, result in ask_in_batches(
            batches, ask, concurrency=NORMALIZE_CONCURRENCY, spend=spend
        ):
            done += len(batch)
            self._on_stage(f"normalizing {done}/{len(missing)}")
            if isinstance(result, BudgetExceededError):
                unasked += len(batch)
                continue
            if isinstance(result, Exception):
                if not isinstance(result, NameNormalizerError):
                    raise result
                failures += len(batch)
                logger.warning(
                    "event=entity_names_batch_failed entities=%d error=%s", len(batch), result
                )
                continue
            rows = [
                {
                    "key": by_forms[batch[position].key],
                    "prompt_version": PROMPT_VERSION,
                    "model": normalizer.model,
                    "nominative": answer.nominative.strip(),
                    "gender": answer.gender,
                    "is_person": answer.is_person,
                }
                for position, answer in result.items()
            ]
            failures += len(batch) - len(rows)
            if rows:
                with self._session_factory.begin() as session:
                    session.execute(
                        pg_insert(EntityNameNormalizationRecord)
                        .values(rows)
                        .on_conflict_do_nothing()
                    )
                for position, answer in result.items():
                    names[batch[position].key] = GivenName(
                        answer.nominative.strip(), answer.gender, answer.is_person
                    )
            asked += len(rows)
        if unasked:
            logger.warning("event=entity_names_budget_spent unasked=%d", unasked)
        return names, asked, cached, failures, unasked, spend.cost_usd if spend else 0.0
