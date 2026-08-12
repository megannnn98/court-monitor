"""Unit tests for press and case event classification (task §7, §8)."""

from __future__ import annotations

from court_monitor.extraction.event_classifier import (
    CaseEventType,
    PressEventType,
    classify_case_event,
    classify_press_event,
)

# ── press release classification ──


def test_press_verdict_explicit():
    cls = classify_press_event("Суд вынес приговор 02.04.2026 по статье 205.1")
    assert cls.event_type == PressEventType.sentence_delivered


def test_press_verdict_condemnation():
    cls = classify_press_event("Суд приговорил Иванова к 8 годам колонии")
    assert cls.event_type == PressEventType.sentence_delivered


def test_press_verdict_convicted():
    cls = classify_press_event("Осужден за экстремизм, назначено 5 лет")
    assert cls.event_type == PressEventType.sentence_delivered


def test_press_verdict_sentence_assigned():
    cls = classify_press_event("Назначил наказание в виде лишения свободы")
    assert cls.event_type == PressEventType.sentence_delivered


def test_press_postanovlenie_about_preventive_is_not_sentence():
    """Task §7: "вынес постановление" alone must NOT become sentence_delivered."""
    cls = classify_press_event(
        "Судья вынес постановление об избрании меры пресечения в виде заключения под стражу"
    )
    assert cls.event_type == PressEventType.preventive_measure_selected


def test_press_postanovlenie_about_verdict_is_sentence():
    """Only when "постановление" is tied to "приговор" does it count as sentence."""
    cls = classify_press_event("Суд вынес постановление об обвинительном приговоре")
    assert cls.event_type == PressEventType.sentence_delivered


def test_press_preventive_measure():
    cls = classify_press_event("Избрана мера пресечения: содержание под стражей")
    assert cls.event_type == PressEventType.preventive_measure_selected


def test_press_detained():
    cls = classify_press_event("Обвиняемый заключен под стражу в зале суда")
    assert cls.event_type == PressEventType.preventive_measure_selected


def test_press_detention_extended():
    cls = classify_press_event("Продлен срок содержания под стражей до 6 месяцев")
    assert cls.event_type == PressEventType.preventive_measure_selected


def test_press_hearing_scheduled():
    cls = classify_press_event("Назначено судебное заседание на 15 июня")
    assert cls.event_type == PressEventType.hearing_scheduled


def test_press_hearing_appointed():
    cls = classify_press_event("Суд назначено слушание дела")
    assert cls.event_type == PressEventType.hearing_scheduled


def test_press_appeal_decided():
    cls = classify_press_event("Рассмотрена апелляционная жалоба, приговор оставлен в силе")
    assert cls.event_type == PressEventType.appeal_decided


def test_press_appeal_complaint():
    cls = classify_press_event("Рассмотрена жалоба защиты")
    assert cls.event_type == PressEventType.appeal_decided


def test_press_case_received():
    cls = classify_press_event("В суд поступило уголовное дело для рассмотрения")
    assert cls.event_type == PressEventType.case_received


def test_press_unknown_when_no_match():
    cls = classify_press_event("Сегодня в городе прошла акция протеста")
    assert cls.event_type == PressEventType.unknown
    assert cls.confidence == 0.0


# ── mixed appeal/sentence regression (task §8) ──


def test_press_appeal_wins_over_past_sentence():
    """'Ранее осужден' is background; 'рассмотрена апелляционная жалоба' is current."""
    cls = classify_press_event(
        "Ранее Иванов был осужден на 8 лет. Рассмотрена апелляционная жалоба."
    )
    assert cls.event_type == PressEventType.appeal_decided


def test_press_appeal_left_without_satisfaction():
    """'осужден' + 'апелляционная жалоба оставлена без удовлетворения' → appeal."""
    cls = classify_press_event(
        "Приговором суда Иванов осужден. Апелляционная жалоба оставлена без удовлетворения."
    )
    assert cls.event_type == PressEventType.appeal_decided


def test_press_plain_sentence_still_works():
    """Regression: plain sentence without appeal context stays sentence_delivered."""
    cls = classify_press_event("Суд осудил Иванова на 8 лет.")
    assert cls.event_type == PressEventType.sentence_delivered


# ── case card classification ──


def test_case_verdict_event_type():
    cls = classify_case_event("Вынесение приговора")
    assert cls.event_type == CaseEventType.sentence_delivered


def test_case_verdict_result():
    cls = classify_case_event("Судебное заседание", result="Вынесен приговор")
    assert cls.event_type == CaseEventType.sentence_delivered


def test_case_appeal_filed():
    cls = classify_case_event("Обжалование приговора")
    assert cls.event_type == CaseEventType.appeal_filed


def test_case_appeal_submitted():
    cls = classify_case_event("Поступила апелляционная жалоба")
    assert cls.event_type == CaseEventType.appeal_filed


def test_case_sentence_overturned():
    cls = classify_case_event("Отмена приговора")
    assert cls.event_type == CaseEventType.sentence_overturned


def test_case_resolution_overturned():
    cls = classify_case_event("Отменено постановление суда первой инстанции")
    assert cls.event_type == CaseEventType.sentence_overturned


def test_case_sentence_modified():
    cls = classify_case_event("Изменение приговора")
    assert cls.event_type == CaseEventType.sentence_modified


def test_case_hearing_event_type():
    cls = classify_case_event("Судебное заседание")
    assert cls.event_type == CaseEventType.hearing


def test_case_hearing_empty_result():
    cls = classify_case_event("Судебное заседание", result=None)
    assert cls.event_type == CaseEventType.hearing


def test_case_preventive_measure():
    cls = classify_case_event("Избрание меры пресечения")
    assert cls.event_type == CaseEventType.preventive_measure


def test_case_preventive_detention():
    cls = classify_case_event("Заключение под стражу")
    assert cls.event_type == CaseEventType.preventive_measure


def test_case_transfer():
    cls = classify_case_event("Передача дела в другой суд")
    assert cls.event_type == CaseEventType.case_transfer


def test_case_appeal_decided_generic():
    cls = classify_case_event("Апелляционное производство завершено")
    assert cls.event_type == CaseEventType.appeal_decided


def test_case_unknown_when_no_match():
    cls = classify_case_event("Что-то произошло")
    assert cls.event_type == CaseEventType.unknown
