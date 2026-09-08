import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from deeper_notebook.graphs.source import transform_content


@pytest.mark.asyncio
async def test_transform_content_resilient_to_provider_failure():
    mock_source = MagicMock()
    mock_source.id = "source:test123"
    mock_source.full_text = "Detailed research text about quantum computing."
    mock_source.add_insight = AsyncMock()

    mock_transformation = MagicMock()
    mock_transformation.name = "summary"
    mock_transformation.title = "Summary"

    state = {
        "source": mock_source,
        "transformation": mock_transformation,
    }

    # Simulate provider / LLM exception during transform
    with patch("deeper_notebook.graphs.source.transform_graph.ainvoke", side_effect=RuntimeError("Provider offline / timeout")):
        result = await transform_content(state)

    # Result should be empty transformation list, no exception thrown
    assert result == {"transformation": []}
    mock_source.add_insight.assert_not_called()


@pytest.mark.asyncio
async def test_transform_content_success():
    mock_source = MagicMock()
    mock_source.id = "source:test123"
    mock_source.full_text = "Detailed research text about quantum computing."
    mock_source.add_insight = AsyncMock()

    mock_transformation = MagicMock()
    mock_transformation.name = "summary"
    mock_transformation.title = "Summary"

    state = {
        "source": mock_source,
        "transformation": mock_transformation,
    }

    with patch("deeper_notebook.graphs.source.transform_graph.ainvoke", return_value={"output": "Key findings summary"}):
        result = await transform_content(state)

    assert result == {
        "transformation": [
            {
                "output": "Key findings summary",
                "transformation_name": "summary",
            }
        ]
    }
    mock_source.add_insight.assert_called_once_with("Summary", "Key findings summary")
