from datetime import UTC, datetime

import pytest

from extraction.documents import build_extraction_document_from_parsed_article
from extraction.events import RuleBasedEventExtractor
from extraction.extractors import RuleBasedEntityExtractor
from extraction.models import EntityType, EventEntityRole, EventType, ExtractionDocument
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

    # The organisation names stay out of the name; the person is the one the golden marks.
    assert persons == ["Виталия Л."]


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


def test_a_place_name_before_the_name_is_not_part_of_it() -> None:
    """Real cases: «России Мария Захарова», «Калуги Иван Любшин», «Подмосковья Александр Шестун»."""
    assert _people("Об этом заявила России Мария Захарова.") == ["Мария Захарова"]
    assert _people("Задержали Калуги Ивана Любшина.") == ["Ивана Любшина"]
    assert _people("Дело Подмосковья Александра Шестуна закрыли.") == ["Александра Шестуна"]


def test_a_place_name_is_kept_when_only_one_name_word_would_remain() -> None:
    """«София» is also a city: trimming it would leave a single word and lose the person."""
    assert _people("Активистка София Чепик уехала.") == ["София Чепик"]
    assert _people("Приговор Ремзи Куртнезирову огласили.") == ["Ремзи Куртнезирову"]


def test_a_span_of_ordinary_words_is_not_a_person() -> None:
    """Real cases from the Telegram corpus: «Российской Федерации» (470 mentions),
    «Танцы Минус», «Верховного Суда» were all stored as persons."""
    assert _people("Это закон Российской Федерации.") == []
    assert _people("Выступали Танцы Минус на площади.") == []
    assert _people("Решение принял Верховного Суда представитель.") == []
    # Unknown to the dictionary: a foreign name must survive.
    assert _people("Приехал Джейкоб Тирни вчера.") == ["Джейкоб Тирни"]


def test_a_given_name_with_a_surname_initial_is_a_person() -> None:
    """Real case: OVD-Info writes «Виталия Л.» when it withholds the surname."""
    assert _people("Суд арестовал Виталия Л. на 15 суток.") == ["Виталия Л."]
    assert _people("Об этом сообщил С. Петров вчера.") == ["С. Петров"]


def test_a_plural_place_name_before_the_name_is_trimmed() -> None:
    """Real case: «Калуги Ивана Любшина» — «калуги» is also a nominative plural."""
    assert _people("Суд оштрафовал жителя Калуги Ивана Любшина.") == ["Ивана Любшина"]


def test_an_unknown_word_before_a_full_name_is_trimmed() -> None:
    """Real case: «Сколтеха Даниила Меркулова» (an organisation the dictionary lacks)."""
    assert _people("Задержали выпускника Сколтеха Даниила Меркулова.") == ["Даниила Меркулова"]
    # One name word left: the unknown word may be a foreign given name and stays.
    assert _people("Приговор Ремзи Куртнезирову огласили.") == ["Ремзи Куртнезирову"]


def test_an_adjective_before_a_name_is_not_part_of_it() -> None:
    """Real case: «Вечная Слава» became a person («Слава» is a name)."""
    assert _people("На плите написано Вечная Слава героям.") == []
    assert _people("Приехал Донской Иван вчера.") == ["Донской Иван"]


def test_a_latin_script_span_is_not_a_person() -> None:
    """Real cases: «Skandi Klubb», «Frankfurter Allgemeine Zeitung», «Just Got Lucky»."""
    assert _people("Об этом пишет Frankfurter Allgemeine Zeitung сегодня.") == []
    assert _people("Клуб Skandi Klubb закрыли.") == []


def test_a_given_name_after_a_patronymic_starts_another_person() -> None:
    """Real case: «Павла Крисевича Елену» became one person «Павел Крисевич Елена»."""
    assert _people("Задержали Павла Крисевича Елену Иванову.") == [
        "Павла Крисевича",
        "Елену Иванову",
    ]
    assert _people("Суд оставил Мифтахова Азата Фанисовича в колонии.") == [
        "Мифтахова Азата Фанисовича"
    ]


def test_a_declined_surname_repeats_the_full_name_of_the_article() -> None:
    """Real case: «Владимир Яроцкий … Яроцкого обвинили» — the genitive was not recognised."""
    text = "Владимир Яроцкий умер в колонии. Яроцкого обвинили в «фейках» в 2023 году."

    assert _people(text) == ["Владимир Яроцкий", "Яроцкого"]


