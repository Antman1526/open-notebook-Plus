"""Tests for desktop.auto_register — model discovery and idempotent registration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from desktop.auto_register import (
    _list_local_ggufs,
    _list_ollama_models,
    auto_register,
)
from desktop.config import Config

# ---------------------------------------------------------------------------
# _list_ollama_models
# ---------------------------------------------------------------------------


def _make_ollama_response(model_names: list[str]) -> httpx.Response:
    """Build a fake Ollama /api/tags response."""
    import json

    body = json.dumps({"models": [{"name": n} for n in model_names]}).encode()
    return httpx.Response(
        200, content=body, headers={"content-type": "application/json"}
    )


def test_list_ollama_models_returns_names_when_reachable(monkeypatch):
    names = ["llama3.1:latest", "mistral:7b"]
    fake_response = _make_ollama_response(names)

    with patch("httpx.get", return_value=fake_response) as mock_get:
        result = _list_ollama_models()

    mock_get.assert_called_once_with("http://127.0.0.1:11434/api/tags", timeout=10.0)
    assert result == names


def test_list_ollama_models_returns_empty_when_unreachable():
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = _list_ollama_models()
    assert result == []


def test_list_ollama_models_returns_empty_on_non_200():
    fake_response = httpx.Response(503, content=b"service unavailable")
    with patch("httpx.get", return_value=fake_response):
        result = _list_ollama_models()
    assert result == []


# ---------------------------------------------------------------------------
# _list_local_ggufs
# ---------------------------------------------------------------------------


def test_list_local_ggufs_skips_small_files(tmp_path):
    big = tmp_path / "big.gguf"
    big.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB
    small = tmp_path / "tiny.gguf"
    small.write_bytes(b"x" * 100)  # 100 bytes — below 1 MB threshold

    result = _list_local_ggufs(tmp_path)
    assert result == ["big.gguf"]


def test_list_local_ggufs_returns_empty_for_missing_dir(tmp_path):
    result = _list_local_ggufs(tmp_path / "nonexistent")
    assert result == []


def test_list_local_ggufs_is_sorted(tmp_path):
    for name in ("zebra.gguf", "alpha.gguf", "middle.gguf"):
        (tmp_path / name).write_bytes(b"x" * (2 * 1024 * 1024))
    result = _list_local_ggufs(tmp_path)
    assert result == sorted(result)


# ---------------------------------------------------------------------------
# auto_register — idempotency test
# ---------------------------------------------------------------------------


def _make_cfg(tmp_path: Path) -> Config:
    return Config(
        model_dir=tmp_path / "models",
        provider="none",
        default_model="",
        surreal_user="root",
        surreal_password="A" * 24,
    )


def _mock_client_responses(
    credentials_list: list[dict],
    models_list: list[dict],
    post_credential_id: str = "credential:1",
) -> MagicMock:
    """Build a mock httpx.Client that returns predictable responses."""
    import json

    def make_resp(status: int, data) -> MagicMock:
        r = MagicMock(spec=httpx.Response)
        r.status_code = status
        r.json.return_value = data
        r.text = json.dumps(data)
        r.raise_for_status = MagicMock()
        return r

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    # On a fresh first run _ensure_credential gets the new cred's id directly
    # from the POST response, so there is no follow-up GET /credentials. The
    # next two GETs come from v0.5's capability-aware assignment:
    #   GET /api/models/defaults — read current to preserve manual overrides
    #   GET /api/models          — read all to score
    registered_model = {"id": "model:1", "name": "llama3.1:latest", "type": "language"}
    client.get.side_effect = [
        make_resp(200, credentials_list),  # GET /credentials (existence check)
        make_resp(200, models_list),  # GET /models      (existence check)
        make_resp(200, {}),  # GET /api/models/defaults (no manual overrides)
        make_resp(200, [registered_model]),  # GET /api/models  (scoring pool)
    ]

    # POST /credentials, POST /models (auto-assign endpoint no longer called)
    client.post.side_effect = [
        make_resp(
            201, {"id": post_credential_id, "name": "Ollama (local)"}
        ),  # POST /credentials
        make_resp(200, registered_model),  # POST /models
    ]
    # v0.5 — PUT /api/models/defaults replaces the old auto-assign POST
    client.put.side_effect = [
        make_resp(200, {}),
    ]
    return client


def test_auto_register_is_idempotent(tmp_path):
    """Running auto_register twice should only POST credentials/models once."""
    cfg = _make_cfg(tmp_path)
    ollama_names = ["llama3.1:latest"]

    # Simulate: first run creates everything; second run finds it all existing.
    with (
        patch("desktop.auto_register._list_ollama_models", return_value=ollama_names),
        patch("desktop.auto_register._list_local_ggufs", return_value=[]),
        # v0.8.65i — isolate the Osaurus path. This test predates the v0.8.36
        # Osaurus auto-register, which attempts its own credential POST (so the
        # count drifted 2 → 3) and was being masked by `build-mac-test | tail -3`.
        patch("desktop.auto_register.register_osaurus_models", return_value=False), patch("desktop.auto_register.register_lmstudio_models", return_value=False),
        patch("httpx.Client") as mock_client_cls,
    ):
        # First run: no existing creds/models
        client1 = _mock_client_responses([], [])
        mock_client_cls.return_value = client1

        auto_register("http://127.0.0.1:9999", cfg)

        # v0.5: POST /credentials + POST /models. The old POST /models/auto-assign
        # was replaced by PUT /api/models/defaults (asserted separately below).
        assert client1.post.call_count == 2
        assert client1.put.call_count == 1

        # Second run: credential and model already exist
        import json

        def make_resp2(status: int, data) -> MagicMock:
            r = MagicMock(spec=httpx.Response)
            r.status_code = status
            r.json.return_value = data
            r.text = json.dumps(data)
            r.raise_for_status = MagicMock()
            return r

        client2 = MagicMock()
        client2.__enter__ = MagicMock(return_value=client2)
        client2.__exit__ = MagicMock(return_value=False)
        # Both credential and model already exist — nothing to create.
        existing_cred = {"id": "credential:1", "name": "Ollama (local)"}
        existing_model = {
            "id": "model:1",
            "name": "llama3.1:latest",
            "type": "language",
        }
        client2.get.side_effect = [
            make_resp2(200, [existing_cred]),  # GET /credentials
            make_resp2(200, [existing_model]),  # GET /models
        ]
        mock_client_cls.return_value = client2

        auto_register("http://127.0.0.1:9999", cfg)

        # No POSTs / PUTs should happen on second run — nothing new registered,
        # so we don't even enter the assignment phase.
        assert client2.post.call_count == 0
        assert client2.put.call_count == 0


def test_auto_register_retries_models_fetch_then_registers(tmp_path, monkeypatch):
    """v0.8.65i — a TRANSIENT /api/models failure must not skip ALL local-model
    registration (which would leave the chat model selector empty). The fetch is
    retried; registration then proceeds normally."""
    import json

    monkeypatch.setattr("time.sleep", lambda *a, **k: None)  # don't actually wait
    cfg = _make_cfg(tmp_path)

    def make_resp(status: int, data) -> MagicMock:
        r = MagicMock(spec=httpx.Response)
        r.status_code = status
        r.json.return_value = data
        r.text = json.dumps(data)
        r.raise_for_status = MagicMock()
        return r

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    registered_model = {"id": "model:1", "name": "llama3.1:latest", "type": "language"}
    client.get.side_effect = [
        make_resp(200, []),  # GET /credentials (existence)
        httpx.ConnectError("transient"),  # GET /api/models attempt 1 → fails
        make_resp(200, []),  # GET /api/models attempt 2 → ok (empty)
        make_resp(200, {}),  # GET /api/models/defaults
        make_resp(200, [registered_model]),  # GET /api/models (scoring pool)
    ]
    client.post.side_effect = [
        make_resp(
            201, {"id": "credential:1", "name": "Ollama (local)"}
        ),  # POST /credentials
        make_resp(200, registered_model),  # POST /models
    ]
    client.put.side_effect = [make_resp(200, {})]

    with (
        patch(
            "desktop.auto_register._list_ollama_models",
            return_value=["llama3.1:latest"],
        ),
        patch("desktop.auto_register._list_local_ggufs", return_value=[]),
        patch("desktop.auto_register.register_osaurus_models", return_value=False), patch("desktop.auto_register.register_lmstudio_models", return_value=False),
        patch("httpx.Client", return_value=client),
    ):
        auto_register("http://127.0.0.1:9999", cfg)

    # Despite the transient /api/models failure, registration PROCEEDED (it would
    # be zero POSTs if auto-register had bailed). The Ollama credential + model
    # were POSTed — robust to the exact provider count.
    post_targets = [c.args[0] for c in client.post.call_args_list if c.args]
    assert any("/credentials" in p for p in post_targets), (
        "auto-register bailed on a transient /api/models error instead of retrying"
    )
    assert any("/models" in p for p in post_targets)


def test_register_voice_models_creates_credentials_and_models(monkeypatch):
    from pathlib import Path

    from desktop.auto_register import register_voice_models
    from desktop.config import Config

    created = []

    class FakeClient:
        def post(self, path, json=None):
            created.append((path, json))

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {"id": f"id-{json.get('name', '')}" if json else "id"}

            return R()

        def get(self, path):
            class R:
                status_code = 200
                text = ""

                def raise_for_status(self):
                    pass

                def json(self):
                    return []

            return R()

    cfg = Config(
        model_dir=Path("/tmp"),
        provider="none",
        default_model="",
        surreal_user="root",
        surreal_password="x" * 24,
    )
    register_voice_models(
        FakeClient(), whisper_port=1234, piper_port=2345, embed_port=3456, cfg=cfg
    )
    paths = [p for p, _ in created]
    assert "/api/credentials" in paths
    payloads = [j for _, j in created if j is not None]
    assert any(j.get("name") == "Whisper (local)" for j in payloads)
    assert any(j.get("name") == "Piper (local)" for j in payloads)
    assert any(j.get("name") == "Local Embeddings (llama.cpp)" for j in payloads)
    assert any(j.get("name") == "piper-amy-en" for j in payloads)
    assert any(j.get("name") == "piper-ryan-en" for j in payloads)


def test_register_voice_models_is_idempotent_when_creds_already_exist():
    """v0.6.21 regression: pre-fix, register_voice_models always passed
    existing_names=set() to _ensure_credential, so the existence check
    was a no-op and every relaunch POSTed a duplicate creation request.

    With the fix, the caller passes its already-fetched set of credential
    names; voice.py uses it. When all names are already in the set,
    NO /api/credentials POST should happen at all.
    """
    from pathlib import Path

    from desktop.auto_register import register_voice_models
    from desktop.config import Config

    posted: list[tuple[str, dict]] = []
    gotten: list[str] = []

    class FakeClient:
        def post(self, path, json=None):
            posted.append((path, json))

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {"id": "id-x"}

            return R()

        def get(self, path):
            gotten.append(path)

            class R:
                status_code = 200
                text = ""

                def raise_for_status(self):
                    pass

                def json(self):
                    # Simulate existing creds (so _ensure_credential's
                    # "name already exists" GET path returns the real ID)
                    return [
                        {"name": "Whisper (local)", "id": "cred:1"},
                        {"name": "Piper (local)", "id": "cred:2"},
                        {"name": "Local Embeddings (llama.cpp)", "id": "cred:3"},
                    ]

            return R()

    cfg = Config(
        model_dir=Path("/tmp"),
        provider="none",
        default_model="",
        surreal_user="root",
        surreal_password="x" * 24,
    )
    existing_cred_names = {
        "whisper (local)",
        "piper (local)",
        "local embeddings (llama.cpp)",
    }
    existing_model_keys = {
        ("whisper-base-en", "speech_to_text"),
        ("piper-amy-en", "text_to_speech"),
        ("piper-ryan-en", "text_to_speech"),
        ("nomic-embed-text-v1.5", "embedding"),
    }
    register_voice_models(
        FakeClient(),
        whisper_port=1234,
        piper_port=2345,
        embed_port=3456,
        cfg=cfg,
        existing_cred_names=existing_cred_names,
        existing_model_keys=existing_model_keys,
    )

    # The crucial assertion: NO POST happened — neither for credentials
    # nor models. The pre-fix code would have POSTed every credential
    # and every model, regardless of the existing sets.
    assert not posted, (
        f"Expected zero POST calls when all entities already exist, got: {posted}"
    )


def test_register_memory_credential_is_idempotent_when_cred_exists():
    """v0.6.22 regression: pre-fix memory.py passed existing_names=set()
    and therefore POSTed a duplicate 'Memory (local)' on every relaunch."""
    from desktop.auto_register.memory import register_memory_credential

    posted: list[tuple[str, dict]] = []

    class FakeClient:
        def post(self, path, json=None):
            posted.append((path, json))

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {"id": "id-x"}

            return R()

        def get(self, path):
            class R:
                status_code = 200
                text = ""

                def raise_for_status(self):
                    pass

                def json(self):
                    return [{"name": "Memory (local)", "id": "cred:42"}]

            return R()

    existing = {"memory (local)"}
    register_memory_credential(
        FakeClient(),
        memory_port=8767,
        cfg=None,
        existing_cred_names=existing,
    )
    # No POST: credential already exists, the existing-set tells us so.
    assert not posted, f"expected zero POSTs when cred exists, got {posted}"


def test_episode_profile_picks_qwen_chat_model_over_voice_models():
    """v0.6.22 + v0.7.149 regression: each preset POST must reference the
    chat model via `outline_llm`/`transcript_llm` (v0.7.149 schema) and
    pair with the speaker profile named in its preset definition.
    """
    from desktop.auto_register.episode_profile import register_default_episode_profile

    posted_episode_profiles: list[dict] = []

    class FakeClient:
        def get(self, path):
            class R:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    if path == "/api/episode-profiles":
                        return []  # no existing profile
                    if path == "/api/speaker-profiles":
                        # v0.7.149 — all 4 local speaker profiles present
                        return [
                            {"name": "Local Duo"},
                            {"name": "Local Solo"},
                            {"name": "Local Debate"},
                            {"name": "Local Interview"},
                        ]
                    if path == "/api/models":
                        # Order matters — the function picks the first non-voice.
                        return [
                            {"name": "Qwen3.6-35B-A3B-Q4_K_M", "id": "model:qwen"},
                            {"name": "piper-amy-en", "id": "model:amy"},
                            {"name": "piper-ryan-en", "id": "model:ryan"},
                            {"name": "nomic-embed-text-v1.5", "id": "model:nomic"},
                        ]
                    return []

            return R()

        def post(self, path, json=None):
            if path == "/api/episode-profiles":
                posted_episode_profiles.append(json)

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {}

            return R()

    register_default_episode_profile(FakeClient())
    # v0.7.30 — preset library expanded from 1 → 9 presets. Each gets
    # POSTed when no existing profiles match. All must reference the
    # chat model via outline_llm/transcript_llm + a valid speaker profile.
    from desktop.auto_register.episode_profile import _PRESETS

    assert len(posted_episode_profiles) == len(_PRESETS), (
        f"expected {len(_PRESETS)} preset POSTs, got {len(posted_episode_profiles)}"
    )
    for profile in posted_episode_profiles:
        # v0.7.149 — chat model routes through outline_llm + transcript_llm.
        # The schema does NOT accept chat_model_id any more.
        assert profile.get("outline_llm") == "model:qwen", (
            f"outline_llm should be Qwen, got {profile.get('outline_llm')!r}"
        )
        assert profile.get("transcript_llm") == "model:qwen", (
            f"transcript_llm should be Qwen, got {profile.get('transcript_llm')!r}"
        )
        # v0.7.149 — speaker_config is REQUIRED by the backend schema.
        # The previous `speakers: [...]` field is gone.
        assert "speakers" not in profile, (
            "v0.7.149 dropped the 'speakers' field — schema doesn't accept it"
        )
        assert "chat_model_id" not in profile, (
            "v0.7.149 dropped 'chat_model_id' — schema doesn't accept it"
        )
        assert "default_length_minutes" not in profile, (
            "default_length_minutes is not in the backend schema"
        )
        assert profile["speaker_config"] in {
            "Local Duo",
            "Local Solo",
            "Local Debate",
            "Local Interview",
        }, f"speaker_config {profile['speaker_config']!r} not in expected set"
        # Each preset must carry its purpose-built briefing + segments.
        assert profile["default_briefing"], (
            f"preset {profile['name']!r} has no briefing"
        )
        assert 3 <= profile["num_segments"] <= 20, (
            f"preset {profile['name']!r} has out-of-range num_segments"
        )
    # Names are unique
    names = [p["name"] for p in posted_episode_profiles]
    assert len(set(names)) == len(names), "duplicate preset names"
    # New installs receive the canonical default preset name.
    assert "Deeper Notebook Local" in names
    # v0.7.149 — Debate preset MUST pair with Local Debate (semantic match)
    debate = next(p for p in posted_episode_profiles if p["name"] == "Debate")
    assert debate["speaker_config"] == "Local Debate", (
        "Debate preset should use the Local Debate speaker profile"
    )
    qa = next(p for p in posted_episode_profiles if p["name"] == "Q&A Interview")
    assert qa["speaker_config"] == "Local Interview", (
        "Q&A Interview preset should use the Local Interview speaker profile"
    )


def test_episode_profile_skips_bge_embedding_in_chat_pick():
    """The old fallback used only prefix matching (piper-, whisper-, nomic-).
    A user with bge-large-en-v1.5 in their model dir (no matching prefix)
    would get it picked as chat model — wrong. The fix also runs the
    embedding heuristic.
    v0.7.149 — verifies the chat model is now plumbed through outline_llm
    (the actual schema field) instead of the dropped chat_model_id."""
    from desktop.auto_register.episode_profile import register_default_episode_profile

    posted: list[dict] = []

    class FakeClient:
        def get(self, path):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    if path == "/api/episode-profiles":
                        return []
                    if path == "/api/speaker-profiles":
                        return [{"name": "Local Duo"}]
                    if path == "/api/models":
                        return [
                            # bge-large-en-v1.5 — NOT matching any old prefix
                            # but IS an embedding model. Old code would have
                            # picked it as chat. New code correctly skips it.
                            {"name": "bge-large-en-v1.5", "id": "model:bge"},
                            {"name": "Qwen-7B-chat", "id": "model:qwen7"},
                            {"name": "piper-amy-en", "id": "model:amy"},
                            {"name": "piper-ryan-en", "id": "model:ryan"},
                        ]
                    return []

            return R()

        def post(self, path, json=None):
            if path == "/api/episode-profiles":
                posted.append(json)

            class R:
                status_code = 201

                def json(self):
                    return {}

            return R()

    register_default_episode_profile(FakeClient())
    assert posted[0]["outline_llm"] == "model:qwen7", (
        "should skip bge-* embedding and pick the real chat model"
    )
    assert posted[0]["transcript_llm"] == "model:qwen7"


def test_speaker_profile_registers_local_piper_library():
    """v0.7.32 — auto-register seeds 4 local-Piper speaker profiles
    so the GeneratePodcastDialog has working defaults instead of the
    cloud-only presets from migration 7.surrealql."""
    from desktop.auto_register.speaker_profile import (
        _build_presets,
        register_default_speaker_profile,
    )

    posted: list[dict] = []

    class FakeClient:
        def get(self, path):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    if path == "/api/speaker-profiles":
                        return []  # nothing exists yet
                    if path == "/api/models":
                        return [
                            {"name": "piper-amy-en", "id": "model:amy"},
                            {"name": "piper-ryan-en", "id": "model:ryan"},
                            {"name": "Qwen-7B-chat", "id": "model:q"},
                        ]
                    return []

            return R()

        def post(self, path, json=None):
            if path == "/api/speaker-profiles":
                posted.append(json)

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {}

            return R()

    register_default_speaker_profile(FakeClient())

    # Library matches the source-of-truth presets
    expected = _build_presets("model:amy", "model:ryan")
    assert len(posted) == len(expected)
    names = {p["name"] for p in posted}
    assert names == {"Local Duo", "Local Solo", "Local Debate", "Local Interview"}

    # All presets use the piper model IDs (profile-level + per-speaker)
    for profile in posted:
        assert profile["voice_model"] in {"model:amy", "model:ryan"}
        for speaker in profile["speakers"]:
            assert speaker["voice_model"] in {"model:amy", "model:ryan"}
            assert speaker["name"]
            assert speaker["backstory"]
            assert speaker["personality"]


def test_speaker_profile_registration_is_idempotent():
    """Re-running with some presets already present only creates the
    missing ones."""
    from desktop.auto_register.speaker_profile import (
        register_default_speaker_profile,
    )

    posted: list[dict] = []
    existing = {"Local Duo", "Local Solo"}

    class FakeClient:
        def get(self, path):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    if path == "/api/speaker-profiles":
                        return [{"name": n} for n in existing]
                    if path == "/api/models":
                        return [
                            {"name": "piper-amy-en", "id": "model:amy"},
                            {"name": "piper-ryan-en", "id": "model:ryan"},
                        ]
                    return []

            return R()

        def post(self, path, json=None):
            if path == "/api/speaker-profiles":
                posted.append(json)

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {}

            return R()

    register_default_speaker_profile(FakeClient())
    # Only the 2 missing ones
    posted_names = {p["name"] for p in posted}
    assert posted_names == {"Local Debate", "Local Interview"}
    # Pre-existing ones are NOT re-posted
    assert not (posted_names & existing)


def test_speaker_profile_skips_when_piper_voices_missing():
    """If Piper isn't registered (yet / at all), do nothing — don't
    create broken profiles."""
    from desktop.auto_register.speaker_profile import (
        register_default_speaker_profile,
    )

    posted: list[dict] = []

    class FakeClient:
        def get(self, path):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    if path == "/api/speaker-profiles":
                        return []
                    if path == "/api/models":
                        # NO piper voices — Piper disabled, or first-run
                        # mid-flight.
                        return [{"name": "Qwen-7B-chat", "id": "model:q"}]
                    return []

            return R()

        def post(self, path, json=None):
            posted.append(json)

            class R:
                status_code = 201

                def json(self):
                    return {}

            return R()

    register_default_speaker_profile(FakeClient())
    assert posted == [], "should not create speaker profiles without piper voices"


def test_episode_profile_library_is_idempotent():
    """v0.7.30 — re-running registration when SOME presets already exist
    only creates the missing ones. Customised existing profiles are
    never overwritten (we POST, not PUT)."""
    from desktop.auto_register.episode_profile import (
        _PRESETS,
        register_default_episode_profile,
    )

    posted: list[dict] = []
    # Simulate a partial install: the user already has "Deep Dive" and
    # "Quick Brief" (perhaps from a prior run, or hand-edited copies).
    existing_names = {"Deep Dive", "Quick Brief"}

    class FakeClient:
        def get(self, path):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    if path == "/api/episode-profiles":
                        return [{"name": n} for n in existing_names]
                    if path == "/api/speaker-profiles":
                        return [
                            {"name": "Local Duo"},
                            {"name": "Local Debate"},
                            {"name": "Local Interview"},
                            {"name": "Local Solo"},
                        ]
                    if path == "/api/models":
                        return [
                            {"name": "Qwen-7B-chat", "id": "model:q"},
                            {"name": "piper-amy-en", "id": "model:amy"},
                            {"name": "piper-ryan-en", "id": "model:ryan"},
                        ]
                    return []

            return R()

        def post(self, path, json=None):
            if path == "/api/episode-profiles":
                posted.append(json)

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {}

            return R()

    register_default_episode_profile(FakeClient())
    expected_new = len(_PRESETS) - len(existing_names)
    assert len(posted) == expected_new, (
        f"expected {expected_new} new POSTs (skipping existing), got {len(posted)}"
    )
    posted_names = {p["name"] for p in posted}
    # The skipped presets are NOT in the POSTed set
    assert not (existing_names & posted_names), (
        f"presets that existed got re-posted: {existing_names & posted_names}"
    )


def test_episode_profile_preserves_legacy_default_without_duplicate():
    """An upgraded profile keeps its persisted legacy name without creating
    a second canonical copy during idempotent auto-registration."""
    from desktop.auto_register.episode_profile import (
        register_default_episode_profile,
    )

    posted: list[dict] = []

    class FakeClient:
        def get(self, path):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    if path == "/api/episode-profiles":
                        return [{"name": "Open Notebook Plus Local"}]
                    if path == "/api/speaker-profiles":
                        return [
                            {"name": "Local Duo"},
                            {"name": "Local Debate"},
                            {"name": "Local Interview"},
                            {"name": "Local Solo"},
                        ]
                    if path == "/api/models":
                        return [{"name": "Qwen-7B-chat", "id": "model:q"}]
                    return []

            return R()

        def post(self, path, json=None):
            if path == "/api/episode-profiles":
                posted.append(json)

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {}

            return R()

    register_default_episode_profile(FakeClient())

    assert all(profile["name"] != "Deeper Notebook Local" for profile in posted)


def test_ensure_credential_does_not_post_when_existing_set_lies():
    """v0.6.30 regression: if pre-fetched existing_names claims a credential
    exists but the /api/credentials response doesn't actually contain it
    (e.g. case mismatch, race with delete, server response variance), the
    old code FELL THROUGH to a POST — creating a duplicate. The fix returns
    None so the caller logs + skips, never creating an unintended duplicate."""
    from desktop.auto_register._http import _ensure_credential

    posted = []

    class FakeClient:
        def get(self, path):
            class R:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return [{"name": "Other Cred", "id": "cred:other"}]

            return R()

        def post(self, path, json=None):
            posted.append((path, json))

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {"id": "id-new"}

            return R()

    existing = {"mismatch cred"}  # claims to exist
    result = _ensure_credential(
        client=FakeClient(),
        existing_names=existing,
        name="Mismatch Cred",
        provider="ollama",
        modalities=["language"],
    )
    # Returns None, NOT the new ID — and crucially never POSTed.
    assert result is None
    assert not posted, f"expected zero POST calls, got: {posted}"


def test_ensure_credential_returns_existing_id_on_match():
    """Happy path control test — when GET response DOES contain the
    credential, return its ID without POSTing."""
    from desktop.auto_register._http import _ensure_credential

    posted = []

    class FakeClient:
        def get(self, path):
            class R:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return [{"name": "Whisper (local)", "id": "cred:whisper-1"}]

            return R()

        def post(self, path, json=None):
            posted.append((path, json))

            class R:
                status_code = 201

                def json(self):
                    return {"id": "would-be-duplicate"}

            return R()

    result = _ensure_credential(
        client=FakeClient(),
        existing_names={"whisper (local)"},
        name="Whisper (local)",
        provider="openai_compatible",
        modalities=["speech_to_text"],
    )
    assert result == "cred:whisper-1"
    assert not posted


def test_episode_profile_skips_when_no_speaker_profiles_exist():
    """v0.7.149 regression.

    `speaker_config` is a REQUIRED field in the backend schema. If the
    speaker bootstrap skipped (e.g. piper voices weren't registered),
    we'd have nothing to put in that field and every POST would 422.
    The fix: detect zero speaker profiles → skip the entire episode
    library bootstrap silently. Better to have no presets than nine
    failed POSTs in the launcher log every launch.
    """
    from desktop.auto_register.episode_profile import register_default_episode_profile

    posted: list[dict] = []

    class FakeClient:
        def get(self, path):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    if path == "/api/episode-profiles":
                        return []
                    if path == "/api/speaker-profiles":
                        return []  # ← no speaker profiles → skip bootstrap
                    if path == "/api/models":
                        return [{"name": "Qwen-7B-chat", "id": "model:q"}]
                    return []

            return R()

        def post(self, path, json=None):
            posted.append({"path": path, "json": json})

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {}

            return R()

    register_default_episode_profile(FakeClient())
    assert posted == [], (
        "must NOT POST any episode profiles when no speaker_config target exists"
    )


def test_episode_profile_skips_when_only_migration_seeded_speakers_exist():
    """v0.7.156 regression.

    Migration 7.surrealql seeds three speaker profiles (`tech_experts`,
    `solo_expert`, `business_panel`) all bound to OpenAI's
    `gpt-4o-mini-tts`. On a fresh install where the Local-* speaker
    bootstrap hasn't run yet (Piper voices not registered), v0.7.149's
    last-resort fallback used `sorted(existing_speakers)[0]` and
    silently picked `business_panel`. The resulting episode preset
    would 500 at podcast-generation TTS time because no OpenAI
    credential exists.

    Fix: filter migration-seeded openai-only speakers out of the
    fallback candidate pool. With no LOCAL-* speakers available, we
    skip the preset and log so the user can re-run after registering
    Piper. Better to ship zero presets than nine that all crash on
    use.
    """
    from desktop.auto_register.episode_profile import register_default_episode_profile

    posted: list[dict] = []

    class FakeClient:
        def get(self, path):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    if path == "/api/episode-profiles":
                        return []
                    if path == "/api/speaker-profiles":
                        # Only migration-seeded openai-only speakers exist.
                        return [
                            {"name": "tech_experts"},
                            {"name": "solo_expert"},
                            {"name": "business_panel"},
                        ]
                    if path == "/api/models":
                        return [{"name": "Qwen", "id": "model:q"}]
                    return []

            return R()

        def post(self, path, json=None):
            if path == "/api/episode-profiles":
                posted.append(json)

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {}

            return R()

    register_default_episode_profile(FakeClient())
    # v0.7.156 — must NOT bind any preset to business_panel / solo_expert /
    # tech_experts. They all require an OpenAI credential the user doesn't
    # have on a fresh install.
    assert posted == [], (
        "Expected zero posts when only migration-seeded (OpenAI-only) "
        "speakers exist. v0.7.156 fix: filter them out of fallback pool."
    )


def test_episode_profile_falls_back_to_local_duo_when_preferred_missing():
    """v0.7.149 regression.

    If the preset's preferred speaker_profile (e.g. 'Local Debate' for
    the Debate preset) is missing but 'Local Duo' exists, we degrade
    the preset to use 'Local Duo' rather than skip it. Goal: ship a
    usable preset library even when only the most-common speaker
    profile is registered. The launcher.log records the degradation
    count for observability.
    """
    from desktop.auto_register.episode_profile import (
        _PRESETS,
        register_default_episode_profile,
    )

    posted: list[dict] = []

    class FakeClient:
        def get(self, path):
            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    if path == "/api/episode-profiles":
                        return []
                    if path == "/api/speaker-profiles":
                        # Only Local Duo — Debate + Interview prefs miss.
                        return [{"name": "Local Duo"}]
                    if path == "/api/models":
                        return [
                            {"name": "Qwen", "id": "model:q"},
                            {"name": "piper-amy-en", "id": "model:amy"},
                            {"name": "piper-ryan-en", "id": "model:ryan"},
                        ]
                    return []

            return R()

        def post(self, path, json=None):
            if path == "/api/episode-profiles":
                posted.append(json)

            class R:
                status_code = 201
                text = ""

                def json(self):
                    return {}

            return R()

    register_default_episode_profile(FakeClient())
    # All 9 presets registered, all using Local Duo.
    assert len(posted) == len(_PRESETS), (
        f"expected all {len(_PRESETS)} presets registered with fallback, got {len(posted)}"
    )
    for profile in posted:
        assert profile["speaker_config"] == "Local Duo", (
            f"preset {profile['name']!r} did not fall back to Local Duo: "
            f"speaker_config={profile['speaker_config']!r}"
        )
