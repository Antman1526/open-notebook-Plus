"""Unit tests for Phase 5.4 Deep Research Agent Mode.

Tests the StateGraph nodes (inquiry planning, hybrid retrieval + reranking, and
brief synthesis) as well as the FastAPI /search/deep-research endpoint.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from deeper_notebook.graphs.agent_fsm import AgentState
from deeper_notebook.graphs.deep_research import (
    DeepResearchState,
    InquiryPath,
    ResearchPlan,
    conduct_hybrid_retrieval,
    deep_research_graph,
    plan_inquiry,
    run_deep_research,
    synthesize_research_brief,
)


@pytest.mark.asyncio
async def test_plan_inquiry_heuristic():
    """Verify heuristic planning creates balanced multi-facet inquiry paths."""
    state: DeepResearchState = {
        "objective": "Quantum Computing Error Mitigation in 2026",
        "max_queries": 3,
    }
    result = await plan_inquiry(state, config={})
    plan: ResearchPlan = result["plan"]

    assert isinstance(plan, ResearchPlan)
    assert plan.objective == "Quantum Computing Error Mitigation in 2026"
    assert len(plan.inquiry_paths) == 3
    assert any(p.facet == "foundational" for p in plan.inquiry_paths)
    assert any(p.facet == "mechanism" for p in plan.inquiry_paths)
    assert any(p.facet == "tradeoff" for p in plan.inquiry_paths)
    for p in plan.inquiry_paths:
        assert p.search_query.strip()
        assert p.sub_question.strip()


@pytest.mark.asyncio
async def test_plan_inquiry_with_model():
    """Verify plan_inquiry parses model output and returns structured ResearchPlan."""
    mock_model = AsyncMock()
    mock_msg = AsyncMock()
    mock_msg.content = json.dumps({
        "strategy_summary": "Investigate quantum algorithms and hardware noise",
        "inquiry_paths": [
            {
                "sub_question": "What is zero-noise extrapolation?",
                "search_query": "zero noise extrapolation quantum error",
                "rationale": "Key error mitigation technique",
                "facet": "mechanism",
            },
            {
                "sub_question": "What are hardware benchmarks on transmon qubits?",
                "search_query": "transmon qubit error mitigation benchmarks",
                "rationale": "Empirical performance data",
                "facet": "outcome",
            },
        ],
    })
    mock_model.ainvoke.return_value = mock_msg

    with patch("deeper_notebook.graphs.deep_research.provision_langchain_model", return_value=mock_model):
        state: DeepResearchState = {
            "objective": "Quantum Error Mitigation",
            "max_queries": 4,
        }
        config = {"configurable": {"strategy_model": "test-strategy-model"}}
        result = await plan_inquiry(state, config=config)

    plan = result["plan"]
    assert isinstance(plan, ResearchPlan)
    assert len(plan.inquiry_paths) == 2
    assert plan.inquiry_paths[0].sub_question == "What is zero-noise extrapolation?"
    assert plan.inquiry_paths[0].facet == "mechanism"


@pytest.mark.asyncio
async def test_conduct_hybrid_retrieval():
    """Verify hybrid search pools results and cross-encoder reranks top items."""
    plan = ResearchPlan(
        objective="Agentic Workflows",
        inquiry_paths=[
            InquiryPath(
                sub_question="How do autonomous agent loops work?",
                search_query="autonomous agent loops fsm state machine",
                rationale="Investigate agent architectures",
                facet="mechanism",
            )
        ],
    )
    state: DeepResearchState = {
        "objective": "Agentic Workflows",
        "plan": plan,
    }

    fake_text_results = [
        {"id": "source:1", "title": "FSM Architecture", "matches": ["Agent loops use state machines to prevent infinite drift."]},
        {"id": "note:2", "title": "Tool Call Patterns", "matches": ["Tools should be strictly isolated."]},
    ]
    fake_vector_results = [
        {"id": "source:1", "title": "FSM Architecture", "matches": ["Agent loops use state machines to prevent infinite drift."]},
        {"id": "source:3", "title": "Evaluation", "matches": ["Claim verification receipts."]},
    ]

    with patch("deeper_notebook.graphs.deep_research.model_manager.get_embedding_model", return_value=AsyncMock()), \
         patch("deeper_notebook.graphs.deep_research.vector_search", return_value=fake_vector_results), \
         patch("deeper_notebook.graphs.deep_research.text_search", return_value=fake_text_results), \
         patch("deeper_notebook.graphs.deep_research.rerank_results") as mock_rerank:

        # Let rerank_results return candidate with rerank_score attached
        async def fake_rerank(query, candidates):
            for idx, c in enumerate(candidates):
                c["rerank_score"] = 0.95 - (idx * 0.1)
            return candidates

        mock_rerank.side_effect = fake_rerank

        result = await conduct_hybrid_retrieval(state, config={})

    evidence = result["evidence"]
    assert len(evidence) >= 2
    # Ensure items are deduplicated and sorted by score descending
    ids = [e["id"] for e in evidence]
    assert len(ids) == len(set(ids))
    assert evidence[0]["score"] >= evidence[1]["score"]


@pytest.mark.asyncio
async def test_synthesize_research_brief_clarify_when_no_evidence():
    """Verify synthesize_research_brief sets CLARIFY when no evidence is found."""
    state: DeepResearchState = {
        "objective": "Unknown obscure topic with zero sources",
        "evidence": [],
    }
    result = await synthesize_research_brief(state, config={})
    assert result["agent_state"] == AgentState.CLARIFY.value
    assert "No relevant evidence was found" in result["research_brief"]
    assert result["citations"] == []


@pytest.mark.asyncio
async def test_synthesize_research_brief_with_evidence():
    """Verify research brief generation includes formatted citations and complete state."""
    state: DeepResearchState = {
        "objective": "Local LLM Inference Optimization",
        "evidence": [
            {
                "id": "source:101",
                "title": "Speculative Decoding on Apple Silicon",
                "text": "Draft models can accelerate generation by 2-3x on M-series chips.",
                "score": 0.92,
            },
            {
                "id": "source:102",
                "title": "KV Cache Quantization",
                "text": "FP8 and INT4 KV caches cut memory footprint drastically.",
                "score": 0.85,
            },
        ],
    }
    result = await synthesize_research_brief(state, config={})
    assert result["agent_state"] == AgentState.COMPLETE.value
    assert "Research Brief: Local LLM Inference Optimization" in result["research_brief"]
    assert len(result["citations"]) == 2
    assert result["citations"][0]["ref"] == "[Ref 1]"
    assert result["citations"][0]["id"] == "source:101"


@pytest.mark.asyncio
async def test_run_deep_research_pipeline_end_to_end():
    """Verify end-to-end deep research execution."""
    fake_text_results = [
        {"id": "source:1", "title": "Deep Research Paper", "matches": ["Deep research combines multi-query inquiry and reranking."]},
    ]

    with patch("deeper_notebook.graphs.deep_research.model_manager.get_embedding_model", return_value=None), \
         patch("deeper_notebook.graphs.deep_research.text_search", return_value=fake_text_results):
        result = await run_deep_research(
            objective="Future of AI Research Assistants",
            max_queries=2,
        )

    assert result["objective"] == "Future of AI Research Assistants"
    assert result["agent_state"] == AgentState.COMPLETE.value
    assert len(result["evidence"]) >= 1
    assert "Research Brief" in result["research_brief"]


def test_api_deep_research_endpoint():
    """Verify POST /search/deep-research returns HTTP 200 and schema conforming response."""
    client = TestClient(app)

    fake_text_results = [
        {"id": "source:200", "title": "Retrieval Augmented Generation", "matches": ["RAG grounds generative models in factual knowledge."]},
    ]

    with patch("deeper_notebook.graphs.deep_research.model_manager.get_embedding_model", return_value=None), \
         patch("deeper_notebook.graphs.deep_research.text_search", return_value=fake_text_results):
        response = client.post(
            "/api/search/deep-research",
            json={
                "objective": "Modern Retrieval Augmented Generation architectures",
                "max_queries": 2,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["objective"] == "Modern Retrieval Augmented Generation architectures"
    assert data["evidence_count"] >= 1
    assert "Research Brief" in data["research_brief"]
    assert len(data["citations"]) >= 1
    assert data["agent_state"] == "complete"
