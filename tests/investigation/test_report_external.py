"""What the model is shown of what was found outside the database.

A text found outside that nobody asked for says the person's name and little else: a
namesake's page, a registry card, a sportsman's card. Left in the block, the model turns
it into a sentence about the person it was asked about, and a sentence is read as a fact.
Such a text is not shown at all. Neither is any text from outside when the database does
not know whom the dossier is about — then there is nobody to tell a namesake apart from.

The evidence itself stays in the result: what the run found is not thrown away with what
the model was allowed to read.
"""

from investigation.models import EvidenceItem
from investigation.nodes import evidence_for_model, report_messages

SAME_NAME = "Игорь Карлов"


def _local(ref: str, text: str = "Приговорён к 18 годам колонии строгого режима.") -> EvidenceItem:
    return EvidenceItem(
        source_type="publication",
        source_id="publications_fts",
        url=f"https://news.example.test/{ref}",
        article_id=1,
        span_start=0,
        span_end=10,
        text=text,
        ref=ref,
    )


def _outside(ref: str, text: str, *, lead: bool = False) -> EvidenceItem:
    return EvidenceItem(
        source_type="external",
        source_id="web:tavily",
        external_id=ref,
        url=f"https://other.example.test/{ref}",
        text=text,
        leads=("charges",) if lead else (),
        ref=ref,
        score_type="source_order",
    )


def test_a_namesake_from_the_web_is_not_shown_to_the_model() -> None:
    """The page of a footballer with the same surname, found by the web, nobody asked
    for: it must not reach the block the model reads."""
    namesake = _outside("E4", f"{SAME_NAME} — карточка игрока: статистика, голы, матчи.")

    readable, withheld = evidence_for_model([_local("E1"), namesake], subject_settled=True)

    assert [item.ref for item in readable] == ["E1"]
    assert withheld is not None and "никто не спрашивал" in withheld


def test_a_web_text_the_run_asked_for_is_shown() -> None:
    """The one text from outside that answers what the run went looking for is shown."""
    lead = _outside("E2", f"{SAME_NAME} вменена статья 280 УК РФ.", lead=True)

    readable, withheld = evidence_for_model([_local("E1"), lead], subject_settled=True)

    assert [item.ref for item in readable] == ["E1", "E2"]
    assert withheld is None


def test_nothing_from_outside_reaches_the_model_when_the_database_has_no_such_person() -> None:
    """There is nobody to tell a namesake apart from, so no text from outside is read."""
    lead = _outside("E2", f"{SAME_NAME} вменена статья 280 УК РФ.", lead=True)
    namesake = _outside("E4", f"{SAME_NAME} — карточка игрока.")

    readable, withheld = evidence_for_model([_local("E1"), lead, namesake], subject_settled=False)

    assert [item.ref for item in readable] == ["E1"]
    assert withheld is not None and "нет человека" in withheld


def test_nothing_is_withheld_when_there_is_nothing_from_outside() -> None:
    readable, withheld = evidence_for_model([_local("E1"), _local("E2")], subject_settled=True)

    assert len(readable) == 2
    assert withheld is None


def test_what_the_model_reads_holds_no_web_text_nobody_asked_for() -> None:
    """The filter is where the block is assembled: read what the model reads, not what
    the run found."""
    namesake = _outside("E4", f"{SAME_NAME} — карточка игрока.")
    lead = _outside("E2", f"{SAME_NAME} вменена статья 280 УК РФ.", lead=True)

    readable, _ = evidence_for_model([_local("E1"), namesake, lead], subject_settled=True)
    _, user = report_messages(f"Что известно про {SAME_NAME}?", readable)

    assert "карточка игрока" not in user
    assert "вменена статья 280" in user
    assert "[E1]" in user
