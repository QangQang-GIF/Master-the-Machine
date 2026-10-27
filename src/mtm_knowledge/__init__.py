"""Revision-aware, bounded repository knowledge mapping."""
from .index import build_index, retrieve_knowledge, clear_index_cache
from .bootstrap import (
    KnowledgeBootstrap,
    detect_tech_stack,
    lookup_section,
    incremental_refresh,
    get_knowledge_or_fallback,
)

__all__ = [
    "build_index",
    "retrieve_knowledge",
    "clear_index_cache",
    "KnowledgeBootstrap",
    "detect_tech_stack",
    "lookup_section",
    "incremental_refresh",
    "get_knowledge_or_fallback",
]
