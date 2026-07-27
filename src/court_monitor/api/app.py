"""FastAPI application — read-only surface for the MVP slice.

Mutating endpoints (``/review/*``, ``/audit``) are added in Etap 7 and will
require authentication. For now all endpoints are GET and side-effect free.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from court_monitor import __version__
from court_monitor.config.settings import settings
from court_monitor.observability import configure_logging, get_logger
from court_monitor.storage import repository as repo
from court_monitor.storage.db import make_engine, make_session_factory

configure_logging(settings.log_level)
_log = get_logger("api")

app = FastAPI(
    title="court-monitor",
    version=__version__,
    description="OSINT monitoring of criminal cases (read-only MVP surface).",
)
_engine = make_engine()
_session_factory = make_session_factory(_engine)


def _session():
    return _session_factory()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "llm_mode": settings.llm_mode}


@app.get("/stats")
def stats() -> dict[str, Any]:
    with _session() as session:
        return {
            "documents": repo.count_documents(session),
            "extracted_facts": repo.count_facts(session),
        }


@app.get("/documents")
def list_documents(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    with _session() as session:
        docs = repo.list_documents(session, limit=limit, offset=offset)
        return [_doc_summary(d) for d in docs]


@app.get("/documents/{document_id}")
def get_document(document_id: int) -> dict[str, Any]:
    with _session() as session:
        doc = repo.get_document(session, document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="document not found")
        return _doc_detail(doc)


def _doc_summary(d: Any) -> dict[str, Any]:
    return {
        "id": d.id,
        "url": d.url,
        "source": d.source_name,
        "title": d.title,
        "parser_status": d.parser_status,
        "published_at": d.published_at.isoformat() if d.published_at else None,
        "content_hash": d.content_hash,
    }


def _doc_detail(d: Any) -> dict[str, Any]:
    return {
        **_doc_summary(d),
        "parser_version": d.parser_version,
        "facts": [
            {
                "field": f.field,
                "value": f.value,
                "status": f.verification_status,
                "confidence": f.confidence,
                "method": f.extraction_method,
                "quote": f.quote,
            }
            for f in d.facts
        ],
    }
