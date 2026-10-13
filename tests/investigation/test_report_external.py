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


def _channel(ref: str, text: str) -> EvidenceItem:
    """A quote of a Telegram channel. External too, and not the web."""
    return EvidenceItem(
        source_type="external",
        source_id="telegram:tg-pzk_memorial",
        external_id=ref,
        url=f"https://t.me/pzk_memorial/{ref}",
        text=text,
        ref=ref,
        score_type="source_order",
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


def test_a_channel_quote_without_a_lead_still_reaches_the_model() -> None:
    """A channel's quote is about a person the run already settled on and the model has
    always read it. Only what the web found is screened, and the mode without a web
    reaches the model exactly what it reached before the web existed."""
    channel = _channel("E3", f"{SAME_NAME} выступил в суде.")

    readable, withheld = evidence_for_model([_local("E1"), channel], subject_settled=True)

    assert [item.ref for item in readable] == ["E1", "E3"]
    assert withheld is None


def test_when_the_database_has_no_such_person_a_channel_quote_still_reaches_the_model() -> None:
    """Only the web is withheld: there is a difference between a page the web found about
    an unknown person and a message in a channel the run is reading."""
    channel = _channel("E3", f"{SAME_NAME} выступил в суде.")
    web = _outside("E4", f"{SAME_NAME} — карточка игрока.")

    readable, withheld = evidence_for_model([_local("E1"), channel, web], subject_settled=False)

    assert [item.ref for item in readable] == ["E1", "E3"]
    assert withheld is not None and "нет человека" in withheld


def test_a_channel_quote_is_not_sunk_by_the_webs_rule() -> None:
    """The rule is asked of the rank itself: with everything else equal, a text the web
    brought that nobody asked for ranks last, and a channel's quote does not — it stands
    where the fused rank put it, as before the web existed."""
    from investigation.services.evidence import _rank

    channel = _channel("c1", f"{SAME_NAME} упомянут в посте.")
    web = _outside("w1", f"{SAME_NAME} — карточка игрока.")

    assert _rank(web, {}, {}) > _rank(channel, {}, {})

    # A web text the run asked for is ranked as it was before the rule: with the channel's.
    web_asked = web.model_copy(update={"leads": ("charges",)})
    assert _rank(web_asked, {}, {}) == _rank(channel, {}, {})
