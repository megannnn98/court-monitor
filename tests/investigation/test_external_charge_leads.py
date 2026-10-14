"""A Criminal Code reference is a lead only when it is about the requested person."""

from __future__ import annotations

import pytest

from investigation.models import (
    EvidenceItem,
    InvestigationIntent,
    InvestigationQuery,
    PersonSubject,
    Subject,
)
from investigation.scenarios.base import ScenarioView
from investigation.scenarios.person_dossier import PersonDossierScenario
from investigation.services.external import SourceSearchRetriever


def charges_leads(text: str) -> tuple[str, ...]:
    return SourceSearchRetriever._leads(
        InvestigationQuery(kind="keywords", name="Мария Бонцлер", fact="charges"), text
    )


@pytest.mark.parametrize(
    "text",
    [
        "Марию Бонцлер обвинили по ч. 3 ст. 207.3 УК РФ.",
        "Марию Бонцлер обвинили по статье 207.3 УК РФ.",
        "Против Марии Бонцлер возбудили уголовное дело по статье 207.3 УК РФ.",
        "Задержана Мария Бонцлер. Ей вменяют статью 207.3 УК РФ.",
        "Суд приговорил Марию Бонцлер к сроку по статье 207.3 УК РФ.",
    ],
)
def test_contextual_charge_lead_handles_normal_accusation_wording(text: str) -> None:
    assert charges_leads(text) == ("charges",)


@pytest.mark.parametrize(
    "text",
    [
        "Иван Петров обвинён по ст. 360 УК, а Мария Бонцлер дала интервью о выборах.",
        "Статья 207.3 УК РФ предусматривает до 15 лет. Мария Бонцлер рассказала о выборах.",
        (
            "Посол вымышленной страны сообщил о статье 360 УК РФ в международном деле. "
            "Мария Бонцлер дала отдельный комментарий."
        ),
    ],
)
def test_an_article_about_someone_else_or_a_general_legal_text_is_not_a_lead(text: str) -> None:
    assert charges_leads(text) == ()


def test_a_rejected_charge_lead_does_not_close_the_charge_follow_up() -> None:
    text = "Иван Петров обвинён по ст. 360 УК, а Мария Бонцлер дала интервью о выборах."
    intent = InvestigationIntent(
        scenario="person_dossier", subject_name="Мария Бонцлер", requested_facts=["charges"]
    )
    assessment = PersonDossierScenario().assess(
        ScenarioView(
            intent=intent,
            subject_ref=PersonSubject(name="Мария Бонцлер"),
            subject=Subject(name="Мария Бонцлер", entity_key="мария бонцлер"),
            evidence=[
                EvidenceItem(
                    source_type="external",
                    source_id="web:test",
                    text=text,
                    leads=charges_leads(text),
                )
            ],
        )
    )

    assert any(query.fact == "charges" for query in assessment.follow_up_queries)
