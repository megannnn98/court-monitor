"""Event evidence is local; a source's name or someone else's story is insufficient."""

import pytest

from channel_feed.case_reports import _ARTICLE, review_basis


@pytest.mark.parametrize(
    "quotation",
    [
        "Сегодня против жителя возбудили дело по ст. 275 УК РФ.",
        "Мужчину приговорили к 12 годам колонии за госизмену.",
        "Против антивоенного активиста возбудили уголовное дело.",
        "По статье 207.3 УК РФ вынесен приговор.",
        # The article number ends the sentence: its full stop is not part of the number.
        "Приговор по статье 280.3. Суд назначил колонию.",
        # An earlier administrative penalty is context of the criminal case (ст. 212.1).
        "Ранее его наказали административно за пикет; затем возбуждено дело по ст. 212.1 УК РФ.",
    ],
)
def test_unnamed_criminal_reports_have_local_selection_evidence(quotation: str) -> None:
    assert review_basis(quotation) is not None


@pytest.mark.parametrize(
    "quotation",
    [
        "Мемориал сообщил об уголовном деле.",
        "Политзаключенную оштрафовали по КоАП.",
        "Мужчину оштрафовали за побои.",
        "Жителя задержали за госизмену, а соседу назначили административный штраф.",
        "По ст. 2750 УК РФ вынесен приговор.",
        "По ст. 275.1 УК РФ вынесен приговор.",
        "Жителя осудили за кражу по ст. 158 УК РФ.",
        "Пишет Мемориал: суд оштрафовал политзаключенного.",
    ],
)
def test_unrelated_or_mixed_administrative_context_is_not_auto_selected(quotation: str) -> None:
    assert review_basis(quotation) is None


@pytest.mark.parametrize(
    ("quotation", "article"),
    [
        ("Приговор по статье 280.3. Суд назначил колонию.", "280.3"),
        ("Дело по ст. 20.3.1 КоАП.", "20.3.1"),
        ("По ст. 2750 УК РФ.", "2750"),
        ("Осужден по ч. 1 ст. 207.3 УК РФ.", "207.3"),
    ],
)
def test_the_article_number_is_read_whole_and_without_a_sentence_stop(
    quotation: str, article: str
) -> None:
    assert _ARTICLE.findall(quotation) == [article]
