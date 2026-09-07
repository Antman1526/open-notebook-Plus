"""Cross-encoder reranker client for Deeper Notebook search retrieval.

Pairs with local or remote reranking endpoints (such as `llama-server --rerank`
serving `bge-reranker-base-Q4_K_M.gguf`, TEI, or Cohere-compatible reranker endpoints).
Fails soft: if the reranker is unreachable or disabled, the original RRF-ranked
results are returned unchanged.
"""

from __future__ import annotations

from typing import Any
import os
import httpx
from loguru import logger

DEFAULT_RERANKER_TIMEOUT_SEC = 5.0


def extract_document_text(row: Any) -> str:
    """Extract representative text from a search result dict or model."""
    if not isinstance(row, dict):
        return str(row) if row is not None else ""
    # Search results commonly use 'content', 'text', 'title', or combined metadata
    text = row.get("content") or row.get("text") or ""
    title = row.get("title") or ""
    if title and text:
        return f"{title}\n\n{text}"
    return text or title or str(row)


async def rerank_results(
    query: str,
    results: list[Any],
    *,
    top_n: int | None = None,
    reranker_url: str | None = None,
    timeout: float = DEFAULT_RERANKER_TIMEOUT_SEC,
) -> list[Any]:
    """Rerank a sequence of search results using a cross-encoder endpoint.

    Args:
        query: The user search query string.
        results: Candidate search rows returned from BM25, vector, or RRF search.
        top_n: Number of top results to return. Defaults to len(results).
        reranker_url: Optional explicit URL. Defaults to DEEPER_NOTEBOOK_RERANKER_URL.
        timeout: Maximum seconds to wait for reranker response.

    Returns:
        The results re-sorted by cross-encoder relevance score, or the original
        results if the reranker is not configured, empty, or fails.
    """
    if not results or not query.strip():
        return results if top_n is None else results[:top_n]

    target_top_n = len(results) if top_n is None or top_n <= 0 else min(top_n, len(results))
    url = reranker_url or os.environ.get("DEEPER_NOTEBOOK_RERANKER_URL", "")
    if not url:
        return results[:target_top_n]

    documents = [extract_document_text(row) for row in results]

    payload = {
        "query": query,
        "documents": documents,
        "top_n": target_top_n,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning(
                    f"Reranker endpoint {url} returned status {resp.status_code}: {resp.text[:200]}"
                )
                return results[:target_top_n]

            data = resp.json()
            # Support both Cohere/llama-server format {"results": [{"index": 0, "relevance_score": 0.9}, ...]}
            # and data format {"data": [{"index": 0, "score": 0.9}, ...]}
            ranked_items = data.get("results") or data.get("data")
            if not isinstance(ranked_items, list):
                logger.warning(f"Reranker endpoint {url} returned unexpected response shape: {data}")
                return results[:target_top_n]

            reordered: list[Any] = []
            seen_indices: set[int] = set()
            for item in ranked_items:
                if not isinstance(item, dict):
                    continue
                idx = item.get("index")
                score = item.get("relevance_score", item.get("score"))
                if isinstance(idx, int) and 0 <= idx < len(results) and idx not in seen_indices:
                    row = results[idx]
                    if isinstance(row, dict) and score is not None:
                        # Shallow copy to stamp score without mutating source
                        row = dict(row)
                        row["rerank_score"] = float(score)
                    reordered.append(row)
                    seen_indices.add(idx)

            # Append any missing candidates in their original order
            for idx, row in enumerate(results):
                if idx not in seen_indices:
                    reordered.append(row)

            return reordered[:target_top_n]

    except Exception as err:
        logger.warning(f"Reranker failed ({err}); falling back to initial ranking")
        return results[:target_top_n]
