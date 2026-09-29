import asyncio
import logging
from functools import lru_cache
from typing import List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _load_model(model_name: str, revision: str):
    """Load a model once per process; it is shared by all EmbeddingService instances."""
    from sentence_transformers import SentenceTransformer

    logger.info("Loading local embedding model: %s @ %s", model_name, revision)
    return SentenceTransformer(
        model_name,
        # Pinned commit from config (MNE-20): floating on a branch would make
        # embeddings silently change between deploys.
        revision=revision or None,
        trust_remote_code=True,
    )


class EmbeddingService:
    """
    Local embedding service powered by sentence-transformers and nomic-ai/nomic-embed-text-v1.5.
    Runs locally without external API keys or billing.
    Generates 768-dimensional dense vector embeddings.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        dimension: Optional[int] = None,
        revision: Optional[str] = None,
    ) -> None:
        self.model = model or settings.EMBEDDING_MODEL
        self.dimension = dimension or settings.EMBEDDING_DIMENSION
        self.revision = revision or settings.EMBEDDING_MODEL_REVISION

    def _get_model(self):
        # Raises on failure (lru_cache doesn't cache exceptions, so the next call retries)
        return _load_model(self.model, self.revision)

    def _encode_sync(
        self, texts: List[str], is_query: bool = False
    ) -> List[List[float]]:
        model_inst = self._get_model()

        # Nomic v1.5 requires 'search_document: ' or 'search_query: ' prefixes
        prefix = "search_query: " if is_query else "search_document: "
        prefixed_texts = [
            t
            if t.startswith("search_document: ") or t.startswith("search_query: ")
            else f"{prefix}{t}"
            for t in texts
        ]

        embeddings = model_inst.encode(
            prefixed_texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return embeddings.tolist()

    async def generate_embeddings(
        self,
        texts: List[str],
        batch_size: int = 100,
        is_query: bool = False,
    ) -> List[List[float]]:
        """
        Generate 768-dimensional vector embeddings for a list of text strings.
        Automatically applies Nomic prefixes and offloads CPU encoding to threadpool.
        """
        if not texts:
            return []

        all_embeddings: List[List[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            cleaned_batch = [t if t.strip() else " " for t in batch]

            batch_embeddings = await asyncio.to_thread(
                self._encode_sync, cleaned_batch, is_query
            )
            all_embeddings.extend(batch_embeddings)

        return all_embeddings
