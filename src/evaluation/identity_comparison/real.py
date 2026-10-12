"""Both models on the mentions of a real database, which says nothing of who is who.

There is no ground truth here, so nothing is scored: the two groupings are laid side
by side, the mentions they group differently are listed, and what a run of each model
costs and keeps is measured. The database is only read; the connection refuses writes.

ER v2 is read as the database has it (`entity_mentions.person_id`, its decisions). The
rules are run here, in memory, over the mentions `EntityCollector` would take: the
person mentions of the latest successful extraction of the articles with a
criminal-case event, the authors' signatures left out.
"""

from __future__ import annotations

import math
import random
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, create_engine, text

from evaluation.identity_comparison.rules import entity_of_mention, rules_only_entities
from processing.identity.grouping import PersonMention, byline, name_key
from processing.identity.normalizer import BATCH_SIZE
from processing.junk.purge import CRIMINAL_EVENT_TYPES

# As `processing.identity.collector._AROUND`: the text around a mention that tells a signature.
AROUND = 40
SAMPLE_PER_STRATUM = 25
SAMPLE_SEED = 20261011
# The share of the articles, oldest first, that the «before» run of the rules sees.
EARLY_SHARE = 0.8

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
    SELECT m.id, c.article_id, a.published_at, m.person_id,
           m.normalized_data->>'first_name', m.normalized_data->>'last_name',
           m.normalized_data->>'patronymic', m.surface_text,
           substring(a.text from '(?:^|\n)Регион: ([^\n]+?)\\.?(?:\n|$)'),
           substr(a.text, greatest(m.start_offset - :around, 0) + 1,
                  least(m.start_offset, :around)),
           substr(a.text, m.end_offset + 1, :around),
           d.action, d.status
    FROM entity_mentions m
    JOIN criminal c ON c.run_id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = c.article_id
    LEFT JOIN LATERAL (
        SELECT action, status FROM person_resolution_decisions
        WHERE mention_id = m.id ORDER BY id DESC LIMIT 1
    ) d ON true
    WHERE m.entity_type = 'person'
    ORDER BY m.id
    """
)
# Every person mention with its latest ER v2 decision: the shares of the three actions.
_DECISIONS = text(
    """
    SELECT d.action, d.status, d.reasons,
           m.normalized_data->>'first_name', m.normalized_data->>'last_name',
           m.normalized_data->>'patronymic'
    FROM entity_mentions m
    LEFT JOIN LATERAL (
        SELECT action, status, reasons FROM person_resolution_decisions
        WHERE mention_id = m.id ORDER BY id DESC LIMIT 1
    ) d ON true
    WHERE m.entity_type = 'person'
    """
)
_PERSON_NAMES = text("SELECT id, canonical_name FROM persons")


@dataclass(frozen=True)
class Row:
    mention: PersonMention
    published_at: datetime | None
    person_id: int | None
    action: str | None
    status: str | None


def read_only_engine(database_url: str) -> Engine:
    return create_engine(
        database_url, connect_args={"options": "-c default_transaction_read_only=on"}
    )


def name_shape(first: str | None, last: str | None, patronymic: str | None) -> str:
    initial = first is not None and len(first.rstrip(".")) <= 1
    if last and first and initial:
        return "surname_and_initial"
    if last and first and patronymic:
        return "given_patronymic_surname"
    if last and first:
        return "given_surname"
    if last:
        return "surname_only"
    if first:
        return "given_only"
    return "unparsed"


def load_rows(engine: Engine) -> tuple[list[Row], int]:
    """The mentions the collector would take, and how many signatures were left out."""
    rows: list[Row] = []
    signatures = 0
    with engine.connect() as connection:
        for (
            mention_id,
            article_id,
            published_at,
            person_id,
            first,
            last,
            patronymic,
            surface,
            region,
            before,
            after,
            action,
            status,
        ) in connection.execute(
            _MENTIONS, {"criminal": list(CRIMINAL_EVENT_TYPES), "around": AROUND}
        ):
            if byline(surface, before or "", after or ""):
                signatures += 1
                continue
            rows.append(
                Row(
                    PersonMention(mention_id, article_id, first, last, patronymic, region, surface),
                    published_at,
                    person_id,
                    action,
                    status,
                )
            )
    return rows, signatures


def _group(rows: Sequence[Row]) -> dict[int, str]:
    mentions = [row.mention for row in rows]
    return entity_of_mention(
        rules_only_entities(
            mentions, {mention.mention_id: mention.article_id for mention in mentions}
        )
    )


def _pairs(count: int) -> int:
    return math.comb(count, 2)


def decision_shares(engine: Engine) -> dict[str, object]:
    actions: Counter[str] = Counter()
    shapes: dict[str, Counter[str]] = defaultdict(Counter)
    reasons: dict[str, Counter[str]] = defaultdict(Counter)
    with engine.connect() as connection:
        for action, status, why, first, last, patronymic in connection.execute(_DECISIONS):
            label = "no_decision" if action is None else f"{action}/{status}"
            actions[label] += 1
            shapes[label][name_shape(first, last, patronymic)] += 1
            for reason in why or []:
                reasons[label][reason] += 1
    total = sum(actions.values())
    return {
        "person_mentions": total,
        "actions": {
            label: {"mentions": count, "share": round(count / total, 4)}
            for label, count in actions.most_common()
        },
        "name_shape_by_action": {
            label: dict(counter.most_common()) for label, counter in shapes.items()
        },
        "reasons_by_action": {
            label: dict(counter.most_common(8)) for label, counter in reasons.items()
        },
    }


def compare(rows: Sequence[Row]) -> tuple[dict[str, object], dict[int, str]]:
    started = time.perf_counter()
    entity_of = _group(rows)
    seconds = time.perf_counter() - started
    again = _group(rows)

    coverage: Counter[str] = Counter()
    both: Counter[tuple[int, str]] = Counter()
    for row in rows:
        er = (
            "linked"
            if row.person_id is not None
            else "pending_review"
            if row.status == "pending_review"
            else "no_decision"
        )
        rules = "in_entity" if row.mention.mention_id in entity_of else "no_entity"
        coverage[f"{er} / {rules}"] += 1
        if row.person_id is not None and row.mention.mention_id in entity_of:
            both[row.person_id, entity_of[row.mention.mention_id]] += 1

    per_person: Counter[int] = Counter()
    per_entity: Counter[str] = Counter()
    entities_of_person: dict[int, set[str]] = defaultdict(set)
    persons_of_entity: dict[str, set[int]] = defaultdict(set)
    for (person_id, key), count in both.items():
        per_person[person_id] += count
        per_entity[key] += count
        entities_of_person[person_id].add(key)
        persons_of_entity[key].add(person_id)
    same_er = sum(_pairs(count) for count in per_person.values())
    same_rules = sum(_pairs(count) for count in per_entity.values())
    same_both = sum(_pairs(count) for count in both.values())
    summary: dict[str, object] = {
        "mentions": len(rows),
        "coverage": dict(coverage.most_common()),
        "mentions_grouped_by_both": sum(both.values()),
        "er_persons": len(per_person),
        "rules_entities": len(per_entity),
        "rules_entities_total": len(set(entity_of.values())),
        "pairs_joined_by_both": same_both,
        "pairs_joined_by_er_only": same_er - same_both,
        "pairs_joined_by_rules_only": same_rules - same_both,
        "er_persons_split_by_rules": sum(len(keys) > 1 for keys in entities_of_person.values()),
        "rules_entities_holding_several_er_persons": sum(
            len(persons) > 1 for persons in persons_of_entity.values()
        ),
        "rules_seconds": round(seconds, 2),
        "rules_repeat_is_identical": again == entity_of,
        "model_calls_for_a_first_build": math.ceil(len(set(entity_of.values())) / BATCH_SIZE),
    }
    return summary, entity_of


def growth(rows: Sequence[Row]) -> dict[str, object]:
    """What a rebuild does to the entities of an earlier one, when articles are added:
    the rules over the oldest articles, then over all of them."""
    dated = sorted(
        {row.mention.article_id: row.published_at for row in rows}.items(),
        key=lambda item: (item[1] is not None, item[1], item[0]),
    )
    early_articles = {article for article, _ in dated[: int(len(dated) * EARLY_SHARE)]}
    early_rows = [row for row in rows if row.mention.article_id in early_articles]
    before = _group(early_rows)
    after = _group(rows)

    mentions_of: dict[str, list[int]] = defaultdict(list)
    for mention_id, key in before.items():
        mentions_of[key].append(mention_id)
    old_keys_of_new: dict[str, set[str]] = defaultdict(set)
    fate: Counter[str] = Counter()
    for key, mention_ids in mentions_of.items():
        new_keys = {after[mention_id] for mention_id in mention_ids if mention_id in after}
        for new_key in new_keys:
            old_keys_of_new[new_key].add(key)
        if not new_keys:
            fate["gone"] += 1
        elif len(new_keys) > 1:
            fate["split"] += 1
        elif new_keys == {key}:
            fate["key_kept"] += 1
        else:
            fate["key_changed"] += 1
    return {
        "articles_before": len(early_articles),
        "articles_after": len(dated),
        "mentions_before": len(early_rows),
        "mentions_after": len(rows),
        "entities_before": len(mentions_of),
        "entities_after": len(set(after.values())),
        "fate_of_entities_before": dict(fate.most_common()),
        "entities_before_merged_into_a_shared_entity": sum(
            len(old) for old in old_keys_of_new.values() if len(old) > 1
        ),
    }


def name_difference(names: Sequence[str]) -> str:
    """How the canonical names of persons ER v2 keeps apart differ, in three kinds."""
    words = [name.split() for name in names]
    if len({name_key(name) for name in names}) == 1:
        return "same_name"
    if any(len(parts) >= 3 for parts in words):
        return "a_name_with_a_third_word"
    if len({name_key(parts[0]) for parts in words if parts}) == 1:
        return "one_given_name_surname_forms_differ"
    return "other"


def disagreement_sample(
    rows: Sequence[Row], entity_of: Mapping[int, str], person_names: Mapping[int, str]
) -> tuple[dict[str, int], list[dict[str, object]]]:
    """Cases the two group differently, by kind (`name_difference` for the persons
    ER v2 keeps apart), and a fixed sample of each kind to be judged by a person."""
    surfaces: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    members: dict[tuple[int, str], list[int]] = defaultdict(list)
    members_of_entity: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        key = entity_of.get(row.mention.mention_id)
        if key is not None:
            members_of_entity[key].append(row.mention.mention_id)
        if row.person_id is not None and key is not None:
            surfaces[row.person_id, key][row.mention.surface or ""] += 1
            members[row.person_id, key].append(row.mention.mention_id)
    persons_of_entity: dict[str, set[int]] = defaultdict(set)
    entities_of_person: dict[int, set[str]] = defaultdict(set)
    for person_id, key in surfaces:
        persons_of_entity[key].add(person_id)
        entities_of_person[person_id].add(key)

    def forms(person_id: int, key: str) -> list[str]:
        return [surface for surface, _ in surfaces[person_id, key].most_common(4)]

    cases: dict[str, list[dict[str, object]]] = defaultdict(list)
    for key, persons in sorted(persons_of_entity.items()):
        if len(persons) < 2:
            continue
        stratum = "rules_join_er_splits/" + name_difference(
            [person_names.get(person_id, "") for person_id in sorted(persons)]
        )
        cases[stratum].append(
            {
                "entity": key,
                "persons": [
                    {
                        "person_id": person_id,
                        "canonical_name": person_names.get(person_id),
                        "surfaces": forms(person_id, key),
                        "mention_ids": members[person_id, key],
                    }
                    for person_id in sorted(persons)
                ],
            }
        )
    for person_id, keys in sorted(entities_of_person.items()):
        if len(keys) < 2:
            continue
        cases["er_joins_rules_split"].append(
            {
                "person_id": person_id,
                "canonical_name": person_names.get(person_id),
                "entities": [
                    {
                        "entity": key,
                        "surfaces": forms(person_id, key),
                        "mention_ids": members[person_id, key],
                    }
                    for key in sorted(keys)
                ],
            }
        )
    # What ER v2 left to a human and the rules placed: the largest difference of all.
    forms_of_entity: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        key = entity_of.get(row.mention.mention_id)
        if key is not None:
            forms_of_entity[key][row.mention.surface or ""] += 1
    for row in rows:
        key = entity_of.get(row.mention.mention_id)
        if row.person_id is None and row.status == "pending_review" and key is not None:
            cases["er_review_rules_place"].append(
                {
                    "mention_id": row.mention.mention_id,
                    "surface": row.mention.surface,
                    "entity": key,
                    "entity_mentions": sum(forms_of_entity[key].values()),
                    "entity_surfaces": [form for form, _ in forms_of_entity[key].most_common(5)],
                    "other_mention_ids": [
                        other for other in members_of_entity[key] if other != row.mention.mention_id
                    ],
                }
            )
    picker = random.Random(SAMPLE_SEED)
    sample = [
        {"stratum": stratum, **case}
        for stratum, found in sorted(cases.items())
        for case in picker.sample(found, min(SAMPLE_PER_STRATUM, len(found)))
    ]
    return {stratum: len(found) for stratum, found in sorted(cases.items())}, sample


def person_names(engine: Engine) -> dict[int, str]:
    with engine.connect() as connection:
        return {person_id: name for person_id, name in connection.execute(_PERSON_NAMES)}