def test_a_surname_repeats_across_dictionary_gender_and_yo() -> None:
    """Real cases: «Романа Паклина … Паклин» (the dictionary base form is feminine) and
    «Анатолия Терешина … Терешин» (the dictionary spells it «Терёшин»)."""
    assert _people("Романа Паклина увезли. Паклин подал жалобу.") == ["Романа Паклина", "Паклин"]
    assert _people("Анатолия Терешина судят. Терешин в СИЗО.") == ["Анатолия Терешина", "Терешин"]


def test_a_surname_outside_the_dictionary_still_repeats() -> None:
    """«Мампория» is not in the dictionary; the fallback stem still matches its forms."""
    text = "Олег Мампория вышел на пикет. Мампорию задержали в апреле."

    assert _people(text) == ["Олег Мампория", "Мампорию"]


def _event_people(text: str) -> list[list[str]]:
    """Surface texts of the persons linked to each event of the text."""
    document = make_document(text)
    extractor = RuleBasedEntityExtractor()
    normalizer = RuleBasedMentionNormalizer()
    mentions = [normalizer.normalize(m, document) for m in extractor.extract(document)]
    return [
        [
            mentions[link.mention_index].surface_text
            for link in event.links
            if link.role is EventEntityRole.TARGET
        ]
        for event in RuleBasedEventExtractor().extract(document, mentions)
    ]


def test_a_pronoun_links_the_event_to_the_person_named_before() -> None:
    """Real cases: «Роман Паклин … Его задержали», «Ему предъявили обвинение»."""
    assert _event_people(
        "Роман Паклин сидит в колонии. Паклин потерял зрение. Его задержали в августе."
    ) == [["Паклин"]]
    assert _event_people(
        "Зарема Мусаева в колонии. Мусаева больна. Ее приговорили к трем годам."
    ) == [["Мусаева"]]


def test_a_pronoun_of_another_gender_is_not_linked() -> None:
    """Attributing an event to the wrong person is worse than leaving it unlinked."""
    assert _event_people(
        "Роман Паклин сидит в колонии. Паклин молчал. Ее приговорили к трем годам."
    ) == [[]]


def test_a_pronoun_is_not_linked_when_two_people_stand_before_it() -> None:
    text = "Иван Петров и Сергей Сидоров вышли на пикет. Его задержали в августе."

    assert _event_people(text) == [[]]


def test_talking_about_an_event_is_not_the_event() -> None:
    """Real case: «суд рассматривал иск о его освобождении» was extracted as a release."""
    assert _event_types("Суд рассматривал иск о его освобождении в связи с болезнью.") == []
    assert _event_types("Его освободили из СИЗО в связи с болезнью.") == ["release"]


def test_a_possessive_pronoun_does_not_link_the_event_to_its_owner() -> None:
    """Review finding: «Его адвоката задержали» is about the lawyer, not about him."""
    assert _event_people("Иван Петров в СИЗО. Петров молчал. Его адвоката задержали вчера.") == [[]]
    assert _event_people("Зарема Мусаева в колонии. Мусаева больна. Ее дочь оштрафовали.") == [[]]


def test_a_plural_pronoun_never_resolves_to_one_person() -> None:
    """Review finding: «им» is also the plural dative and stood in the masculine set."""
    assert _event_people("Иван Петров выступал. Петров молчал. Им предъявили обвинение.") == [[]]
    assert _event_people("Иван Петров выступал. Петров молчал. Их задержали в августе.") == [[]]


def test_a_single_earlier_mention_is_not_enough_for_a_pronoun() -> None:
    """Review finding: the depth of two was not enforced — one mention passed the check."""
    assert _event_people("Иван Петров выступал вчера. Его задержали в августе.") == [[]]
    assert _event_people(
        "Иван Петров выступал вчера. Иван Петров молчал. Его задержали в августе."
    ) == [["Иван Петров"]]


def test_a_pronoun_does_not_reach_across_a_paragraph() -> None:
    text = "Иван Петров выступал. Иван Петров молчал.\n\nЕго задержали в августе."

    assert _event_people(text) == [[]]


