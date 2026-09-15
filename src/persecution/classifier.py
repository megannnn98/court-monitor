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
    "212.1",  # Неоднократное нарушение порядка проведения акции («дадинская»)
    "274.1",  # Нецелевое использование госсредств
    "275",  # Государственная измена
    "280",  # Призывы к экстремизму
    "280.1",  # Призывы к нарушению территориальной целостности
    "280.3",  # Дискредитация армии
    "280.4",  # Призывы к санкциям
    "281",  # Диверсии
    "282",  # Экстремизм
    "282.1",  # Организация экстремистской деятельности
    "282.2",  # Участие в экстремистской деятельности
    "282.3",  # Финансирование экстремизма
    "282.4",  # Демонстрация запрещённой символики (повторная)
    "283.1",  # Неполучение сведений гостайны
    "284.1",  # Нежелательная организация
    "284.2",  # Непрекращение связи с нежелательной организацией
    "330.1",  # Уклонение от обязанностей «иностранного агента»
    "354",  # Реабилитация нацизма
    "354.1",  # Реабилитация нацизма / символы воинской славы
}

# Administrative articles of the same repressive practice (КоАП РФ).
POLITICAL_ADMINISTRATIVE_ARTICLES = {
    "19.34",  # Нарушение порядка деятельности «иностранного агента»
    "20.2",  # Нарушение порядка проведения публичного мероприятия
    "20.3",  # Демонстрация запрещённой символики
    "20.3.1",  # Возбуждение ненависти
    "20.3.3",  # Дискредитация армии
    "20.33",  # Участие в деятельности нежелательной организации
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
    "трансгендер",
    "религиозное преследование",
    "Свидетели Иеговы",
    "вероисповедание",
}


# Short abbreviations only count as whole words («сми», not «Смирнов»; «гей»,
# not «Сергей»). Every other keyword counts at the start of a word, so stems
# («журналист», «антивоен») still match their inflected forms.
_WHOLE_WORD_KEYWORDS = frozenset({"гей", "геи", "сми", "медиа", "sota"})


def _keyword_pattern(keywords: Sequence[str]) -> re.Pattern[str]:
    alternatives = sorted(
        (
            rf"{re.escape(keyword.lower())}(?!\w)"
            if keyword.lower() in _WHOLE_WORD_KEYWORDS
            else re.escape(keyword.lower())
            for keyword in keywords
        ),
        key=len,
        reverse=True,
    )
    return re.compile(rf"(?<!\w)(?:{'|'.join(alternatives)})")


_POLITICAL_KEYWORDS_PATTERN = _keyword_pattern(sorted(POLITICAL_KEYWORDS))
# News outlets («сообщили ОВД-Инфо», «пишет SOTA») are attribution, not evidence.
_HUMAN_RIGHTS_PATTERN = _keyword_pattern(["правозащит", "мемориал"])
_JOURNALISM_PATTERN = _keyword_pattern(
    ["журналист", "медиа", "сми", "пресса", "редактор", "корреспондент"]
)
_ANTI_WAR_PATTERN = _keyword_pattern(["антивоен", "против войны", "нет войне", "мир без войны"])
_RELIGIOUS_PATTERN = _keyword_pattern(
    ["свидетели иеговы", "свидетелей иеговы", "религиозн", "вероисповедан", "церков", "мечет"]
)
_LGBT_PATTERN = _keyword_pattern(["лгбт", "гей", "геи", "лесбиян", "трансгендер"])


class RuleBasedPersecutionClassifier:
    classifier_name = "rule-based-persecution-classifier"
    # 1.1.0: evidence windows stop at sentences that mention other persons;
    # keywords match at word start («гей» no longer matches «Сергей»).
    # 1.2.0: inside one sentence, windows stop at the clause of another person.
    # 1.3.0: the article title counts only when it names the person; without a
    # persecution event of their own a person is at most UNCERTAIN; news outlet names
    # are not evidence; political articles are matched within their own code
    # (УК or КоАП), with the articles OVD-Info practice uses (212.1, 282.3, 330.1,
    # КоАП 20.3.3, 20.33, 19.34, …).
    classifier_version = "1.3.0"

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
        elif not events:
            # Political context around a person with no persecution event of their own
            # (a lawyer, a judge, a source quoted in the article) is not evidence that
            # this person is persecuted: a human decides.
            status = PersecutionClassificationStatus.UNCERTAIN
            confidence = 0.5
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
        # An article number only counts in its own code: КоАП ст. 282 is not УК ст. 282.
        articles = (
            POLITICAL_ADMINISTRATIVE_ARTICLES if "коап" in charge.lower() else POLITICAL_ARTICLES
        )
        for article in articles:
            pattern = rf"(?<![\d.]){re.escape(article)}(?![\d.])"
            if re.search(pattern, charge):
                return True

        return False

        for article in POLITICAL_ARTICLES:
            pattern = rf"(?<![\d.]){re.escape(article)}(?![\d.])"
            if re.search(pattern, charge):
                return True

        return False

    def _contains_political_keywords(self, text: str) -> bool:
        return _POLITICAL_KEYWORDS_PATTERN.search(text.lower()) is not None

    def _mentions_human_rights(self, text: str) -> bool:
        return _HUMAN_RIGHTS_PATTERN.search(text) is not None

    def _mentions_journalism(self, text: str) -> bool:
        return _JOURNALISM_PATTERN.search(text) is not None

    def _mentions_anti_war(self, text: str) -> bool:
        return _ANTI_WAR_PATTERN.search(text) is not None

    def _mentions_religious(self, text: str) -> bool:
        return _RELIGIOUS_PATTERN.search(text) is not None

    def _mentions_lgbt(self, text: str) -> bool:
        return _LGBT_PATTERN.search(text) is not None
