"""regulatory_docs agent.

Uploads or finds regulatory documents and, for a selected document, vectorizes it and stores the
vectors in a Qdrant cluster. Vectorization is configured by named profiles in
``config/vectorization.json`` (embedding provider/model/dimension, chunking, target cluster/
collection), so the embedding model or store can be changed without touching code.
"""
from .agent import (
    AGENT_NAME,
    FIND_DOCUMENTS,
    UPLOAD_DOCUMENT,
    VECTORIZE_DOCUMENT,
    RegulatoryDocsHandler,
)

__all__ = [
    "AGENT_NAME",
    "UPLOAD_DOCUMENT",
    "FIND_DOCUMENTS",
    "VECTORIZE_DOCUMENT",
    "RegulatoryDocsHandler",
]
