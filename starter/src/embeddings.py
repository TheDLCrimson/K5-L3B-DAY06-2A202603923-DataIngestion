"""Local embedding model: intfloat/multilingual-e5-base through sentence-transformers.

e5 expects "query: " on questions and "passage: " on documents; vectors are
L2-normalised so cosine similarity equals the dot product. There is no fallback:
if the model cannot load or a text is too long, this raises.
"""
from typing import Protocol

MODEL_NAME = "intfloat/multilingual-e5-base"
MODEL_REVISION = "d128750597153bb5987e10b1c3493a34e5a4502a"  # Hugging Face commit, pinned


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class E5Embedder:
    QUERY_PREFIX = "query: "
    PASSAGE_PREFIX = "passage: "

    def __init__(self, model_name: str = MODEL_NAME, revision: str = MODEL_REVISION,
                 device: str | None = None, batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.revision = revision
        self.model = SentenceTransformer(model_name, revision=revision, device=device)
        self.device = str(self.model.device)
        self.dimensions = self.model.get_sentence_embedding_dimension()
        self.max_tokens = self.model.max_seq_length
        self.batch_size = batch_size

    def count_tokens(self, texts: list[str]) -> list[int]:
        """Token count of each passage as the model sees it, prefix included."""
        tokenizer = self.model.tokenizer
        return [len(tokenizer(self.PASSAGE_PREFIX + t)["input_ids"]) for t in texts]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=False)
        return vectors.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        too_long = [i for i, n in enumerate(self.count_tokens(texts)) if n > self.max_tokens]
        if too_long:
            raise ValueError(f"{len(too_long)} passages exceed {self.max_tokens} tokens and would be "
                             f"silently truncated (first index {too_long[0]})")
        return self._encode([self.PASSAGE_PREFIX + t for t in texts])

    def embed_query(self, question: str) -> list[float]:
        return self._encode([self.QUERY_PREFIX + question])[0]
