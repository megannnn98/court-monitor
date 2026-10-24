"""A surname followed by initials («Бастрыкин А.И.») is a person mention (extractor 1.7.0).

The rule only finds the mention. It never completes the name and never decides who the person is: linking goes through
the normalizer and the resolution policy, where such a name stays an incomplete identity.
"""

import pytest

from monitor_core.model import ParsedArticle
from processing.extraction.documents import build_extraction_document_from_parsed_article
from processing.extraction.extractors import RuleBasedEntityExtractor
from processing.extraction.models import EntityType, ExtractionDocument
from processing.extraction.normalizers import RuleBasedMentionNormalizer


def make_document(text: str) -> ExtractionDocument:
    return build_extraction_document_from_parsed_article(
        article_id=1,
        article=ParsedArticle(
            external_id="1", url="https://t.me/test/1", title="Test", published_at=None, text=text
        ),
        source_name="telegram",
        source_url="https://t.me/test/1",
    )


def people(text: str) -> list[tuple[str, int, int]]:
    return [
        (m.surface_text, m.start_offset, m.end_offset)
        for m in RuleBasedEntityExtractor().extract(make_document(text))
        if m.entity_type is EntityType.PERSON
    ]


def surfaces(text: str) -> list[str]:
    return [s for s, _, _ in people(text)]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Бастрыкин А.И. поручил проверить.", "Бастрыкин А.И."),
        ("Бастрыкин А. И. поручил проверить.", "Бастрыкин А. И."),
        ("Иванов И.И. заявил.", "Иванов И.И."),
        ("Иванов И. И. заявил.", "Иванов И. И."),
        ("Иванов-Петров А.Б. заявил.", "Иванов-Петров А.Б."),
        ("Подсудимый Ботоногов А.А. прибыл в отдел полиции.", "Ботоногов А.А."),
        ("Вину признал Ботоногов А.А.", "Ботоногов А.А."),
        ("Ботоногов А.А., 1990 г.р., признан виновным.", "Ботоногов А.А."),
        ("(Иванов И.И.) заявил.", "Иванов И.И."),
    ],
)
def test_surname_with_initials_is_one_person_mention(text: str, expected: str) -> None:
    assert surfaces(text) == [expected]


def test_the_mention_keeps_its_source_text_and_offsets() -> None:
    text = "Глава ведомства Бастрыкин А.И. поручил."
    [(surface, start, end)] = people(text)
    assert text[start:end] == surface == "Бастрыкин А.И."


def test_offsets_are_unicode_code_points_after_an_emoji() -> None:
    text = "👉🇷🇺 Следователь Иванов А.А. задержал Петрова П.П.!"
    found = people(text)
    assert [s for s, _, _ in found] == ["Иванов А.А.", "Петрова П.П."]
    assert all(text[start:end] == surface for surface, start, end in found)


def test_several_people_in_one_sentence() -> None:
    assert surfaces("Иванов И.И. и Петров П.П. встретились с Сидоровым С.С.") == [
        "Иванов И.И.",
        "Петров П.П.",
        "Сидоровым С.С.",
    ]


def test_repeated_mentions_are_kept_one_per_occurrence() -> None:
    text = "Бастрыкин А.И. поручил. Позже Бастрыкин А.И. потребовал отчёт."
    found = people(text)
    assert [s for s, _, _ in found] == ["Бастрыкин А.И.", "Бастрыкин А.И."]
    assert found[0][1] != found[1][1]


def test_a_surname_in_an_oblique_case_is_found_as_written() -> None:
    assert surfaces("Поручение дано Ярошу С.М. и Азизову Б.Р.") == ["Ярошу С.М.", "Азизову Б.Р."]


def test_the_name_is_not_completed_and_the_normalizer_does_not_invent_a_given_name() -> None:
    document = make_document("Малютиной К.С. назначен штраф.")
    [mention] = [
        m
        for m in RuleBasedEntityExtractor().extract(document)
        if m.entity_type is EntityType.PERSON
    ]
    normalized = RuleBasedMentionNormalizer().normalize(mention, document)
    assert mention.surface_text == "Малютиной К.С."
    assert (
        normalized.normalized_text == "Малютина К.С."
    )  # the existing normalizer brings the surname to the nominative
    assert "К.С." in normalized.normalized_text and len(normalized.normalized_text.split()) == 2


@pytest.mark.parametrize(
    "text",
    [
        "Этот текст, т.е. сокращение, не имя.",
        "Приказ Н. подписан.",
        "Москва Д. сообщила о задержании.",
        "В городе, т. е. в столице, холодно.",
        "Статья А.Б. кодекса действует.",
        "Ответ В.В. пока не получен.",
    ],
)
def test_abbreviations_and_ordinary_words_with_initials_are_not_people(text: str) -> None:
    assert surfaces(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Жил на ул. Ленина В.И. дом 5.",
        "Больница им. Пушкина А.С. закрыта.",
        "Проспект Мира А. перекрыт.",
        "Школа имени Чехова А.П. открылась.",
    ],
)
def test_streets_and_institutions_named_after_people_are_not_people(text: str) -> None:
    assert surfaces(text) == []


def test_two_capital_letters_without_dots_are_not_initials() -> None:
    assert surfaces("Иванов АБ заявил.") == []
    assert surfaces("ФКУ ИК-6 ГУФСИН России") == []


def test_a_full_name_with_patronymic_is_not_split_by_the_new_rule() -> None:
    assert surfaces("Иванов Иван Иванович А.А. пришёл.") == ["Иванов Иван Иванович"]
    assert surfaces("Александр Бастрыкин и Александра Иванова пришли.") == [
        "Александр Бастрыкин",
        "Александра Иванова",
    ]


def test_initials_before_the_surname_still_work() -> None:
    assert surfaces("Слово предоставили А.И. Бастрыкину.") == ["А.И. Бастрыкину"]


def test_a_surname_with_initials_next_to_a_full_name_keeps_both() -> None:
    assert surfaces("Александра Иванова и Бастрыкина А.И. вызвали.") == [
        "Александра Иванова",
        "Бастрыкина А.И.",
    ]


def test_a_city_before_a_surname_with_initials_is_not_a_street() -> None:
    """Found after the first evaluation: «по г. Москве Ярошу С.М.» was dropped by the street guard (1.7.0)."""
    assert surfaces("ГСУ СК России по г. Москве Ярошу С.М. доложить о ходе проверки.") == [
        "Ярошу С.М."
    ]


def test_a_house_number_before_initials_is_still_not_a_person() -> None:
    assert surfaces("Прописан в д. Петрово А.Б.") == []
