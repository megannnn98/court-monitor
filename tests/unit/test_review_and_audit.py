"""Unit tests: ReviewItem and AuditLog repository functions.

TDD seam for Etap 4 slice 1 — the generic operator review queue and the
append-only audit trail. Written before the repository implementation.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from court_monitor.storage import repository as repo
from court_monitor.storage.orm import Base, SourceDocument


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = factory()
    try:
        yield s
    finally:
        s.close()


def _make_document(session) -> SourceDocument:
    doc = SourceDocument(
        url="https://test/doc",
        source_type="sudrf",
        content_hash="hash1",
        parser_status="parser_failed",
        content="<html></html>",
    )
    session.add(doc)
    session.flush()
    return doc


# ======================================================================
# ReviewItem
# ======================================================================


def test_create_review_item_defaults(session):
    item = repo.create_review_item(session, item_type="parser_failed")
    assert item.id is not None
    assert item.item_type == "parser_failed"
    assert item.priority == "medium"
    assert item.status == "pending"
    assert item.document_id is None
    assert item.data_json is None


def test_create_review_item_with_document_and_data(session):
    doc = _make_document(session)
    item = repo.create_review_item(
        session,
        item_type="parser_failed",
        priority="high",
        document_id=doc.id,
        source_id="2zovs-test",
        source_url="https://test/doc",
        data={"error": "boom"},
    )
    assert item.priority == "high"
    assert item.document_id == doc.id
    assert item.source_id == "2zovs-test"
    assert json.loads(item.data_json) == {"error": "boom"}


def test_upsert_review_item_does_not_duplicate_pending(session):
    """Repeated failures on the same document must refresh, not pile up."""
    doc = _make_document(session)
    first = repo.upsert_review_item(
        session, item_type="parser_failed", document_id=doc.id, data={"error": "boom v1"}
    )
    second = repo.upsert_review_item(
        session, item_type="parser_failed", document_id=doc.id, data={"error": "boom v2"}
    )

    assert first.id == second.id
    assert repo.count_review_items(session) == 1
    assert json.loads(second.data_json) == {"error": "boom v2"}


def test_upsert_review_item_reopens_after_resolution(session):
    """Once resolved, the next occurrence of the same failure opens a new item."""
    doc = _make_document(session)
    first = repo.upsert_review_item(session, item_type="parser_failed", document_id=doc.id)
    repo.resolve_review_item(session, first.id, resolved_by="op1")

    second = repo.upsert_review_item(session, item_type="parser_failed", document_id=doc.id)

    assert second.id != first.id
    assert repo.count_review_items(session) == 2
    assert repo.count_review_items(session, status="pending") == 1


def test_upsert_review_item_without_any_key_always_creates(session):
    """Neither document_id nor source_id means no dedup key — every call creates a new item."""
    repo.upsert_review_item(session, item_type="source_blocked")
    repo.upsert_review_item(session, item_type="source_blocked")
    assert repo.count_review_items(session) == 2


def test_upsert_review_item_dedups_by_source_id_when_no_document(session):
    """A source-level problem (no document_id) dedups on source_id instead —
    repeated fetch-source runs against a still-blocked source must not pile up."""
    first = repo.upsert_review_item(
        session, item_type="source_blocked", source_id="2zovs", data={"health": "blocked"}
    )
    second = repo.upsert_review_item(
        session, item_type="source_blocked", source_id="2zovs", data={"health": "http_error"}
    )

    assert first.id == second.id
    assert repo.count_review_items(session) == 1
    assert json.loads(second.data_json) == {"health": "http_error"}

    # A different source is a different dedup key.
    repo.upsert_review_item(session, item_type="source_blocked", source_id="uovs")
    assert repo.count_review_items(session) == 2


def test_upsert_review_item_source_id_reopens_after_resolution(session):
    first = repo.upsert_review_item(session, item_type="source_blocked", source_id="2zovs")
    repo.resolve_review_item(session, first.id, resolved_by="op1")

    second = repo.upsert_review_item(session, item_type="source_blocked", source_id="2zovs")

    assert second.id != first.id
    assert repo.count_review_items(session) == 2
    assert repo.count_review_items(session, status="pending") == 1


def test_upsert_review_item_document_id_takes_priority_over_source_id(session):
    """When both are given, document_id is the dedup key (document-level
    granularity is finer than source-level)."""
    doc = _make_document(session)
    first = repo.upsert_review_item(
        session, item_type="parser_failed", document_id=doc.id, source_id="2zovs"
    )
    second = repo.upsert_review_item(
        session, item_type="parser_failed", document_id=doc.id, source_id="2zovs"
    )
    assert first.id == second.id
    assert repo.count_review_items(session) == 1


def test_list_review_items_filters_by_status(session):
    repo.create_review_item(session, item_type="parser_failed")
    a = repo.create_review_item(session, item_type="parser_failed")
    repo.resolve_review_item(session, a.id, resolved_by="op1")

    pending = repo.list_review_items(session, status="pending")
    resolved = repo.list_review_items(session, status="resolved")
    assert len(pending) == 1
    assert len(resolved) == 1
    assert resolved[0].id == a.id


def test_list_review_items_filters_by_item_type(session):
    repo.create_review_item(session, item_type="parser_failed")
    repo.create_review_item(session, item_type="source_blocked")

    assert len(repo.list_review_items(session, item_type="parser_failed")) == 1
    assert len(repo.list_review_items(session, item_type="source_blocked")) == 1
    assert len(repo.list_review_items(session)) == 2


def test_count_review_items(session):
    repo.create_review_item(session, item_type="parser_failed")
    repo.create_review_item(session, item_type="parser_failed")
    assert repo.count_review_items(session) == 2
    assert repo.count_review_items(session, status="pending") == 2
    assert repo.count_review_items(session, status="resolved") == 0


def test_get_review_item_missing_returns_none(session):
    assert repo.get_review_item(session, 999) is None


def test_resolve_review_item(session):
    item = repo.create_review_item(session, item_type="parser_failed")
    resolved = repo.resolve_review_item(
        session, item.id, resolved_by="op1", comment="fixed selector"
    )
    assert resolved is not None
    assert resolved.status == "resolved"
    assert resolved.resolved_by == "op1"
    assert resolved.resolution_comment == "fixed selector"
    assert resolved.resolved_at is not None


def test_resolve_review_item_custom_status(session):
    item = repo.create_review_item(session, item_type="parser_failed")
    dismissed = repo.resolve_review_item(session, item.id, status="dismissed", resolved_by="op1")
    assert dismissed.status == "dismissed"


def test_resolve_review_item_missing_returns_none(session):
    assert repo.resolve_review_item(session, 999, resolved_by="op1") is None


def test_resolve_review_item_creates_audit_log_entry(session):
    item = repo.create_review_item(session, item_type="parser_failed")
    repo.resolve_review_item(
        session,
        item.id,
        resolved_by="op1",
        comment="fixed selector",
        correlation_id="cid-1",
    )

    entries = repo.list_audit_log(session, object_type="review_item", object_id=item.id)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor == "op1"
    assert entry.action == "review_item_resolved"
    assert entry.correlation_id == "cid-1"
    assert json.loads(entry.old_value_json) == {"status": "pending"}
    assert json.loads(entry.new_value_json) == {
        "status": "resolved",
        "comment": "fixed selector",
    }


# ======================================================================
# AuditLog
# ======================================================================


def test_create_audit_log_entry(session):
    entry = repo.create_audit_log_entry(
        session,
        actor="op1",
        action="match_status_change",
        object_type="match_candidate",
        object_id=42,
        old_value={"status": "pending"},
        new_value={"status": "confirmed"},
        correlation_id="cid-1",
    )
    assert entry.id is not None
    assert entry.actor == "op1"
    assert json.loads(entry.old_value_json) == {"status": "pending"}
    assert json.loads(entry.new_value_json) == {"status": "confirmed"}
    assert entry.correlation_id == "cid-1"


def test_create_audit_log_entry_without_values(session):
    entry = repo.create_audit_log_entry(
        session,
        actor="op1",
        action="review_item_resolved",
        object_type="review_item",
        object_id=1,
    )
    assert entry.old_value_json is None
    assert entry.new_value_json is None
    assert entry.correlation_id is None


def test_list_audit_log_filters_by_object(session):
    repo.create_audit_log_entry(
        session, actor="op1", action="a", object_type="match_candidate", object_id=1
    )
    repo.create_audit_log_entry(
        session, actor="op1", action="a", object_type="match_candidate", object_id=2
    )
    repo.create_audit_log_entry(
        session, actor="op1", action="a", object_type="review_item", object_id=1
    )

    assert len(repo.list_audit_log(session, object_type="match_candidate")) == 2
    assert len(repo.list_audit_log(session, object_type="match_candidate", object_id=1)) == 1
    assert len(repo.list_audit_log(session)) == 3


def test_list_audit_log_ordered_newest_first(session):
    first = repo.create_audit_log_entry(
        session, actor="op1", action="a", object_type="review_item", object_id=1
    )
    second = repo.create_audit_log_entry(
        session, actor="op1", action="b", object_type="review_item", object_id=1
    )
    entries = repo.list_audit_log(session)
    assert entries[0].id == second.id
    assert entries[1].id == first.id
