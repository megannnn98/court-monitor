"""The conservative eligibility gate: it rules out only digests and administrative-,
election- and organization-only articles, and in doubt lets an article through."""

from __future__ import annotations

import pytest

from evaluation.relevance.eligibility_gate import (
    ADMINISTRATIVE,
    DIGEST,
    ELECTION,
    ORGANIZATION,
    gate,
    is_organization_only,
)

# Article 909 of the development train, a criminal verdict for speech.
VERDICT_909 = (
    "Тихоокеанский флотский военный суд оштрафовал жительницу Камчатского края на 480 тысяч "
    "рублей и запретил ей на 3 года размещать обращения и материалы в интернете. Женщину "
    "признали виновной по статьям о публичном оправдании терроризма и призывах к "
    "экстремистской деятельности из-за аудиосообщений в WhatsApp."
)


def test_the_criminal_verdict_of_909_goes_to_the_classifier() -> None:
    assert gate("Военный суд оштрафовал жительницу Камчатки", VERDICT_909) is None


@pytest.mark.parametrize(
    "verdict",
    [
        "Мужчина признан виновным в призывах к экстремистской деятельности.",
        "Женщина признана виновной в оправдании терроризма.",
        "Оба признаны виновными в экстремистской деятельности.",
        "Суд признал виновным местного жителя.",
        "Суд признала виновной журналистку.",
        "Суд признал её виновной.",
        "Суд признали виновными двоих активистов.",
    ],
)
def test_a_verdict_lets_the_article_through(verdict: str) -> None:
    assert gate("Новость", f"Протокол по КоАП не составляли. {verdict}") is None


@pytest.mark.parametrize(
    "text",
    [
        "Суд признал организацию «Альфа» экстремистской и запретил ее деятельность.",
        "Генпрокуратура признала британский фонд «Бета» нежелательным.",
        "Верховный суд признал движение «Гамма» террористической организацией.",
        "Суд ликвидировал общество «Дельта».",
    ],
)
def test_an_organization_only_action_is_ruled_out(text: str) -> None:
    assert gate("Решение суда", text) == ORGANIZATION


@pytest.mark.parametrize(
    "text",
    [
        "Человека признали виновным в экстремистской деятельности.",
        "Человека осудили за участие в экстремистской организации.",
        "Человека признали виновным в финансировании террористической организации.",
    ],
)
def test_a_person_is_not_an_organization(text: str) -> None:
    assert not is_organization_only(text)
    assert gate("Новость", text) is None


def test_a_person_named_beside_no_organization_is_not_organization_only() -> None:
    text = "Женщину признали виновной в призывах к экстремистской деятельности."
    assert not is_organization_only(text)


def test_a_criminal_fine_is_not_ruled_out() -> None:
    text = "Суд назначил уголовный штраф 300 тысяч рублей по статье 280.3 УК."
    assert gate("Штраф", text) is None


def test_an_administrative_fine_is_ruled_out() -> None:
    text = "Суд оштрафовал жительницу на 30 тысяч рублей по ч. 1 ст. 20.3.3 КоАП."
    assert gate("Штраф", text) == ADMINISTRATIVE


def test_an_administrative_case_beside_a_criminal_one_goes_through() -> None:
    text = "Его оштрафовали по КоАП, а затем возбудили уголовное дело."
    assert gate("Новость", text) is None


def test_a_bare_fine_is_not_called_administrative() -> None:
    assert gate("Новость", "Суд оштрафовал жительницу на 480 тысяч рублей.") is None


def test_an_election_only_article_is_ruled_out() -> None:
    text = "Избирком отказал в регистрации кандидату от партии."
    assert gate("Выборы", text) == ELECTION


def test_an_election_article_with_a_criminal_case_goes_through() -> None:
    text = "Кандидата задержали по уголовному делу о фейках."
    assert gate("Выборы", text) is None


@pytest.mark.parametrize(
    "title",
    ["Главные новости к вечеру", "А что случилось? Главные новости за 6 сентября", "Судный день"],
)
def test_a_digest_is_ruled_out_even_with_a_case(title: str) -> None:
    assert gate(title, "— Активиста приговорили к 5 годам колонии — Погода") == DIGEST


def test_a_long_article_is_not_a_digest() -> None:
    text = "Суд приговорил активиста. " * 20 + "Защита обжалует приговор."
    assert gate("Приговор активисту", text) is None


def test_an_action_without_an_organization_named_is_not_organization_only() -> None:
    text = "Суд признал экстремистским ролик блогера."
    assert not is_organization_only(text)
    assert gate("Решение суда", text) is None


# Article 1040 of the development validation, the person's name left out: a suspended
# sentence for donations to «undesirable» organizations, which gate v1 ruled out.
SUSPENDED_1040 = (
    "33-летней жительнице вменили 13 переводов на общую сумму в 34 395 рублей на счета двух "
    "религиозных организаций, зарегистрированных в Украине и признанных «нежелательными» в "
    "России. В суде сообщили, что она переводила им деньги с декабря 2022 года по февраль "
    "2023-го. Суд счел смягчающим обстоятельством то, что у нее на иждивении трое детей."
)
TITLE_1040 = (
    "Жительнице Краснодарского края назначили условный срок за 13 переводов «нежелательным» "
    "религиозным организациям из Украины"
)


def test_the_suspended_sentence_of_1040_goes_to_the_classifier() -> None:
    assert gate(TITLE_1040, SUSPENDED_1040) is None
    assert not is_organization_only(f"{TITLE_1040}. {SUSPENDED_1040}")


@pytest.mark.parametrize(
    "text",
    [
        "Человеку дали условный срок за переводы нежелательной организации.",
        "Человеку вменили финансирование террористической организации.",
        "Ему назначили условное наказание.",
        "Прокурор попросил реальный срок.",
        "Ей вменяется участие в запрещенной организации.",
        "Ему предъявили обвинение.",
    ],
)
def test_a_sentence_or_a_charge_lets_the_article_through(text: str) -> None:
    assert gate("Новость", text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Мужчина перевел деньги фонду, который признали нежелательным.",
        "Она пожертвовала организации, которую признали экстремистской.",
        "Он финансировал организацию, которую признали террористической.",
        "Он участвовал в организации, которую признали экстремистской.",
        "Она состояла в движении, которое признали экстремистским.",
    ],
)
def test_a_persons_tie_to_an_organization_is_not_organization_only(text: str) -> None:
    assert not is_organization_only(text)
    assert gate("Новость", text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Организацию признали нежелательной.",
        "Фонд ликвидировали.",
        "Против организации подан иск о признании ее экстремистской.",
        "Суд конфисковал имущество общества «Эпсилон».",
    ],
)
def test_a_process_against_the_organization_itself_is_ruled_out(text: str) -> None:
    assert gate("Решение суда", text) == ORGANIZATION


@pytest.mark.parametrize(
    "phrase",
    [
        "Ей назначили условный срок.",
        "Ему назначили условное наказание.",
        "Прокурор попросил реальный срок.",
        "Ему вменили два эпизода.",
        "Ей вменяется один эпизод.",
        "Им вменяют три эпизода.",
    ],
)
def test_a_sentence_or_a_charge_outweighs_an_administrative_mention(phrase: str) -> None:
    assert gate("Новость", f"Ранее его штрафовали по КоАП. {phrase}") is None
