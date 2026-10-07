"""Who among the people other publications name an unnamed figurant may be."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import EntityMentionRecord, UnnamedFigurantRecord
from entities.collector import EntityCollector
from entities.named_candidates import named_candidates, person_candidate_key
from entities.unnamed import DIFFERENT, EXISTING_PERSON, SAME, decide, forget, resolve_identity

COURT = "Суд вынес приговор 53-летней жительнице Якутии."
KEY = "u" * 64


class _Told(TypedDict, total=False):
    """What a publication tells beyond the name and the sentence."""

    day: int
    event: str
    article: str | None


def _named(
    session: Session,
    seed: DatabaseSeeder,
    source: int,
    name: str,
    sentence: str,
    *,
    day: int = 2,
    event: str = "sentence",
    article: str | None = "205.5",
) -> None:
    """A publication that names its person: `sentence` holds the name as written."""
    first, last = name.split()
    _, run = seed.article(
        source,
        external_id=f"{name}-{day}-{event}",
        title=f"О деле: {name}",
        text=sentence,
        published_at=datetime(2026, 10, day, 12, tzinfo=UTC),
    )
    mention = seed.mention(run, name, person_id=None)
    session.get_one(EntityMentionRecord, mention).normalized_data = {
        "first_name": first,
        "last_name": last,
        "patronymic": None,
    }
    links = [(mention, "target")]
    if article:
        law = seed.mention(
            run, f"ст. {article} УК РФ", person_id=None, entity_type="legal_reference"
        )
        session.get_one(EntityMentionRecord, law).normalized_data = {
            "code": "УК РФ",
            "article": article,
            "part": None,
            "clause": None,
        }
        links.append((law, "legal_basis"))
    seed.event(run, sentence, event_type=event, event_date=None, links=[], entity_links=links)


def _seed(session_factory: sessionmaker[Session], **told: Any) -> UnnamedFigurantRecord:
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        court = seed.source("Суд", "https://court.example.test")
        agency = seed.source("Прокуратура <b>", "https://proc.example.test")
        article, _ = seed.article(
            court,
            external_id="unnamed",
            title="Приговор",
            text=COURT,
            published_at=datetime(2026, 10, 2, 9, tzinfo=UTC),
        )
        people: list[tuple[str, str, _Told]] = [
            ("Мария Ханова", "В Якутии осуждена Мария Ханова, 53 года, по ст. 205.5 УК РФ.", {}),
            # The same place, days and event — and nothing of the person fits.
            (
                "Ольга Другая",
                "В Якутии осуждена Ольга Другая, 40 лет, по ст. 275 УК РФ.",
                {"article": "275"},
            ),
            # The age beside the name, a man: the sex contradicts.
            ("Пётр Мужчинов", "В Якутии осуждён Пётр Мужчинов, 53 года, по ст. 205.5 УК РФ.", {}),
            # Her age and article, but arrested, not sentenced; in another place; a week on.
            (
                "Анна Арестова",
                "В Якутии арестована Анна Арестова, 53 года, по ст. 205.5 УК РФ.",
                {"event": "arrest"},
            ),
            ("Вера Тульская", "В Туле осуждена Вера Тульская, 53 года, по ст. 205.5 УК РФ.", {}),
            (
                "Нина Поздняя",
                "В Якутии осуждена Нина Поздняя, 53 года, по ст. 205.5 УК РФ.",
                {"day": 9},
            ),
            # The age beside the name and no article: between the two.
            ("Инна Третья", "В Якутии осуждена Инна Третья, 53 года.", {"article": None}),
            # Ханова again the next day, with no age: one person is one row, and the
            # publication that tells the most of her is the one shown.
            (
                "Мария Ханова",
                "Позже в Якутии Мария Ханова осуждена по ст. 205.5 УК РФ.",
                {"day": 3},
            ),
            # Three days on by the calendar, though more than 72 hours after the court's
            # morning release: the days are whole days.
            ("Лена Пятая", "В Якутии осуждена Лена Пятая, 53 года.", {"day": 5, "article": None}),
            # The day before the court's release: the days are counted both ways.
            ("Рая Ранняя", "В Якутии осуждена Рая Ранняя, 53 года.", {"day": 1, "article": None}),
            # No age told, the same article: a weaker sign, shown after the age.
            ("Зоя Статейная", "В Якутии осуждена Зоя Статейная по ст. 205.5 УК РФ.", {}),
        ]
        for name, sentence, extra in people:
            _named(session, seed, agency, name, sentence, **extra)
        figurant = UnnamedFigurantRecord(
            **{
                "key": KEY,
                "article_id": article,
                "start_offset": 0,
                "end_offset": len(COURT),
                "quote": COURT,
                "age": 53,
                "gender": "female",
                "place": "Якутия",
                "initial": None,
                "articles": ["205.5"],
                "event_type": "sentence",
                "explanation": "приговор",
                "published_at": datetime(2026, 10, 2, 9, tzinfo=UTC),
                **told,
            }
        )
        session.add(figurant)
        session.commit()
    EntityCollector(session_factory).run()
    with session_factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO entity_group_roles (group_id, role, kind, method, reason, quote) "
                "SELECT id, CASE WHEN name LIKE 'Ольга%' THEN 'mentioned' ELSE 'figurant' END, "
                "'accused', 'model', '', '' FROM entity_groups"
            )
        )
        return session.get_one(UnnamedFigurantRecord, figurant.id)


def _names(found: Any) -> list[str]:
    return [item.name for item in found]


def test_the_person_another_publication_names_with_the_same_age_and_article(
    session_factory: sessionmaker[Session],
) -> None:
    figurant = _seed(session_factory)

    with session_factory() as session:
        found = named_candidates(session, figurant)

    # The age beside the name and the article; the age; the article alone.
    assert _names(found) == [
        "Мария Ханова",
        "Инна Третья",
        "Лена Пятая",
        "Рая Ранняя",
        "Зоя Статейная",
    ]
    first = found[0]
    assert first.reasons == [
        "Прокуратура <b>, 02.10.2026: то же место, та же стадия дела",
        "возраст 53 назван в тексте около имени",
        "та же статья: 205.5",
    ]
    assert (first.title, first.key) == ("О деле: Мария Ханова", "мария ханова")
    # The link leads to the name in that publication's text.
    assert (first.start, first.end) == (18, 30)
    assert found[4].reasons[1:] == ["та же статья: 205.5"]


def test_what_the_text_does_not_tell_does_not_narrow_and_no_place_finds_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    figurant = _seed(session_factory, gender=None, articles=[], initial="М")

    with session_factory() as session:
        found = named_candidates(session, figurant)
        figurant.event_type = "other"
        any_event = named_candidates(session, figurant)
        figurant.place = ""
        nowhere = named_candidates(session, figurant)

    # No sex told: the man of 53 fits by the age, and by the initial as well.
    assert _names(found) == [
        "Инна Третья",
        "Лена Пятая",
        "Мария Ханова",
        "Пётр Мужчинов",
        "Рая Ранняя",
    ]
    assert found[3].reasons[1:] == [
        "возраст 53 назван в тексте около имени",
        "в имени есть слово на «М»",
    ]
    # An event the model could not name is no stage to search by: the arrested one too —
    # and the reason does not claim a stage that was not compared.
    arrested = next(item for item in any_event if item.name == "Анна Арестова")
    assert arrested.reasons[0] == (
        "Прокуратура <b>, 02.10.2026: то же место, вид события не сравнивался — он не определён"
    )
    assert nowhere == []


def test_a_person_s_word_on_a_named_candidate(session_factory: sessionmaker[Session]) -> None:
    figurant = _seed(session_factory)

    with session_factory.begin() as session:
        decide(session, KEY, person_candidate_key("мария ханова"), DIFFERENT)
        resolve_identity(
            session,
            KEY,
            EXISTING_PERSON,
            normalized_name="Зоя Статейная",
            existing_person_key="зоя статейная",
        )
    with session_factory() as session:
        found = named_candidates(session, figurant)
        one = named_candidates(session, figurant, shown=1)

    # The confirmed first, the rejected last — whatever tells the most of whom.
    assert [(item.name, item.decision) for item in found] == [
        ("Зоя Статейная", SAME),
        ("Инна Третья", None),
        ("Лена Пятая", None),
        ("Рая Ранняя", None),
        ("Мария Ханова", DIFFERENT),
    ]
    assert _names(one) == ["Зоя Статейная"]

    # A word taken back is that one word: the other stays.
    with session_factory.begin() as session:
        decide(session, KEY, person_candidate_key("инна третья"), DIFFERENT)
        forget(session, KEY, person_candidate_key("мария ханова"))
    with session_factory() as session:
        after = {item.name: item.decision for item in named_candidates(session, figurant)}
    assert (after["Мария Ханова"], after["Инна Третья"]) == (None, DIFFERENT)


def test_a_case_before_the_trial_is_one_stage_and_a_sentence_another(
    session_factory: sessionmaker[Session],
) -> None:
    """What one source calls a detention another calls an arrest: one stage. A sentence
    is found by a sentence only."""
    figurant = _seed(session_factory, event_type="detention")

    with session_factory() as session:
        detained = named_candidates(session, figurant)
        figurant.event_type = "sentence"
        sentenced = named_candidates(session, figurant)

    assert _names(detained) == ["Анна Арестова"]
    assert detained[0].reasons[0].endswith("то же место, та же стадия дела")
    assert "Анна Арестова" not in _names(sentenced) and "Мария Ханова" in _names(sentenced)


def test_the_publication_itself_is_no_other_publication(
    session_factory: sessionmaker[Session],
) -> None:
    """A person named in the very publication the unnamed one stands in is not found by
    it: the model has read that text already, and said they are not the same."""
    figurant = _seed(session_factory)
    with session_factory() as session:
        hers = session.scalar(
            text("SELECT id FROM parsed_articles WHERE title = 'О деле: Инна Третья'")
        )
        figurant.article_id = hers
        found = named_candidates(session, figurant)

    assert "Инна Третья" not in _names(found) and "Мария Ханова" in _names(found)


def test_an_age_is_a_number_of_its_own() -> None:
    from entities.named_candidates import _age_pattern

    age = _age_pattern(53)
    for told in ("Ханова, 53 года,", "53-летняя Ханова", "Хановой 53 лет", "53  -  лет"):
        assert age.search(told), told
    for other in ("153 года", "53 тысячи", "в 1953 году", "530 лет"):
        assert not age.search(other), other
