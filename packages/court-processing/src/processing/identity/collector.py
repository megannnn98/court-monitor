"""Rebuild the person entities from the articles with a criminal case.

The whole table at once, in one transaction: grouping reads every mention anyway, and a
reader never sees a half-built list. What a rebuild keeps stable is the entity key, not
its id.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, distinct, func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from ingestion import read
from llm.chat import BudgetExceededError, Spend, ask_in_batches
from processing.answers import input_hash
from processing.identity.disputes import KeyIndex, merge_decided, same_pairs
from processing.identity.downstream import IdentifiedFigurant, IdentityDownstream
from processing.identity.grouping import (
    Entity,
    GivenName,
    PersonMention,
    apply_names,
    attach_aliases,
    attach_bare,
    byline,
    given_name_first,
    group_mentions,
    kinship,
    merge_swapped,
    name_key,
    nominative_form,
)
from processing.identity.normalizer import (
    BATCH_SIZE,
    PROMPT_VERSION,
    NameItem,
    NameNormalizer,
    NameNormalizerError,
    NormalizedName,
)
from processing.identity.overrides import apply_overrides, name_overrides
from processing.identity.removals import removed_keys, without_removed
from processing.junk.purge import CRIMINAL_EVENT_TYPES
from processing.storage.models import (
    EntityGroupChargeRecord,
    EntityGroupMentionRecord,
    EntityGroupRecord,
    EntityGroupUnnamedMentionRecord,
    EntityNameNormalizationRecord,
)

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
    SELECT m.id, c.article_id,
           m.normalized_data->>'first_name',
           m.normalized_data->>'last_name',
           m.normalized_data->>'patronymic',
           m.surface_text, m.start_offset, m.end_offset
    FROM entity_mentions m
    JOIN criminal c ON c.run_id = m.extraction_run_id
    WHERE m.entity_type = 'person'
    """
)
# A registry card (memopzk) names its person's region on a line of its own.
_REGION = "(?:^|\n)Регион: ([^\n]+?)\\.?(?:\n|$)"
# How much text around a mention `byline` reads: the end of the line before, and whether
# anything follows.
_AROUND = 40
# A person's mention in brackets right after another's: «Дмитрия Пуркина (Дед Архимед)».
# Offsets are Python's, from 0; `substr` counts from 1.
_PERSON_SPANS = text(
    """
    SELECT m.id, m.extraction_run_id, r.article_id, m.start_offset, m.end_offset
    FROM entity_mentions m
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    WHERE m.entity_type = 'person' AND m.start_offset > 0
    """
)
# The person mentions before a bracketed one, in its extraction run.
_BEFORE_BRACKETS = text(
    """
    SELECT a.id, n.id, r.article_id, n.end_offset, a.start_offset
    FROM entity_mentions a
    JOIN article_extraction_runs r ON r.id = a.extraction_run_id
    JOIN entity_mentions n
      ON n.extraction_run_id = a.extraction_run_id AND n.entity_type = 'person'
     AND n.end_offset < a.start_offset
    WHERE a.id = ANY(:bracketed)
    """
)
_OPENING_BRACKET = re.compile(r"[ \t]*\(")
_EVENTS = text(
    """
    SELECT em.mention_id, e.event_type FROM event_entity_mentions em
    JOIN extracted_events e ON e.id = em.event_id
    WHERE em.mention_id = ANY(:mentions)
    """
)

