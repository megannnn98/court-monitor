from collections.abc import Sequence

import pytest

from cross_encoder_reranker import CrossEncoderReranker
from models import SearchHit
from reranker import Reranker


class FakeCrossEncoderModel:
    def __init__(self, scores: Sequence[float]) -> None:
        self._scores = list(scores)
        self.inputs: list[tuple[str, str]] = []
        self.call_count = 0
        self.show_progress_bar: bool | None = None

    def predict(
        self,
        inputs: list[tuple[str, str]],
        *,
        show_progress_bar: bool = False,
    ) -> list[float]:
        self.call_count += 1
        self.inputs = list(inputs)
        self.show_progress_bar = show_progress_bar

        return list(self._scores)


def make_hit(
    chunk_id: int,
    *,
    score: float = 1.0,
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        article_id=chunk_id,
        source_base_url="https://example.com",
        external_id=f"article-{chunk_id}",
        ordinal=0,
        title=f"Article {chunk_id}",
        published_at=None,
        url=f"https://example.com/{chunk_id}",
        text=f"Chunk {chunk_id}",
        score=score,
    )


def test_reranks_candidates_using_cross_encoder_scores() -> None:
    model = FakeCrossEncoderModel(
        [
            0.2,
            0.9,
            0.9,
        ]
    )

    reranker: Reranker = CrossEncoderReranker(model)

    candidates = [
        make_hit(1),
        make_hit(2),
        make_hit(3),
    ]

    result = reranker.rerank(
        "реабилитация нацизма",
        candidates,
        limit=2,
    )

    assert [hit.chunk_id for hit in result] == [2, 3]
    assert [hit.score for hit in result] == [0.9, 0.9]

    assert model.inputs == [
        ("реабилитация нацизма", "Chunk 1"),
        ("реабилитация нацизма", "Chunk 2"),
        ("реабилитация нацизма", "Chunk 3"),
    ]
    assert model.show_progress_bar is False


def test_empty_candidates_do_not_call_model() -> None:
    model = FakeCrossEncoderModel([])

    reranker = CrossEncoderReranker(model)

    result = reranker.rerank(
        "реабилитация нацизма",
        [],
        limit=3,
    )

    assert result == []
    assert model.call_count == 0


@pytest.mark.parametrize(
    "limit",
    [
        0,
        -1,
    ],
)
def test_non_positive_limit_is_rejected(
    limit: int,
) -> None:
    model = FakeCrossEncoderModel([])

    reranker = CrossEncoderReranker(model)

    with pytest.raises(
        ValueError,
        match="limit must be greater than 0",
    ):
        reranker.rerank(
            "реабилитация нацизма",
            [],
            limit=limit,
        )


def test_wrong_number_of_scores_is_rejected() -> None:
    model = FakeCrossEncoderModel(
        [
            0.5,
        ]
    )

    reranker = CrossEncoderReranker(model)

    candidates = [
        make_hit(1),
        make_hit(2),
    ]

    with pytest.raises(
        RuntimeError,
        match="different number of scores",
    ):
        reranker.rerank(
            "реабилитация нацизма",
            candidates,
            limit=2,
        )
