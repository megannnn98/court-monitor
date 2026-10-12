"""Which scenario a question is, and the registry a service is built with."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping

from investigation import intent as intent_rules
from investigation.models import FactRequirement, InvestigationIntent, ScenarioName
from investigation.scenarios.base import InvestigationScenario
from investigation.scenarios.person_dossier import PersonDossierScenario
from investigation.scenarios.unnamed_figurant import UnnamedFigurantScenario


class ScenarioRegistry:
    def __init__(self, scenarios: Iterable[InvestigationScenario]) -> None:
        self._by_name: dict[ScenarioName, InvestigationScenario] = {}
        for scenario in scenarios:
            if scenario.name in self._by_name:
                raise ValueError(f"two scenarios named {scenario.name!r}")
            self._by_name[scenario.name] = scenario

    def __iter__(self) -> Iterator[InvestigationScenario]:
        return iter(self._by_name.values())

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def get(self, name: ScenarioName) -> InvestigationScenario:
        return self._by_name[name]

    @property
    def facts(self) -> Mapping[ScenarioName, Mapping[str, FactRequirement]]:
        return {name: scenario.facts for name, scenario in self._by_name.items()}

    def by_rules(
        self, question: str, *, scenario: ScenarioName | None = None
    ) -> InvestigationIntent:
        """The intent of the question by rules. A named scenario is kept to; otherwise a
        question that names a publication and either asks who is unnamed in it or names
        nobody is that publication's figurant, and anything else a person's dossier."""
        if scenario is not None:
            chosen = self.get(scenario).intent_by_rules(question)
            return chosen or InvestigationIntent(scenario=scenario)
        if not {"person_dossier", "identify_unnamed"} <= set(self._by_name):
            # Another set of scenarios: the first, in the order registered, that finds
            # something to look up in the question; else the first one's reading.
            readings = [item.intent_by_rules(question) for item in self]
            for reading, item in zip(readings, self, strict=True):
                if reading is not None and item.subject_of(reading) is not None:
                    return reading
            first = next(iter(self._by_name))
            return next((reading for reading in readings if reading), None) or (
                InvestigationIntent(scenario=first)
            )
        unnamed = "identify_unnamed" in self and self.get("identify_unnamed").intent_by_rules(
            question
        )
        person = self.get("person_dossier").intent_by_rules(question)
        if unnamed and (
            intent_rules.asks_for_unnamed(question) or person is None or not person.subject_name
        ):
            return unnamed
        return person or InvestigationIntent(scenario="person_dossier")


def default_scenarios() -> ScenarioRegistry:
    return ScenarioRegistry([PersonDossierScenario(), UnnamedFigurantScenario()])
