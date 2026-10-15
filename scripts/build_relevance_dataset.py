import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

SAMPLE_PATH = ROOT / "var/junk_screen/sample.jsonl"
LABELS_PATH = ROOT / "evaluation/junk_screen/labels.json"

OUTPUT_DIR = ROOT / "data/ml/relevance"
TRAIN_PATH = OUTPUT_DIR / "train.jsonl"
TEST_PATH = OUTPUT_DIR / "test.jsonl"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main() -> None:
    samples = read_jsonl(SAMPLE_PATH)

    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    labels_by_id: dict[int, dict[str, Any]] = {row["article_id"]: row for row in labels}

    train: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []

    for sample in samples:
        article_id = sample["article_id"]

        label_info = labels_by_id.get(article_id)
        if label_info is None:
            continue

        row = {
            "article_id": article_id,
            "title": sample["title"],
            "text": sample["text"],
            "source": sample["source"],
            "url": sample["url"],
            "published_at": sample["published_at"],
            # Основная бинарная ML-метка.
            "label": int(label_info["kind"] == "case"),
            # Оставляем исходную подробную классификацию для анализа ошибок.
            "kind": label_info["kind"],
            "political": label_info["political"],
            "stratum": sample["stratum"],
        }

        # Сохраняем уже существующий temporal split.
        if label_info["part"] == "calibration":
            train.append(row)
        elif label_info["part"] == "validation":
            test.append(row)
        else:
            raise ValueError(f"Unknown part: {label_info['part']}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for path, rows in ((TRAIN_PATH, train), (TEST_PATH, test)):
        with path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"train: {len(train)}")
    print(f"test:  {len(test)}")

    for name, rows in (("train", train), ("test", test)):
        positive = sum(row["label"] for row in rows)
        negative = len(rows) - positive

        print(
            f"{name}: "
            f"positive={positive}, "
            f"negative={negative}, "
            f"positive_ratio={positive / len(rows):.3f}"
        )


if __name__ == "__main__":
    main()
