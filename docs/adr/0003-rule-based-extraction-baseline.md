# Rule-based extraction baseline

## Status

Accepted

## Context

Этап 10 должен извлекать mentions и события из полного текста статьи, сохраняя provenance, offsets и версионность. Нельзя возвращать persistent chunks, создавать canonical entities или использовать LLM/платные API. Тесты должны быть воспроизводимыми и не скачивать модель при каждом запуске.

## Decision

Используем deterministic rule-based baseline:

- `RuleBasedEntityExtractor` извлекает raw mentions регулярными выражениями и словарями триггеров;
- `RuleBasedMentionNormalizer` приводит mention к typed Pydantic data;
- `RuleBasedEventExtractor` создаёт события по sentence-level trigger words и связывает mentions ролями;
- `SqlAlchemyExtractionPersistence` сохраняет run/mentions/events/links в PostgreSQL транзакционно;
- идемпотентность строится на `article_id + article_content_hash + extractor_version + normalizer_version`.

Готовую NLP-библиотеку на этом этапе не добавляем: для текущего Python 3.13 baseline важнее CPU-only, small dependency surface, no model download in tests, deterministic output.

## Consequences

Плюсы:

- воспроизводимые тесты без сети;
- маленький diff и простая эксплуатация;
- строгие offsets и provenance;
- безопасная граница между mention extraction и будущим entity resolution.

Минусы:

- качество ниже полноценного NER;
- падежная нормализация имён эвристическая;
- события зависят от триггеров и не являются юридической классификацией.

Улучшение качества можно делать следующим этапом, не меняя persistence contract.
