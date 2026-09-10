from text_embedder import TextEmbedder


def test_text_embedder() -> None:
    embedder = TextEmbedder("intfloat/multilingual-e5-base")

    query_vector = embedder.embed_query("реабилитация нацизма")
    document_vectors = embedder.embed_documents(
        [
            "Возбуждено уголовное дело о реабилитации нацизма.",
            "Сегодня ожидается дождливая погода.",
        ]
    )

    assert len(query_vector) == 768
    assert len(document_vectors) == 2
    assert len(document_vectors[0]) == 768


def dot_product(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_semantically_related_text_has_higher_similarity() -> None:
    embedder = TextEmbedder("intfloat/multilingual-e5-base")

    query = embedder.embed_query("реабилитация нацизма")

    documents = embedder.embed_documents(
        [
            "Возбуждено уголовное дело о реабилитации нацизма.",
            "Сегодня ожидается дождливая погода.",
        ]
    )

    relevant_score = dot_product(query, documents[0])
    irrelevant_score = dot_product(query, documents[1])

    assert relevant_score > irrelevant_score
