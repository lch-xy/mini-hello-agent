"""P13：持久化证据库与可替换的检索契约。"""

from .store import DocumentResult, RAGStore, RetrievalResult
from .pipeline import RetrievalPipeline, Reranker, SearchBackend

__all__ = ["DocumentResult", "RAGStore", "RetrievalResult", "RetrievalPipeline", "Reranker", "SearchBackend"]

from .embeddings import EmbeddingProvider, OpenAIEmbeddingProvider, FastEmbedProvider
from .qdrant import QdrantSearch, QdrantCloudInference, IndexStaleError
from .hybrid import HybridSearch
from .rerank import LLMReranker
from .evaluation import ranking_metrics, evaluate_retrieval

__all__ += [
    "EmbeddingProvider", "OpenAIEmbeddingProvider", "FastEmbedProvider",
    "QdrantSearch", "QdrantCloudInference", "IndexStaleError", "HybridSearch",
    "LLMReranker", "ranking_metrics", "evaluate_retrieval",
]
