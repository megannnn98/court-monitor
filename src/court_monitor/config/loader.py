"""YAML rules loader: monitored articles/keywords + source adapters config.

Editing rules does NOT require code changes — spec §5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from court_monitor.config.settings import settings
from court_monitor.domain.models import SourceBackend, SourceType


@dataclass(frozen=True)
class MonitoringConfig:
    criminal_articles: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    def article_set(self) -> set[str]:
        return {a.strip() for a in self.criminal_articles if a.strip()}

    def keyword_set(self) -> set[str]:
        return {k.strip().lower() for k in self.keywords if k.strip()}


@dataclass(frozen=True)
class SourceConfig:
    name: str
    type: SourceType
    backend: SourceBackend
    base_url: str | None = None
    paths: tuple[str, ...] = ()
    fixture_path: str | None = None
    parser: str | None = None
    enabled: bool = True
    court_name: str | None = None
    court_region: str | None = None
    press_module: str | None = None
    case_module: str | None = None


@dataclass(frozen=True)
class AirtableBaseRef:
    base_id: str
    shared_view_id: str | None
    table_name: str | None
    purpose: str | None = None


@dataclass(frozen=True)
class AirtableConfig:
    mode: str = "read_only"
    bases: dict[str, AirtableBaseRef] = field(default_factory=dict)


def _resolve_config_path(path: Path | str | None, default_name: str) -> Path:
    return Path(path) if path is not None else settings.config_path / default_name


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):  # pragma: no cover - defensive
        raise TypeError(f"{path}: expected mapping at top level, got {type(data).__name__}")
    return data


def load_monitoring(path: Path | str | None = None) -> MonitoringConfig:
    path = _resolve_config_path(path, "monitoring.yaml")
    data = _read_yaml(path)
    return MonitoringConfig(
        criminal_articles=list(data.get("criminal_articles", []) or []),
        keywords=list(data.get("keywords", []) or []),
    )


def load_sources(path: Path | str | None = None) -> list[SourceConfig]:
    path = _resolve_config_path(path, "sources.yaml")
    data = _read_yaml(path)
    raw_sources = data.get("sources", []) or []
    result: list[SourceConfig] = []
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        result.append(
            SourceConfig(
                name=str(item["name"]),
                type=SourceType(str(item.get("type", "sudrf"))),
                backend=SourceBackend(str(item.get("backend", "fixture"))),
                base_url=item.get("base_url"),
                paths=tuple(item.get("paths", []) or []),
                fixture_path=item.get("fixture_path"),
                parser=item.get("parser"),
                enabled=bool(item.get("enabled", True)),
                court_name=item.get("court_name"),
                court_region=item.get("court_region"),
                press_module=item.get("press_module"),
                case_module=item.get("case_module"),
            )
        )
    return result


def load_airtable(path: Path | str | None = None) -> AirtableConfig:
    path = _resolve_config_path(path, "sources.yaml")
    data = _read_yaml(path)
    raw = data.get("airtable", {}) or {}
    bases_raw = raw.get("bases", {}) or {}
    bases: dict[str, AirtableBaseRef] = {}
    for key, val in bases_raw.items():
        if not isinstance(val, dict):
            continue
        bases[str(key)] = AirtableBaseRef(
            base_id=str(val["base_id"]),
            shared_view_id=val.get("shared_view_id"),
            table_name=val.get("table_name"),
            purpose=val.get("purpose"),
        )
    return AirtableConfig(mode=str(raw.get("mode", "read_only")), bases=bases)


def get_source(name: str, path: Path | str | None = None) -> SourceConfig | None:
    for src in load_sources(path):
        if src.name == name:
            return src
    return None
