"""Local manual labelling of the generated identity disagreement sample.

Only JSON files are read and written. No database, network or model is used.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LABELS = ("same_person", "different_person", "insufficient_evidence")
ANSWERS = dict(zip("sdi", LABELS, strict=True))
DEFAULT_SAMPLE = Path("var/identity_comparison/disagreement_annotation.json")
DEFAULT_ANNOTATIONS = Path("var/identity_comparison/manual_annotations.json")


@dataclass(frozen=True)
class Pair:
    number: int
    case: dict[str, Any]
    data: dict[str, Any]

    @property
    def identifier(self) -> str:
        return str(self.data["pair_id"])


def pairs_of(sample: dict[str, Any]) -> list[Pair]:
    pairs = [
        Pair(number, case, pair)
        for number, (case, pair) in enumerate(
            ((case, pair) for case in sample["cases"] for pair in case["pairs"]), start=1
        )
    ]
    identifiers = [pair.identifier for pair in pairs]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate pair_id in sample")
    for pair in pairs:
        sides = {side["side"]: side for side in pair.case["sides"]}
        if len(pair.data["sides"]) != 2 or any(side not in sides for side in pair.data["sides"]):
            raise ValueError("pair must reference two stored sides")
    return pairs


def load_annotations(path: Path, pairs: list[Pair]) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "annotations": {}}
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("unsupported annotations format")
    entries = document.get("annotations")
    if not isinstance(entries, dict):
        raise TypeError("annotations must be keyed by pair_id")
    known = {pair.identifier for pair in pairs}
    for identifier, entry in entries.items():
        if identifier not in known:
            raise ValueError("annotation refers to a pair absent from sample")
        if not isinstance(entry, dict) or "label" not in entry:
            raise ValueError("annotation must contain a label")
        if entry["label"] is not None and entry["label"] not in LABELS:
            raise ValueError("invalid label")
        if not isinstance(entry.get("skipped", False), bool):
            raise TypeError("skipped must be a boolean")
        if entry["label"] is not None and entry.get("skipped", False):
            raise ValueError("labelled pair cannot be skipped")
    return document


def atomic_write(path: Path, document: dict[str, Any]) -> None:
    """Flush a complete file before replacing the old one on the same filesystem."""
    payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record(
    path: Path,
    document: dict[str, Any],
    identifier: str,
    label: str | None,
    *,
    overwrite: bool = False,
) -> None:
    if label is not None and label not in LABELS:
        raise ValueError("invalid label")
    existing = document["annotations"].get(identifier, {})
    if existing.get("label") is not None and not overwrite:
        raise ValueError("existing label requires explicit --edit")
    updated = copy.deepcopy(document)
    updated["annotations"][identifier] = {
        "label": label,
        "comment": existing.get("comment"),
        "skipped": label is None,
    }
    atomic_write(path, updated)
    document.clear()
    document.update(updated)


def statistics(pairs: list[Pair], document: dict[str, Any]) -> dict[str, int]:
    entries = document["annotations"]
    counts = {
        label: sum(entries.get(pair.identifier, {}).get("label") == label for pair in pairs)
        for label in LABELS
    }
    labelled = sum(counts.values())
    return {
        "total": len(pairs),
        "labelled": labelled,
        **counts,
        "skipped": sum(bool(entries.get(pair.identifier, {}).get("skipped")) for pair in pairs),
        "unlabelled": len(pairs) - labelled,
    }


def merged_sample(sample: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(sample)
    for pair in pairs_of(merged):
        entry = document["annotations"].get(pair.identifier)
        if entry is not None:
            pair.data["annotation"] = {
                "label": entry["label"],
                "comment": entry.get("comment"),
            }
    return merged


def show_pair(pair: Pair, total: int, write: Callable[[str], None]) -> None:
    write(f"\npair {pair.number}/{total} ({pair.identifier})")
    sides = {side["side"]: side for side in pair.case["sides"]}
    for heading, identifier in zip(("A", "B"), pair.data["sides"], strict=True):
        write(f"\n[{heading}]")
        for index, mention in enumerate(sides[identifier]["mentions"], start=1):
            write(f"Mention {index}: {mention['surface']} / {mention['normalized']}")
            article = mention["article"]
            write(str(article.get("title") or "—"))
            write(f"{article.get('published_at') or '—'} | {article.get('source') or '—'}")
            write(str(mention["context"]["text"]))


def run(
    pairs: list[Pair],
    document: dict[str, Any],
    path: Path,
    *,
    edit: str | None = None,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    if edit is not None:
        pending = [pair for pair in pairs if pair.identifier == edit]
        if not pending:
            raise ValueError("unknown pair_id for --edit")
    else:
        pending = [
            pair
            for pair in pairs
            if document["annotations"].get(pair.identifier, {}).get("label") is None
        ]
    try:
        for pair in pending:
            show_pair(pair, len(pairs), write)
            existing = document["annotations"].get(pair.identifier, {}).get("label")
            if edit is not None:
                write(f"Editing existing label: {existing or 'unlabelled'}")
            while True:
                answer = read(
                    "[s] same [d] different [i] insufficient [k] skip [m] models [q] quit: "
                )
                answer = answer.strip().lower()
                if answer == "q":
                    return
                if answer == "m":
                    write(json.dumps(pair.case["decisions"], ensure_ascii=False))
                    continue
                if answer == "k":
                    # Editing and skipping an existing label leaves it intact.
                    if existing is None:
                        record(path, document, pair.identifier, None)
                    break
                if answer in ANSWERS:
                    record(
                        path, document, pair.identifier, ANSWERS[answer], overwrite=edit is not None
                    )
                    write("Saved.")
                    break
                write("Invalid answer; use s/d/i/k/m/q.")
        write("Session finished. Skipped pairs remain unlabelled and will return on resume.")
    except (KeyboardInterrupt, EOFError):
        write("\nStopped. Previously saved answers are on disk.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--stats", action="store_true")
    modes.add_argument("--edit", metavar="PAIR_ID")
    modes.add_argument("--merge-out", type=Path)
    args = parser.parse_args()
    try:
        if args.sample.resolve() == args.annotations.resolve():
            raise ValueError("sample and annotations must be separate files")
        sample = json.loads(args.sample.read_text(encoding="utf-8"))
        pairs = pairs_of(sample)
        document = load_annotations(args.annotations, pairs)
        if args.stats:
            print(json.dumps(statistics(pairs, document), indent=2))
        elif args.merge_out:
            if args.merge_out.resolve() in {args.sample.resolve(), args.annotations.resolve()}:
                raise ValueError("merge output must not overwrite sample or annotations")
            atomic_write(args.merge_out, merged_sample(sample, document))
        else:
            run(pairs, document, args.annotations, edit=args.edit)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Labelling failed: {error}\n")


if __name__ == "__main__":
    main()
