import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DenseSearchConfig:
    qdrant_url: str
    collection_name: str
    embedding_model_id: str

    @classmethod
    def from_env(cls) -> "DenseSearchConfig":
        return cls(
            qdrant_url=os.environ["QDRANT_URL"],
            collection_name=os.environ["QDRANT_COLLECTION"],
            embedding_model_id=os.environ["EMBEDDING_MODEL_ID"],
        )
