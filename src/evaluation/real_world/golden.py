"""Golden dataset schema and validation for real-world validation.

Layout (committed, human-reviewable):

    evaluation/real_world/golden/
        VERSION.json        dataset_version + golden_dataset_hash (must match the content)
        persons.json        GoldenPerson registry with person-level expectations
        articles/<case>.json  one GoldenArticle per annotated publication

Identity is never a database id: persons are `golden_person_id`, mentions and
events are character spans of the parsed article text. Articles keep only
short excerpts around the annotated spans, never the whole publication.

An annotation produced by the agent or copied from system output is DRAFT.
Only a human sets VERIFIED (`real-world-golden verify`), and only VERIFIED
cases count for the locked test split.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evaluation.real_world.models import (
    DEFAULT_DATA_DIR,
    CorpusManifest,
    TemporalPeriod,
    article_key,
    sha256_json,
    sha256_text,
)
from extraction.models import EventType
from persecution.models import PersecutionClassificationStatus

DEFAULT_GOLDEN_DIR = DEFAULT_DATA_DIR / "golden"
EXCERPT_CONTEXT_CHARS = 120
SPLIT_SHARES = (("dev", 0.6), ("validation", 0.2), ("test", 0.2))


class AnnotationStatus(StrEnum):
    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"


class AnnotationOrigin(StrEnum):
    # Written by an AI agent from the article text: never ground truth by itself.
    AGENT_DRAFT = "agent_draft"
    # Prefilled from court-monitor output: must be checked against the text.
    SYSTEM_OUTPUT = "system_output"
    HUMAN = "human"


class GoldenSplit(StrEnum):
    DEV = "dev"
    VALIDATION = "validation"
    TEST = "test"


class DangerousKind(StrEnum):
    """False statements about a real person; counted separately, never averaged away."""

    FALSE_PERSON_LINK = "false_person_link"
    FALSE_POLITICAL_CLASSIFICATION = "false_political_classification"
    FALSE_RF_NOT_MATCHED = "false_rf_not_matched"
    UNSUPPORTED_ABSENCE_CLAIM = "unsupported_absence_claim"
    CROSS_PERSON_EVIDENCE = "cross_person_evidence"
    UNSUPPORTED_EVENT_CLAIM = "unsupported_event_claim"
    FALSE_ACTIONABLE_CANDIDATE = "false_actionable_candidate"


class RosfinExpectedStatus(StrEnum):
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    AMBIGUOUS = "ambiguous"
    NEEDS_REVIEW = "needs_review"
    INSUFFICIENT_DATA = "insufficient_data"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TextSpan(_Strict):
    """A character span of the parsed article text (`ParsedArticle.text`)."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _length(self) -> TextSpan:
        if self.end - self.start != len(self.text):
            raise ValueError(
                f"span {self.start}..{self.end} does not have the length of {self.text!r}"
            )
        return self

    def overlaps(self, start: int, end: int) -> bool:
        return self.start < end and start < self.end


class GoldenMention(TextSpan):
    golden_person_id: str


class GoldenEvent(_Strict):
    event_id: str
    event_type: EventType
    # Empty for an event about no named person (an organization ban, an anonymous detention).
    person_ids: list[str] = Field(default_factory=list)
    evidence: TextSpan
    # Explicit: one event of several people (a group detention, a joint sentence).
    shared: bool = False
    # A reference to something that happened before this publication, not a new event.
    historical: bool = False

    @model_validator(mode="after")
    def _shared(self) -> GoldenEvent:
        if self.shared != (len(set(self.person_ids)) > 1):
            raise ValueError(
                f"event {self.event_id}: shared must be true exactly for several persons"
            )
        return self


class EvidenceRef(_Strict):
    case_id: str
    span: TextSpan


