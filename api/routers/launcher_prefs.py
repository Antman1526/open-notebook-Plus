"""v0.8.6 Item D — GET/PUT /api/launcher-prefs.

Admin-only endpoints (protected by PasswordAuthMiddleware, same as all
other API endpoints) for reading and writing the launcher.env preference
file at ``~/.deeper-notebook/launcher.env``.

Endpoints
---------
GET  /api/launcher-prefs
    Returns ``{"prefs": {KEY: VALUE, ...}}``.  Empty dict when the file
    doesn't exist.

PUT  /api/launcher-prefs
    Accepts ``{"prefs": {KEY: VALUE | null, ...}}``.  Keys with null
    values are removed from the file.  Returns the new merged state.
    400 if a non-whitelisted key is present in the payload.

Note: changes are written immediately but only take effect after the
launcher is restarted, since env vars are read once at startup before
the API exists.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class PrefsResponse(BaseModel):
    prefs: dict[str, str]


class PrefsUpdate(BaseModel):
    # Values may be str (set) or None (remove the key).
    prefs: dict[str, str | None]


@router.get("/api/launcher-prefs", response_model=PrefsResponse)
async def get_launcher_prefs():
    """Return current launcher.env preference values.

    Returns an empty ``prefs`` dict when the file does not exist.
    """
    try:
        from desktop.launcher_prefs import get_prefs

        prefs = get_prefs()
        return {"prefs": prefs}
    except ValueError as exc:
        # Malformed line in the file — surface as 400 so the UI can
        # show a useful error rather than a 500 with a traceback.
        raise HTTPException(
            status_code=400,
            detail=f"launcher.env is malformed: {exc}",
        )
    except (PermissionError, OSError) as exc:
        # v0.8.14 — also catch fs errors (file owned by another user,
        # read-only DMG, etc.) so the UI gets an actionable 400 instead
        # of a 500. The launcher_prefs module itself never raises these
        # since it handles missing-file via path.exists(); but a chmod
        # or fs quirk could leave the file unreadable.
        raise HTTPException(
            status_code=400,
            detail=f"launcher.env could not be read: {exc}",
        )
    except (ImportError, ModuleNotFoundError):
        # v0.8.65g — `desktop.launcher_prefs` is a desktop-only module that
        # wasn't always bundled into the PyInstaller app (missing from the
        # spec hiddenimports), so this endpoint 500'd in the built app. Launcher
        # prefs are an optional UI nicety; degrade to empty prefs instead of a
        # 500. The spec now bundles the module, so this is belt-and-braces.
        return {"prefs": {}}


@router.put("/api/launcher-prefs", response_model=PrefsResponse)
async def update_launcher_prefs(body: PrefsUpdate):
    """Write updates to launcher.env and return the new merged state.

    Accepts ``{prefs: {KEY: VALUE | null}}``.  Null removes the key.
    Returns 400 for non-whitelisted keys or malformed existing file.
    """
    try:
        from desktop.launcher_prefs import update_prefs

        new_prefs = update_prefs(body.prefs)
        return {"prefs": new_prefs}
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
    except (PermissionError, OSError) as exc:
        # v0.8.14 — atomic write goes through `tmp.replace(path)`.
        # Can fail with PermissionError (target unwritable) or OSError
        # (different filesystem, no space, etc.). Surface as 400 with
        # the underlying message so the UI can show actionable info.
        raise HTTPException(
            status_code=400,
            detail=f"launcher.env could not be written: {exc}",
        )


class HardwareProfileResponse(BaseModel):
    system: str
    machine: str
    chip_name: str
    is_apple_silicon: bool
    total_ram_bytes: int
    total_ram_gb: float
    tier_name: str
    guidance: str
    recommended_context: int
    recommended_quant: str
    recommended_flash_attn: bool
    recommended_kv_quant: str


@router.get("/api/launcher-prefs/hardware-profile", response_model=HardwareProfileResponse)
async def get_hardware_profile_endpoint():
    """Profile host machine hardware and return unified-memory-aware recommendations."""
    try:
        from desktop.hardware_profiler import get_hardware_profile

        return get_hardware_profile()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to profile hardware: {exc}",
        )

