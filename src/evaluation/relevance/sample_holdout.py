"""A fixed random sample of the relevance holdout, to be labelled by hand.

    python -m evaluation.relevance.sample_holdout

Draws 400 articles uniformly, without replacement, from the frozen holdout's list of
article ids — sorted first, so the draw does not depend on the file's order — with a fixed
seed. Nothing but that list decides: no model, no label, no stratum, no keyword. The
holdout is only read; the sample, once written, is frozen like it.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.relevance.holdout import FrozenError, ordered, read_jsonl, write_atomically

ROOT = Path(__file__).resolve().parents[3]
HOLDOUT_DIR = ROOT / "data/ml/relevance/holdout"
DATASET = "relevance-holdout-v1-sample"
SAMPLE_SIZE = 400
SEED = 20261015
FILES = ("sample_articles.jsonl", "sample_metadata.json")


def draw(article_ids: Sequence[int], size: int = SAMPLE_SIZE, seed: int = SEED) -> list[int]:
    """`size` distinct ids, uniformly, by `seed`; the same ids for the same population."""
    population = sorted(set(article_ids))
    if len(population) != len(article_ids):
        raise ValueError("the population repeats an article id")
    if not 0 < size <= len(population):
        raise ValueError(f"cannot draw {size} of {len(population)}")
    return sorted(random.Random(seed).sample(population, size))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_sample(
    holdout_dir: Path,
    *,
    size: int = SAMPLE_SIZE,
    seed: int = SEED,
    created_at: datetime,
) -> dict[str, Any]:
    """Write the sample's articles and metadata next to the holdout; refuse, changing
    nothing, if a sample is there already."""
    existing = [name for name in FILES if (holdout_dir / name).exists()]
    if existing:
        raise FrozenError(f"the sample is frozen: {', '.join(existing)} already exist")
    population_path = holdout_dir / "articles.jsonl"
    holdout_metadata = json.loads((holdout_dir / "metadata.json").read_text(encoding="utf-8"))
    if not holdout_metadata.get("frozen"):
        raise ValueError("the holdout is not frozen")
    population = read_jsonl(population_path)
    chosen = set(draw([int(article["article_id"]) for article in population], size, seed))
    sample = ordered(article for article in population if int(article["article_id"]) in chosen)
    sample_path = holdout_dir / "sample_articles.jsonl"
    write_atomically(
        sample_path,
        "".join(json.dumps(article, ensure_ascii=False) + "\n" for article in sample),
    )
    metadata = {
        "dataset": DATASET,
        "population": "articles.jsonl",
        "population_dataset": holdout_metadata.get("dataset"),
        "population_size": len(population),
        "population_sha256": sha256_of(population_path),
        "sample_size": len(sample),
        "sample_sha256": sha256_of(sample_path),
        "sampling": "uniform_without_replacement",
        "population_order": "article_id ascending",
        "seed": seed,
        "predictions_used": False,
        "labels_used": False,
        "frozen": True,
        "created_at": created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "unique_normalized_text_hashes": len({article["text_hash"] for article in sample}),
        "article_ids": sorted(chosen),
    }
    write_atomically(
        holdout_dir / "sample_metadata.json",
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
    )
    return metadata


def check_frozen(holdout_dir: Path) -> Mapping[str, Any]:
    """The sample's metadata, once the files are checked to be those it was frozen with."""
    metadata: dict[str, Any] = json.loads(
        (holdout_dir / "sample_metadata.json").read_text(encoding="utf-8")
    )
    for name, key in (
        ("articles.jsonl", "population_sha256"),
        ("sample_articles.jsonl", "sample_sha256"),
    ):
        if sha256_of(holdout_dir / name) != metadata[key]:
            raise ValueError(f"{name} changed since the sample was frozen")
    return metadata


def main() -> int:
    try:
        metadata = make_sample(HOLDOUT_DIR, created_at=datetime.now(UTC))
    except FrozenError as error:
        print(f"{error}; nothing changed", file=sys.stderr)
        return 1
    for key in (
        "population_size",
        "sample_size",
        "seed",
        "unique_normalized_text_hashes",
        "population_sha256",
        "sample_sha256",
    ):
        print(f"{key}: {metadata[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
