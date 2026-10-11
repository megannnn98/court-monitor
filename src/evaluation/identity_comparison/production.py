"""The identity the operator cycle really builds, replayed on the labelled sample.

`EntityCollector.run` groups mentions by rules, renames every entity by a model's answer
(DeepSeek through OpenRouter, `entities.normalizer`) and merges entities the model named
alike, then applies a person's decisions. This replays those steps in memory over the
mentions of the real database, with the model's answers a production snapshot already
holds (`entity_name_normalizations`); the few it lacks may be asked once, with the
production prompt and model, and are kept in a local file so that nothing is asked
twice. Both databases are only read.

    python -m evaluation.identity_comparison.production \\
        --real-url …/junk_eval --cache-url …/junk_prod_20260929 \\
        --sample var/identity_comparison/disagreement_sample.json \\
        --answers var/identity_comparison/deepseek_name_answers.json \\
        --decisions tests/fixtures/identity_comparison/disagreement_decisions.json

It writes, for every pair of the sample, what each variant decided, into `--decisions`
(no names, no text), next to the stored decisions of ER v2 and of the rules alone.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from entities.answers import input_hash
from entities.collector import _ALIASES, MAX_FORMS, QUOTE_CONTEXT
from entities.disputes import EntityRef, find_pairs, merge_decided, pair_keys
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
from entities.normalizer import BATCH_SIZE, PROMPT_VERSION, NameItem, name_normalizer_from_env
from entities.overrides import apply_overrides
from evaluation.identity_comparison import annotation, labelling, real
from evaluation.identity_comparison.rules import entity_of_mention
from processing.extraction.models import NAMED_TITLE_SOURCES
from processing.extraction.normalizers import name_parts

ONE_ENTITY = "one_entity"
DIFFERENT_ENTITIES = "different_entities"
# A side none of whose mentions is in any entity: the pipeline made nobody of it.
NO_ENTITY = "no_entity"
# Kept apart, and shown to the operator as a pair of «Спорные случаи» to decide.
OFFERED = "offered_to_operator"
# Variants written to the decisions file, weakest first.
RULES = "rules_replayed"
RULES_DEEPSEEK = "rules_deepseek"
PRODUCTION = "rules_deepseek_operator"
# The same three over the name parts the current normalizer would store, where the
# database holds those of an older one (`renormalized`).
CURRENT = "_normalizer_current"

_CACHE = text(
    "SELECT key, nominative, gender, is_person FROM entity_name_normalizations "
    "WHERE prompt_version = :version"
)
_STORED_NAMES = text(
    """
    SELECT m.id, m.normalized_text, s.name
    FROM entity_mentions m
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    JOIN source_documents sd ON sd.id = a.document_id
    JOIN sources s ON s.id = sd.source_id
    WHERE m.id = ANY(:ids)
    """
)
_QUOTES = text(
    """
    SELECT m.id, substr(a.text, greatest(m.start_offset - :context, 0) + 1,
                        m.end_offset - greatest(m.start_offset - :context, 0) + :context)
    FROM entity_mentions m
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    JOIN parsed_articles a ON a.id = r.article_id
    WHERE m.id = ANY(:ids)
    """
)


def renormalized(mentions: Sequence[PersonMention], engine: Engine) -> list[PersonMention]:
    """The mentions with the name parts the current normalizer derives from the stored
    full name. A registry card's name and a name written with initials keep their
    parts: the normalizer takes those by other rules, which did not change."""
    with engine.connect() as connection:
        stored = {
            mention_id: (full_name or "", source)
            for mention_id, full_name, source in connection.execute(
                _STORED_NAMES, {"ids": [mention.mention_id for mention in mentions]}
            )
        }
    result: list[PersonMention] = []
    for mention in mentions:
        full_name, source = stored.get(mention.mention_id, ("", None))
        words = full_name.split()
        if not words or "." in words[0] or source in NAMED_TITLE_SOURCES:
            result.append(mention)
            continue
        first, last, patronymic = name_parts(words)
        result.append(replace(mention, first_name=first, last_name=last, patronymic=patronymic))
    return result


def forms_of(entity: Entity) -> tuple[str, ...]:
    return tuple(form for form, _ in entity.variants.most_common(MAX_FORMS))


def forms_key(entity: Entity) -> str:
    return f"forms:{input_hash(*forms_of(entity))}"


def names_for(
    entities: Sequence[Entity], answers: Mapping[str, GivenName]
) -> tuple[dict[str, GivenName], list[Entity]]:
    """As `EntityCollector._names` finds them: an answer by the forms, else by the rule
    key, else the rule for a single nominative form. And the entities left to ask."""
    names: dict[str, GivenName] = {}
    missing: list[Entity] = []
    for entity in entities:
        given = answers.get(forms_key(entity)) or answers.get(entity.key)
        if given is None and len(forms := forms_of(entity)) == 1:
            given = nominative_form(forms[0])
        if given is None:
            missing.append(entity)
        else:
            names[entity.key] = given
    return names, missing


def build(
    mentions: Sequence[PersonMention],
    *,
    names: Mapping[str, GivenName] | None = None,
    aliases: Sequence[tuple[int, int]] = (),
    same_pairs: Sequence[tuple[str, str]] = (),
    overrides: Mapping[str, str] | None = None,
) -> list[Entity]:
    """The grouping steps of `EntityCollector.run`, in its order. Without `names` this is
    the rules alone; a person's decisions come in through `same_pairs` and `overrides`."""
    entities = group_mentions([mention for mention in mentions if not kinship(mention)])
    entities = merge_swapped(apply_names(entities, names or {}))
    entities = merge_decided(entities, same_pairs)
    entities = apply_overrides(entities, overrides or {})
    entities = attach_aliases(entities, aliases)
    return attach_bare(entities, {mention.mention_id: mention.article_id for mention in mentions})


