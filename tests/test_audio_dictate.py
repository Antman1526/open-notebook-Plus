"""Unit tests for /api/audio/dictate endpoint."""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


def test_dictate_audio_empty_file():
    response = client.post(
        "/api/audio/dictate",
        files={"file": ("empty.wav", b"", "audio/wav")},
    )
    assert response.status_code == 400
    assert "Empty" in response.json()["detail"]


def test_dictate_audio_successful_mock():
    class MockTranscript:
        text = "Hello Deeper Notebook dictation"

    mock_stt = AsyncMock()
    mock_stt.atranscribe = AsyncMock(return_value=MockTranscript())

    with patch("deeper_notebook.ai.models.model_manager.get_speech_to_text", AsyncMock(return_value=mock_stt)):
        response = client.post(
            "/api/audio/dictate",
            files={"file": ("recording.webm", b"RIFFfakeaudiobytes", "audio/webm")},
        )
        assert response.status_code == 200
        assert response.json()["text"] == "Hello Deeper Notebook dictation"


def test_dictate_audio_no_model_error():
    with patch("deeper_notebook.ai.models.model_manager.get_speech_to_text", AsyncMock(return_value=None)), \
         patch.dict("os.environ", {}, clear=True):
        response = client.post(
            "/api/audio/dictate",
            files={"file": ("recording.webm", b"RIFFfakeaudiobytes", "audio/webm")},
        )
        assert response.status_code == 400
        assert "No speech-to-text model configured" in response.json()["detail"]
