import pytest

from app.config import Settings
from app.rag.providers import EmbeddingProviderError, LocalSparseEmbeddingProvider, get_embedding_provider


def test_local_sparse_embedding_provider_normalizes_rag_terms():
    provider = LocalSparseEmbeddingProvider()

    decision_vector = provider.embed("Alice decided which files were mentioned.")
    query_vector = provider.embed("What decision file mention?")

    assert "decide" in decision_vector
    assert "file" in decision_vector
    assert "mention" in decision_vector
    assert provider.similarity(query_vector, decision_vector) > 0


def test_get_embedding_provider_rejects_unknown_provider():
    with pytest.raises(EmbeddingProviderError, match="Unsupported RAG embedding provider"):
        get_embedding_provider(Settings(rag_embedding_provider="paid_cloud_embedding"))
