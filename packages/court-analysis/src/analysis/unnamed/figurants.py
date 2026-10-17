"""The figurants the publications do not name — «17-летний житель Тюмени», «задержали
57-летнюю жительницу Кушвы» — and the decisions a person takes on them.

1. Rules pick the sentences of the publications with a criminal case that describe a
   person by age; a model reads each (`processing.evidence.unnamed`). Answers are cached
   by the sentence (`processing.answers`). Whoever that leaves is read once more with
   the whole publication around the sentence (`WIDE_CONTEXT`): a press release tells the
   article and the age paragraphs away from the sentence that describes the person, and
   names them further on. That reading also tells a common crime the text gives no
   article number for («за секс с несовершеннолетним»): such a person is no figurant.
2. The unnamed figurants are rewritten; the list is searched for each on demand
   (`analysis.unnamed.matching`); a person's decisions are kept by key (`decide`). Whom
   the search sets aside on its own — a common crime, one more sentence about a person
   already on a card — is kept beside them with the reason (`UnnamedScreenedRecord`),
   and a person takes any of them back (`keep`). Whoever a person spoke of is never set
   aside.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import delete, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from analysis.politics.classification import COMMON_CRIME_ARTICLES
from analysis.storage.models import (
    UnnamedAnswerRecord,
    UnnamedDecisionRecord,
    UnnamedFigurantRecord,
    UnnamedIdentityResolutionRecord,
    UnnamedKeepRecord,
    UnnamedScreenedRecord,
)
from processing.answers import AnswerCache, AskResult, ask_missing, input_hash
from processing.evidence.unnamed import (
    UnnamedAnswer,
    UnnamedItem,
    UnnamedReader,
    UnnamedReaderError,
    described_sentences,
)
from processing.junk.purge import CRIMINAL_EVENT_TYPES

logger = logging.getLogger("entities")


# v2: «motive» — a common crime told in words, without an article number, was a card.
PROMPT_VERSION = "unnamed-v2"
BATCH_SIZE = 20
CONCURRENCY = 8

# `unnamed_decisions.candidate` holds this many characters.
CANDIDATE_KEY_LENGTH = 255
SAME = "same"
DIFFERENT = "different"
NONE = "none"
RF_ENTRY = "rf_entry"
EXISTING_PERSON = "existing_person"
SUPPLIED_NAME = "supplied_name"
NO_RF_MATCH = "no_rf_match"
INSUFFICIENT = "insufficient"
# Why the search set an unnamed person aside (`UnnamedScreenedRecord.reason`).
COMMON_CRIME = "common_crime"
CRIMINAL_MOTIVE = "criminal_motive"
DUPLICATE = "duplicate"
IDENTIFIED = frozenset({RF_ENTRY, EXISTING_PERSON, SUPPLIED_NAME})
RESOLUTIONS = frozenset({RF_ENTRY, EXISTING_PERSON, SUPPLIED_NAME, NO_RF_MATCH, INSUFFICIENT})

# The publications with a criminal case: their latest successful extraction found one.
_PUBLICATIONS = text(
    """
    SELECT a.id, a.text, a.published_at FROM parsed_articles a
    WHERE EXISTS (
        SELECT 1 FROM article_extraction_runs r
        JOIN extracted_events e ON e.extraction_run_id = r.id
        WHERE r.article_id = a.id AND r.status = 'succeeded' AND e.event_type = ANY(:criminal)
    )
    ORDER BY a.id
    """
)

UNNAMED_CACHE: AnswerCache[UnnamedAnswer] = AnswerCache(
    record=UnnamedAnswerRecord,
    field="answer",
    prompt_version=PROMPT_VERSION,
    # An earlier answer that makes no figurant holds: only a figurant's motive is asked.
    accepted=lambda _version, value, _explanation: (
        not _kept(UnnamedAnswer.model_validate_json(value))
    ),
    sticky=lambda _value: False,
    make=lambda item_id, value, _explanation: UnnamedAnswer.model_validate_json(value).model_copy(
        update={"id": item_id}
    ),
)


@dataclass
class UnnamedResult:
    sentences: int = 0
    unnamed: int = 0
    named: int = 0
    not_cases: int = 0
    # A case of common crime only (theft, alimony): nobody the list would carry.
    common_crime: int = 0
    # A common crime by the whole publication's telling, no article number given.
    criminal_motive: int = 0
    # One more sentence of a publication about a person already on a card.
    duplicates: int = 0
    asked_now: int = 0
    cached: int = 0
    failures: int = 0
    unasked: int = 0
    cost_usd: float = 0.0


# Common crime, as «Отобрать политические дела» reads it, and more the unnamed bring:
# drawing a minor into a crime, alimony, abuse images of minors, drunk driving.
UNNAMED_COMMON_CRIME = COMMON_CRIME_ARTICLES | {"150", "157", "242.2", "264.1"}
# An attempt and complicity say nothing of what the crime is.
_NEUTRAL_ARTICLES = frozenset({"30", "33"})


def common_crime_only(articles: Sequence[str]) -> bool:
    """Every article common crime, none of them political: the list — terrorists and
    extremists — does not carry such a person. No articles: nothing told, kept."""
    told = {article.strip() for article in articles} - _NEUTRAL_ARTICLES - {""}
    return bool(told) and told <= UNNAMED_COMMON_CRIME


def _kept(answer: UnnamedAnswer) -> bool:
    """An unnamed figurant by this answer: a case, no name, not common crime alone."""
    return answer.is_case and not answer.named and not common_crime_only(answer.articles)


def _person(row: Mapping[str, Any]) -> tuple[object, ...] | None:
    """Who a row is about, as far as one publication tells its people apart: the age, the
    sex, the place and the initial. None without an age: too little to call two
    sentences one person."""
    if row["age"] is None:
        return None
    return (row["article_id"], row["age"], row["gender"], row["place"].casefold(), row["initial"])


def _initial(value: str) -> str | None:
    letter = value.strip().rstrip(".").upper()[:1]
    return letter if letter.isalpha() else None


class UnnamedFinder:
    """Reads the described sentences and rewrites the unnamed figurants."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        reader: UnnamedReader | None = None,
        on_stage: Callable[[str], None] = lambda _stage: None,
    ) -> None:
        self._session_factory = session_factory
        self._reader = reader
        self._on_stage = on_stage

    def _ask(
        self, items: Mapping[int, UnnamedItem], keys: Mapping[int, str], event: str
    ) -> AskResult[UnnamedAnswer]:
        return ask_missing(
            self._session_factory,
            UNNAMED_CACHE,
            self._reader,
            items,
            keys,
            {position: input_hash(item.sentence, item.context) for position, item in items.items()},
            batch_size=BATCH_SIZE,
            concurrency=CONCURRENCY,
            value_of=lambda answer: answer.model_dump_json(),
            explanation_of=lambda answer: answer.explanation,
            on_stage=self._on_stage,
            event=event,
            errors=(UnnamedReaderError,),
        )

    def run(self) -> UnnamedResult:
        self._on_stage("reading")
        with self._session_factory() as session:
            sentences = [
                sentence
                for article_id, text_, published_at in session.execute(
                    _PUBLICATIONS, {"criminal": list(CRIMINAL_EVENT_TYPES)}
                ).all()
                for sentence in described_sentences(article_id, text_ or "", published_at)
            ]
        # One sentence once, however many times a text repeats it.
        by_key = {sentence.key: sentence for sentence in sentences}
        ordered = list(by_key.values())
        items = {
            position: UnnamedItem(position, sentence.text, sentence.context)
            for position, sentence in enumerate(ordered)
        }
        keys = {position: sentence.key for position, sentence in enumerate(ordered)}
        found = self._ask(items, keys, "unnamed")
        # Whoever the short reading would keep, read with the whole publication.
        wide_items = {
            position: UnnamedItem(position, ordered[position].text, ordered[position].wide)
            for position, answer in found.answers.items()
            if _kept(answer)
        }
        # A publication no longer than the short context is the same question: cached.
        again = self._ask(wide_items, keys, "unnamed_wide")
        answers = {**found.answers, **again.answers}
        result = UnnamedResult(
            sentences=len(ordered),
            asked_now=found.asked + again.asked,
            cached=found.cached,
            failures=found.failures + again.failures,
            unasked=found.unasked + again.unasked,
            # One reader, one count of spending: the later reading holds both.
            cost_usd=round(max(found.cost_usd, again.cost_usd), 6),
        )
        with self._session_factory() as session:
            # A person's word: taken back from the set-aside, or decided on.
            held = set(session.scalars(select(UnnamedKeepRecord.key))) | set(
                session.scalars(select(UnnamedIdentityResolutionRecord.figurant_key))
            )
        found_rows: list[tuple[dict[str, Any], bool]] = []
        screened: list[dict[str, Any]] = []
        for position, sentence in enumerate(ordered):
            answer = answers.get(position)
            if answer is None:
                continue
            kept_by_word = sentence.key in held
            if not kept_by_word and not answer.is_case:
                result.not_cases += 1
                continue
            if not kept_by_word and answer.named:
                result.named += 1
                continue
            row = {
                "key": sentence.key,
                "article_id": sentence.article_id,
                "start_offset": sentence.start,
                "end_offset": sentence.end,
                "quote": sentence.text,
                "age": answer.age,
                "gender": None if answer.gender == "unknown" else answer.gender,
                "place": answer.place.strip(),
                "initial": _initial(answer.initial),
                "articles": sorted(
                    {article.strip() for article in answer.articles if article.strip()}
                ),
                "event_type": answer.event,
                "explanation": answer.explanation,
                "published_at": sentence.published_at,
            }
            if kept_by_word:
                found_rows.append((row, True))
            elif common_crime_only(answer.articles):
                result.common_crime += 1
                screened.append({**row, "reason": COMMON_CRIME})
            # Only by the whole publication: a failed second reading drops nobody.
            elif position in again.answers and answer.motive == "criminal":
                result.criminal_motive += 1
                screened.append({**row, "reason": CRIMINAL_MOTIVE})
            else:
                found_rows.append((row, False))
        # One publication tells of a person in several sentences: one card, the first.
        # Those a person spoke of all stay, and stand for their own.
        told = {_person(row) for row, kept_by_word in found_rows if kept_by_word}
        rows: list[dict[str, Any]] = []
        for row, kept_by_word in found_rows:
            person = _person(row)
            if kept_by_word or person is None or person not in told:
                told.add(person)
                rows.append(row)
            else:
                result.duplicates += 1
                screened.append({**row, "reason": DUPLICATE})
        result.unnamed = len(rows)
        self._on_stage("writing")
        with self._session_factory.begin() as session:
            session.execute(delete(UnnamedFigurantRecord))
            session.execute(delete(UnnamedScreenedRecord))
            if rows:
                session.execute(insert(UnnamedFigurantRecord), rows)
            if screened:
                session.execute(insert(UnnamedScreenedRecord), screened)
        logger.info("event=unnamed_found %s", result)
        return result


