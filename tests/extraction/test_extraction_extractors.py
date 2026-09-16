from datetime import UTC, datetime

import pytest

from extraction.documents import build_extraction_document_from_parsed_article
from extraction.events import RuleBasedEventExtractor
from extraction.extractors import RuleBasedEntityExtractor
from extraction.models import EntityType, EventType, ExtractionDocument
from extraction.normalizers import RuleBasedMentionNormalizer
from sources.models import ParsedArticle


def make_document(text: str) -> ExtractionDocument:
    return build_extraction_document_from_parsed_article(
        article_id=1,
        article=ParsedArticle(
            external_id="1",
            url="https://ovd.info/test",
            title="Test",
            published_at=None,
            text=text,
        ),
        source_name="ОВД-Инфо",
        source_url="https://ovd.info/test",
    )


def test_extracts_legal_reference_person_court_and_event() -> None:
    text = (
        "Басманный районный суд Москвы заочно арестовал Александра Иванова по ч. 2 ст. 207.3 УК РФ."
    )
    document = make_document(text)
    extractor = RuleBasedEntityExtractor()

    mentions = extractor.extract(document)

    assert (EntityType.COURT, "Басманный районный суд Москвы") in {
        (mention.entity_type, mention.surface_text) for mention in mentions
    }
    assert (EntityType.PERSON, "Александра Иванова") in {
        (mention.entity_type, mention.surface_text) for mention in mentions
    }
    assert (EntityType.LEGAL_REFERENCE, "ч. 2 ст. 207.3 УК РФ") in {
        (mention.entity_type, mention.surface_text) for mention in mentions
    }
    assert [document.text[m.start_offset : m.end_offset] for m in mentions] == [
        m.surface_text for m in mentions
    ]


def test_extracts_supported_legal_reference_variants() -> None:
    text = (
        "Дело возбудили по ст. 207.3 УК РФ. "
        "Следствие упомянуло п. «а» ч. 2 ст. 282 УК РФ. "
        "В протоколе остался КоАП РФ."
    )

    mentions = RuleBasedEntityExtractor().extract(make_document(text))

    surfaces = [mention.surface_text for mention in mentions]
    assert "ст. 207.3 УК РФ" in surfaces
    assert "п. «а» ч. 2 ст. 282 УК РФ" in surfaces
    assert "КоАП РФ" in surfaces


def test_normalizes_person_and_legal_reference() -> None:
    document = make_document("Александра Иванова обвинили по ч. 2 ст. 207.3 УК РФ.")
    normalizer = RuleBasedMentionNormalizer()
    mentions = RuleBasedEntityExtractor().extract(document)

    normalized = [normalizer.normalize(mention, document) for mention in mentions]

    # «Александра Иванова» is both a masculine genitive and a feminine nominative; the
    # normalizer does not guess and keeps the surface (see test_name_morphology.py).
    assert ("person", "Александра Иванова") in {
        (mention.entity_type.value, mention.normalized_text) for mention in normalized
    }
    legal = next(
        mention for mention in normalized if mention.entity_type is EntityType.LEGAL_REFERENCE
    )
    assert legal.normalized_data.model_dump()["article"] == "207.3"
    assert legal.normalized_data.model_dump()["part"] == "2"


def test_extracts_events_with_links() -> None:
    document = make_document(
        "Следственный комитет задержал Марии Петровой в Москве по ст. 282 УК РФ."
    )
    normalizer = RuleBasedMentionNormalizer()
    mentions = [
        normalizer.normalize(mention, document)
        for mention in RuleBasedEntityExtractor().extract(document)
    ]

    events = RuleBasedEventExtractor().extract(document, mentions)

    assert [event.event_type for event in events] == [EventType.DETENTION]
    assert events[0].links


def test_order_is_deterministic() -> None:
    document = make_document("МВД задержало Ивана Петрова. МВД задержало Ивана Петрова.")
    extractor = RuleBasedEntityExtractor()

    first = extractor.extract(document)
    second = extractor.extract(document)

    assert first == second


def _linked_people(text: str) -> list[list[str]]:
    document = make_document(text)
    normalizer = RuleBasedMentionNormalizer()
    mentions = [
        normalizer.normalize(mention, document)
        for mention in RuleBasedEntityExtractor().extract(document)
    ]
    events = RuleBasedEventExtractor().extract(document, mentions)
    return [
        [
            mentions[link.mention_index].surface_text
            for link in event.links
            if mentions[link.mention_index].entity_type is EntityType.PERSON
        ]
        for event in events
    ]


def test_reporting_source_is_not_linked_to_the_event_it_reports() -> None:
    """Real cases (real-world validation v1): the defender who reported a release and a
    rights defender quoted as a source were linked to the event and classified political."""
    assert _linked_people(
        "Защитница Ольга Иванова сообщила, что из отдела отпустили еще одну девушку."
    ) == [[]]
    assert _linked_people(
        "По данным правозащитницы Анны Тажеевой, мужчин задержали у магазина."
    ) == [[]]
    assert _linked_people(
        "Сергея Петрова задержали у здания суда, рассказал ОВД-Инфо его адвокат Иван Смирнов."
    ) == [["Сергея Петрова"]]