def pair_decision(
    left: Sequence[int],
    right: Sequence[int],
    entity_of: Mapping[int, str],
    offered: frozenset[tuple[str, str]] = frozenset(),
) -> str:
    """One entity when any mention of one side shares an entity with one of the other;
    `offered` when the two are apart and a pair of theirs waits for the operator."""
    first = {entity_of[mention_id] for mention_id in left if mention_id in entity_of}
    second = {entity_of[mention_id] for mention_id in right if mention_id in entity_of}
    if not first or not second:
        return NO_ENTITY
    if first & second:
        return ONE_ENTITY
    if any(pair_keys(one, other) in offered for one in first for other in second):
        return OFFERED
    return DIFFERENT_ENTITIES


def offered_pairs(
    entities: Sequence[Entity], decided: Sequence[tuple[str, str]] = ()
) -> frozenset[tuple[str, str]]:
    """The pairs «Спорные случаи» would show for these entities (`disputes.find_pairs`)."""
    refs = [
        EntityRef(id=index, key=entity.key, name=entity.name, mention_count=len(entity.mention_ids))
        for index, entity in enumerate(entities)
    ]
    return frozenset(pair.keys for pair in find_pairs(refs, decided))


def decisions_of(
    sample: Sequence[Mapping[str, Any]],
    entity_of: Mapping[int, str],
    offered: frozenset[tuple[str, str]] = frozenset(),
) -> dict[str, str]:
    """Pair id -> decision, for the pairs `annotation.annotate` makes of the sample."""
    found: dict[str, str] = {}
    for case in sample:
        sides, _ = annotation.sides_of(case)
        identifier = annotation.case_id(
            str(case["stratum"]), [mention_id for side in sides for mention_id in side.mention_ids]
        )
        lettered = [(chr(ord("a") + index), side) for index, side in enumerate(sides)]
        for (left_letter, left), (right_letter, right) in combinations(lettered, 2):
            found[f"{identifier}:{left_letter}-{right_letter}"] = pair_decision(
                left.mention_ids, right.mention_ids, entity_of, offered
            )
    return found


def cached_answers(engine: Engine) -> dict[str, GivenName]:
    with engine.connect() as connection:
        return {
            key: GivenName(nominative, gender, is_person)
            for key, nominative, gender, is_person in connection.execute(
                _CACHE, {"version": PROMPT_VERSION}
            )
        }


def operator_decisions(
    engine: Engine,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict[str, str]]:
    """Pairs decided «same», every decided pair, and corrected names, as the snapshot
    holds them. Read by column name: the snapshot may be at an older revision than the
    ORM models."""
    with engine.connect() as connection:
        decided = [
            (key_a, key_b, decision)
            for key_a, key_b, decision in connection.execute(
                text("SELECT key_a, key_b, decision FROM entity_pair_decisions")
            )
        ]
        pairs = [(key_a, key_b) for key_a, key_b, decision in decided if decision == "same"]
        overrides = {
            key: name
            for key, name in connection.execute(text("SELECT key, name FROM entity_name_overrides"))
        }
    return pairs, [(key_a, key_b) for key_a, key_b, _ in decided], overrides


def load_answers(path: Path) -> dict[str, GivenName]:
    if not path.exists():
        return {}
    stored = json.loads(path.read_text(encoding="utf-8"))["answers"]
    return {key: GivenName(**answer) for key, answer in stored.items()}


