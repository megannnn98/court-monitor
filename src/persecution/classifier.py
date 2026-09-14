from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from persecution.models import (
    PersecutionClassification,
    PersecutionClassificationStatus,
    PersecutionEvidenceType,
)

POLITICAL_ARTICLES = {
    "205.2",  # Содействие терроризму
    "207.3",  # Фейки об армии
    "208",  # Насильственное захват власти
    "212",  # Призывы к массовым беспорядкам
    "274.1",  # Нецелевое использование госсредств
    "275",  # Государственная измена
    "280",  # Призывы к экстремизму
    "280.3",  # Дискредитация армии
    "280.4",  # Призывы к санкциям
    "281",  # Диверсии
    "282",  # Экстремизм
    "282.1",  # Организация экстремистской деятельности
    "282.2",  # Участие в экстремистской деятельности
    "283.1",  # Неполучение сведений гостайны
    "284.1",  # Нежелательная организация
    "284.2",  # Непрекращение связи с нежелательной организацией
    "354",  # Реабилитация нацизма
}

POLITICAL_KEYWORDS = {
    "политический",
    "политзаключенный",
    "политический заключенный",
    "политическое преследование",
    "политически мотивированное",
    "правозащитник",
    "правозащитница",
    "правозащитный",
    "Мемориал",
    "ОВД-Инфо",
    "SOTA",
    "Mediazona",
    "Медиазона",
    "антивоенный",
    "антивоенная",
    "против войны",
    "демократия",
    "демократический",
    "оппозиция",
    "оппозиционный",
    "протест",
    "протестный",
    "митинг",
    "пикет",
    "свобода слова",
    "свобода собраний",
    "инакомыслящий",
    "диссидент",
    "критик власти",
    "критика власти",
    "иностранный агент",
    "нежелательная организация",
    "ЛГБТ",
    "гей",
    "лесбиянка",
    "транссендер",
    "религиозное преследование",
    "Свидетели Иеговы",
    "вероисповедание",
}


class RuleBasedPersecutionClassifier:
    classifier_name = "rule-based-persecution-classifier"
    classifier_version = "1.0.0"

    def classify(
        self,
        *,
        person_id: int,
        events: Sequence[dict[str, Any]],
        articles: Sequence[dict[str, Any]],
    ) -> PersecutionClassification:
        evidence_types: list[PersecutionEvidenceType] = []
        reasons: list[str] = []

        for event in events:
            event_dict = event if isinstance(event, dict) else vars(event)
            event_type = event_dict.get("event_type", "")
            attributes = event_dict.get("attributes", {})

            if event_type in {"arrest", "detention", "charge", "sentence"}:
                charge = attributes.get("charge", "")
                if self._is_political_charge(charge):
                    evidence_types.append(PersecutionEvidenceType.POLITICAL_CHARGE)
                    reasons.append(f"Политическая статья: {charge}")

        for article in articles:
            article_dict = article if isinstance(article, dict) else vars(article)
            text = article_dict.get("text", "")
            title = article_dict.get("title", "")
            full_text = f"{title} {text}".lower()

            if self._contains_political_keywords(full_text):
                evidence_types.append(PersecutionEvidenceType.POLITICAL_ARTICLE)
                reasons.append("Статья содержит признаки политического преследования")

            if self._mentions_human_rights(full_text):
                evidence_types.append(PersecutionEvidenceType.HUMAN_RIGHTS_DEFENDER)
                reasons.append("Упоминание правозащитной деятельности")

            if self._mentions_journalism(full_text):
                evidence_types.append(PersecutionEvidenceType.JOURNALIST)
                reasons.append("Упоминание журналистской деятельности")

            if self._mentions_anti_war(full_text):
                evidence_types.append(PersecutionEvidenceType.ANTI_WAR_ACTIVITY)
                reasons.append("Антивоенная деятельность")

            if self._mentions_religious(full_text):
                evidence_types.append(PersecutionEvidenceType.RELIGIOUS_PERSECUTION)
                reasons.append("Религиозное преследование")

            if self._mentions_lgbt(full_text):
                evidence_types.append(PersecutionEvidenceType.LGBT_PERSECUTION)
                reasons.append("Преследование по признаку ЛГБТ")

        evidence_types = list(dict.fromkeys(evidence_types))

        if not evidence_types:
            status = PersecutionClassificationStatus.NON_POLITICAL
            confidence = 0.0
        elif PersecutionEvidenceType.POLITICAL_CHARGE in evidence_types or len(evidence_types) >= 2:
            status = PersecutionClassificationStatus.POLITICAL
            confidence = min(0.9, 0.7 + (len(evidence_types) - 1) * 0.1)
        else:
            # A single soft keyword signal (no political charge, no
            # corroborating second signal) isn't strong enough to call
            # POLITICAL on its own — flag for review instead.
            status = PersecutionClassificationStatus.UNCERTAIN
            confidence = 0.6

        return PersecutionClassification(
            person_id=person_id,
            status=status,
            confidence=confidence,
            reasons=reasons,
            evidence_types=evidence_types,
            classifier_name=self.classifier_name,
            classifier_version=self.classifier_version,
            classified_at=datetime.now(UTC),
        )

    def _is_political_charge(self, charge: str) -> bool:
        if not charge:
            return False

        for article in POLITICAL_ARTICLES:
            pattern = rf"(?<![\d.]){re.escape(article)}(?![\d.])"
            if re.search(pattern, charge):
                return True

        return False

    def _contains_political_keywords(self, text: str) -> bool:
        text_lower = text.lower()
        for keyword in POLITICAL_KEYWORDS:
            if keyword in text_lower:
                return True
        return False

    def _mentions_human_rights(self, text: str) -> bool:
        return any(
            word in text for word in ["правозащит", "мемориал", "ова-инфо", "овд-инфо", "sota"]
        )

    def _mentions_journalism(self, text: str) -> bool:
        return any(
            word in text
            for word in ["журналист", "медиа", "сми", "пресса", "редактор", "корреспондент"]
        )

    def _mentions_anti_war(self, text: str) -> bool:
        return any(
            phrase in text for phrase in ["антивоен", "против войны", "нет войне", "мир без войны"]
        )

    def _mentions_religious(self, text: str) -> bool:
        return any(
            word in text
            for word in ["свидетели иеговы", "религиозн", "вероисповедан", "церквь", "мечеть"]
        )

    def _mentions_lgbt(self, text: str) -> bool:
        return any(word in text for word in ["лгбт", "гей", "лесбиян", "транссенд"])