def test_procedural_actor_before_the_name_is_not_the_event_subject() -> None:
    assert _linked_people("Судья Олег Нефедов арестовал Павла Зайцева на два месяца.") == [
        ["Павла Зайцева"]
    ]
    assert _linked_people("Следователь Мария Кузнецова предъявила обвинение Андрею Лебедеву.") == [
        ["Андрею Лебедеву"]
    ]


def test_subject_with_a_descriptor_is_still_linked() -> None:
    assert _linked_people("Суд арестовал журналиста Ивана Фролова.") == [["Ивана Фролова"]]
    assert _linked_people("Нападение на правозащитника Алексея Соколова: его задержали.") == [
        ["Алексея Соколова"]
    ]


def _people(text: str) -> list[str]:
    return [
        mention.surface_text
        for mention in RuleBasedEntityExtractor().extract(make_document(text))
        if mention.entity_type is EntityType.PERSON
    ]


def test_surname_repeated_alone_is_a_mention_of_the_named_person() -> None:
    """Real cases: 138 of 153 missed person mentions were a surname alone referring back to a
    full name in the same article («Зареме Мусаевой… Мусаеву осудили»), so the events of
    those sentences had no subject."""
    text = (
        "Шалинский суд вынес приговор Зареме Мусаевой. "
        "Мусаеву признали виновной. Мусаева обжаловала приговор, говорит Мусаев-младший."
    )
    assert _people(text) == ["Зареме Мусаевой", "Мусаеву", "Мусаева"]


def test_capitalized_word_is_not_a_surname_without_a_full_name_in_the_article() -> None:
    assert _people("Суд арестовал Петрова. Позже Кузнецова отпустили.") == []


def test_role_and_sentence_words_are_trimmed_from_the_name() -> None:
    assert _people("Судья Александр Сенькин арестовал Любшина.") == ["Александр Сенькин"]
    assert _people("Также Нелли Кирман рассказала о пытках.") == ["Нелли Кирман"]
    assert _people("Против Михаила Битова возбудили дело.") == ["Михаила Битова"]


def test_overlapping_name_spans_keep_only_the_longest() -> None:
    assert _people("Защитница Ольга Иванова сообщила о задержании.") == ["Ольга Иванова"]


def _event_types(text: str) -> list[str]:
    document = make_document(text)
    return [event.event_type.value for event in RuleBasedEventExtractor().extract(document, [])]


def test_event_keywords_match_whole_words_and_case_needs_an_opening_verb() -> None:
    """Real cases: «в отделе» matched the keyword «деле» and any «дело» opened a case."""
    assert _event_types("Защитников не пускают в отделе к остальным.") == []
    assert _event_types("Судья, который рассматривает его дело, направил запрос в СИЗО.") == []
    assert _event_types("Против активиста возбудили уголовное дело.") == ["case_opened"]
    assert _event_types("На блогера завели дело о фейках.") == ["case_opened"]


def test_negated_event_verb_is_not_an_event() -> None:
    assert _event_types("Силовики его до сих пор не отпустили.") == []


def test_verb_trigger_wins_over_a_noun_mention_of_another_event() -> None:
    """Real cases: «после оглашения приговора его освободили» was a sentence event."""
    assert _event_types("После оглашения приговора его освободили в зале суда.") == ["release"]
    assert _event_types("Приговор огласили 25 мая, и сразу после этого его взяли под стражу.") == [
        "arrest"
    ]


def test_detainees_as_a_noun_are_not_a_detention_event() -> None:
    """Real cases: «сообщили сами задержанные», «к задержанным», «перед задержанием»."""
    assert _event_types("Об этом ОВД-Инфо сообщили сами задержанные.") == []
    assert _event_types("Защитники приехали в отдел к задержанным.") == []
    assert _event_types("Он показал удостоверение прессы перед задержанием.") == []
    assert _event_types("Активист был задержан у здания суда.") == ["detention"]


def test_references_to_other_events_are_not_events() -> None:
    """Real cases: «в разговоре с другими осужденными», «до ареста делали операции»."""
    assert _event_types("В разговоре с другими осужденными он одобрил поступок.") == []
    assert _event_types("Из-за проблем со зрением ему до ареста делали операции.") == []
    assert _event_types("Его осудили на пять лет.") == ["sentence"]


def test_writing_source_is_not_linked_to_the_event() -> None:
    assert _linked_people("Активист Аскер Сохт писал, что задержанных отпустили после беседы.") == [
        []
    ]


def test_legal_references_without_rf_suffix_are_extracted() -> None:
    """Real cases: OVD-Info writes «(ч. 2 ст. 207.3 УК)», «(ст. 20.3.3 КоАП)» without «РФ»;
    no political charge was ever recognised for those persons."""
    text = (
        "Его обвинили в «фейках» (п. «д» ч. 2 ст. 207.3 УК). "
        "Суд оштрафовал его по статье о дискредитации (ч. 1 ст. 20.3.3 КоАП). "
        "Дело возбудили по ст. 212.1 УК."
    )
    legal = [
        mention.surface_text
        for mention in RuleBasedEntityExtractor().extract(make_document(text))
        if mention.entity_type is EntityType.LEGAL_REFERENCE
    ]
    assert legal == ["п. «д» ч. 2 ст. 207.3 УК", "ч. 1 ст. 20.3.3 КоАП", "ст. 212.1 УК"]


