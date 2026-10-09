"""Architecture spike: a second monitoring consumer of `monitor_core`, not Court Monitor.

A company-news monitor written against the core's public API as it is, importing
nothing of Court Monitor. Where the API makes it invent something it does not have,
the line says `FRICTION:`; nothing papers over it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import BaseModel

from monitor_core.ingestion import IngestionPipeline, SourceIngestion
from monitor_core.llm import CHAT_ENVELOPE_ERRORS, post_json_chat, read_chat_completion
from monitor_core.model import ParsedArticle, PersistenceResult, RawDocument, SourceReference

NEWS = {"acme-1": "Acme привлекла $10M", "beta-7": "Beta сменила CEO"}


class CompanyNewsSource:
    """An in-memory feed: an id and a text per item, nothing more."""

    async def discover(self, *, limit: int) -> list[SourceReference]:
        # FRICTION: SourceReference.url is required; this feed has no URLs.
        return [SourceReference(external_id=key, url=f"memory://{key}") for key in NEWS][:limit]

    async def fetch(self, reference: SourceReference) -> RawDocument:
        return RawDocument(
            external_id=reference.external_id,
            url=reference.url,
            fetched_at=datetime(2026, 10, 9, tzinfo=UTC),
            content_type="text/plain",
            content=NEWS[reference.external_id].encode(),
        )


class CompanyArticleParser:
    def parse(self, raw: RawDocument) -> ParsedArticle:
        text = raw.content.decode()
        # Acceptable here: a headline is a title. A feed of untitled posts would invent one.
        return ParsedArticle(
            external_id=raw.external_id, url=raw.url, title=text, published_at=None, text=text
        )


class CompanyStore:
    def __init__(self) -> None:
        self.texts: dict[int, str] = {}

    def save(self, raw_document: RawDocument, article: ParsedArticle) -> PersistenceResult:
        key = len(self.texts) + 1
        self.texts[key] = article.text
        # FRICTION: two int ids, one of them named after an "article"; this store has one key.
        return PersistenceResult(document_id=key, article_id=key)


class CompanyEvent(BaseModel):
    company: str
    event_type: Literal["funding", "management_change"]


EVENT_SCHEMA = CompanyEvent.model_json_schema()


def extract_event(http: httpx.Client, text: str) -> CompanyEvent:
    response = post_json_chat(
        http,
        "https://llm.test/v1/chat/completions",
        model="any/model",
        system="Name the company and the kind of event in the news.",
        user=text,
        schema_name="company_event",
        schema=EVENT_SCHEMA,
        headers={},
        timeout_seconds=10.0,
    )
    response.raise_for_status()
    try:
        completion = read_chat_completion(response)
    except CHAT_ENVELOPE_ERRORS as exc:
        raise ValueError("no answer in the envelope") from exc
    # FRICTION: no shared "accept the answer's text" step; every consumer writes its own.
    if completion.finish_reason not in (None, "stop") or not isinstance(completion.content, str):
        raise ValueError(f"unusable answer: {completion.finish_reason}")
    return CompanyEvent.model_validate_json(completion.content)


def fake_model(request: httpx.Request) -> httpx.Response:
    text = json.loads(request.content)["messages"][1]["content"]
    event = (
        {"company": "Acme", "event_type": "funding"}
        if "Acme" in text
        else {"company": "Beta", "event_type": "management_change"}
    )
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(event)}, "finish_reason": "stop"}]},
    )


def test_a_company_monitor_runs_on_the_core_alone() -> None:
    source, store = CompanyNewsSource(), CompanyStore()
    ingestion = SourceIngestion(
        source_adapter=source,
        pipeline=IngestionPipeline(source, CompanyArticleParser(), store),
    )

    result = asyncio.run(ingestion.run(limit=10))
    http = httpx.Client(transport=httpx.MockTransport(fake_model))
    events = [extract_event(http, text) for text in store.texts.values()]

    assert (len(result.results), result.failures) == (2, [])
    assert events == [
        CompanyEvent(company="Acme", event_type="funding"),
        CompanyEvent(company="Beta", event_type="management_change"),
    ]