def ask_missing(
    missing: Sequence[Entity], quotes: Mapping[int, str], path: Path, known: dict[str, GivenName]
) -> dict[str, Any]:
    """Ask the production model, with the production prompt, for the entities no stored
    answer covers; keep every answer in `path` at once."""
    normalizer = name_normalizer_from_env()
    if normalizer is None:
        raise ValueError("no model is configured (OPENROUTER_API_KEY)")
    asked = answered = 0
    for start in range(0, len(missing), BATCH_SIZE):
        batch = missing[start : start + BATCH_SIZE]
        items = [
            NameItem(
                id=position, forms=forms_of(entity), quote=quotes.get(entity.mention_ids[0], "")
            )
            for position, entity in enumerate(batch)
        ]
        result = normalizer.normalize(items)
        asked += len(batch)
        for position, answer in result.items():
            known[forms_key(batch[position])] = GivenName(
                answer.nominative.strip(), answer.gender, answer.is_person
            )
            answered += 1
        labelling.atomic_write(
            path,
            {
                "model": normalizer.model,
                "prompt_version": PROMPT_VERSION,
                "answers": {
                    key: {
                        "nominative": given.nominative,
                        "gender": given.gender,
                        "is_person": given.is_person,
                    }
                    for key, given in sorted(known.items())
                },
            },
        )
    spend = getattr(normalizer, "spend", None)
    return {
        "model": normalizer.model,
        "calls": math.ceil(len(missing) / BATCH_SIZE),
        "asked": asked,
        "answered": answered,
        "cost_usd": round(spend.cost_usd, 4) if spend is not None else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--real-url", required=True)
    parser.add_argument("--cache-url", required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument(
        "--ask", action="store_true", help="ask the model for entities without a stored answer"
    )
    args = parser.parse_args()

    sample = json.loads(args.sample.read_text(encoding="utf-8"))
    real_engine = real.read_only_engine(args.real_url)
    cache_engine = real.read_only_engine(args.cache_url)
    try:
        rows, _ = real.load_rows(real_engine)
        stored_mentions = [row.mention for row in rows]
        passes = {"": stored_mentions, CURRENT: renormalized(stored_mentions, real_engine)}
        answers = {**cached_answers(cache_engine), **load_answers(args.answers)}
        first = {
            suffix: group_mentions([mention for mention in mentions if not kinship(mention)])
            for suffix, mentions in passes.items()
        }
        missing = {
            forms_key(entity): entity
            for entities in first.values()
            for entity in names_for(entities, answers)[1]
        }
        report: dict[str, Any] = {
            "mentions": len(stored_mentions),
            "mentions_with_other_name_parts": sum(
                (old.first_name, old.last_name, old.patronymic)
                != (new.first_name, new.last_name, new.patronymic)
                for old, new in zip(stored_mentions, passes[CURRENT], strict=True)
            ),
            "without_an_answer": len(missing),
            "calls_needed": math.ceil(len(missing) / BATCH_SIZE),
        }
        if args.ask and missing:
            with real_engine.connect() as connection:
                quotes = {
                    mention_id: " ".join((quote or "").split())
                    for mention_id, quote in connection.execute(
                        _QUOTES,
                        {
                            "ids": [entity.mention_ids[0] for entity in missing.values()],
                            "context": QUOTE_CONTEXT,
                        },
                    )
                }
            own = load_answers(args.answers)
            report["asked"] = ask_missing(list(missing.values()), quotes, args.answers, own)
            answers = {**answers, **own}
        with real_engine.connect() as connection:
            aliases = [(alias, named) for alias, named in connection.execute(_ALIASES)]
        same_pairs, decided_pairs, overrides = operator_decisions(cache_engine)
    finally:
        real_engine.dispose()
        cache_engine.dispose()

    variants: dict[str, list[Entity]] = {}
    for suffix, mentions in passes.items():
        names, left = names_for(first[suffix], answers)
        report[f"without_an_answer_in_the_end{suffix}"] = len(left)
        variants[RULES + suffix] = build(mentions)
        variants[RULES_DEEPSEEK + suffix] = build(mentions, names=names, aliases=aliases)
        variants[PRODUCTION + suffix] = build(
            mentions, names=names, aliases=aliases, same_pairs=same_pairs, overrides=overrides
        )
    decided = {
        variant: decisions_of(
            sample,
            entity_of_mention(entities),
            # A pair a person already decided is not shown again.
            offered_pairs(entities, decided_pairs if variant.startswith(PRODUCTION) else ()),
        )
        for variant, entities in variants.items()
    }
    report["entities"] = {variant: len(entities) for variant, entities in variants.items()}
    report["operator_same_pairs"] = len(same_pairs)
    report["operator_name_overrides"] = len(overrides)

    document = json.loads(args.decisions.read_text(encoding="utf-8"))
    written = 0
    for case in document["cases"]:
        for pair in case["pairs"]:
            pair["decisions"] = {variant: decided[variant][pair["pair_id"]] for variant in variants}
            written += 1
    labelling.atomic_write(args.decisions, document)
    report["pairs_written"] = written
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
