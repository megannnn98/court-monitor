"""The public API of `monitor_core`: six public modules, each with a small `__all__`.

Code outside the core imports from these, never from the modules behind them."""

import importlib
import typing

from monitor_core.model import IngestionResult
from monitor_core.ports import IngestionPersistence

PUBLIC_API = {
    "monitor_core.model": {
        "IngestionResult",
        "ParsedArticle",
        "RawDocument",
        "SourceReference",
    },
    "monitor_core.ports": {
        "ArticleParser",
        "DocumentFetcher",
        "IngestionPersistence",
        "SourceAdapter",
    },
    "monitor_core.ingestion": {
        "ArticleIngestionPipeline",
        "FetchedArticle",
        "IngestionPipeline",
        "RetryingDocumentFetcher",
        "SourceIngestion",
        "SourceIngestionFailure",
        "SourceIngestionResult",
    },
    "monitor_core.llm": {
        "CHAT_ENVELOPE_ERRORS",
        "ChatCompletion",
        "post_json_chat",
        "read_chat_completion",
    },
    "monitor_core.retry": {"retry", "retry_async"},
    "monitor_core.errors": {
        "DiscoveryError",
        "FetchError",
        "IngestionError",
        "ListingPageNotFoundError",
        "NoTextError",
        "ParseError",
        "PermanentDiscoveryError",
        "PermanentFetchError",
        "PersistenceError",
        "TransientDiscoveryError",
        "TransientFetchError",
    },
}


def test_each_public_package_exports_exactly_its_api() -> None:
    exported = {
        name: set(getattr(importlib.import_module(name), "__all__", ())) for name in PUBLIC_API
    }

    assert exported == PUBLIC_API


def test_every_exported_name_resolves() -> None:
    missing = [
        f"{package}.{name}"
        for package, names in PUBLIC_API.items()
        for name in names
        if not hasattr(importlib.import_module(package), name)
    ]

    assert missing == []


def test_the_core_package_root_exports_nothing() -> None:
    # One import path per name: no catch-all re-export at the package root.
    root = importlib.import_module("monitor_core")

    assert getattr(root, "__all__", []) == []


def test_what_a_store_hands_back_is_the_store_s_own_type() -> None:
    """The core passes a storage's result on without knowing its shape: no ids, no fields
    of its own, only the type parameter the storage fills in."""
    [result_type] = IngestionResult.__type_params__
    [saved_type] = IngestionPersistence.__type_params__

    assert set(IngestionResult.model_fields) == {"article", "persistence"}
    persistence: object = IngestionResult.model_fields["persistence"].annotation
    assert persistence is result_type
    assert typing.get_type_hints(IngestionPersistence.save)["return"] is saved_type