def forget(session: Session, figurant_key: str, candidate: str) -> None:
    """Take a word on a candidate back: neither «это он» nor «не он»."""
    session.execute(
        delete(UnnamedDecisionRecord).where(
            UnnamedDecisionRecord.figurant_key == figurant_key,
            UnnamedDecisionRecord.candidate == candidate,
        )
    )


def decide(session: Session, figurant_key: str, candidate: str, decision: str) -> None:
    """A person's word that a list entry is not this unnamed figurant."""
    if decision != DIFFERENT:
        raise ValueError(f"unknown decision: {decision}")
    statement = pg_insert(UnnamedDecisionRecord).values(
        figurant_key=figurant_key, candidate=candidate, decision=decision
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["figurant_key", "candidate"],
            set_={"decision": decision, "decided_at": text("now()")},
        )
    )


def resolve_identity(
    session: Session,
    figurant_key: str,
    resolution: str,
    *,
    normalized_name: str | None = None,
    existing_person_key: str | None = None,
    rf_name: str | None = None,
    rf_birth_date: date | None = None,
) -> None:
    """A person's stable identification of an unnamed figurant."""
    if resolution not in RESOLUTIONS:
        raise ValueError(f"unknown resolution: {resolution}")
    if resolution == RF_ENTRY and (not normalized_name or not rf_name or rf_birth_date is None):
        raise ValueError("rf_entry needs a name and birth date")
    if resolution == EXISTING_PERSON and not existing_person_key:
        raise ValueError("existing_person needs a person key")
    if resolution == SUPPLIED_NAME and not normalized_name:
        raise ValueError("supplied_name needs a name")
    statement = pg_insert(UnnamedIdentityResolutionRecord).values(
        figurant_key=figurant_key,
        resolution=resolution,
        normalized_name=normalized_name,
        existing_person_key=existing_person_key,
        rf_name=rf_name,
        rf_birth_date=rf_birth_date,
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["figurant_key"],
            set_={
                "resolution": resolution,
                "normalized_name": normalized_name,
                "existing_person_key": existing_person_key,
                "rf_name": rf_name,
                "rf_birth_date": rf_birth_date,
                "source": "manual",
                "decided_at": text("now()"),
            },
        )
    )


def clear_resolution(session: Session, figurant_key: str) -> None:
    session.execute(
        delete(UnnamedIdentityResolutionRecord).where(
            UnnamedIdentityResolutionRecord.figurant_key == figurant_key
        )
    )


def keep(session: Session, figurant_key: str) -> bool:
    """A person takes back a figurant the search set aside: on a card at once, and through
    every search to come. False when the search set no such figurant aside."""
    screened = session.scalar(
        select(UnnamedScreenedRecord).where(UnnamedScreenedRecord.key == figurant_key)
    )
    if screened is None:
        return False
    session.execute(pg_insert(UnnamedKeepRecord).values(key=figurant_key).on_conflict_do_nothing())
    session.add(
        UnnamedFigurantRecord(
            **{
                column.name: getattr(screened, column.name)
                for column in UnnamedFigurantRecord.__table__.columns
                if column.name != "id"
            }
        )
    )
    session.delete(screened)
    return True
