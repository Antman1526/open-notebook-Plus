"""Phase 5.4 — Deep Research Agent Mode.

Multi-step inquiry, hybrid search + reranker synthesis, and research brief generation.
This graph orchestrates:
  1. `plan_inquiry`: Breaks down the research objective into structured inquiry paths
     and targeted search queries across distinct analytical facets.
  2. `conduct_hybrid_retrieval`: Runs parallel text + vector search fused with
     Reciprocal Rank Fusion (RRF) and scored with cross-encoder reranking.
  3. `synthesize_research_brief`: Synthesizes multi-faceted evidence into an
     exhaustive, structured Research Brief with grounded citations.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, List, Optional
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from deeper_notebook.ai.models import model_manager
from deeper_notebook.ai.provision import provision_langchain_model
from deeper_notebook.domain.notebook import text_search, vector_search
from deeper_notebook.exceptions import DeeperNotebookError, ExternalServiceError
from deeper_notebook.graphs.agent_fsm import AgentState
from deeper_notebook.search.fusion import reciprocal_rank_fusion
from deeper_notebook.search.reranker import rerank_results
from deeper_notebook.utils.error_classifier import classify_error
from deeper_notebook.utils.text_utils import clean_thinking_content, extract_text_content

_DEFAULT_RESEARCH_TIMEOUT_SEC = 120.0


def _research_timeout_sec() -> float:
    raw = (os.environ.get("DEEPER_NOTEBOOK_RESEARCH_NODE_TIMEOUT_SEC") or "").strip()
    if not raw:
        return _DEFAULT_RESEARCH_TIMEOUT_SEC
    try:
        val = float(raw)
        return val if val > 0 else _DEFAULT_RESEARCH_TIMEOUT_SEC
    except ValueError:
        return _DEFAULT_RESEARCH_TIMEOUT_SEC


class InquiryPath(BaseModel):
    """An individual sub-question explored during deep research."""

    model_config = ConfigDict(extra="ignore")

    sub_question: str = Field(..., description="Focused sub-question or angle of investigation")
    search_query: str = Field(..., description="Optimized search query for text and vector retrieval")
    rationale: str = Field(default="", description="Why this inquiry path is needed")
    facet: str = Field(default="core", description="Analytical facet: foundational, mechanism, tradeoff, outcome")


class ResearchPlan(BaseModel):
    """The multi-inquiry strategic plan generated for an objective."""

    model_config = ConfigDict(extra="ignore")

    objective: str
    inquiry_paths: list[InquiryPath] = Field(default_factory=list)
    strategy_summary: str = Field(default="")


class EvidenceItem(BaseModel):
    """A grounded piece of evidence retrieved and reranked."""

    model_config = ConfigDict(extra="ignore")

    id: str
    query: str
    title: str = ""
    text: str = ""
    score: float = 0.0
    source_type: str = "source"
    parent_id: Optional[str] = None


class DeepResearchState(TypedDict, total=False):
    objective: str
    notebook_id: Optional[str]
    max_queries: int
    plan: ResearchPlan
    evidence: list[dict[str, Any]]
    research_brief: str
    citations: list[dict[str, Any]]
    agent_state: str


def _clean_json_str(raw: str) -> str:
    """Extract JSON content from free text or markdown fenced blocks."""
    cleaned = clean_thinking_content(extract_text_content(raw)).strip()
    # Match ```json ... ```
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, re.I)
    if fence_match:
        return fence_match.group(1).strip()
    return cleaned


def _generate_heuristic_plan(objective: str, max_queries: int = 4) -> ResearchPlan:
    """Fallback inquiry planner when LLM call is unavailable or unparseable."""
    base = objective.strip()
    paths = [
        InquiryPath(
            sub_question=f"What are the foundational definitions and core concepts of {base}?",
            search_query=f"{base} definition overview fundamentals",
            rationale="Establish baseline ground-truth and core taxonomy.",
            facet="foundational",
        ),
        InquiryPath(
            sub_question=f"What are the key mechanisms, practical workflows, and architecture of {base}?",
            search_query=f"{base} architecture implementation methodology",
            rationale="Investigate specific functional mechanisms and execution details.",
            facet="mechanism",
        ),
        InquiryPath(
            sub_question=f"What are the primary tradeoffs, limitations, and empirical findings regarding {base}?",
            search_query=f"{base} tradeoffs limitations performance analysis",
            rationale="Assess contrasting evidence, failure modes, and boundaries.",
            facet="tradeoff",
        ),
    ]
    if max_queries >= 4:
        paths.append(
            InquiryPath(
                sub_question=f"What are the emerging developments, future outlook, and synthesis for {base}?",
                search_query=f"{base} conclusions synthesis future roadmap",
                rationale="Synthesize broader strategic takeaways and trajectories.",
                facet="outcome",
            )
        )
    return ResearchPlan(
        objective=base,
        inquiry_paths=paths[:max_queries],
        strategy_summary=f"Multi-facet investigation exploring foundations, mechanisms, and tradeoffs for '{base}'.",
    )


async def plan_inquiry(state: DeepResearchState, config: RunnableConfig) -> dict[str, Any]:
    """Node 1: Analyze research objective and generate targeted inquiry paths."""
    objective = state.get("objective", "").strip()
    max_queries = state.get("max_queries", 4) or 4

    configurable = config.get("configurable", {}) if config else {}
    strategy_model_id = configurable.get("strategy_model")

    if not strategy_model_id:
        return {"plan": _generate_heuristic_plan(objective, max_queries)}

    prompt = (
        "You are an elite research strategist. Decompose the following research objective into "
        f"up to {max_queries} structured inquiry paths.\n\n"
        f"Objective: {objective}\n\n"
        "Return a JSON object with this exact structure:\n"
        "{\n"
        '  "strategy_summary": "High level strategy overview",\n'
        '  "inquiry_paths": [\n'
        '    {"sub_question": "...", "search_query": "...", "rationale": "...", "facet": "..."}\n'
        "  ]\n"
        "}\n"
        "Ensure queries are precise keywords suitable for document search."
    )

    timeout = _research_timeout_sec()
    try:
        model = await provision_langchain_model(
            prompt,
            strategy_model_id,
            "tools",
            max_tokens=1500,
            structured=dict(type="json"),
        )
        ai_msg = await asyncio.wait_for(model.ainvoke(prompt), timeout=timeout)
        parsed_json = json.loads(_clean_json_str(ai_msg.content))
        paths_raw = parsed_json.get("inquiry_paths", [])
        paths = [
            InquiryPath(
                sub_question=p.get("sub_question", ""),
                search_query=p.get("search_query", objective),
                rationale=p.get("rationale", ""),
                facet=p.get("facet", "core"),
            )
            for p in paths_raw
            if p.get("search_query")
        ][:max_queries]
        if not paths:
            return {"plan": _generate_heuristic_plan(objective, max_queries)}
        return {
            "plan": ResearchPlan(
                objective=objective,
                inquiry_paths=paths,
                strategy_summary=parsed_json.get("strategy_summary", ""),
            )
        }
    except Exception as exc:
        logger.warning(f"plan_inquiry model failed ({exc}); using heuristic inquiry plan")
        return {"plan": _generate_heuristic_plan(objective, max_queries)}


async def conduct_hybrid_retrieval(state: DeepResearchState, config: RunnableConfig) -> dict[str, Any]:
    """Node 2: Execute hybrid search (vector + text + RRF) and cross-encoder reranking."""
    plan: ResearchPlan | None = state.get("plan")
    if not plan or not plan.inquiry_paths:
        return {"evidence": []}

    gathered_evidence: dict[str, dict[str, Any]] = {}
    has_embed = bool(await model_manager.get_embedding_model())

    for path in plan.inquiry_paths:
        query = path.search_query.strip()
        if not query:
            continue

        vector_res: list[dict[str, Any]] = []
        if has_embed:
            try:
                vector_res = await vector_search(query, results=10, source=True, note=True)
            except Exception as e:
                logger.warning(f"Vector search failed for '{query}': {e}")

        text_res: list[dict[str, Any]] = []
        try:
            text_res = await text_search(query, results=10, source=True, note=True)
        except Exception as e:
            logger.warning(f"Text search failed for '{query}': {e}")

        # Reciprocal Rank Fusion
        fused = reciprocal_rank_fusion([vector_res, text_res], limit=10, k=60)

        # Cross-encoder rerank top candidates
        reranked = await rerank_results(query, fused)

        for item in reranked:
            item_id = str(item.get("id", ""))
            if not item_id:
                continue

            matches = item.get("matches", [])
            text_snippet = ""
            if isinstance(matches, list):
                text_snippet = "\n".join(str(m) for m in matches if m)
            elif isinstance(matches, str):
                text_snippet = matches

            # Truncate per-snippet to prevent context explosion
            if len(text_snippet) > 2000:
                text_snippet = text_snippet[:2000] + "..."

            score = float(item.get("rerank_score") or item.get("score") or item.get("similarity") or 0.0)

            # Deduplicate by item_id, retaining highest score
            if item_id not in gathered_evidence or score > gathered_evidence[item_id]["score"]:
                gathered_evidence[item_id] = {
                    "id": item_id,
                    "query": query,
                    "title": str(item.get("title") or item_id),
                    "text": text_snippet,
                    "score": score,
                    "parent_id": item.get("parent_id"),
                }

    # Sort gathered evidence by score descending
    sorted_evidence = sorted(gathered_evidence.values(), key=lambda x: x["score"], reverse=True)
    return {"evidence": sorted_evidence}


async def synthesize_research_brief(state: DeepResearchState, config: RunnableConfig) -> dict[str, Any]:
    """Node 3: Synthesize comprehensive research brief with citations."""
    objective = state.get("objective", "").strip()
    evidence = state.get("evidence", [])
    plan: ResearchPlan | None = state.get("plan")

    if not evidence:
        return {
            "research_brief": (
                f"No relevant evidence was found in the knowledge base for '{objective}'. "
                "Please consider importing relevant sources or refining the research objective."
            ),
            "citations": [],
            "agent_state": AgentState.CLARIFY.value,
        }

    # Format evidence dossier
    dossier_lines: list[str] = []
    citations: list[dict[str, Any]] = []
    for idx, item in enumerate(evidence[:15], start=1):
        ref_tag = f"[Ref {idx}]"
        citations.append({
            "ref": ref_tag,
            "id": item["id"],
            "title": item["title"],
            "score": item["score"],
        })
        dossier_lines.append(
            f"{ref_tag} Title: {item['title']} (Score: {item['score']:.3f})\n"
            f"Passage: {item['text']}\n"
        )
    dossier = "\n---\n".join(dossier_lines)

    inquiry_context = ""
    if plan and plan.inquiry_paths:
        inquiry_context = "\n".join(
            f"- {p.sub_question} (Facet: {p.facet})" for p in plan.inquiry_paths
        )

    prompt = (
        "You are a lead research analyst. Synthesize the provided grounded evidence into a "
        "comprehensive, executive-grade Research Brief.\n\n"
        f"RESEARCH OBJECTIVE: {objective}\n\n"
        f"INQUIRY PATHS INVESTIGATED:\n{inquiry_context}\n\n"
        f"EVIDENCE DOSSIER:\n{dossier}\n\n"
        "REQUIREMENTS:\n"
        "1. Structure your response in clean GitHub-Flavored Markdown:\n"
        f"   # Research Brief: {objective}\n"
        "   ## Executive Summary\n"
        "   ## Key Findings & Sub-Inquiry Insights\n"
        "   ## Tradeoffs, Nuances & Limitations\n"
        "   ## Strategic Takeaways & Recommendations\n"
        "   ## References\n"
        "2. Directly cite evidence using the [Ref N] tags provided in the dossier.\n"
        "3. Every claim must be grounded in the retrieved evidence.\n"
    )

    configurable = config.get("configurable", {}) if config else {}
    synthesis_model_id = configurable.get("synthesis_model")

    if not synthesis_model_id:
        # Generate clean structured brief directly from evidence
        brief = (
            f"# Research Brief: {objective}\n\n"
            f"## Executive Summary\n\n"
            f"An automated deep research investigation was conducted for '{objective}' across "
            f"{len(evidence)} evidence passages.\n\n"
            f"## Key Findings\n\n"
        )
        for c in citations[:5]:
            brief += f"- **{c['title']}** {c['ref']}: Score {c['score']:.3f}\n"
        brief += "\n## References\n\n"
        for c in citations:
            brief += f"- {c['ref']} {c['title']} (`{c['id']}`)\n"
        return {
            "research_brief": brief,
            "citations": citations,
            "agent_state": AgentState.COMPLETE.value,
        }

    timeout = _research_timeout_sec()
    try:
        model = await provision_langchain_model(
            prompt,
            synthesis_model_id,
            "transformation",
            max_tokens=4000,
        )
        ai_msg = await asyncio.wait_for(model.ainvoke(prompt), timeout=timeout)
        brief_text = clean_thinking_content(extract_text_content(ai_msg.content)).strip()
        return {
            "research_brief": brief_text,
            "citations": citations,
            "agent_state": AgentState.COMPLETE.value,
        }
    except Exception as exc:
        logger.warning(f"Synthesis model failed ({exc}); generating evidence summary brief")
        brief = (
            f"# Research Brief: {objective}\n\n"
            f"## Executive Summary\n\n"
            f"Synthesized evidence from {len(evidence)} retrieved passages for '{objective}'.\n\n"
            f"## Key Evidence Items\n\n"
        )
        for c in citations[:5]:
            brief += f"- **{c['title']}** {c['ref']} (Relevance: {c['score']:.3f})\n"
        brief += "\n## References\n\n"
        for c in citations:
            brief += f"- {c['ref']} {c['title']} (`{c['id']}`)\n"
        return {
            "research_brief": brief,
            "citations": citations,
            "agent_state": AgentState.COMPLETE.value,
        }


# Build LangGraph StateGraph
deep_research_graph_builder = StateGraph(DeepResearchState)
deep_research_graph_builder.add_node("plan_inquiry", plan_inquiry)
deep_research_graph_builder.add_node("conduct_hybrid_retrieval", conduct_hybrid_retrieval)
deep_research_graph_builder.add_node("synthesize_research_brief", synthesize_research_brief)

deep_research_graph_builder.add_edge(START, "plan_inquiry")
deep_research_graph_builder.add_edge("plan_inquiry", "conduct_hybrid_retrieval")
deep_research_graph_builder.add_edge("conduct_hybrid_retrieval", "synthesize_research_brief")
deep_research_graph_builder.add_edge("synthesize_research_brief", END)

deep_research_graph = deep_research_graph_builder.compile()


async def run_deep_research(
    objective: str,
    *,
    notebook_id: Optional[str] = None,
    max_queries: int = 4,
    strategy_model: Optional[str] = None,
    synthesis_model: Optional[str] = None,
) -> dict[str, Any]:
    """Convenience executor for Deep Research."""
    initial_state: DeepResearchState = {
        "objective": objective,
        "notebook_id": notebook_id,
        "max_queries": max_queries,
        "evidence": [],
        "citations": [],
    }
    config = {
        "configurable": {
            "strategy_model": strategy_model,
            "synthesis_model": synthesis_model,
        }
    }
    result = await deep_research_graph.ainvoke(initial_state, config=config)
    return result
