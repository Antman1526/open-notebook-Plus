"""Unit tests for git-backed vault snapshot and versioning."""

from pathlib import Path
from deeper_notebook.vault.git_sync import (
    is_git_repo,
    init_vault_git,
    create_vault_snapshot,
    get_vault_history,
)


def test_vault_git_sync_lifecycle(tmp_path: Path):
    vault = tmp_path / "MyVault"
    vault.mkdir()

    assert not is_git_repo(vault)

    # Initialize
    res = init_vault_git(vault)
    assert res["ok"] is True
    assert is_git_repo(vault)

    # Re-init is safe
    re_res = init_vault_git(vault)
    assert re_res["ok"] is True

    # No changes snapshot
    snap1 = create_vault_snapshot(vault)
    assert snap1["ok"] is True
    assert snap1["committed"] is False

    # Add a note
    note = vault / "Ideas.md"
    note.write_text("# Next Big Idea\n\nBuild amazing local AI tools.", encoding="utf-8")

    snap2 = create_vault_snapshot(vault, message="Add Ideas note")
    assert snap2["ok"] is True
    assert snap2["committed"] is True
    assert "commit" in snap2

    # Get history
    history = get_vault_history(vault, limit=5)
    assert len(history) >= 2
    assert history[0]["message"] == "Add Ideas note"


def test_auto_snapshot_vault_debouncing(tmp_path: Path):
    from deeper_notebook.vault.git_sync import auto_snapshot_vault

    vault = tmp_path / "AutoVault"
    vault.mkdir()
    init_vault_git(vault)

    # First auto-snapshot
    note = vault / "Draft.md"
    note.write_text("First draft", encoding="utf-8")
    res1 = auto_snapshot_vault(vault, debounce_seconds=60)
    assert res1["ok"] is True
    assert res1.get("committed") is True

    # Immediate second auto-snapshot should be debounced
    note.write_text("Second draft edit", encoding="utf-8")
    res2 = auto_snapshot_vault(vault, debounce_seconds=60)
    assert res2["ok"] is True
    assert res2.get("debounced") is True


def test_vault_git_remote_and_sync(tmp_path: Path):
    import subprocess
    from deeper_notebook.vault.git_sync import (
        get_vault_remotes,
        set_vault_remote,
        push_vault_git,
        pull_vault_git,
    )

    vault = tmp_path / "RemoteVault"
    vault.mkdir()
    init_vault_git(vault)

    assert get_vault_remotes(vault) == []

    # Configure remote
    res = set_vault_remote(vault, "origin", "https://github.com/user/my-vault.git")
    assert res["ok"] is True
    remotes = get_vault_remotes(vault)
    assert len(remotes) == 1
    assert remotes[0]["name"] == "origin"
    assert remotes[0]["url"] == "https://github.com/user/my-vault.git"

    # Update remote URL
    res2 = set_vault_remote(vault, "origin", "git@github.com:user/my-vault.git")
    assert res2["ok"] is True
    remotes2 = get_vault_remotes(vault)
    assert remotes2[0]["url"] == "git@github.com:user/my-vault.git"

    # Test real local bare remote push/pull
    bare_remote = tmp_path / "bare_remote.git"
    subprocess.run(["git", "init", "--bare", str(bare_remote)], check=True)

    set_vault_remote(vault, "local_bare", str(bare_remote))

    note = vault / "GroundedIdea.md"
    note.write_text("# Knowledge\nCross-device sync verified.", encoding="utf-8")
    create_vault_snapshot(vault, message="Add GroundedIdea")

    push_res = push_vault_git(vault, remote="local_bare")
    assert push_res["ok"] is True

    # Pull in a clone
    clone_vault = tmp_path / "CloneVault"
    subprocess.run(["git", "clone", str(bare_remote), str(clone_vault)], check=True)
    pull_res = pull_vault_git(clone_vault, remote="origin")
    assert pull_res["ok"] is True

