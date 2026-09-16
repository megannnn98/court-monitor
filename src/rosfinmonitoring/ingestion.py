"""Rosfinmonitoring data ingestion pipeline."""

from __future__ import annotations

from datetime import UTC, datetime

from rosfinmonitoring.models import (
    RosfinmonitoringIngestionResult,
    RosfinmonitoringParser,
    RosfinmonitoringSnapshot,
)
from rosfinmonitoring.parser import (
    CsvRosfinmonitoringParser,
    HtmlRosfinmonitoringParser,
    JsonRosfinmonitoringParser,
    XmlRosfinmonitoringParser,
)
from rosfinmonitoring.persistence import (
    RosfinmonitoringPersistence,
    compute_content_hash,
)


class RosfinmonitoringIngestionPipeline:
    """Pipeline for ingesting Rosfinmonitoring data."""

    def __init__(
        self,
        persistence: RosfinmonitoringPersistence,
        parser: RosfinmonitoringParser | None = None,
    ) -> None:
        self._persistence = persistence
        self._parser = parser

    def _detect_parser(self, raw_content: bytes) -> RosfinmonitoringParser:
        """Detect parser based on content."""
        text = raw_content[:100].decode("utf-8-sig", errors="ignore").strip()

        if text.lower().startswith(("<!doctype html", "<html")):
            return HtmlRosfinmonitoringParser()
        elif text.startswith(("<?xml", "<")):
            return XmlRosfinmonitoringParser()
        elif text.startswith(("{", "[")):
            return JsonRosfinmonitoringParser()
        else:
            return CsvRosfinmonitoringParser()

    def ingest(
        self,
        raw_content: bytes,
        source_url: str,
        snapshot_date: datetime | None = None,
    ) -> RosfinmonitoringIngestionResult:
        """Ingest Rosfinmonitoring data from raw content."""
        content_hash = compute_content_hash(raw_content)

        if self._persistence.snapshot_exists(content_hash):
            raise ValueError(f"Snapshot with content hash {content_hash} already exists")

        parser = self._parser or self._detect_parser(raw_content)
        entries = parser.parse(raw_content)

        if not entries:
            raise ValueError("No entries found in the provided content")

        snapshot = RosfinmonitoringSnapshot(
            snapshot_date=snapshot_date or datetime.now(UTC),
            source_url=source_url,
            content_hash=content_hash,
            entry_count=len(entries),
            fetched_at=datetime.now(UTC),
            raw_content=raw_content,
        )

        snapshot_id = self._persistence.create_snapshot(snapshot)

        for entry in entries:
            entry.snapshot_id = snapshot_id

        result = self._persistence.save_entries(snapshot_id, entries)

        return result

    def ingest_from_file(
        self,
        file_path: str,
        source_url: str,
        snapshot_date: datetime | None = None,
    ) -> RosfinmonitoringIngestionResult:
        """Ingest Rosfinmonitoring data from a file."""
        with open(file_path, "rb") as f:
            raw_content = f.read()

        return self.ingest(raw_content, source_url, snapshot_date)