class GoldenArticle(_Strict):
    case_id: str
    source: str
    external_id: str
    canonical_url: str
    published_at: datetime
    content_hash: str
    split: GoldenSplit
    annotation_status: AnnotationStatus = AnnotationStatus.DRAFT
    annotation_origin: AnnotationOrigin = AnnotationOrigin.AGENT_DRAFT
    annotators: list[str] = Field(default_factory=list)
    verified_by: str | None = None
    verified_at: datetime | None = None
    # Hard-negative and coverage categories this article demonstrates.
    tags: list[str] = Field(default_factory=list)
    excerpts: list[TextSpan] = Field(default_factory=list)
    mentions: list[GoldenMention] = Field(default_factory=list)
    events: list[GoldenEvent] = Field(default_factory=list)
    # Open questions for the human reviewer (ambiguous reading, uncertain status).
    disputed: list[str] = Field(default_factory=list)
    notes: str | None = None
    known_limitation: str | None = None
    known_limitation_kinds: list[DangerousKind] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return article_key(self.source, self.external_id)

    @model_validator(mode="after")
    def _internal(self) -> GoldenArticle:
        problems: list[str] = []
        if self.annotation_status is AnnotationStatus.VERIFIED and (
            self.verified_by is None or self.verified_at is None
        ):
            problems.append("VERIFIED needs verified_by and verified_at")
        if self.annotation_status is AnnotationStatus.DRAFT and self.verified_by is not None:
            problems.append("a DRAFT cannot carry verified_by")
        if self.known_limitation_kinds and self.known_limitation is None:
            problems.append("known_limitation_kinds without known_limitation")
        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            problems.append("event ids must be unique within the article")
        mention_spans = [(m.start, m.end) for m in self.mentions]
        if len(mention_spans) != len(set(mention_spans)):
            problems.append("duplicate mention spans")
        spans: list[TextSpan] = [*self.mentions, *(event.evidence for event in self.events)]
        for span in spans:
            if not _inside_excerpt(span, self.excerpts):
                problems.append(
                    f"span {span.start}..{span.end} {span.text!r} is not covered by an excerpt"
                )
        if problems:
            raise ValueError(f"{self.case_id}: " + "; ".join(problems))
        return self


def _inside_excerpt(span: TextSpan, excerpts: Sequence[TextSpan]) -> bool:
    for excerpt in excerpts:
        if excerpt.start <= span.start and span.end <= excerpt.end:
            offset = span.start - excerpt.start
            return excerpt.text[offset : offset + len(span.text)] == span.text
    return False


class GoldenPersecutionDecision(_Strict):
    expected_status: PersecutionClassificationStatus
    # Statuses a careful human would also accept (e.g. needs_review for a borderline case).
    acceptable_statuses: list[PersecutionClassificationStatus] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    notes: str | None = None

    @property
    def accepted(self) -> set[str]:
        return {self.expected_status.value, *(status.value for status in self.acceptable_statuses)}


class GoldenRosfinDecision(_Strict):
    # Id of the evaluation snapshot this expectation is relative to.
    snapshot: str
    expected_status: RosfinExpectedStatus
    # Snapshot entry name when the person is listed (matched / ambiguous).
    expected_entry: str | None = None
    # A review outcome (needs_review / ambiguous) is acceptable instead of the expected one.
    acceptable_review: bool = False

    @property
    def truly_listed(self) -> bool:
        return self.expected_entry is not None


class GoldenCandidateDecision(_Strict):
    """The main product question: POLITICAL + RF NOT_MATCHED."""

    expected_main_candidate: bool
    expected_first_finding_period: TemporalPeriod | None = None