def test_two_names_in_a_row_are_not_one_three_word_span() -> None:
    """Review finding: «Иван Петров Сергей Сидоров» produced «Петров Сергей Сидоров»."""
    assert _people("Задержали Иван Петров Сергей Сидоров.") == ["Иван Петров", "Сергей Сидоров"]


def test_a_three_word_name_with_a_patronymic_stays_whole() -> None:
    assert _people("Суд оставил Мифтахова Азата Фанисовича в колонии.") == [
        "Мифтахова Азата Фанисовича"
    ]
    assert _people("Шевченко Татьяна Андреевна подала жалобу.") == ["Шевченко Татьяна Андреевна"]


def test_a_masculine_surname_is_not_a_repeat_of_a_feminine_one() -> None:
    """Review finding: folding «иванова» to «иванов» made a man a repeat of a woman."""
    assert _people("Анна Иванова выступила. Иванов сообщил другое.") == ["Анна Иванова"]
    # The same person in another case still repeats.
    assert _people("Романа Паклина увезли. Паклин подал жалобу.") == ["Романа Паклина", "Паклин"]


def test_defendants_as_a_noun_are_not_a_charge_event() -> None:
    """Real case: «Все обвиняемые отрицают свою вину» was extracted as a charge."""
    assert _event_types("Все обвиняемые отрицают свою вину.") == []
    assert _event_types("Активист стал обвиняемым по делу о фейках.") == ["charge"]


def test_overlapping_spans_keep_the_one_with_more_name_words() -> None:
    """Real cases: a longer span of outlet or agency words beat the trimmed full name and
    the person lost the surname («Popcorn Books Дмитрия», «Росмолодежи Ксения»)."""
    assert _people("Редактора Popcorn Books Дмитрия Протопопова осудили.") == [
        "Дмитрия Протопопова"
    ]
    assert _people("Против экс-главы Росмолодежи Ксении Разуваевой возбудили дело.") == [
        "Ксении Разуваевой"
    ]


def test_jehovahs_witnesses_are_an_organization_not_part_of_a_name() -> None:
    """Real cases: the dictionary reads «Иеговы» as a given name, and 21 persons were stored
    as «Иегова Виктор Урс», «Свидетель Иегова» and alike."""
    assert _people("Суд приговорил 60-летнего Свидетеля Иеговы Виктора Урсу к шести годам.") == [
        "Виктора Урсу"
    ]
    assert _people("38-летняя Свидетельница Иеговы Сона Олопова освободилась.") == ["Сона Олопова"]
    assert _people("63-летний Свидетель Иеговы вышел на свободу.") == []
    assert _people("Свидители Иеговы Ирину Ушакову задержали.") == ["Ирину Ушакову"]


def test_a_latin_word_is_not_part_of_a_person_name() -> None:
    """Real cases: people are written in Cyrillic in these sources, while mixed spans were
    outlets and brands around a name («The Insider Романа», «Say Agency Анна»)."""
    assert _people("Главреда The Insider Романа Доброхотова объявили в розыск.") == [
        "Романа Доброхотова"
    ]
    assert _people("Фотографа Say Agency Анну Петрову задержали.") == ["Анну Петрову"]
    assert _people("Канал Соловьев Live закрыли.") == []


def test_a_name_does_not_span_a_line_break() -> None:
    """Real case: the title «…в поддержку Марии Бонцлер» and the text «На Старом Арбате…»
    were read as one name «Бонцлер На Старом»."""
    assert _people("в поддержку Марии Бонцлер\nНа Старом Арбате прошел пикет") == ["Марии Бонцлер"]
    assert _people("Задержаны:\nПетров\nСидоров") == []


def test_a_surname_or_place_before_a_given_name_is_not_part_of_the_name() -> None:
    """Real cases: «Глазов Андрей Едигарев», «Навальный Сергей Бойко», «Коми Игорь Сажин»,
    «Марий Эл Алексей». Without a patronymic a Russian name is «given name, surname»."""
    assert _people("Против депутата из Глазова Андрея Едигарева возбудили дело.") == [
        "Андрея Едигарева"
    ]
    assert _people("Суд арестовал координатора штаба Навального Сергея Бойко.") == ["Сергея Бойко"]
    assert _people("Обыск прошел у правозащитника из Коми Игоря Сажина.") == ["Игоря Сажина"]
    assert _people("Задержали жителя Республики Марий Эл Алексея Петрова.") == ["Алексея Петрова"]
    # «Surname, given name, patronymic» stays whole, including a patronymic the
    # dictionary also knows as a surname.
    assert _people("Поспелов Дмитрий Александрович осужден.") == ["Поспелов Дмитрий Александрович"]