def test_spelled_out_criminal_code_without_country_is_normalized_without_guessing_it() -> None:
    """Real Telegram cases crashed the whole extraction run: «ч. 2 ст. 161 Уголовного кодекса»;
    «статьи 437 и 367 Уголовного кодекса» is the Ukrainian code, so the country is not guessed."""
    text = (
        "Его обвинили по ч. 2 ст. 161 Уголовного кодекса. "
        "Дело возбудили по признакам статьи 438 Уголовного кодекса."
    )
    document = make_document(text)
    normalizer = RuleBasedMentionNormalizer()

    legal = [
        normalizer.normalize(mention, document).normalized_text
        for mention in RuleBasedEntityExtractor().extract(document)
        if mention.entity_type is EntityType.LEGAL_REFERENCE
    ]

    assert legal == ["УК ст. 161 ч. 2", "УК ст. 438"]


def test_inflected_organization_name_is_not_part_of_a_person_name() -> None:
    text = (
        "Суд арестовал охранника Минюста Виталия Л. на 15 суток, сотрудники Медиазоны пришли в суд."
    )

    persons = [
        mention.surface_text
        for mention in RuleBasedEntityExtractor().extract(make_document(text))
        if mention.entity_type is EntityType.PERSON
    ]

    assert persons == []


def test_person_accusing_or_condemning_others_is_not_charged_or_sentenced() -> None:
    """Real case: «Мампория … обвинил российских военных в убийстве гражданских»."""
    assert _event_types("Пенсионер сорвал букву Z и обвинил российских военных в убийствах.") == []
    assert _event_types("Он публично осудил войну.") == []
    assert _event_types("Раньше блогер обвинял власти во лжи.") == []
    assert _event_types("Активиста обвинили в оправдании терроризма.") == ["charge"]
    assert _event_types("Ее обвиняют в фейках об армии.") == ["charge"]
    assert _event_types("Его также обвиняли в хранении взрывчатки.") == ["charge"]
    assert _event_types("Художница обвинена в вандализме.") == ["charge"]
    assert _event_types("Его осудили на три года.") == ["sentence"]


def test_according_to_a_sentence_is_a_reference_not_an_event() -> None:
    """Real case: «Согласно второму приговору, Мифтахов должен был отбыть…»."""
    assert _event_types("Согласно второму приговору, он должен был отбыть срок в тюрьме.") == []
    assert _event_types("После первого ареста он уехал из города.") == []
    assert _event_types("Суд вынес приговор активисту.") == ["sentence"]


_PUBLISHED = datetime(2026, 5, 5, tzinfo=UTC)


def _event_dates(text: str, published_at: datetime | None = _PUBLISHED) -> list[datetime | None]:
    document = make_document(text).model_copy(update={"published_at": published_at})
    return [event.event_date for event in RuleBasedEventExtractor().extract(document, [])]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Real case: «Мампорию задержали … в апреле 2025 года» got the 2026 publication date.
        ("Мампорию задержали в апреле 2025 года.", None),
        ("В 2025 году Мампорию задержали.", None),
        ("В апреле 2025 года, по данным правозащитников, Мампорию задержали.", None),
        # Real case: the year opens the sentence of the detention itself.
        ("В 2022 году его вместе с женой, с которой он жил в Твери, задержали.", None),
        ("Мампорию задержали и поместили под домашний арест, это было в апреле 2025 года.", None),
        # Real case: the year dates a relative clause about another fact.
        ("Дело возбудили из-за доната, который он перевел ФБК 5 августа 2021 года.", _PUBLISHED),
        # The year dates another fact of the sentence, not this trigger.
        ("В 2025 году Иванов переехал в Москву, а сегодня его задержали.", _PUBLISHED),
        ("Иванова, осужденного в 2024 году, сегодня задержали.", _PUBLISHED),
        ("После задержания в 2025 году Иванов уехал, а сегодня его арестовали.", _PUBLISHED),
        ("Задержали активиста 1990 года рождения.", _PUBLISHED),
        ("В 2026 году его арестовали.", _PUBLISHED),
    ],
)
def test_event_date_uses_the_year_of_its_own_trigger_context(
    text: str, expected: datetime | None
) -> None:
    assert _event_dates(text) == [expected]


def test_one_event_per_sentence_is_dated_by_the_earliest_trigger() -> None:
    """One event per sentence (earliest verb trigger): «задержали» owns 2025, so no date."""
    text = "В 2025 году Иванова задержали, а сегодня его снова арестовали."
    assert _event_dates(text) == [None]


def test_event_without_a_publication_date_has_no_date() -> None:
    assert _event_dates("Сегодня его задержали.", published_at=None) == [None]
    assert _event_dates("В 2025 году его задержали.", published_at=None) == [None]