class GoldenPerson(_Strict):
    golden_person_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    same_as: list[str] = Field(default_factory=list)
    distinct_from: list[str] = Field(default_factory=list)
    persecution: GoldenPersecutionDecision | None = None
    rosfinmonitoring: GoldenRosfinDecision | None = None
    candidate: GoldenCandidateDecision | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _internal(self) -> GoldenPerson:
        me = self.golden_person_id
        if me in self.same_as or me in self.distinct_from:
            raise ValueError(f"{me}: same_as/distinct_from must not reference itself")
        both = set(self.same_as) & set(self.distinct_from)
        if both:
            raise ValueError(f"{me}: {sorted(both)} is both same_as and distinct_from")
        if self.candidate is not None and self.candidate.expected_main_candidate:
            if self.persecution is None or self.rosfinmonitoring is None:
                raise ValueError(
                    f"{me}: an expected main candidate needs persecution and RF annotations"
                )
            if (
                self.persecution.expected_status is not PersecutionClassificationStatus.POLITICAL
                or self.rosfinmonitoring.expected_status is not RosfinExpectedStatus.NOT_MATCHED
            ):
                raise ValueError(f"{me}: a main candidate must be POLITICAL and RF NOT_MATCHED")
        return self


class ClaimExpectation(_Strict):
    claim_type: str
    golden_person_id: str
    value: str


class GoldenReportExpectation(_Strict):
    required_claims: list[ClaimExpectation] = Field(default_factory=list)
    forbidden_claims: list[ClaimExpectation] = Field(default_factory=list)
    required_evidence: list[EvidenceRef] = Field(default_factory=list)


class GoldenVersion(_Strict):
    dataset_version: str
    golden_dataset_hash: str
    rf_snapshot_id: str
    rf_snapshot_path: str


class GoldenDataset(_Strict):
    version: GoldenVersion
    persons: list[GoldenPerson]
    articles: list[GoldenArticle]

    @model_validator(mode="after")
    def _references(self) -> GoldenDataset:
        problems = reference_problems(self)
        if problems:
            raise ValueError("golden dataset is inconsistent:\n- " + "\n- ".join(problems))
        return self

    def person(self, golden_person_id: str) -> GoldenPerson:
        return self._persons()[golden_person_id]

    def _persons(self) -> dict[str, GoldenPerson]:
        return {person.golden_person_id: person for person in self.persons}

    def content_hash(self) -> str:
        return golden_content_hash(self.persons, self.articles)

    def articles_of(self, golden_person_id: str) -> list[GoldenArticle]:
        return [
            article
            for article in self.articles
            if any(m.golden_person_id == golden_person_id for m in article.mentions)
            or any(golden_person_id in event.person_ids for event in article.events)
        ]

    def person_split(self, golden_person_id: str) -> GoldenSplit | None:
        splits = {article.split for article in self.articles_of(golden_person_id)}
        return next(iter(splits)) if len(splits) == 1 else None

    def person_verified(self, golden_person_id: str) -> bool:
        articles = self.articles_of(golden_person_id)
        return bool(articles) and all(
            article.annotation_status is AnnotationStatus.VERIFIED for article in articles
        )

    def select(self, split: GoldenSplit | None, *, verified_only: bool) -> GoldenDataset:
        """Articles of one split (all when None) and the persons they mention."""
        articles = [
            article
            for article in self.articles
            if (split is None or article.split is split)
            and (not verified_only or article.annotation_status is AnnotationStatus.VERIFIED)
        ]
        mentioned = {m.golden_person_id for a in articles for m in a.mentions} | {
            pid for a in articles for e in a.events for pid in e.person_ids
        }
        persons = [p for p in self.persons if p.golden_person_id in mentioned]
        # Keep the reference closure valid: drop same_as/distinct_from to persons outside.
        kept = {p.golden_person_id for p in persons}
        persons = [
            p.model_copy(
                update={
                    "same_as": [x for x in p.same_as if x in kept],
                    "distinct_from": [x for x in p.distinct_from if x in kept],
                }
            )
            for p in persons
        ]
        return GoldenDataset.model_construct(
            version=self.version, persons=persons, articles=articles
        )


def golden_content_hash(persons: Iterable[GoldenPerson], articles: Iterable[GoldenArticle]) -> str:
    return sha256_json(
        {
            "persons": [
                p.model_dump(mode="json") for p in sorted(persons, key=lambda p: p.golden_person_id)
            ],
            "articles": [
                a.model_dump(mode="json") for a in sorted(articles, key=lambda a: a.case_id)
            ],
        }
    )


