"""What happens when CM_NER_MODE=spacy but the model was never downloaded.

The rest of the NER tests skip themselves when ``ru_core_news_lg`` is absent,
so this path — the likely one, since the model is a ~500 MB opt-in extra — had
no coverage at all. It must fail once, cheaply and loudly, not once per
document.
"""

from __future__ import annotations

import pytest

from court_monitor.config.settings import settings
from court_monitor.extraction import ner_names
from court_monitor.services import parse_and_extract
from court_monitor.storage.orm import SourceDocument


@pytest.fixture(autouse=True)
def _clear_load_cache():
    """The load attempt is memoized for the process; isolate each test."""
    ner_names.reset_model_cache()
    yield
    ner_names.reset_model_cache()


@pytest.fixture()
def missing_model(monkeypatch):
    """Stand in for spacy.load raising OSError, counting the attempts."""
    attempts: list[str] = []

    class _FakeSpacy:
        @staticmethod
        def load(name: str, **_kwargs):
            attempts.append(name)
            raise OSError(f"Can't find model '{name}'")

    monkeypatch.setattr(ner_names, "_import_spacy", lambda: _FakeSpacy)
    return attempts


def test_the_model_is_looked_for_only_once(missing_model):
    """Regression: lru_cache does not memoize exceptions, so every document
    re-ran spacy.load and re-logged the same error — 379 documents, 379
    identical ERROR lines. The retry is cheap; the log spam is the damage."""
    for _ in range(10):
        with pytest.raises(ner_names.NerModelUnavailable):
            ner_names.extract_name_candidates_ner("Иванов Иван Иванович")

    assert len(missing_model) == 1


def test_the_failure_is_reported_once_not_per_call(missing_model, caplog):
    with caplog.at_level("ERROR"):
        for _ in range(5):
            with pytest.raises(ner_names.NerModelUnavailable):
                ner_names.extract_name_candidates_ner("Иванов Иван Иванович")

    assert len(missing_model) == 1


def test_the_error_names_the_command_that_fixes_it(missing_model):
    with pytest.raises(ner_names.NerModelUnavailable) as exc:
        ner_names.extract_name_candidates_ner("Иванов Иван Иванович")

    assert "spacy download" in str(exc.value)
    assert ner_names._MODEL_NAME in str(exc.value)


def test_a_missing_extra_degrades_like_a_missing_model(monkeypatch):
    """spacy itself is optional. Without the `nlp` extra the import raises
    ImportError, which used to escape uncaught and take the whole source down
    with it — the same setup mistake, a far worse outcome."""

    def _no_spacy():
        raise ImportError("No module named 'spacy'")

    monkeypatch.setattr(ner_names, "_import_spacy", _no_spacy)

    with pytest.raises(ner_names.NerModelUnavailable) as exc:
        ner_names.extract_name_candidates_ner("Иванов Иван Иванович")

    assert isinstance(exc.value.__cause__, ImportError)


def test_the_original_oserror_is_kept_as_the_cause(missing_model):
    """Whoever debugs this needs spaCy's own message, not just ours."""
    with pytest.raises(ner_names.NerModelUnavailable) as exc:
        ner_names.extract_name_candidates_ner("Иванов Иван Иванович")

    assert isinstance(exc.value.__cause__, OSError)


def test_empty_text_does_not_even_look_for_the_model(missing_model):
    """The short-circuit on empty text must come before loading anything."""
    assert ner_names.extract_name_candidates_ner("") == []
    assert missing_model == []


def test_a_working_model_is_loaded_once_too(monkeypatch):
    """The success path stays memoized — loading takes ~1s."""
    loads: list[str] = []

    class _Doc:
        ents: list = []

    class _FakeNlp:
        def __call__(self, _text):
            return _Doc()

    class _FakeSpacy:
        @staticmethod
        def load(name: str, **_kwargs):
            loads.append(name)
            return _FakeNlp()

    monkeypatch.setattr(ner_names, "_import_spacy", lambda: _FakeSpacy)

    for _ in range(5):
        assert ner_names.extract_name_candidates_ner("текст") == []

    assert len(loads) == 1


def test_the_pipeline_degrades_to_regex_names_instead_of_failing(
    db_session, monitoring_cfg, missing_model, monkeypatch
):
    """A missing optional model must not cost the document its other facts."""
    monkeypatch.setattr(settings, "ner_mode", "spacy")
    doc = SourceDocument(
        url="https://example.invalid/1",
        source_type="telegram",
        content_hash="h1",
        text="Иванов Иван Иванович осуждён по ст. 205.1 УК РФ",
        parser_status="pending",
    )
    db_session.add(doc)
    db_session.flush()

    facts = parse_and_extract(db_session, doc, monitoring_cfg)

    assert any(f.field == "full_name_original" for f in facts), "regex names must survive"
    assert any(f.field == "criminal_article" for f in facts), "articles must survive"
    assert doc.parser_status != "parser_failed"
