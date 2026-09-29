from unittest.mock import MagicMock, patch
import pytest

from app.modules.documents.services.embedding_service import EmbeddingService


@pytest.mark.asyncio
async def test_generate_embeddings_raises_when_model_fails_to_load():
    service = EmbeddingService(dimension=768)
    with patch.object(service, "_get_model", side_effect=OSError("model not found")):
        with pytest.raises(OSError):
            await service.generate_embeddings(["hello world"])


@pytest.mark.asyncio
async def test_generate_embeddings_uses_query_prefix():
    import numpy as np

    service = EmbeddingService(dimension=768)
    mock_model = MagicMock()
    mock_model.encode.return_value = np.array([[0.1] * 768])

    with patch.object(service, "_get_model", return_value=mock_model):
        await service.generate_embeddings(["what is rag"], is_query=True)
        mock_model.encode.assert_called_once_with(
            ["search_query: what is rag"],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )


@pytest.mark.asyncio
async def test_generate_embeddings_with_mocked_model():
    service = EmbeddingService(model="nomic-ai/nomic-embed-text-v1.5", dimension=768)

    mock_model = MagicMock()
    # Return 2 vectors of dimension 768
    import numpy as np
    mock_model.encode.return_value = np.array([[0.1] * 768, [0.2] * 768])

    with patch.object(service, "_get_model", return_value=mock_model):
        embeddings = await service.generate_embeddings(["chunk one", "chunk two"])
        assert len(embeddings) == 2
        assert len(embeddings[0]) == 768
        assert embeddings[0] == [0.1] * 768
        assert embeddings[1] == [0.2] * 768
        mock_model.encode.assert_called_once_with(
            ["search_document: chunk one", "search_document: chunk two"],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )


@pytest.mark.asyncio
async def test_generate_embeddings_empty_list():
    service = EmbeddingService()
    embeddings = await service.generate_embeddings([])
    assert embeddings == []


def test_service_loads_model_at_configured_revision(monkeypatch):
    from app.core.config import settings
    from app.modules.documents.services import embedding_service as es

    calls = {}

    class FakeSentenceTransformer:
        def __init__(self, model_name, revision=None, trust_remote_code=False):
            calls["model"] = model_name
            calls["revision"] = revision

    monkeypatch.setattr(
        "sentence_transformers.SentenceTransformer", FakeSentenceTransformer
    )
    es._load_model.cache_clear()

    service = EmbeddingService()
    assert service.revision == settings.EMBEDDING_MODEL_REVISION
    service._get_model()

    assert calls["model"] == settings.EMBEDDING_MODEL
    assert calls["revision"] == settings.EMBEDDING_MODEL_REVISION
    es._load_model.cache_clear()
