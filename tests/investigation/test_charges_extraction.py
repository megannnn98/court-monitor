"""A web lead becomes a charge only through a grounded structured answer."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from investigation.models import EvidenceItem, InvestigationIntent, PersonSubject, Subject
from investigation.nodes import EVIDENCE_SHOWN, Toolbox, _normalised_charges, extract_charges
from investigation.scenarios.base import ScenarioView
from investigation.scenarios.person_dossier import PersonDossierScenario
from investigation.scenarios.registry import default_scenarios
from investigation.services.model import ModelReply
from investigation.services.retrieval import RetrieverRegistry
from llm.chat import ModelError


class Model:
    def __init__(self, answer: dict[str, object]) -> None:
        self.answer = answer
        self.user = ""

    def ask(self, **kwargs: object) -> ModelReply:
        self.user = str(kwargs["user"])
        return ModelReply(text=json.dumps(self.answer))


class FailingModel(Model):
    def ask(self, **kwargs: object) -> ModelReply:
        raise ModelError("unavailable")


def lead(text: str) -> EvidenceItem:
    return EvidenceItem(
        ref="E17",
        source_type="external",
        source_id="web:tavily",
        url="https://example.test/case",
        text=text,
        leads=("charges",),
    )


def extract(model: Model, item: EvidenceItem) -> dict[str, Any]:
    return asyncio.run(
        extract_charges(
            {
                "subject": Subject(name="Мария Бонцлер", entity_key="мария бонцлер"),
                "evidence": [item],
            },
            Toolbox(RetrieverRegistry([]), default_scenarios(), model=model),
        )
    )


def test_a_charge_is_confirmed_only_from_its_cited_text() -> None:
    model = Model({"supported": True, "value": "ч. 3 ст. 207.3 УК РФ", "evidence_refs": ["E17"]})

    update = extract(model, lead("Марию Бонцлер обвинили по ч. 3 ст. 207.3 УК РФ."))

    assert "Вопрос о: Мария Бонцлер" in model.user
    assert "https://example.test" not in model.user
    (confirmed,) = update["evidence"]
    assert (confirmed.fact, confirmed.value, confirmed.ref) == (
        "charges",
        "ч. 3 ст. 207.3 УК РФ",
        "E17",
    )


def test_an_incidental_article_does_not_confirm_a_charge_or_stop_the_follow_up() -> None:
    model = Model({"supported": False, "value": None, "evidence_refs": []})
    item = lead("В материале о другом деле упомянута ст. 207.3 УК РФ.")

    update = extract(model, item)

    assert "evidence" not in update


def test_every_cited_ref_must_hold_the_value_and_an_unseen_ref_is_refused() -> None:
    first = lead("Марию обвинили по ст. 280 УК РФ.")
    second = lead("Другой человек проходит по ст. 207.3 УК РФ.").model_copy(update={"ref": "E18"})
    model = Model({"supported": True, "value": "ст. 280 УК РФ", "evidence_refs": ["E17", "E18"]})

    update = asyncio.run(
        extract_charges(
            {
                "subject": Subject(name="Мария Бонцлер", entity_key="мария бонцлер"),
                "evidence": [first, second],
            },
            Toolbox(RetrieverRegistry([]), default_scenarios(), model=model),
        )
    )

    assert "evidence" not in update
    foreign = Model({"supported": True, "value": "ст. 280 УК РФ", "evidence_refs": ["E99"]})
    assert "evidence" not in extract(foreign, first)


def test_second_and_full_spelled_out_charge_are_grounded_but_hidden_tail_is_not() -> None:
    model = Model({"supported": True, "value": "ч. 3 ст. 207.3 УК РФ", "evidence_refs": ["E17"]})
    assert "evidence" in extract(
        model, lead("Сначала ст. 280 УК РФ, затем по части 3 статьи 207.3 УК РФ.")
    )
    tail = "x" * EVIDENCE_SHOWN + " по части 3 статьи 207.3 УК РФ"
    assert "evidence" not in extract(model, lead(tail))


def test_supported_without_refs_or_without_the_value_is_refused() -> None:
    item = lead("Марию обвинили по ст. 280 УК РФ.")
    assert "evidence" not in extract(
        Model({"supported": True, "value": "ст. 280 УК РФ", "evidence_refs": []}), item
    )
    assert "evidence" not in extract(
        Model({"supported": True, "value": "ст. 207.3 УК РФ", "evidence_refs": ["E17"]}), item
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("оштрафован по ч. 2 ст. 20.3.3 КоАП РФ", set()),
        ("оштрафован по ч. 2 ст. 20.3.3 УК РФ", {"ч. 2 ст. 20.3.3 УК РФ"}),
        ("опубликовал пост 12 января", set()),
        ("текст 3 раза", set()),
        ("100 мест 5 рядов", set()),
        ("осуждён по ст. 159 ГК", set()),
        ("по статьёй 280 УК", {"ст. 280 УК РФ"}),
    ],
)
def test_only_criminal_code_charges_are_normalised(text: str, expected: set[str]) -> None:
    assert _normalised_charges(text) == expected


def test_a_model_value_with_a_non_criminal_code_or_two_charges_is_refused() -> None:
    koap = lead("Марию оштрафовали по ч. 2 ст. 20.3.3 КоАП РФ.")
    assert "evidence" not in extract(
        Model({"supported": True, "value": "ч. 2 ст. 20.3.3 КоАП РФ", "evidence_refs": ["E17"]}),
        koap,
    )
    two = lead("Марию обвинили по ст. 280 УК РФ и ст. 207.3 УК РФ.")
    assert "evidence" not in extract(
        Model(
            {"supported": True, "value": "ст. 280 УК РФ, ст. 207.3 УК РФ", "evidence_refs": ["E17"]}
        ),
        two,
    )


def test_a_model_error_leaves_the_ref_unasked() -> None:
    update = extract(FailingModel({}), lead("Марию обвинили по ст. 280 УК РФ."))

    assert "charges_asked" not in update


def test_an_unconfirmed_charge_lead_keeps_a_follow_up_but_a_confirmed_charge_closes_it() -> None:
    subject = Subject(name="Мария Бонцлер", entity_key="мария бонцлер")
    intent = InvestigationIntent(
        scenario="person_dossier", subject_name=subject.name, requested_facts=["charges"]
    )
    subject_ref = PersonSubject(name=subject.name)
    scenario = PersonDossierScenario()
    pending = scenario.assess(
        ScenarioView(
            intent=intent,
            subject_ref=subject_ref,
            subject=subject,
            evidence=[lead("ст. 280 УК РФ")],
        )
    )
    confirmed = scenario.assess(
        ScenarioView(
            intent=intent,
            subject_ref=subject_ref,
            subject=subject,
            evidence=[
                lead("ст. 280 УК РФ").model_copy(
                    update={"fact": "charges", "value": "ст. 280 УК РФ"}
                )
            ],
        )
    )

    assert any(query.fact == "charges" for query in pending.follow_up_queries)
    assert not any(query.fact == "charges" for query in confirmed.follow_up_queries)
