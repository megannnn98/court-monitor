"""The preprocessing ablation of the relevance baseline: cleanup and lemmatization on their
own, the same split for every variant, the choice made on validation alone."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sklearn")

from evaluation.relevance.ablation import (
    collapsed_forms,
    error_diff,
    fit_variants,
    run_ablation,
    select_variant,
    tracked_weights,
)
from evaluation.relevance.baseline import Article, build_model, run
from evaluation.relevance.preprocessing import PREPROCESSORS, cleanup, cleanup_lemma, lemmatize

START = datetime(2026, 8, 1, tzinfo=UTC)

# --- cleanup: the patterns found in the corpus --------------------------------------

AI_DISCLAIMER = (
    "Этот пост написал AI. А опубликовала, по мнению российских властей, экстремистская "
    "организация. Публично делиться им может быть небезопасно."
)
MEMO_LABEL = (
    'НАСТОЯЩИЙ МАТЕРИАЛ (ИНФОРМАЦИЯ) ПРОИЗВЕДЕН И РАСПРОСТРАНЕН ИНОСТРАННЫМ АГЕНТОМ ООО "МЕМО", '
    'ЛИБО КАСАЕТСЯ ДЕЯТЕЛЬНОСТИ ИНОСТРАННОГО АГЕНТА ООО "МЕМО". 18+'
)
MEDIAZONA_APPEAL = (
    "🌟«Медиазоне» отключили все донаты из России. Поддержите нашу работу, если считаете ее "
    "важной: donate.zona.media"
)
BODY = "Суд в Москве арестовал активиста по делу о «фейках»."


@pytest.mark.parametrize(
    "noise",
    [
        AI_DISCLAIMER,
        "Подписаться | Помочь RusNews",
        "BAZA в MAX",
        "BAZA в Telegram",
        "❗ Не грузятся фото и видео? Читайте нас в MAX",
        "✔ Подписывайтесь на «Ъ» в MAX | в Telegram",
        "▪️ Подписывайтесь на «Ъ» в MAX | в Telegram",
        "🔴Подписывайтесь на Следком в MAX",
        MEMO_LABEL,
        MEDIAZONA_APPEAL,
        "Здесь мы всегда на связи. Подписывайтесь",
        "Читайте нас в МАХ:",
        "💫 Подписаться на ASTRA в Instagram, Facebook, X на русском, X на английском, TikTok",
    ],
)
def test_cleanup_removes_a_channels_noise_and_keeps_the_article(noise: str) -> None:
    cleaned = cleanup(f"{BODY}\n{noise}")
    assert cleaned.rstrip("\n") == BODY


def test_cleanup_takes_only_the_call_glued_to_a_sentence() -> None:
    sentence = "Песков заявил, что Кремль не комментирует приговор."
    for call in (
        " ✔ Подписывайтесь на «Ъ» в MAX | в Telegram",
        " ▪️ Подписывайтесь на «Ъ» в MAX | в Telegram",
        " Подписаться | Помочь RusNews",
        "✔ Подписывайтесь на «Ъ» в MAX | в Telegram",
    ):
        assert cleanup(sentence + call) == sentence
    hashtag = "Надписи в поддержку «Яблока», мира и свободы #москва"
    assert cleanup(f"{hashtag} Подписаться | Помочь RusNews") == hashtag


def test_cleanup_removes_links_and_handles() -> None:
    text = (
        "Подробнее: https://www.rapsinews.ru/judicial_news/20260825/312128948.html и "
        "www.example.org/a, канал t.me/ovdinfo, max.ru/id7718123097_gos, @opersvodki."
    )
    cleaned = cleanup(text)
    for gone in ("https", "rapsinews", "www.", "t.me", "max.ru", "@opersvodki"):
        assert gone not in cleaned
    assert cleaned.startswith("Подробнее:  и ")
    assert "канал" in cleaned


@pytest.mark.parametrize(
    "content",
    [
        "Он отказался подписаться под письмом.",
        "Подписание договора состоялось в пятницу.",
        "Источник: Медиазона",
        "#судебная_практика Суд отказал в иске.",
        "Депутат продвигал закон о «национальном мессенджере» MAX.",
        "Россиянин донатил ФБК, его обвиняют в финансировании экстремизма.",
        (
            "Мы вас не бросим, подписывайтесь — сказал он на митинге, где потом задержали 10"
            " человек и возбудили уголовное дело по статье о массовых беспорядках."
        ),
        "ОС прокуратуры направила дело в суд.",
    ],
)
def test_cleanup_keeps_content(content: str) -> None:
    assert cleanup(content) == content


def test_cleanup_is_deterministic_and_idempotent() -> None:
    text = f"{BODY} ✔ Подписывайтесь на «Ъ» в MAX | в Telegram\n{AI_DISCLAIMER}"
    once = cleanup(text)
    assert cleanup(text) == once
    assert cleanup(once) == once


# --- lemmatization -------------------------------------------------------------------


def test_lemmatize_puts_cyrillic_words_in_dictionary_form() -> None:
    assert lemmatize("По делу «Яблока» возбуждено уголовное дело") == (
        "по дело яблоко возбудить уголовный дело"
    )


def test_lemmatize_only_lowercases_everything_else() -> None:
    assert lemmatize("Ст. 280 УК РФ, MAX и Telegram, 2026г, ID_42") == (
        "ст 280 ук рф max и telegram 2026г id_42"
    )


def test_lemmatize_leaves_mixed_tokens_to_tf_idf() -> None:
    # pymorphy3 would read these as «20летний», «2026год», «iphone».
    assert lemmatize("20летнего 2026года iPhoneы") == "20летнего 2026года iphoneы"


def test_lemmatize_is_deterministic() -> None:
    text = "Суды арестовали активистов; делу дали ход."
    assert lemmatize(text) == lemmatize(text) == "суд арестовать активист дело дать ход"


def test_cleanup_lemma_cleans_first() -> None:
    text = f"{BODY} ✔ Подписывайтесь на «Ъ» в MAX | в Telegram"
    assert cleanup_lemma(text) == lemmatize(BODY)
    assert "подписываться" not in cleanup_lemma(text)


# --- variants ------------------------------------------------------------------------


def article(article_id: int, label: int, *, text: str = "") -> Article:
    words = "возбуждено уголовное дело арестован по делу" if label else "погода концерт футбол"
    noise = "✔ Подписывайтесь на «Ъ» в MAX | в Telegram" if article_id % 2 else ""
    return Article(
        article_id=article_id,
        title=f"Заголовок {article_id}",
        text=text or f"{words} {words} номер{article_id} {noise}",
        published_at=START + timedelta(days=article_id),
        label=label,
        kind="case" if label else "other",
        source="Лента",
        stratum="kept",
    )


def corpus(size: int, first_id: int = 0) -> list[Article]:
    return [article(first_id + i, int(i % 3 == 0)) for i in range(size)]


def test_every_variant_has_the_same_split() -> None:
    variants = fit_variants(corpus(40))
    assert list(variants) == ["raw", "cleanup", "lemma", "cleanup_lemma"]
    splits = {
        (
            tuple(r.article_id for r in v.fitted.train),
            tuple(r.article_id for r in v.fitted.validation),
        )
        for v in variants.values()
    }
    assert len(splits) == 1


def test_variants_differ_only_in_what_the_model_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def recording() -> object:
        model = build_model()
        original = model.fit

        def fit(texts: list[str], labels: list[int]) -> object:
            seen.append(list(texts))
            return original(texts, labels)

        model.fit = fit
        return model

    rows = corpus(40)
    fit_variants(rows, {"raw": PREPROCESSORS["raw"], "lemma": lemmatize}, recording)
    raw_texts, lemma_texts = seen
    assert [lemmatize(text) for text in raw_texts] == lemma_texts


# --- selection -----------------------------------------------------------------------


def entry(
    target_met: bool, precision: float, recall: float = 0.9, ap: float = 0.5
) -> dict[str, Any]:
    return {
        "target_met": target_met,
        "precision": precision,
        "recall": recall,
        "average_precision": ap,
    }


def test_selection_takes_the_most_precise_variant_reaching_the_recall() -> None:
    validation = {
        "raw": entry(True, 0.40),
        "cleanup": entry(True, 0.45),
        "lemma": entry(False, 0.90, recall=0.85),
        "cleanup_lemma": entry(True, 0.44),
    }
    assert select_variant(validation) == ("cleanup", True)


def test_selection_breaks_ties_by_pr_auc_then_simplicity() -> None:
    validation = {
        "raw": entry(True, 0.40, ap=0.70),
        "cleanup": entry(True, 0.40, ap=0.75),
        "lemma": entry(True, 0.40, ap=0.75),
    }
    assert select_variant(validation) == ("cleanup", True)
    assert select_variant({"raw": entry(True, 0.4), "lemma": entry(True, 0.4)}) == ("raw", True)


def test_selection_without_a_variant_reaching_the_recall_says_so() -> None:
    validation = {
        "raw": entry(False, 0.6, recall=0.80),
        "cleanup": entry(False, 0.5, recall=0.85),
    }
    assert select_variant(validation) == ("cleanup", False)


def test_selection_refuses_no_variants() -> None:
    with pytest.raises(ValueError, match="no variants"):
        select_variant({})


def test_the_test_does_not_choose_the_preprocessing() -> None:
    train = corpus(40)
    test = corpus(15, first_id=100)
    flipped = [replace(row, label=1 - row.label, text="погода концерт") for row in test]
    flipped[0] = replace(flipped[0], label=1)
    flipped[1] = replace(flipped[1], label=0)
    first = run_ablation(train, test)
    second = run_ablation(train, flipped)
    assert first["selected"] == second["selected"]
    for name in PREPROCESSORS:
        assert first[name]["validation"] == second[name]["validation"]
        assert first[name]["threshold"] == second[name]["threshold"]
    assert first["raw"]["test"] != second["raw"]["test"]


def test_the_report_says_where_the_choice_came_from() -> None:
    report = run_ablation(corpus(40), corpus(15, first_id=100))
    assert report["selection_source"] == "validation"
    assert report["test_previously_observed"] is True
    assert report["selected"] in PREPROCESSORS
    assert report["split"] == {"train": 32, "validation": 8, "test": 15}
    for name in PREPROCESSORS:
        assert {"threshold", "validation", "top_features", "tracked_features"} <= set(report[name])
    assert "test" in report["raw"]
    assert "test" in report[report["selected"]]
    unselected = [n for n in PREPROCESSORS if n not in ("raw", report["selected"])]
    assert all("test" not in report[name] for name in unselected)
    assert set(report["errors_vs_raw"]) == {"validation", "test"}


def test_the_run_is_reproducible() -> None:
    train, test = corpus(40), corpus(15, first_id=100)
    assert run_ablation(train, test) == run_ablation(train, test)


def test_ablation_needs_the_raw_control() -> None:
    with pytest.raises(ValueError, match="raw baseline"):
        run_ablation(corpus(40), corpus(5, 100), {"lemma": lemmatize})


# --- comparison ----------------------------------------------------------------------


def test_error_diff_names_the_fixed_and_the_introduced() -> None:
    rows = [article(1, 1), article(2, 1), article(3, 0), article(4, 0), article(5, 1)]
    base = [0.2, 0.6, 0.7, 0.1, 0.3]  # FN 1, 5; FP 3
    new = [0.6, 0.4, 0.2, 0.55, 0.3]  # FN 2, 5; FP 4
    assert error_diff(rows, base, 0.5, new, 0.5) == {
        "fn_fixed": [1],
        "fn_introduced": [2],
        "fp_fixed": [3],
        "fp_introduced": [4],
    }


def test_error_diff_uses_each_variants_own_threshold() -> None:
    rows = [article(1, 1), article(2, 0)]
    diff = error_diff(rows, [0.4, 0.45], 0.5, [0.4, 0.45], 0.3)
    assert diff == {"fn_fixed": [1], "fn_introduced": [], "fp_fixed": [], "fp_introduced": [2]}


def test_error_diff_counts_a_probability_at_the_threshold_as_a_case() -> None:
    rows = [article(1, 1), article(2, 0)]
    diff = error_diff(rows, [0.4, 0.4], 0.5, [0.5, 0.4], 0.5)
    assert diff["fn_fixed"] == [1]


def test_error_diff_refuses_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="differ in number"):
        error_diff([article(1, 1)], [0.1, 0.2], 0.5, [0.1], 0.5)


def test_tracked_and_collapsed_features() -> None:
    variants = fit_variants(corpus(40), {"raw": PREPROCESSORS["raw"], "lemma": lemmatize})
    raw_model = variants["raw"].fitted.model
    lemma_model = variants["lemma"].fitted.model
    raw_weights = tracked_weights(raw_model, ["дело", "делу", "по делу", "нет такого"])
    assert raw_weights["нет такого"] is None
    assert raw_weights["дело"] is not None and raw_weights["дело"] > 0
    lemma_weights = tracked_weights(lemma_model, ["делу", "дело"])
    assert lemma_weights["делу"] is None
    collapsed = collapsed_forms(raw_model, lemma_model, ["дело"])
    assert collapsed["дело"] == {
        "raw_forms": ["дело", "делу"],
        "forms_left_after_lemma": ["дело"],
        "lemma_is_feature": True,
    }


def test_errors_vs_raw_go_from_raw_to_the_selected() -> None:
    # A control that reads nothing useful: a word that says only whether the id is odd.
    def blind(text: str) -> str:
        odd = int(text.split("Заголовок ")[1].split()[0]) % 2
        return f"слово{odd} слово{odd}"

    report = run_ablation(
        corpus(40), corpus(15, first_id=100), {"raw": blind, "real": PREPROCESSORS["raw"]}
    )
    assert report["selected"] == "real"
    for part in ("validation", "test"):
        diff = report["errors_vs_raw"][part]
        raw, real = report["raw"][part], report["real"][part]
        assert len(diff["fn_introduced"]) - len(diff["fn_fixed"]) == real["fn"] - raw["fn"]
        assert len(diff["fp_introduced"]) - len(diff["fp_fixed"]) == real["fp"] - raw["fp"]
    assert report["errors_vs_raw"]["test"]["fp_fixed"]


def test_the_model_reads_preprocessed_text_everywhere() -> None:
    read: list[str] = []

    def recording() -> Any:
        model = build_model()
        fit, predict = model.fit, model.predict_proba

        def fit_(texts: list[str], labels: list[int]) -> Any:
            read.extend(texts)
            return fit(texts, labels)

        def predict_(texts: list[str]) -> Any:
            read.extend(texts)
            return predict(texts)

        model.fit, model.predict_proba = fit_, predict_
        return model

    def marked(text: str) -> str:
        return f"метка {text}"

    run_ablation(corpus(40), corpus(15, first_id=100), {"raw": marked}, recording)
    assert len(read) == 32 + 8 + 15
    assert all(text.startswith("метка ") for text in read)
    read.clear()
    run(corpus(40), corpus(15, first_id=100), model_factory=recording, preprocess=marked)
    assert len(read) == 32 + 8 + 15
    assert all(text.startswith("метка ") for text in read)


def test_variants_split_differently_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from evaluation.relevance import baseline

    real = baseline.fit_and_choose_threshold
    calls: list[int] = []

    def shifted(*args: Any, **kwargs: Any) -> Any:
        fitted = real(*args, **kwargs)
        calls.append(1)
        if len(calls) == 2:
            return replace(fitted, train=fitted.train[1:])
        return fitted

    monkeypatch.setattr("evaluation.relevance.ablation.fit_and_choose_threshold", shifted)
    with pytest.raises(RuntimeError, match="split differently"):
        fit_variants(corpus(40))
