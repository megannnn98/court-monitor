"""The queue for the customer's channel: what it published, drafts, names for unnamed news."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from candidates.models import PoliticalPersecutionCandidate, RosfinmonitoringStatus
from channel_feed.published import name_key, parse_channel_page, published_name_keys
from channel_feed.queue import QueueSource, articles_of, draft_post, is_published
from channel_feed.unnamed import CaseEvent, facts_of, match_score, suggest_names

CHANNEL_HTML = (Path(__file__).parents[1] / "fixtures" / "enbv2022_channel.html").read_bytes()


def test_a_name_key_ignores_order_patronymic_and_case() -> None:
    assert name_key("Ярош Сергей Васильевич") == name_key("сергей ярош") == "сергей ярош"
    assert name_key("Савельева Светлана Игоревна") == name_key("Светлана Савельёва")
    assert name_key("Людмила Леонидовна А.") is None


def test_the_channel_page_gives_its_posts_newest_first() -> None:
    posts = parse_channel_page(CHANNEL_HTML)

    assert posts[0].post_id == 21
    assert posts[0].published_at == datetime(2026, 9, 17, 9, 0, 29, tzinfo=UTC)
    assert "Дмитрий Коротков" in posts[0].text
    assert [post.post_id for post in posts] == sorted((p.post_id for p in posts), reverse=True)


def test_every_person_the_channel_published_since_august_is_known() -> None:
    keys = published_name_keys(parse_channel_page(CHANNEL_HTML))

    for name in (
        "Мамут Белялов",
        "Светлана Савельева",
        "Андрей Смирнов",
        "Сергей Ярош",
        "Дмитрий Коротков",
        "Иван Любшин",
        "Арсений Турбин",
        "Евгений Поливко",
    ):
        assert name_key(name) in keys, name


def _candidate(
    name: str,
    reasons: list[str],
    status: RosfinmonitoringStatus = RosfinmonitoringStatus.NOT_MATCHED,
) -> PoliticalPersecutionCandidate:
    return PoliticalPersecutionCandidate(
        person_id=1,
        canonical_name=name,
        normalized_name=name,
        persecution_status="political",
        persecution_confidence=0.8,
        persecution_reasons=reasons,
        rosfinmonitoring_status=status,
        rosfinmonitoring_match_confidence=0.8,
        event_count=1,
        alias_count=1,
        last_event_date=None,
    )


def test_a_published_person_is_recognized_in_another_word_order() -> None:
    published = {"сергей ярош"}

    assert is_published(_candidate("Ярош Сергей Васильевич", []), published)
    assert not is_published(_candidate("Сергей Ярошенко", []), published)


def test_articles_are_read_out_of_the_reasons_once_each() -> None:
    reasons = [
        "Политическая статья: УК РФ ст. 207.3 ч. 2 п. д; УК РФ ст. 275",
        "Политическая статья: УК РФ ст. 275",
        "Статья содержит признаки политического преследования",
    ]

    assert articles_of(reasons) == ["п. «д» ч. 2 ст. 207.3 УК РФ", "ст. 275 УК РФ"]


def test_the_draft_names_the_person_the_case_the_source_and_the_list() -> None:
    candidate = _candidate(
        "Медведев Петр Евгеньевич",
        ["Политическая статья: УК РФ ст. 275"],
        RosfinmonitoringStatus.MATCHED,
    )

    draft = draft_post(candidate, QueueSource("https://t.me/uovs_info/1408", "sentence"))

    assert draft == (
        "Медведев Петр Евгеньевич\n"
        "Вынесен приговор, статьи: ст. 275 УК РФ.\n"
        "Внимание: в перечне Росфинмониторинга.\n"
        "Источник: https://t.me/uovs_info/1408"
    )


def test_a_draft_without_a_source_still_names_the_person() -> None:
    assert draft_post(_candidate("Сергей Ярош", []), None) == "Сергей Ярош"


_DAY = datetime(2026, 9, 9, 12, tzinfo=UTC)


def _event(
    sentence: str,
    *,
    article_id: int,
    target: str | None = None,
    event_type: str = "sentence",
    published_at: datetime = _DAY,
    political: bool = True,
) -> CaseEvent:
    return CaseEvent(
        event_id=article_id,
        event_type=event_type,
        article_id=article_id,
        published_at=published_at,
        source="source",
        url=f"https://example.test/{article_id}",
        title=sentence[:40],
        facts=facts_of(sentence),
        political=political,
        target=target,
        target_person_id=article_id if target else None,
    )


# The real pair: Mediazona's Telegram left him unnamed, Kommersant's site named him.
UNNAMED = (
    "Жителя Крыма приговорили к 21 году колонии строгого режима, обвинив в участии в "
    "подготовке убийства чиновника. Против 27-летнего крымчанина возбудили дело о госизмене."
)
NAMED = (
    "Верховный суд Крыма приговорил местного жителя к 21 году колонии строгого режима. "
    "27-летнего жителя Советского района Мамута Белялова задержали в 2022 году."
)


def test_the_facts_of_a_sentence() -> None:
    facts = facts_of(UNNAMED)

    assert facts.terms == {"21"}
    assert facts.ages == {"27"}
    assert facts.places == {"крыма"}


def test_an_unnamed_verdict_gets_the_name_from_the_same_case_elsewhere() -> None:
    unnamed = _event(UNNAMED, article_id=1)
    named = _event(NAMED, article_id=2, target="Мамут Белялов")
    other = _event("Жителя Москвы приговорили к 21 году колонии.", article_id=3, target="Иванов")

    [suggestion] = suggest_names([unnamed, named, other])

    assert suggestion.unnamed is unnamed
    assert suggestion.named is named


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"event_type": "arrest"}, "another kind of event"),
        ({"published_at": _DAY + timedelta(days=4)}, "too far apart"),
    ],
)
def test_events_that_cannot_be_one_case_do_not_match(change: dict[str, object], why: str) -> None:
    named = _event(NAMED, article_id=2, target="Мамут Белялов", **change)  # type: ignore[arg-type]

    assert match_score(_event(UNNAMED, article_id=1), named) is None, why


def test_a_verdict_needs_more_than_the_term() -> None:
    unnamed = _event("Мужчину приговорили к 21 году колонии.", article_id=1)
    named = _event("Суд приговорил Иванова к 21 году колонии.", article_id=2, target="Иванов")

    assert match_score(unnamed, named) is None


def test_unpolitical_or_factless_unnamed_news_gets_no_suggestion() -> None:
    named = _event(NAMED, article_id=2, target="Мамут Белялов")

    assert suggest_names([_event(UNNAMED, article_id=1, political=False), named]) == []
    assert suggest_names([_event("Жителя Крыма задержали.", article_id=3), named]) == []