def reference_problems(dataset: GoldenDataset) -> list[str]:
    problems: list[str] = []
    person_ids = [person.golden_person_id for person in dataset.persons]
    duplicates = sorted({pid for pid in person_ids if person_ids.count(pid) > 1})
    if duplicates:
        problems.append(f"duplicate golden_person_id {duplicates}")
    case_ids = [article.case_id for article in dataset.articles]
    duplicate_cases = sorted({cid for cid in case_ids if case_ids.count(cid) > 1})
    if duplicate_cases:
        problems.append(f"duplicate case_id {duplicate_cases}")
    keys = [article.key for article in dataset.articles]
    duplicate_keys = sorted({key for key in keys if keys.count(key) > 1})
    if duplicate_keys:
        problems.append(f"article annotated twice {duplicate_keys}")

    known = set(person_ids)
    by_id = {person.golden_person_id: person for person in dataset.persons}
    for person in dataset.persons:
        for target in [*person.same_as, *person.distinct_from]:
            if target not in known:
                problems.append(
                    f"{person.golden_person_id}: unknown same_as/distinct_from {target}"
                )
        for target in person.same_as:
            other = by_id.get(target)
            if other is not None and person.golden_person_id in other.distinct_from:
                problems.append(
                    f"{person.golden_person_id} same_as {target}, but {target} distinct_from it"
                )
    articles = {article.case_id: article for article in dataset.articles}
    for article in dataset.articles:
        for mention in article.mentions:
            if mention.golden_person_id not in known:
                problems.append(f"{article.case_id}: mention of unknown {mention.golden_person_id}")
        for event in article.events:
            for pid in event.person_ids:
                if pid not in known:
                    problems.append(f"{article.case_id}/{event.event_id}: unknown person {pid}")
                elif not any(m.golden_person_id == pid for m in article.mentions):
                    problems.append(
                        f"{article.case_id}/{event.event_id}: {pid} has no mention in the article"
                    )
    for person in dataset.persons:
        evidence = person.persecution.evidence if person.persecution else []
        for ref in evidence:
            source_article = articles.get(ref.case_id)
            if source_article is None:
                problems.append(
                    f"{person.golden_person_id}: evidence in unknown case {ref.case_id}"
                )
            elif not _inside_excerpt(ref.span, source_article.excerpts):
                problems.append(
                    f"{person.golden_person_id}: evidence {ref.span.text!r} not in {ref.case_id} excerpts"
                )
        if (
            person.rosfinmonitoring is not None
            and person.rosfinmonitoring.snapshot != dataset.version.rf_snapshot_id
        ):
            problems.append(
                f"{person.golden_person_id}: RF expectation for snapshot "
                f"{person.rosfinmonitoring.snapshot}, dataset uses {dataset.version.rf_snapshot_id}"
            )
        splits = {a.split for a in dataset.articles_of(person.golden_person_id)}
        if len(splits) > 1:
            problems.append(
                f"split leakage: {person.golden_person_id} appears in {sorted(s.value for s in splits)}"
            )
    return problems


# -- validation against the corpus ------------------------------------------------------


def text_problems(dataset: GoldenDataset, texts: Mapping[str, str]) -> list[str]:
    """Offsets and hashes against the full parsed texts (from the local corpus cache)."""
    problems: list[str] = []
    for article in dataset.articles:
        text = texts.get(article.key)
        if text is None:
            problems.append(f"{article.case_id}: article text not available ({article.key})")
            continue
        if sha256_text(text) != article.content_hash:
            problems.append(f"{article.case_id}: content hash differs from the parsed text")
        spans: list[TextSpan] = [
            *article.excerpts,
            *article.mentions,
            *(event.evidence for event in article.events),
        ]
        for span in spans:
            if text[span.start : span.end] != span.text:
                problems.append(
                    f"{article.case_id}: offset {span.start}..{span.end} is "
                    f"{text[span.start : span.end]!r}, annotated {span.text!r}"
                )
    return problems


