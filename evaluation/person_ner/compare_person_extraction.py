"""Experiment: rule-based person extraction against GLiNER on real court-monitor articles.

Answers one question only — «is this span a mention of a person» — and never «is this the
same person», which stays with entity resolution. Nothing here is imported by production
code; it exists to decide whether a model is worth integrating at all.

    uv run python evaluation/person_ner/compare_person_extraction.py --limit 45
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evaluation.real_world.corpus_cache import RawCorpusCache
from extraction.extractors import RuleBasedEntityExtractor
from extraction.models import EntityType
from sources.ingestion_errors import ParseError
from sources.source_registry import get_source_definition

GOLDEN_DIR = Path("evaluation/real_world/golden/articles")
CACHE_DIR = Path("var/real_world")
MODEL_ID = "vladlinv/ru-pii-ner-gliner2.5"
PERSON_LABELS = ["PERSON"]


@dataclass(frozen=True)
class Span:
    article: str
    url: str
    surface_text: str
    start_offset: int
    end_offset: int
    confidence: float
    extractor: str


@dataclass(frozen=True)
class Article:
    case_id: str
    url: str
    text: str
    golden: list[tuple[int, int, str]]


def load_articles(limit: int) -> list[Article]:
    """Golden articles whose text can be rebuilt from the raw corpus cache."""
    cache = RawCorpusCache(CACHE_DIR)
    parsed_by_source: dict[str, dict[str, Any]] = {}
    articles: list[Article] = []

    for path in sorted(GOLDEN_DIR.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        source = record["source"]
        if source not in parsed_by_source:
            parsed_by_source[source] = cache.entries(source)
        entry = parsed_by_source[source].get(record["external_id"])
        if entry is None or entry.content_b64 is None:
            continue
        definition = get_source_definition(source)
        try:
            article = definition.create_parser().parse(entry.raw_document())
        except ParseError:
            # A parser failure is not what this experiment measures; the corpus cache
            # keeps such entries deliberately.
            continue
        golden = [
            (mention["start"], mention["end"], mention["text"])
            for mention in record.get("mentions") or []
        ]
        articles.append(
            Article(
                case_id=record["case_id"],
                url=record.get("canonical_url") or "",
                text=article.text,
                golden=golden,
            )
        )
        if len(articles) >= limit:
            break
    return articles


def rule_based_spans(articles: list[Article]) -> tuple[list[Span], float]:
    from extraction.documents import build_extraction_document_from_parsed_article
    from sources.models import ParsedArticle

    extractor = RuleBasedEntityExtractor()
    spans: list[Span] = []
    started = time.monotonic()
    for article in articles:
        document = build_extraction_document_from_parsed_article(
            article_id=1,
            article=ParsedArticle(
                external_id=article.case_id,
                url=article.url or "https://example.invalid/a",
                title="",
                published_at=None,
                text=article.text,
            ),
            source_name="ОВД-Инфо",
            source_url=article.url or "https://example.invalid/a",
        )
        for mention in extractor.extract(document):
            if mention.entity_type is not EntityType.PERSON:
                continue
            spans.append(
                Span(
                    article=article.case_id,
                    url=article.url,
                    surface_text=mention.surface_text,
                    start_offset=mention.start_offset,
                    end_offset=mention.end_offset,
                    confidence=mention.confidence,
                    extractor="rule_based",
                )
            )
    return spans, time.monotonic() - started


def gliner_spans(
    articles: list[Article], *, device: str, threshold: float
) -> tuple[list[Span], float, float]:
    """GLiNER person spans; the model is loaded once, not per article."""
    import torch
    from gliner2 import AutoExtractor

    load_started = time.monotonic()
    model = AutoExtractor.from_pretrained(MODEL_ID)
    moved = getattr(model, "to", None)
    if callable(moved):
        model = moved(device)
    if hasattr(model, "eval"):
        model.eval()
    load_seconds = time.monotonic() - load_started

    spans: list[Span] = []
    started = time.monotonic()
    with torch.inference_mode():
        for article in articles:
            result = model.extract_entities(
                article.text,
                PERSON_LABELS,
                threshold=threshold,
                include_confidence=True,
                include_spans=True,
            )
            entities = result.get("entities", result)
            for label in PERSON_LABELS:
                for entity in entities.get(label, entities.get(label.lower(), [])) or []:
                    start, end = _entity_offsets(entity)
                    if start is None or end is None:
                        continue
                    spans.append(
                        Span(
                            article=article.case_id,
                            url=article.url,
                            surface_text=article.text[start:end],
                            start_offset=start,
                            end_offset=end,
                            confidence=round(float(entity.get("confidence", 1.0)), 4),
                            extractor="gliner",
                        )
                    )
    return spans, time.monotonic() - started, load_seconds


def _entity_offsets(entity: Any) -> tuple[int | None, int | None]:
    """Offsets of one GLiNER2 entity, whatever key shape the version reports them under."""
    if not isinstance(entity, dict):
        return None, None
    for start_key, end_key in (("start", "end"), ("start_offset", "end_offset")):
        if start_key in entity and end_key in entity:
            return int(entity[start_key]), int(entity[end_key])
    span = entity.get("span") or entity.get("offsets")
    if isinstance(span, (list, tuple)) and len(span) == 2:
        return int(span[0]), int(span[1])
    return None, None


def check_offsets(articles: list[Article], spans: list[Span]) -> list[str]:
    """`article.text[start:end] == surface_text` for every span, or it is reported."""
    texts = {article.case_id: article.text for article in articles}
    broken: list[str] = []
    for span in spans:
        text = texts[span.article]
        if text[span.start_offset : span.end_offset] != span.surface_text:
            broken.append(
                f"{span.extractor} {span.article} [{span.start_offset}:{span.end_offset}] "
                f"{span.surface_text!r} != {text[span.start_offset : span.end_offset]!r}"
            )
    return broken


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=45)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=Path("var/person_ner"))
    args = parser.parse_args()

    articles = load_articles(args.limit)
    chars = sum(len(a.text) for a in articles)
    golden_total = sum(len(a.golden) for a in articles)
    print(f"articles={len(articles)} chars={chars} golden_person_mentions={golden_total}")

    rule_spans, rule_seconds = rule_based_spans(articles)
    print(f"rule_based spans={len(rule_spans)} seconds={rule_seconds:.2f}")

    model_spans, model_seconds, load_seconds = gliner_spans(
        articles, device=args.device, threshold=args.threshold
    )
    print(
        f"gliner spans={len(model_spans)} seconds={model_seconds:.2f} "
        f"load={load_seconds:.1f}s device={args.device}"
    )

    broken = check_offsets(articles, rule_spans + model_spans)
    print(f"offset_invariant_violations={len(broken)}")
    for line in broken[:10]:
        print("  ", line)

    args.output.mkdir(parents=True, exist_ok=True)
    payload = {
        "model_id": MODEL_ID,
        "device": args.device,
        "threshold": args.threshold,
        "articles": len(articles),
        "chars": chars,
        "golden_person_mentions": golden_total,
        "golden_status": "DRAFT (agent_draft) — not human-verified",
        "rule_based": {"spans": len(rule_spans), "seconds": round(rule_seconds, 2)},
        "gliner": {
            "spans": len(model_spans),
            "seconds": round(model_seconds, 2),
            "load_seconds": round(load_seconds, 1),
        },
        "offset_violations": broken,
        "spans": [asdict(span) for span in rule_spans + model_spans],
        "golden": {
            article.case_id: [{"start": s, "end": e, "text": t} for s, e, t in article.golden]
            for article in articles
        },
        "texts": {article.case_id: article.text for article in articles},
    }
    out = args.output / "comparison.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
