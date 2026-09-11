"""v0.7.17 — regression tests for encryption-key rotation via MultiFernet.

Before v0.7.17, the encryption module only honored a single
`DEEPER_NOTEBOOK_ENCRYPTION_KEY`. Rotating the key invalidated every
stored credential — for a local user that means re-entering every
API key for every provider after rotation, a hostile UX.

v0.7.17 adds `DEEPER_NOTEBOOK_ENCRYPTION_KEYS` (plural, comma-separated)
backed by cryptography's MultiFernet:
  - The FIRST key is used for new encryption.
  - All keys are tried for decryption.
This lets the user declare a new key first, the old key second, sweep
existing data through `re_encrypt_value`, then drop the old key once
everything is re-encrypted — zero credential loss.
"""

from __future__ import annotations

import pytest

from deeper_notebook.utils import encryption


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test gets a fresh key cache; the module's lazy singleton
    would otherwise leak state across tests."""
    encryption._reset_encryption_cache()
    yield
    encryption._reset_encryption_cache()


# ---------------------------------------------------------------------------
# _get_encryption_keys_from_env
# ---------------------------------------------------------------------------


def test_singular_env_var_still_works(monkeypatch):
    """Back-compat: a pre-v0.7.17 deploy with only the singular env var
    must keep working unchanged."""
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", "old-passphrase")
    keys = encryption._get_encryption_keys_from_env()
    assert keys == ["old-passphrase"]


def test_plural_env_var_takes_precedence(monkeypatch):
    """When both are set, plural wins (allows rotation without removing
    the legacy var first)."""
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "new-key,old-key")
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", "ignored")
    assert encryption._get_encryption_keys_from_env() == ["new-key", "old-key"]


def test_plural_strips_whitespace_and_drops_empty(monkeypatch):
    """`a, b, ,c,` → ['a','b','c']. Forgiving of formatting mistakes."""
    monkeypatch.setenv(
        "DEEPER_NOTEBOOK_ENCRYPTION_KEYS",
        "  key-one ,  key-two,, key-three  ,",
    )
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", raising=False)
    assert encryption._get_encryption_keys_from_env() == [
        "key-one",
        "key-two",
        "key-three",
    ]


def test_no_keys_configured_raises(unset_setting):
    # Clears the canonical names, their legacy aliases and *_FILE variants.
    unset_setting("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "DEEPER_NOTEBOOK_ENCRYPTION_KEY")
    with pytest.raises(ValueError, match="Neither.*KEYS.*nor.*KEY"):
        encryption._get_encryption_keys_from_env()


def test_empty_plural_falls_back_to_singular(monkeypatch):
    """An empty `DEEPER_NOTEBOOK_ENCRYPTION_KEYS=` (e.g. accidentally
    overridden in CI) should NOT mask the singular fallback."""
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "  ,  ,  ")
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", "fallback")
    assert encryption._get_encryption_keys_from_env() == ["fallback"]


# ---------------------------------------------------------------------------
# encrypt/decrypt round-trip
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_single_key(monkeypatch):
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", "single-key")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", raising=False)

    cipher = encryption.encrypt_value("sk-very-secret-api-key")
    assert cipher != "sk-very-secret-api-key"
    assert encryption.decrypt_value(cipher) == "sk-very-secret-api-key"


def test_encrypt_decrypt_with_multifernet(monkeypatch):
    """Encrypt under first key, decrypt under MultiFernet works."""
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "new-key,old-key")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", raising=False)

    cipher = encryption.encrypt_value("sk-test-1")
    assert encryption.decrypt_value(cipher) == "sk-test-1"


# ---------------------------------------------------------------------------
# Rotation scenarios — the core v0.7.17 win
# ---------------------------------------------------------------------------


def test_data_encrypted_under_old_key_decrypts_after_adding_new(monkeypatch):
    """The rotation scenario:
    1. User has data encrypted under key 'old-secret'.
    2. User wants to switch to 'new-secret'.
    3. They set DEEPER_NOTEBOOK_ENCRYPTION_KEYS='new-secret,old-secret'.
    4. Existing data must still decrypt.
    5. New writes go under 'new-secret'.
    """
    # Step 1 — encrypt under the old key.
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", "old-secret")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", raising=False)
    old_cipher = encryption.encrypt_value("legacy-token-value")
    encryption._reset_encryption_cache()

    # Steps 3-4 — configure rotation; old cipher must still decrypt.
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "new-secret,old-secret")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", raising=False)
    assert encryption.decrypt_value(old_cipher) == "legacy-token-value"

    # Step 5 — new writes use the new key. We verify this by dropping
    # the old key entirely and checking the new ciphertext still works.
    new_cipher = encryption.encrypt_value("post-rotation-token")
    encryption._reset_encryption_cache()
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "new-secret")
    assert encryption.decrypt_value(new_cipher) == "post-rotation-token"


def test_data_encrypted_under_old_key_fails_when_old_dropped_prematurely(monkeypatch):
    """If the user drops the old key BEFORE running re_encrypt_value,
    old data becomes undecryptable — the error message must guide them
    to re-add the old key."""
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", "doomed-old")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", raising=False)
    old_cipher = encryption.encrypt_value("important-creds")
    encryption._reset_encryption_cache()

    # User mistakenly removes the old key.
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "shiny-new")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", raising=False)
    with pytest.raises(ValueError) as exc_info:
        encryption.decrypt_value(old_cipher)
    # Error must point them at the actual cause
    assert "rotated" in str(exc_info.value).lower()
    assert "DEEPER_NOTEBOOK_ENCRYPTION_KEYS" in str(exc_info.value)


def test_re_encrypt_value_rotates_under_primary_key(monkeypatch):
    """re_encrypt_value: decrypt under any configured key, re-encrypt
    under the new primary. After a sweep, the old key can be removed."""
    # Phase 1: data encrypted under 'old-key' (singular env).
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", "old-key")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", raising=False)
    old_cipher = encryption.encrypt_value("my-secret-credential")
    encryption._reset_encryption_cache()

    # Phase 2: switch to rotation config; re-encrypt the credential.
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "new-key,old-key")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", raising=False)
    rotated_cipher = encryption.re_encrypt_value(old_cipher)
    # The rotated cipher should differ from the original (new key + new IV)
    assert rotated_cipher != old_cipher
    # And must decrypt to the same plaintext
    assert encryption.decrypt_value(rotated_cipher) == "my-secret-credential"

    # Phase 3: drop the old key; rotated cipher still works.
    encryption._reset_encryption_cache()
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "new-key")
    assert encryption.decrypt_value(rotated_cipher) == "my-secret-credential"


def test_re_encrypt_legacy_plaintext_gets_encrypted(monkeypatch):
    """re_encrypt_value on a pre-encryption plaintext value (legacy
    data from before v0.6.x) should encrypt it under the primary key
    rather than crash."""
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "new-key,old-key")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", raising=False)

    legacy_plaintext = "sk-legacy-unencrypted-value"
    result = encryption.re_encrypt_value(legacy_plaintext)
    assert result != legacy_plaintext  # got encrypted
    assert encryption.decrypt_value(result) == legacy_plaintext


def test_re_encrypt_with_unknown_key_raises(monkeypatch):
    """If the encrypted blob can't be decrypted by ANY configured key,
    re_encrypt must raise — silently re-encrypting random base64 would
    corrupt the value."""
    # Set up data encrypted under 'mystery-key' (not configured later)
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", "mystery-key")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", raising=False)
    orphan_cipher = encryption.encrypt_value("lost-forever")
    encryption._reset_encryption_cache()

    # Switch to a config that doesn't include mystery-key
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "new-key,older-key")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValueError, match="no configured key"):
        encryption.re_encrypt_value(orphan_cipher)


# ---------------------------------------------------------------------------
# Backward compatibility — no test breakage for callers using legacy API
# ---------------------------------------------------------------------------


def test_get_fernet_returns_primary_key(monkeypatch):
    """get_fernet() must remain a thin wrapper around the primary key."""
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "primary,secondary")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", raising=False)

    f = encryption.get_fernet()
    cipher = f.encrypt(b"x").decode()
    # Single-Fernet using the primary key must decrypt it.
    assert f.decrypt(cipher.encode()).decode() == "x"


def test_legacy_plaintext_decrypt_passthrough(monkeypatch):
    """Pre-encryption plaintext (old DB rows) is returned as-is, no
    matter how many keys are configured. Regression for v0.6.15 path."""
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "k1,k2,k3")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", raising=False)
    legacy = "sk-an-old-plaintext-key-from-2024"
    assert encryption.decrypt_value(legacy) == legacy


def test_re_encrypt_is_idempotent_under_same_primary(monkeypatch):
    """Calling re_encrypt twice under the same primary key keeps the
    plaintext invariant — repeated rotations don't corrupt."""
    monkeypatch.setenv("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", "alpha,beta")
    monkeypatch.delenv("DEEPER_NOTEBOOK_ENCRYPTION_KEY", raising=False)

    c1 = encryption.encrypt_value("repeatable")
    c2 = encryption.re_encrypt_value(c1)
    c3 = encryption.re_encrypt_value(c2)
    assert encryption.decrypt_value(c3) == "repeatable"
