"""Which figurants' cases are political persecution, on PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import (
    AirtableKnownPersonRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityMentionRecord,
)
from entities.collector import EntityCollector
from entities.politics import (
    POLITICAL,
    PoliticsAnswer,
    PoliticsClassifierError,
    PoliticsFinder,
    PoliticsItem,
    Verdict,
    decide_politics,
    matched_answers,
    verdict_of,
)
from entities.rf_check import EntityRfCheck
from entities.roles import quotes_of
from monitoring.hold_reader import add_event

RF_PAGE = """<!doctype html><html>
<div class="panel-heading"><h4>Физические лица</h4></div>
<div class="panel-body"><ol><li>1. ОРЛОВ ОЛЕГ ИВАНОВИЧ*, 01.01.1980 г.р. , Г. ТВЕРЬ;</li></ol></div>
</html>""".encode()


def _person(session: Session, seed: DatabaseSeeder, run: int, surface: str, name: str) -> int:
    first, last = name.split()
    mention_id = seed.mention(run, surface, person_id=None)
    session.get_one(EntityMentionRecord, mention_id).normalized_data = {
        "first_name": first,
        "last_name": last,
        "patronymic": None,
    }
    return mention_id


def _seed(
    session_factory: sessionmaker[Session], extra: Sequence[tuple[str, str, str, str]] = ()
) -> None:
    """Петров: a political article. Беда: a sentence the rules cannot read. Смирнова: a
    «Мемориал» card. Сирош: a lawyer. Орлов: on the list, judged all the same."""
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        text_ = "Суд арестовал Ивана Петрова по ч. 2 ст. 205.2 УК РФ."
        _, run = seed.article(source, external_id="petrov", title="Арест", text=text_)
        petrov = _person(session, seed, run, "Ивана Петрова", "Иван Петров")
        law = seed.mention(
            run, "ч. 2 ст. 205.2 УК РФ", person_id=None, entity_type="legal_reference"
        )
        session.get_one(EntityMentionRecord, law).normalized_data = {
            "code": "УК РФ",
            "article": "205.2",
            "part": "2",
            "clause": None,
        }
        seed.event(
            run,
            text_,
            event_type="arrest",
            event_date=None,
            links=[],
            entity_links=[(petrov, "target"), (law, "legal_basis")],
        )
        # A charge under an article no rule settles (318: often the protests): the model
        # decides.
        text_ = "Суд осудил Александра Беду по ч. 1 ст. 318 УК РФ за удар полицейского."
        _, run = seed.article(source, external_id="beda", title="Приговор", text=text_)
        beda = _person(session, seed, run, "Александра Беду", "Александр Беда")
        murder = seed.mention(
            run, "ч. 1 ст. 318 УК РФ", person_id=None, entity_type="legal_reference"
        )
        session.get_one(EntityMentionRecord, murder).normalized_data = {
            "code": "УК РФ",
            "article": "318",
            "part": "1",
            "clause": None,
        }
        seed.event(
            run,
            text_,
            event_type="sentence",
            event_date=None,
            links=[],
            entity_links=[(beda, "target"), (murder, "legal_basis")],
        )
        for external_id, text_, surface, name in (
            ("sirosh", "Адвокат Фёдор Сирош обжалует приговор.", "Фёдор Сирош", "Фёдор Сирош"),
            ("orlov", "Суд арестовал Олега Орлова.", "Олега Орлова", "Олег Орлов"),
            (
                "card",
                (
                    "Анна Смирнова обвиняется. Проект «Поддержка политзаключённых. Мемориал» "
                    "внёс человека в реестр преследуемых: «Другие жертвы политических репрессий»."
                ),
                "Анна Смирнова",
                "Анна Смирнова",
            ),
            *extra,
        ):
            _, run = seed.article(source, external_id=external_id, title=external_id, text=text_)
            _person(session, seed, run, surface, name)
            seed.event(run, text_[:10], event_type="sentence", event_date=None, links=[])
        session.commit()
    EntityCollector(session_factory).run()
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE entity_groups SET name = 'Олег Иванович Орлов' WHERE name = 'Олег Орлов'")
        )
        for group_id, name in session.execute(select(EntityGroupRecord.id, EntityGroupRecord.name)):
            role = "mentioned" if name == "Фёдор Сирош" else "figurant"
            session.add(
                EntityGroupRoleRecord(
                    group_id=group_id, role=role, method="model", reason="", quote=""
                )
            )
    EntityRfCheck(session_factory, download=lambda: RF_PAGE).run()


class FakeClassifier:
    model = "fake-model"

    def __init__(self, verdicts: dict[str, str], *, fail: bool = False) -> None:
        self.verdicts = verdicts
        self.fail = fail
        self.asked: list[PoliticsItem] = []

    def classify(self, items: Sequence[PoliticsItem]) -> dict[int, PoliticsAnswer]:
        self.asked += items
        if self.fail:
            raise PoliticsClassifierError("provider down")
        return {
            item.id: PoliticsAnswer(
                id=item.id,
                source=item.name,
                verdict=self.verdicts[item.name],  # type: ignore[arg-type]
                explanation=f"так про {item.name}",
            )
            for item in items
        }


VERDICTS = {
    "Александр Беда": "criminal",
    "Анна Смирнова": "political",
    # On the list, and judged all the same.
    "Олег Иванович Орлов": "political",
}


def _verdicts(session_factory: sessionmaker[Session]) -> dict[str, tuple[str, str]]:
    with session_factory() as session:
        rows = session.execute(
            select(
                EntityGroupRecord.name,
                EntityGroupPoliticsRecord.verdict,
                EntityGroupPoliticsRecord.method,
            ).join(EntityGroupRecord, EntityGroupRecord.id == EntityGroupPoliticsRecord.group_id)
        ).all()
    return {name: (verdict, method) for name, verdict, method in rows}


def test_a_political_article_settles_it_and_a_model_reads_the_rest(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    classifier = FakeClassifier(VERDICTS)

    result = PoliticsFinder(session_factory, classifier=classifier).run()

    # Петров by the rules; Сирош is no figurant; Орлов on the list is judged too: the list
    # confirms who a person is, not which case.
    assert sorted(item.name for item in classifier.asked) == [
        "Александр Беда",
        "Анна Смирнова",
        "Олег Иванович Орлов",
    ]
    smirnova = next(item for item in classifier.asked if item.name == "Анна Смирнова")
    beda = next(item for item in classifier.asked if item.name == "Александр Беда")
    assert beda.articles == ("318",)
    assert smirnova.memorial == "Другие жертвы политических репрессий"
    assert _verdicts(session_factory) == {
        "Иван Петров": ("political", "article"),
        "Александр Беда": ("criminal", "model"),
        "Анна Смирнова": ("political", "model"),
        "Олег Иванович Орлов": ("political", "model"),
    }
    assert (result.figurants, result.political_rules, result.political_model) == (4, 1, 2)
    assert (result.criminal, result.unclear, result.asked_now) == (1, 0, 3)


def test_the_same_input_is_answered_from_the_cache(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS)).run()
    again = FakeClassifier(VERDICTS)

    result = PoliticsFinder(session_factory, classifier=again).run()

    assert again.asked == []
    assert (result.asked_now, result.cached, result.political_model) == (0, 3, 2)


def test_a_failed_model_leaves_unclear_never_political(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)

    result = PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS, fail=True)).run()

    assert (result.failures, result.unclear, result.political_model) == (3, 3, 0)
    assert _verdicts(session_factory)["Анна Смирнова"] == ("unclear", "model")


# What the case is about stands further from the name than a short quote reaches.
FAR = "за антивоенный пост"
DOLGOV = (
    "dolgov",
    f"Суд арестовал Глеба Долгова. {'Заседание шло долго. ' * 12}Его судят {FAR}.",
    "Глеба Долгова",
    "Глеб Долгов",
)
# A long publication whose short quote already tells: nothing to ask again. A common
# crime, since a political answer sticks to the person and would not be asked anyway.
THEFT = "за кражу"
BLIZKY = (
    "blizky",
    f"Суд арестовал Яна Близкого {THEFT}. {'Заседание шло долго. ' * 12}",
    "Яна Близкого",
    "Ян Близкий",
)


class ReadingClassifier:
    """Tells a case only where its quotes say what it is about; Беда it never can."""

    model = "fake-model"

    def __init__(self, *, wide_fails: bool = False) -> None:
        self.wide_fails = wide_fails
        self.asked: list[list[PoliticsItem]] = []

    def classify(self, items: Sequence[PoliticsItem]) -> dict[int, PoliticsAnswer]:
        self.asked.append(list(items))
        if self.wide_fails and len(self.asked) > 1:
            raise PoliticsClassifierError("provider down")

        def verdict(item: PoliticsItem) -> Verdict:
            text_ = " ".join(item.quotes)
            return "political" if FAR in text_ else "criminal" if THEFT in text_ else "unknown"

        return {
            item.id: PoliticsAnswer(
                id=item.id,
                source=item.name,
                verdict=verdict(item),
                explanation=f"по {len(' '.join(item.quotes))} знакам",
            )
            for item in items
        }


def _row(session_factory: sessionmaker[Session], name: str) -> tuple[str, str, str, str]:
    with session_factory() as session:
        row = session.execute(
            select(
                EntityGroupPoliticsRecord.verdict,
                EntityGroupPoliticsRecord.method,
                EntityGroupPoliticsRecord.reason,
                EntityGroupPoliticsRecord.quote,
            )
            .join(EntityGroupRecord, EntityGroupRecord.id == EntityGroupPoliticsRecord.group_id)
            .where(EntityGroupRecord.name == name)
        ).one()
    return row.verdict, row.method, row.reason, row.quote


def test_what_short_quotes_do_not_tell_is_asked_again_with_wide_ones(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory, [DOLGOV, BLIZKY])
    classifier = ReadingClassifier()

    result = PoliticsFinder(session_factory, classifier=classifier).run()

    first, second = classifier.asked
    short = next(item for item in first if item.name == "Глеб Долгов")
    assert FAR not in " ".join(short.quotes)
    # Only who was not told and has more text to show is asked again: Близкий was told,
    # and a short publication, widened, is the very question already answered.
    assert [item.name for item in second] == ["Глеб Долгов"]
    assert FAR in " ".join(second[0].quotes)
    verdict, method, reason, quote = _row(session_factory, "Глеб Долгов")
    # The answer, its reason and the quote shown for it are those of the wide reading.
    assert (verdict, method) == ("political", "model")
    assert reason == f"по {len(second[0].quotes[0])} знакам" and FAR in quote
    assert _row(session_factory, "Александр Беда")[:2] == ("unclear", "model")
    assert (result.political_model, result.criminal, result.unclear) == (1, 1, 3)
    assert result.asked_now == 6


def test_the_wide_answer_is_cached_as_any_other(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory, [DOLGOV])
    PoliticsFinder(session_factory, classifier=ReadingClassifier()).run()
    again = ReadingClassifier()

    result = PoliticsFinder(session_factory, classifier=again).run()

    assert again.asked == [] and result.asked_now == 0
    assert _row(session_factory, "Глеб Долгов")[0] == "political"


def test_a_failed_wide_asking_leaves_the_short_answer(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory, [DOLGOV])

    result = PoliticsFinder(session_factory, classifier=ReadingClassifier(wide_fails=True)).run()

    verdict, _, reason, quote = _row(session_factory, "Глеб Долгов")
    assert verdict == "unclear" and FAR not in quote
    assert reason == f"по {len(quote)} знакам"
    assert (result.failures, result.unclear) == (1, 4)


def test_a_failed_first_asking_is_not_asked_wide(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory, [DOLGOV])
    classifier = FakeClassifier(VERDICTS, fail=True)

    PoliticsFinder(session_factory, classifier=classifier).run()

    # No answer is not «cannot tell»: one batch was sent, and no second.
    assert len(classifier.asked) == 4


INOY = (
    "inoy",
    f"Суд арестовал Льва Иного. {'Заседание шло долго. ' * 12}Его судят {FAR}.",
    "Льва Иного",
    "Лев Иной",
)
# Another publication of Долгов, by the rules: what it tells far from the name stays unseen.
DOLGOV_AGAIN = (
    "dolgov-again",
    f"Суд продлил арест Глебу Долгову. {'Заседание шло долго. ' * 12}Прежде его судили {THEFT}.",
    "Глебу Долгову",
    "Глеб Долгов",
)
DOLGOV_THIRD = (
    "dolgov-third",
    "Глебу Долгову отказали в свидании.",
    "Глебу Долгову",
    "Глеб Долгов",
)


def test_whoever_an_article_read_by_a_model_alone_names_is_shown_wide_quotes_at_once(
    session_factory: sessionmaker[Session],
) -> None:
    # The article a model read is the oldest of his three: later ones do not crowd it out.
    _seed(session_factory, [DOLGOV, DOLGOV_AGAIN, DOLGOV_THIRD, INOY])
    with session_factory.begin() as session:
        # Долгов's article is in the work by a model's reading: the rules found no case.
        article = session.scalar(text("SELECT id FROM parsed_articles WHERE title = 'dolgov'"))
        add_event(session, article, "sentence", "прочитано моделью")
        ids = dict(
            session.execute(
                select(EntityGroupRecord.name, EntityGroupRecord.id).where(
                    EntityGroupRecord.name.in_(["Глеб Долгов", "Лев Иной"])
                )
            ).all()
        )
        shown = quotes_of(session, list(ids.values()))
    classifier = ReadingClassifier()

    PoliticsFinder(session_factory, classifier=classifier).run()

    # What steps 4 and 5 both show: the whole telling of the one, a short quote of the other.
    assert FAR in " ".join(shown[ids["Глеб Долгов"]]) and FAR not in shown[ids["Лев Иной"]][0]
    first, second = classifier.asked
    told = {item.name: FAR in " ".join(item.quotes) for item in first}
    assert (told["Глеб Долгов"], told["Лев Иной"]) == (True, False)
    # His other publication, the rules' own, keeps the short quote: only what the model
    # alone read is shown wide.
    assert len(shown[ids["Глеб Долгов"]]) == 2 and THEFT not in " ".join(shown[ids["Глеб Долгов"]])
    # The wide one first, then the latest of the rest.
    assert (
        FAR in shown[ids["Глеб Долгов"]][0]
        and "отказали в свидании" in shown[ids["Глеб Долгов"]][1]
    )
    # Asked wide at once, he is not asked a second time; the other is, as anyone.
    assert [item.name for item in second] == ["Лев Иной"]
    assert _row(session_factory, "Глеб Долгов")[:2] == ("political", "model")


def test_an_article_a_model_read_is_shown_once_beside_the_entity_s_other(
    session_factory: sessionmaker[Session],
) -> None:
    earlier = ("kot-earlier", "Суд продлил арест Петру Котову.", "Петру Котову", "Пётр Котов")
    latest = (
        "kot",
        f"Суд арестовал Петра Котова. {'Шло долго. ' * 20}{FAR}.",
        "Петра Котова",
        "Пётр Котов",
    )
    _seed(session_factory, [earlier, latest])
    with session_factory.begin() as session:
        article = session.scalar(text("SELECT id FROM parsed_articles WHERE title = 'kot'"))
        add_event(session, article, "sentence", "прочитано моделью")
        group = session.scalar(
            select(EntityGroupRecord.id).where(EntityGroupRecord.name == "Пётр Котов")
        )
        shown = quotes_of(session, [group])[group]

    # Wide, and not once more short: the second place is the other article's.
    assert FAR in shown[0] and shown[1] == "Суд продлил арест Петру Котову."


def _base(
    session_factory: sessionmaker[Session], name: str, articles: str, *, active: bool = True
) -> None:
    with session_factory.begin() as session:
        session.add(
            AirtableKnownPersonRecord(
                external_id=name,
                full_name=name,
                normalized_name=name.lower(),
                matching_key=name.lower().replace(" ", ""),
                articles=articles,
                active=active,
            )
        )


def test_a_case_the_operator_s_base_tracks_is_political_whatever_the_model_reads(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    # Her one record of that name, the same article; and a namesake of another crime.
    _base(session_factory, "Беда Александр Петрович", "ст. 318 УК РФ ч. 1")
    _base(session_factory, "Смирнова Анна Олеговна", "ст. 205.2 УК РФ")
    classifier = FakeClassifier(VERDICTS)

    result = PoliticsFinder(session_factory, classifier=classifier).run()

    # The model would call Беда's case common crime; it is not asked.
    assert "Александр Беда" not in {item.name for item in classifier.asked}
    verdict, method, reason, _quote = _row(session_factory, "Александр Беда")
    assert (verdict, method) == ("political", "base")
    assert reason == "запись «Беда Александр Петрович», та же ст. 318 УК"
    # A name alone is a namesake: the model reads her as before.
    assert _row(session_factory, "Анна Смирнова")[:2] == ("political", "model")
    assert (result.political_base, result.political_model, result.criminal) == (1, 2, 0)


def test_a_record_the_operator_switched_off_tracks_no_case(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    _base(session_factory, "Беда Александр Петрович", "ст. 318 УК РФ ч. 1", active=False)

    PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS)).run()

    assert _row(session_factory, "Александр Беда")[:2] == ("criminal", "model")


def test_a_person_s_word_outweighs_the_base(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)
    _base(session_factory, "Беда Александр Петрович", "ст. 318 УК РФ ч. 1")
    with session_factory.begin() as session:
        beda = session.scalars(
            select(EntityGroupRecord).where(EntityGroupRecord.name == "Александр Беда")
        ).one()
        decide_politics(session, beda, "criminal")

    result = PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS)).run()

    assert _row(session_factory, "Александр Беда")[:2] == ("criminal", "manual")
    assert (result.political_base, result.criminal_manual) == (0, 1)


def test_an_answer_that_names_someone_else_is_dropped() -> None:
    items = [PoliticsItem(1, "Анна Смирнова", (), (), None)]
    answers = [PoliticsAnswer(id=1, source="Иван Петров", verdict="political", explanation="—")]

    assert matched_answers(items, answers) == {}


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [("political", "political"), ("criminal", "criminal"), ("unknown", "unclear")],
)
def test_an_answer_maps_to_a_verdict(answer: str, verdict: str) -> None:
    assert verdict_of(answer) == verdict


def test_a_political_verdict_sticks_when_the_input_changes(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS)).run()
    with session_factory.begin() as session:
        session.execute(text("UPDATE entity_politics_answers SET input_hash = md5(input_hash)"))
    again = FakeClassifier(VERDICTS)

    PoliticsFinder(session_factory, classifier=again).run()

    # Смирнова stays political unasked; Беда's «criminal» is asked again.
    assert [item.name for item in again.asked] == ["Александр Беда"]


def test_the_rules_settle_common_crime_and_the_memorial_s_sure_categories(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    with session_factory.begin() as session:
        # Беда charged with murder alone; Смирнова on the «Антивоенное дело» list.
        session.execute(
            text("UPDATE entity_group_charges SET article = '105' WHERE article = '318'")
        )
        session.execute(
            text(
                "UPDATE parsed_articles SET text = replace(text, "
                "'«Другие жертвы политических репрессий»', '«Антивоенное дело»')"
            )
        )
    classifier = FakeClassifier(VERDICTS)

    result = PoliticsFinder(session_factory, classifier=classifier).run()

    # Only Орлов, with neither article nor category, is left to the model.
    assert [item.name for item in classifier.asked] == ["Олег Иванович Орлов"]
    assert _verdicts(session_factory) == {
        "Иван Петров": ("political", "article"),
        "Александр Беда": ("criminal", "article"),
        "Анна Смирнова": ("political", "memorial"),
        "Олег Иванович Орлов": ("political", "model"),
    }
    assert (result.political_memorial, result.criminal_rules, result.criminal) == (1, 1, 1)
    # «Политические по статье УК» must mean the article rule alone. Counting the
    # «Мемориал» category in both `political_rules` and `political_memorial` put one
    # entity into two counters of the same run summary.
    assert result.political_rules == 1, (
        f"political_rules={result.political_rules} but only Иван Петров carries a "
        f"political article; the «Мемориал» category has its own counter "
        f"({result.political_memorial})"
    )
    accounted = (
        result.political_rules
        + result.political_memorial
        + result.political_model
        + result.political_manual
        + result.criminal
        + result.unclear
    )
    assert accounted == result.figurants, (
        f"{result.figurants} figurants but {accounted} counted across the verdicts"
    )


def test_hooliganism_is_no_common_crime_for_the_rules(
    session_factory: sessionmaker[Session],
) -> None:
    """ст. 213: Pussy Riot's article; the model reads it."""
    _seed(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE entity_group_charges SET article = '213' WHERE article = '318'")
        )
    classifier = FakeClassifier(VERDICTS)

    PoliticsFinder(session_factory, classifier=classifier).run()

    assert "Александр Беда" in [item.name for item in classifier.asked]


def test_a_person_s_word_on_politics_survives_the_next_run(
    session_factory: sessionmaker[Session],
) -> None:
    """«Неясная политичность» and a person's decision on it: step 5 rewrites the
    verdicts, so the decision is kept by the key and written again over the next run."""
    _seed(session_factory)
    PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS)).run()
    with session_factory.begin() as session:
        beda = session.scalar(
            select(EntityGroupRecord).where(EntityGroupRecord.name == "Александр Беда")
        )
        assert beda is not None
        decide_politics(session, beda, POLITICAL)

    PoliticsFinder(session_factory, classifier=FakeClassifier(VERDICTS)).run()

    assert _verdicts(session_factory)["Александр Беда"] == ("political", "manual")
