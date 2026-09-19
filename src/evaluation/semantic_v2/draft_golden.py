"""Write agent-drafted golden articles from a compact annotation spec (Semantic Retrieval v2).

The annotator (an AI agent reading the article text, never court-monitor output) decides
who is mentioned, in which surface forms, which events happen and what each person's
expected decisions are. This script only turns that into the golden format: it finds the
offsets of every surface form and event evidence in the article text, assigns splits by
the golden rules and appends the new persons. Every article it writes is DRAFT with
annotation_origin agent_draft: not ground truth until a human verifies it.

Spec (JSON list, one entry per article):

    {"key": "ovd-info:/express-news/...", "tags": [...], "notes": "...",
     "persons": [{"id": "gp-...", "name": "Имя Фамилия", "surfaces": ["Фамилии", ...],
                  "aliases": [], "persecution": {"status": "political",
                  "acceptable": [], "evidence": "<exact text>"} | null, "notes": null}],
     "events": [{"id": "e1", "type": "sentence", "persons": ["gp-..."],
                 "evidence": "<exact text>", "historical": false, "shared": false}]}

A person already in persons.json is referenced by id with only "surfaces".

    PYTHONPATH=src uv run python -m evaluation.semantic_v2.draft_golden --spec spec.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from evaluation.real_world.cli_helpers import corpus_texts
from evaluation.real_world.corpus_cache import RawCorpusCache
from evaluation.real_world.golden import assign_splits
from evaluation.real_world.models import DEFAULT_CACHE_DIR, DEFAULT_MANIFEST_PATH, load_manifest

GOLDEN = Path("evaluation/real_world/golden")
RF_SNAPSHOT = Path("evaluation/real_world/rf_snapshot_eval_v1.csv")
ANNOTATOR = "claude-code-agent (AI draft, not human-verified)"
SPLIT_SEED = 20260920


def _occurrences(text: str, surface: str) -> list[tuple[int, int]]:
    pattern = re.compile(rf"(?<!\w){re.escape(surface)}(?!\w)")
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


def _span(text: str, fragment: str, where: str) -> dict[str, Any]:
    start = text.find(fragment)
    if start < 0:
        raise SystemExit(f"{where}: {fragment!r} is not in the article text")
    return {"start": start, "end": start + len(fragment), "text": fragment}


def _rf_entries() -> list[str]:
    with RF_SNAPSHOT.open(encoding="utf-8") as handle:
        return [row["full_name"] for row in csv.DictReader(handle)]


def _rf_decision(name: str, entries: list[str]) -> dict[str, Any]:
    words = name.upper().replace("Ё", "Е").split()
    if len(words) < 2:
        return {"snapshot": "rf-eval-v1", "expected_status": "insufficient_data"}
    surname, given = words[-1], words[0]
    for entry in entries:
        parts = entry.replace("Ё", "Е").split()
        if len(parts) >= 2 and parts[0] == surname and parts[1] == given:
            return {
                "snapshot": "rf-eval-v1",
                "expected_status": "matched",
                "expected_entry": entry,
                "acceptable_review": True,
            }
    return {"snapshot": "rf-eval-v1", "expected_status": "not_matched"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True, nargs="+")
    parser.add_argument("--dry-run", action="store_true", help="Check the spec, write nothing")
    args = parser.parse_args()
    spec: list[dict[str, Any]] = [
        entry for path in args.spec for entry in json.loads(path.read_text(encoding="utf-8"))
    ]
    manifest = load_manifest(DEFAULT_MANIFEST_PATH)
    by_key = {a.key: a for a in manifest.articles}
    texts = corpus_texts(RawCorpusCache(DEFAULT_CACHE_DIR), manifest)
    persons: list[dict[str, Any]] = json.loads((GOLDEN / "persons.json").read_text("utf-8"))
    known = {p["golden_person_id"]: p for p in persons}
    existing = [
        json.loads(f.read_text("utf-8")) for f in sorted((GOLDEN / "articles").glob("*.json"))
    ]
    existing_keys = {f"{a['source']}:{a['external_id']}" for a in existing}
    person_split: dict[str, str] = {}
    for article in existing:
        for mention in article["mentions"]:
            person_split[mention["golden_person_id"]] = article["split"]
    group_split = {
        by_key[f"{a['source']}:{a['external_id']}"].duplicate_group: a["split"] for a in existing
    }
    next_index = len(existing)
    rf_entries = _rf_entries()

    drafts: dict[str, dict[str, Any]] = {}
    new_persons: dict[str, dict[str, Any]] = {}
    first_seen: dict[str, tuple[Any, str]] = {}
    for entry in spec:
        key = entry["key"]
        if key in existing_keys:
            raise SystemExit(f"{key} is already a golden article")
        meta, text = by_key[key], texts[key]
        taken: list[tuple[int, int]] = []
        mentions: list[dict[str, Any]] = []
        for person in entry["persons"]:
            surfaces = sorted(set(person["surfaces"]), key=len, reverse=True)
            found = 0
            for surface in surfaces:
                for start, end in _occurrences(text, surface):
                    if any(s < end and start < e for s, e in taken):
                        continue
                    taken.append((start, end))
                    mentions.append(
                        {
                            "start": start,
                            "end": end,
                            "text": surface,
                            "golden_person_id": person["id"],
                        }
                    )
                    found += 1
            if not found:
                raise SystemExit(f"{key}: no surface of {person['id']} found")
            if person["id"] not in known:
                seen = (meta.published_at, meta.corpus_split.value)
                first_seen[person["id"]] = min(first_seen.get(person["id"], seen), seen)
            if person["id"] not in known and "name" in person:
                decision = person.get("persecution")
                record: dict[str, Any] = {
                    "golden_person_id": person["id"],
                    "canonical_name": person["name"],
                }
                if person.get("aliases"):
                    record["aliases"] = person["aliases"]
                if decision is not None:
                    record["persecution"] = {
                        "expected_status": decision["status"],
                        **(
                            {"acceptable_statuses": decision["acceptable"]}
                            if decision.get("acceptable")
                            else {}
                        ),
                        "evidence": [
                            {
                                "case_id": "?",
                                "span": _span(text, decision["evidence"], person["id"]),
                            }
                        ],
                    }
                record["rosfinmonitoring"] = _rf_decision(person["name"], rf_entries)
                if person.get("notes"):
                    record["notes"] = person["notes"]
                record["_source"] = key
                previous = new_persons.get(person["id"])
                # The record that carries a decision wins; its evidence is its own article's.
                if previous is None or ("persecution" in record and "persecution" not in previous):
                    new_persons[person["id"]] = record
        events = []
        for event in entry.get("events", []):
            item: dict[str, Any] = {
                "event_id": event["id"],
                "event_type": event["type"],
                "person_ids": event["persons"],
                "evidence": _span(text, event["evidence"], f"{key} {event['id']}"),
            }
            if event.get("shared"):
                item["shared"] = True
            if event.get("historical"):
                item["historical"] = True
            events.append(item)
        mentions.sort(key=lambda m: m["start"])
        drafts[key] = {
            "case_id": None,
            "source": meta.source,
            "external_id": meta.external_id,
            "canonical_url": meta.canonical_url,
            "published_at": meta.published_at.isoformat(),
            "content_hash": meta.content_hash,
            "split": None,
            "annotation_status": "DRAFT",
            "annotation_origin": "agent_draft",
            "annotators": [ANNOTATOR],
            "verified_by": None,
            "verified_at": None,
            "tags": entry.get("tags", []),
            "excerpts": [{"start": 0, "end": len(text), "text": text}],
            "mentions": mentions,
            "events": events,
            "disputed": entry.get("disputed", []),
            "notes": entry.get("notes"),
        }

    undefined = sorted(set(first_seen) - set(new_persons))
    if undefined:
        raise SystemExit(f"persons referenced without a definition (name): {undefined}")

    # Splits: a person or a duplicate story never spans two splits. Groups touching an
    # existing golden article inherit its split; the others get the 60/20/20 rule.
    parent = {key: key for key in drafts}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    owners: dict[str, list[str]] = defaultdict(list)
    for key, draft in drafts.items():
        owners[f"group:{by_key[key].duplicate_group}"].append(key)
        for mention in draft["mentions"]:
            owners[mention["golden_person_id"]].append(key)
    for keys in owners.values():
        for other in keys[1:]:
            parent[find(other)] = find(keys[0])
    inherited: dict[str, str] = {}
    for key, draft in drafts.items():
        root = find(key)
        splits = {
            person_split[m["golden_person_id"]]
            for m in draft["mentions"]
            if m["golden_person_id"] in person_split
        }
        if by_key[key].duplicate_group in group_split:
            splits.add(group_split[by_key[key].duplicate_group])
        if len(splits) > 1:
            raise SystemExit(f"{key}: links golden articles of splits {sorted(splits)}")
        if splits:
            linked = inherited.setdefault(root, next(iter(splits)))
            if linked != next(iter(splits)):
                raise SystemExit(f"{key}: its group links splits {linked} and {splits}")
    free = {key: find(key) for key in drafts if find(key) not in inherited}
    assigned = assign_splits(free, SPLIT_SEED)
    for key, draft in drafts.items():
        root = find(key)
        draft["split"] = inherited[root] if root in inherited else assigned[key].value

    ordered = sorted(drafts, key=lambda k: (by_key[k].published_at, k))
    for offset, key in enumerate(ordered):
        draft = drafts[key]
        draft["case_id"] = f"rw-{by_key[key].published_at:%Y%m%d}-{next_index + offset:02d}"
    case_of = {key: drafts[key]["case_id"] for key in drafts}
    for record in new_persons.values():
        source = record.pop("_source")
        period = first_seen[record["golden_person_id"]][1]
        if "persecution" in record:
            for evidence in record["persecution"]["evidence"]:
                evidence["case_id"] = case_of[source]
        main = (
            record.get("persecution", {}).get("expected_status") == "political"
            and record["rosfinmonitoring"]["expected_status"] == "not_matched"
        )
        record["candidate"] = (
            {"expected_main_candidate": True, "expected_first_finding_period": period}
            if main
            else {"expected_main_candidate": False}
        )
        known[record["golden_person_id"]] = record
    counts: dict[str, int] = defaultdict(int)
    for draft in drafts.values():
        counts[draft["split"]] += 1
    summary = f"{len(drafts)} articles {dict(counts)}, {len(new_persons)} new persons"
    if args.dry_run:
        for key in ordered:
            draft = drafts[key]
            print(draft["case_id"], draft["split"], len(draft["mentions"]), "mentions", key)
        print("dry run:", summary)
        return
    for key in ordered:
        path = GOLDEN / "articles" / f"{drafts[key]['case_id']}.json"
        path.write_text(json.dumps(drafts[key], ensure_ascii=False, indent=2) + "\n", "utf-8")
    merged = [known[pid] for pid in sorted(known)]
    (GOLDEN / "persons.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    print("wrote", summary)


if __name__ == "__main__":
    main()