def test_a_bullet_opens_a_sentence() -> None:
    """Real case: Telegram digests start items with «🔹», and «🔹 Приговор Гладких…» kept
    the capitalized common noun as part of a name."""
    assert _people("🔹 Приговор Иванову увеличили до 20 лет") == []
    assert _people("• Приговор Иванову увеличили до 20 лет") == []


def test_a_given_name_ending_a_span_starts_the_next_person() -> None:
    """Real case: «бойца Рамзана Кадырова Никиту Журавеля» — preferring more name words
    picked «Рамзана Кадырова Никиту» over the two people."""
    assert _people("Бойцы Рамзана Кадырова Никиту Журавеля избили.") == [
        "Рамзана Кадырова",
        "Никиту Журавеля",
    ]


def test_a_name_with_initials_is_brought_to_the_nominative_case() -> None:
    """Real cases: «Е.А. Аничкиной», «Ф.Э. Дзержинского» were stored as written."""
    normalizer = RuleBasedMentionNormalizer()
    assert normalizer.normalize_person("Е.А. Аничкиной")[0] == "Е.А. Аничкина"
    assert normalizer.normalize_person("Ф. Э. Дзержинского")[0] == "Ф. Э. Дзержинский"
    # No gender in the name: an ambiguous surname stays as written.
    assert normalizer.normalize_person("В. Волкова")[0] == "В. Волкова"
    _, data = normalizer.normalize_person("Е.А. Аничкиной")
    assert data.last_name == "Аничкина"
    assert data.matching_key == normalizer.normalize_person("Е.А. Аничкина")[1].matching_key


def test_a_dash_opens_a_sentence() -> None:
    """Review request: a capitalized common noun after a dash is not part of a name."""
    assert _people("Он заявил — Приговор Иванову изменили.") == []


def _normalized_people(text: str) -> dict[str, str]:
    document = make_document(text)
    normalizer = RuleBasedMentionNormalizer()
    return {
        mention.surface_text: normalizer.normalize(mention, document).normalized_text
        for mention in RuleBasedEntityExtractor().extract(document)
        if mention.entity_type is EntityType.PERSON
    }


def test_a_surname_alone_in_the_article_still_settles_the_gender() -> None:
    """The case the article evidence exists for: «Телин» says «Федора Телина» is a man."""
    text = "Дело возбудили против юриста Федора Телина. Телин покинул Россию в 2021 году."
    assert _normalized_people(text)["Федора Телина"] == "Федор Телин"


def test_a_surname_repeated_alone_is_still_settled_by_the_full_name() -> None:
    """A bare surname is settled by the full name written elsewhere in the article."""
    text = "Владимир Путин подписал закон. Критику Путина задержали."
    assert _normalized_people(text)["Путина"] == "Путин"


def test_a_person_named_beside_a_document_verdict_gets_no_event() -> None:
    """Real case: «…поддерживает идеологию Брейвика, и даже приложило к делу переведенный
    с норвежского приговор неонацисту» made Брейвик the target of a sentence and a
    politically persecuted candidate.

    Dropping a name that owns a noun («идеологию Брейвика») was tried and rejected: over
    all 20 996 stored articles it cut 1 081 correct links («в отношении Алексея Суслова»,
    «жителя Кинешмы Олега Гребенюка»).
    """
    text = (
        "Суд назначил Светлане Махнорыловой лечение. Следствие сочло, что она поддерживает "
        "идеологию Андреаса Брейвика, и даже приложило к делу переведенный с норвежского "
        "приговор неонацисту."
    )
    assert [people for people in _event_people(text) if "Андреаса Брейвика" in people] == []


def test_a_sentence_document_is_not_a_sentence_event() -> None:
    """«приложило к делу … приговор», «текст приговора»: the verdict is a document here."""
    assert _event_types("Следствие приложило к делу переведенный с норвежского приговор.") == []
    assert _event_types("Журналисты опубликовали копию приговора.") == []
    assert _event_types("Суд огласил приговор активисту.") == ["sentence"]