def manifest_problems(dataset: GoldenDataset, manifest: CorpusManifest) -> list[str]:
    """Golden articles belong to the corpus; one duplicate story never spans two splits."""
    problems: list[str] = []
    by_key = manifest.by_key()
    group_splits: dict[str, set[GoldenSplit]] = defaultdict(set)
    for article in dataset.articles:
        entry = by_key.get(article.key)
        if entry is None:
            problems.append(f"{article.case_id}: {article.key} is not in the corpus manifest")
            continue
        if entry.content_hash != article.content_hash:
            problems.append(f"{article.case_id}: content hash differs from the manifest")
        group_splits[entry.duplicate_group].add(article.split)
    for group, splits in sorted(group_splits.items()):
        if len(splits) > 1:
            problems.append(
                f"split leakage: duplicate story {group} in {sorted(s.value for s in splits)}"
            )
    return problems


def assign_splits(groups: Mapping[str, str], seed: int) -> dict[str, GoldenSplit]:
    """Deterministic 60/20/20 split over groups (duplicate stories, shared persons).

    `groups` maps article key to group key; all members of a group share a split.
    Groups are ordered by a seeded hash and filled by article count.
    """
    members: dict[str, list[str]] = defaultdict(list)
    for key, group in groups.items():
        members[group].append(key)
    ordered = sorted(members, key=lambda group: sha256_text(f"{seed}:{group}"))
    total = len(groups)
    split: dict[str, GoldenSplit] = {}
    assigned = 0
    for group in ordered:
        share = assigned / total if total else 0.0
        cumulative = 0.0
        chosen = GoldenSplit.TEST
        for name, fraction in SPLIT_SHARES:
            cumulative += fraction
            if share < cumulative - 1e-9:
                chosen = GoldenSplit(name)
                break
        for key in members[group]:
            split[key] = chosen
        assigned += len(members[group])
    return split


# -- files ------------------------------------------------------------------------------


def load_golden_dataset(directory: Path = DEFAULT_GOLDEN_DIR) -> GoldenDataset:
    version = GoldenVersion.model_validate_json((directory / "VERSION.json").read_text("utf-8"))
    persons = [
        GoldenPerson.model_validate(item)
        for item in json.loads((directory / "persons.json").read_text("utf-8"))
    ]
    articles = [
        GoldenArticle.model_validate_json(path.read_text("utf-8"))
        for path in sorted((directory / "articles").glob("*.json"))
    ]
    return GoldenDataset(version=version, persons=persons, articles=articles)


def write_golden_dataset(dataset: GoldenDataset, directory: Path = DEFAULT_GOLDEN_DIR) -> None:
    (directory / "articles").mkdir(parents=True, exist_ok=True)
    version = dataset.version.model_copy(update={"golden_dataset_hash": dataset.content_hash()})
    (directory / "VERSION.json").write_text(version.model_dump_json(indent=2) + "\n", "utf-8")
    persons = [p.model_dump(mode="json", exclude_defaults=True) for p in dataset.persons]
    (directory / "persons.json").write_text(
        json.dumps(persons, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    wanted = set()
    for article in dataset.articles:
        path = directory / "articles" / f"{article.case_id}.json"
        wanted.add(path.name)
        path.write_text(article.model_dump_json(indent=2) + "\n", "utf-8")
    for stale in (directory / "articles").glob("*.json"):
        if stale.name not in wanted:
            stale.unlink()


def excerpt_windows(text: str, spans: Iterable[tuple[int, int]]) -> list[TextSpan]:
    """Merged context windows around spans: the minimal text kept in Git."""
    windows: list[list[int]] = []
    for start, end in sorted(spans):
        window_start = max(0, start - EXCERPT_CONTEXT_CHARS)
        window_end = min(len(text), end + EXCERPT_CONTEXT_CHARS)
        if windows and window_start <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], window_end)
        else:
            windows.append([window_start, window_end])
    return [TextSpan(start=s, end=e, text=text[s:e]) for s, e in windows]
