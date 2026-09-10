import torch
from sentence_transformers import SentenceTransformer


class TextEmbedder:
    def __init__(self, model_id: str) -> None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = SentenceTransformer(model_id, device=device)

    def embed_query(self, text: str) -> list[float]:
        vector = self._model.encode(
            f"query: {text}",
            normalize_embeddings=True,
        )
        return [float(value) for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            [f"passage: {text}" for text in texts],
            normalize_embeddings=True,
        )

        return [[float(value) for value in vector] for vector in vectors]
