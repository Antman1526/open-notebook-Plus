"""Speech-to-text audio dictation endpoint for Deeper Notebook note-taking.

Accepts audio recordings from the browser MediaRecorder and transcribes them
using the active local Whisper or configured SpeechToTextModel.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger

from deeper_notebook.ai.models import model_manager

router = APIRouter()


@router.post("/api/audio/dictate")
async def dictate_audio(file: UploadFile = File(...)) -> dict[str, str]:
    """Transcribe an uploaded audio clip to text for push-to-talk dictation."""
    suffix = Path(file.filename or "recording.webm").suffix or ".webm"
    tmp_path: Path | None = None

    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty audio recording")

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        # 1. Check if model_manager has a configured STT model (e.g. Esperanto / Whisper)
        try:
            stt_model = await model_manager.get_speech_to_text()
            if stt_model is not None:
                if hasattr(stt_model, "atranscribe"):
                    transcript = await stt_model.atranscribe(audio_file=tmp_path)
                    text = getattr(transcript, "text", str(transcript)).strip()
                    return {"text": text}
                elif hasattr(stt_model, "transcribe"):
                    transcript = stt_model.transcribe(audio_file=tmp_path)
                    text = getattr(transcript, "text", str(transcript)).strip()
                    return {"text": text}
        except Exception as exc:
            logger.warning(f"Configured STT model failed ({exc}); checking local whisper fallback")

        # 2. Check direct Whisper shim endpoint
        whisper_url = os.environ.get("DEEPER_NOTEBOOK_LOCAL_WHISPER_URL")
        if whisper_url:
            target = f"{whisper_url.rstrip('/')}/audio/transcriptions"
            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(tmp_path, "rb") as f:
                    resp = await client.post(
                        target,
                        files={"file": (tmp_path.name, f, "audio/webm")},
                        data={"model": "whisper-base-en"},
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    return {"text": data.get("text", "").strip()}

        raise HTTPException(
            status_code=400,
            detail="No speech-to-text model configured or reachable. Please configure a Speech-to-Text model in Settings.",
        )

    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