_UNNAMED_CHARGES = text(
    """
    WITH latest AS (
        SELECT DISTINCT ON (article_id) id, article_id
        FROM article_extraction_runs
        WHERE status = 'succeeded'
        ORDER BY article_id, id DESC
    )
    , u AS (
        -- The identified figurants just linked to the groups, one row per article of
        -- their event: the analysis hands them over (`IdentityDownstream`).
        SELECT * FROM jsonb_to_recordset(CAST(:figurants AS jsonb))
            AS x(group_id integer, article_id integer, event_type text, quote text,
                 article text)
    )
    INSERT INTO entity_group_charges
        (group_id, event_id, publication_id, article, part, clause, event_type,
         other_targets, quote)
    SELECT u.group_id, e.id, u.article_id, u.article, NULL, NULL, u.event_type, 0, u.quote
    FROM u
    JOIN LATERAL (
        SELECT e.id FROM latest r
        JOIN extracted_events e ON e.extraction_run_id = r.id
        WHERE r.article_id = u.article_id
          AND e.event_type = u.event_type
        ORDER BY e.id DESC LIMIT 1
    ) e ON true
    WHERE coalesce(u.article, '') <> ''
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
    SELECT DISTINCT ON (g.group_id, e.id, l.normalized_data->>'article',
                        l.normalized_data->>'part', l.normalized_data->>'clause')
           g.group_id, e.id AS event_id, r.article_id AS publication_id,
           l.normalized_data->>'article' AS article, l.normalized_data->>'part' AS part,
           l.normalized_data->>'clause' AS clause, e.event_type,
           (SELECT count(*) FROM event_entity_mentions o
            WHERE o.event_id = e.id AND o.role = 'target'
              AND NOT EXISTS (SELECT 1 FROM entity_group_mentions s
                              WHERE s.mention_id = o.mention_id AND s.group_id = g.group_id))
               AS other_targets,
           e.start_offset, e.end_offset
    FROM entity_group_mentions g
    JOIN event_entity_mentions t ON t.mention_id = g.mention_id AND t.role = 'target'
    JOIN extracted_events e ON e.id = t.event_id
    JOIN event_entity_mentions b ON b.event_id = e.id AND b.role = 'legal_basis'
    JOIN entity_mentions l ON l.id = b.mention_id
    JOIN article_extraction_runs r ON r.id = e.extraction_run_id
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


def _apply_unnamed_resolutions(
    entities: Sequence[Entity], identified: Sequence[IdentifiedFigurant]
) -> tuple[list[Entity], dict[str, list[IdentifiedFigurant]]]:
    """Add operator-identified unnamed figurants as evidence of their real person."""
    by_key = {entity.key: entity for entity in entities}
    index = KeyIndex(by_key)
    evidence: dict[str, list[IdentifiedFigurant]] = defaultdict(list)
    for figurant in identified:
        if figurant.resolution == "existing_person":
            target_key = index.today(figurant.existing_person_key or "")
            if target_key is None:
                continue
        else:
            name = given_name_first((figurant.normalized_name or "").strip())
            if not name:
                continue
            target_key = name_key(name)
            if target_key not in by_key:
                by_key[target_key] = Entity(
                    key=target_key,
                    name=name,
                    variants=Counter({name: 1}),
                    gender=figurant.gender,
                    name_source="operator",
                )
                index = KeyIndex(by_key)
            elif by_key[target_key].gender is None:
                by_key[target_key].gender = figurant.gender
        evidence[target_key].append(figurant)
    return sorted(
        by_key.values(), key=lambda entity: (-len(entity.mention_ids), entity.key)
    ), evidence


@dataclass(frozen=True)
class _MentionRow:
    mention_id: int
    article_id: int
    published_at: datetime | None
    first: str | None
    last: str | None
    patronymic: str | None
    surface: str
    quote: str | None
    region: str | None
    before: str | None
    after: str | None


def _mention_rows(session: Session) -> list[_MentionRow]:
    """The person mentions of the criminal articles, with what the rebuild reads of their
    article's text: the quote for the model, the card's region, the text around for the
    byline. The text is the ingestion's (`ingestion.read`)."""
    rows = session.execute(_MENTIONS, {"criminal": list(CRIMINAL_EVENT_TYPES)}).all()
    articles = {row[1] for row in rows}
    published = read.published_at(session, articles)
    # An article deleted since its extraction is no evidence.
    rows = [row for row in rows if row[1] in published]
    regions = read.text_pattern(session, articles, _REGION)
    windows: list[read.TextWindow] = []
    for row in rows:
        article_id, start, end = row[1], row[6], row[7]
        quoted = max(start - QUOTE_CONTEXT, 0)
        windows += [
            read.TextWindow(article_id, quoted, end - quoted + QUOTE_CONTEXT),
            read.TextWindow(article_id, max(start - _AROUND, 0), min(start, _AROUND)),
            read.TextWindow(article_id, end, _AROUND),
        ]
    texts = read.text_windows(session, windows)
    return [
        _MentionRow(
            mention_id,
            article_id,
            published[article_id],
            first,
            last,
            patronymic,
            surface,
            texts[3 * index],
            regions.get(article_id),
            texts[3 * index + 1],
            texts[3 * index + 2],
        )
        for index, (mention_id, article_id, first, last, patronymic, surface, _, _) in enumerate(
            rows
        )
    ]


def bracketed_aliases(session: Session) -> list[tuple[int, int]]:
    """(alias, named): a person's mention in brackets right after another's — «Дмитрия
    Пуркина (Дед Архимед)». Offsets are Python's, from 0."""
    spans = session.execute(_PERSON_SPANS).all()
    around = read.text_windows(
        session,
        [
            window
            for _, _, article_id, start, end in spans
            for window in (
                read.TextWindow(article_id, start - 1, 1),
                read.TextWindow(article_id, end, 1),
            )
        ],
    )
    bracketed = [
        span[0]
        for index, span in enumerate(spans)
        if around[2 * index] == "(" and around[2 * index + 1] == ")"
    ]
    if not bracketed:
        return []
    pairs = session.execute(_BEFORE_BRACKETS, {"bracketed": bracketed}).all()
    between = read.text_windows(
        session,
        [read.TextWindow(article_id, end, start - end) for _, _, article_id, end, start in pairs],
    )
    return [
        (alias, named)
        for (alias, named, _, _, _), gap in zip(pairs, between, strict=True)
        if gap is not None and _OPENING_BRACKET.fullmatch(gap)
    ]


def _charge_rows(session: Session) -> list[dict[str, object]]:
    """The Criminal Code articles the events tie to the groups just written, each with
    its event's text (at most `CHARGE_QUOTE_LIMIT` characters) as the quote."""
    rows = session.execute(_CHARGES).all()
    quotes = read.text_windows(
        session,
        [
            read.TextWindow(
                row.publication_id,
                row.start_offset,
                min(row.end_offset - row.start_offset, CHARGE_QUOTE_LIMIT),
            )
            for row in rows
        ],
    )
    return [
        {
            "group_id": row.group_id,
            "event_id": row.event_id,
            "publication_id": row.publication_id,
            "article": row.article,
            "part": row.part,
            "clause": row.clause,
            "event_type": row.event_type,
            "other_targets": row.other_targets,
            "quote": quote,
        }
        for row, quote in zip(rows, quotes, strict=True)
        # An article deleted since its extraction is no evidence.
        if quote is not None
    ]


class EntityCollector:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        on_stage: Callable[[str], None] = lambda _stage: None,
        normalizer: NameNormalizer | None = None,
        downstream: IdentityDownstream,
    ) -> None:
        self._session_factory = session_factory
        self._on_stage = on_stage
        self._normalizer = normalizer
        # The operator's identifications of unnamed figurants, the analysis's data.
        self._downstream = downstream

    def run(self) -> CollectResult:
        with self._session_factory() as session:
            self._on_stage("reading")
            rows = _mention_rows(session)
            published: dict[int, datetime | None] = {}
            quotes: dict[int, str] = {}
            mentions: list[PersonMention] = []
            signatures = 0
            for row in rows:
                mention_id, article_id, surface = row.mention_id, row.article_id, row.surface
                first, last, patronymic, region = row.first, row.last, row.patronymic, row.region
                quote, before, after = row.quote, row.before, row.after
                published_at = row.published_at
                # The journalist's name under the text is no person of the news.
                if byline(surface, before or "", after or ""):
                    signatures += 1
                    continue
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
        logger.info("event=entity_bylines_skipped count=%d", signatures)

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
            aliases = bracketed_aliases(session)
        entities = attach_aliases(entities, aliases)
        # A surname alone joins the full name of that surname its article names.
        entities = attach_bare(entities, articles)
        with self._session_factory() as session:
            entities, unnamed = _apply_unnamed_resolutions(
                entities, self._downstream.identified_figurants(session)
            )
            # A person's «there is no such person» takes an entity out, last of all: what
            # the steps above join to it goes with it.
            entities = without_removed(entities, removed_keys(session))

        with self._session_factory.begin() as session:
            self._on_stage("writing")
            session.execute(delete(EntityGroupRecord))
            links: list[dict[str, int]] = []
            unnamed_links: list[dict[str, int | str]] = []
            linked_figurants: list[tuple[dict[str, int], IdentifiedFigurant]] = []
            for entity in entities:
                dates = [d for m in entity.mention_ids if (d := published[m]) is not None]
                unnamed_rows = unnamed.get(entity.key, [])
                dates += [
                    figurant.published_at for figurant in unnamed_rows if figurant.published_at
                ]
                article_ids = {articles[m] for m in entity.mention_ids} | {
                    figurant.article_id for figurant in unnamed_rows
                }
                event_types = Counter(kind for m in entity.mention_ids for kind in events[m])
                event_types.update(figurant.event_type for figurant in unnamed_rows)
                group_id = session.execute(
                    insert(EntityGroupRecord)
                    .values(
                        key=entity.key,
                        name=entity.name,
                        variants=[list(item) for item in entity.variants.most_common()],
                        mention_count=len(entity.mention_ids),
                        article_count=len(article_ids),
                        event_types=dict(event_types),
                        last_published_at=max(dates) if dates else None,
                        gender=entity.gender,
                        name_source=entity.name_source,
                        regions=[list(item) for item in entity.regions.most_common()],
                    )
                    .returning(EntityGroupRecord.id)
                ).scalar_one()
                links += [{"group_id": group_id, "mention_id": m} for m in entity.mention_ids]
                unnamed_links += [
                    {"group_id": group_id, "figurant_key": figurant.key}
                    for figurant in unnamed_rows
                ]
                linked_figurants += [
                    ({"group_id": group_id}, figurant) for figurant in unnamed_rows
                ]
            for start in range(0, len(links), INSERT_CHUNK):
                session.execute(
                    insert(EntityGroupMentionRecord), links[start : start + INSERT_CHUNK]
                )
            for start in range(0, len(unnamed_links), INSERT_CHUNK):
                session.execute(
                    insert(EntityGroupUnnamedMentionRecord),
                    unnamed_links[start : start + INSERT_CHUNK],
                )
            self._on_stage("charges")
            charge_rows = _charge_rows(session)
            for start in range(0, len(charge_rows), INSERT_CHUNK):
                session.execute(
                    insert(EntityGroupChargeRecord), charge_rows[start : start + INSERT_CHUNK]
                )
            session.execute(
                _UNNAMED_CHARGES,
                {
                    "figurants": json.dumps(
                        [
                            {
                                "group_id": link["group_id"],
                                "article_id": figurant.article_id,
                                "event_type": figurant.event_type,
                                "quote": figurant.quote,
                                "article": article,
                            }
                            for link, figurant in linked_figurants
                            for article in figurant.articles
                        ]
                    )
                },
            )
            charges, charged = session.execute(
                select(
                    func.count(), func.count(distinct(EntityGroupChargeRecord.group_id))
                ).select_from(EntityGroupChargeRecord)
            ).one()
        result = CollectResult(
            mentions=len(mentions),
            entities=len(entities),
            grouped=len(links) + len(unnamed_links),
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
        query = select(EntityNameNormalizationRecord).where(
            EntityNameNormalizationRecord.prompt_version == PROMPT_VERSION,
            EntityNameNormalizationRecord.key.in_(set(by_forms.values()) | set(by_forms)),
        )
        # The asking model's own answers only (`processing.answers`); with no model, any.
        if self._normalizer is not None:
            query = query.where(EntityNameNormalizationRecord.model == self._normalizer.model)
        else:
            # Any model's answer, the newest winning the same way every time.
            query = query.order_by(
                EntityNameNormalizationRecord.created_at.asc(),
                EntityNameNormalizationRecord.model.asc(),
            )
        with self._session_factory() as session:
            records = {
                record.key: GivenName(record.nominative, record.gender, record.is_person)
                for record in session.scalars(query)
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
