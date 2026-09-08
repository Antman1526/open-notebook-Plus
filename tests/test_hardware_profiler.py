"""Tests for hardware profiler and unified memory advisor."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from desktop.hardware_profiler import get_hardware_profile
from desktop.launcher_prefs import ALLOWED_KEYS
from api.routers.launcher_prefs import router


def test_hardware_profiler_output_structure():
    """Verify hardware profile contains all expected keys and valid types."""
    profile = get_hardware_profile()
    assert isinstance(profile["system"], str)
    assert isinstance(profile["machine"], str)
    assert isinstance(profile["chip_name"], str)
    assert isinstance(profile["is_apple_silicon"], bool)
    assert isinstance(profile["total_ram_bytes"], int)
    assert isinstance(profile["total_ram_gb"], float)
    assert isinstance(profile["tier_name"], str)
    assert isinstance(profile["guidance"], str)
    assert isinstance(profile["recommended_context"], int)
    assert isinstance(profile["recommended_quant"], str)
    assert isinstance(profile["recommended_flash_attn"], bool)
    assert isinstance(profile["recommended_kv_quant"], str)


def test_launcher_prefs_allowed_keys_includes_hardware_opts():
    """Verify FlashAttention and KV Quantization keys are whitelisted."""
    assert "DEEPER_NOTEBOOK_LLAMACPP_FLASH_ATTN" in ALLOWED_KEYS
    assert "DEEPER_NOTEBOOK_LLAMACPP_KV_QUANT" in ALLOWED_KEYS


@pytest.mark.asyncio
async def test_hardware_profile_endpoint():
    """Verify GET /api/launcher-prefs/hardware-profile returns 200."""
    app = FastAPI()
    app.include_router(router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/launcher-prefs/hardware-profile")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_apple_silicon" in data
        assert "recommended_context" in data
        assert "recommended_quant" in data
        assert "tier_name" in data
