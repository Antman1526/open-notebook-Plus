"""Git-backed snapshot and version history management for approved Markdown vaults.

Provides automatic and on-demand version control for local Markdown vaults without
affecting the read-only vault indexing boundary.
"""

from __future__ import annotations

import datetime
from pathlib import Path
import subprocess
from typing import Any
from loguru import logger


def is_git_repo(vault_path: Path | str) -> bool:
    """Check if the specified directory is an initialized Git repository."""
    p = Path(vault_path)
    return p.is_dir() and (p / ".git").is_dir()


def init_vault_git(vault_path: Path | str) -> dict[str, Any]:
    """Initialize a Git repository inside a vault root directory."""
    p = Path(vault_path)
    if not p.is_dir():
        return {"ok": False, "error": f"Directory does not exist: {p}"}

    if is_git_repo(p):
        return {"ok": True, "message": "Already a Git repository"}

    try:
        subprocess.run(["git", "init"], cwd=p, check=True, capture_output=True, text=True)
        # Create minimal .gitignore if none exists
        gitignore = p / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(".DS_Store\n*.tmp\n*.part\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=p, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: initialize vault version tracking"],
            cwd=p,
            check=True,
            capture_output=True,
            text=True,
        )
        return {"ok": True, "message": "Initialized Git repository in vault"}
    except subprocess.CalledProcessError as exc:
        return {"ok": False, "error": exc.stderr.strip() or str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def create_vault_snapshot(vault_path: Path | str, message: str | None = None) -> dict[str, Any]:
    """Record a Git snapshot (commit) of the current vault state.

    Args:
        vault_path: Path to the vault root directory.
        message: Optional commit message. Defaults to timestamped snapshot.

    Returns:
        Dict with status, commit hash (if changes were committed), and output.
    """
    p = Path(vault_path)
    if not is_git_repo(p):
        return {"ok": False, "error": "Vault is not an initialized Git repository"}

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    commit_msg = message or f"Snapshot: {ts}"

    try:
        # Stage all changes
        subprocess.run(["git", "add", "-A"], cwd=p, check=True, capture_output=True, text=True)

        # Check if there is anything to commit
        diff = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=p,
            check=True,
            capture_output=True,
            text=True,
        )
        if not diff.stdout.strip():
            return {"ok": True, "committed": False, "message": "No changes to snapshot"}

        res = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=p,
            check=True,
            capture_output=True,
            text=True,
        )
        # Get latest commit hash
        hash_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=p,
            check=True,
            capture_output=True,
            text=True,
        )
        commit_hash = hash_res.stdout.strip()
        logger.info(f"Created vault snapshot {commit_hash[:8]} in {p.name}: {commit_msg}")
        return {
            "ok": True,
            "committed": True,
            "commit": commit_hash,
            "message": commit_msg,
        }
    except subprocess.CalledProcessError as exc:
        return {"ok": False, "error": exc.stderr.strip() or str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_vault_history(vault_path: Path | str, limit: int = 15) -> list[dict[str, Any]]:
    """Retrieve recent snapshot history for a vault."""
    p = Path(vault_path)
    if not is_git_repo(p):
        return []

    try:
        res = subprocess.run(
            ["git", "log", f"-n{max(1, limit)}", "--pretty=format:%H|%an|%ad|%s", "--date=iso"],
            cwd=p,
            check=True,
            capture_output=True,
            text=True,
        )
        history: list[dict[str, Any]] = []
        for line in res.stdout.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                history.append(
                    {
                        "hash": parts[0],
                        "author": parts[1],
                        "date": parts[2],
                        "message": parts[3],
                    }
                )
        return history
    except Exception as exc:
        logger.warning(f"Failed to read git log for vault {p}: {exc}")
        return []
