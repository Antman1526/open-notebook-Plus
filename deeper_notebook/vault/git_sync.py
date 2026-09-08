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


_LAST_SNAPSHOT_TIMESTAMPS: dict[str, float] = {}


def auto_snapshot_vault(
    vault_path: Path | str,
    debounce_seconds: int = 300,
    message: str | None = None,
) -> dict[str, Any]:
    """Take a debounced Git snapshot of a vault if changes exist and debounce window has elapsed."""
    import time

    p = Path(vault_path).resolve()
    key = str(p)
    now = time.time()
    last_time = _LAST_SNAPSHOT_TIMESTAMPS.get(key, 0.0)

    if (now - last_time) < debounce_seconds:
        return {
            "ok": True,
            "debounced": True,
            "message": f"Snapshot debounced. Last snapshot was {now - last_time:.0f}s ago (window: {debounce_seconds}s).",
        }

    result = create_vault_snapshot(p, message=message or "Auto-snapshot")
    if result.get("ok"):
        _LAST_SNAPSHOT_TIMESTAMPS[key] = now
    return result


def get_vault_remotes(vault_path: Path | str) -> list[dict[str, str]]:
    """Return configured Git remotes for a vault."""
    p = Path(vault_path)
    if not is_git_repo(p):
        return []
    try:
        res = subprocess.run(
            ["git", "remote", "-v"],
            cwd=p,
            check=True,
            capture_output=True,
            text=True,
        )
        remotes: dict[str, dict[str, str]] = {}
        for line in res.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                name, url = parts[0], parts[1]
                remotes[name] = {"name": name, "url": url}
        return list(remotes.values())
    except Exception as exc:
        logger.warning(f"Failed to get git remotes for vault {p}: {exc}")
        return []


def set_vault_remote(
    vault_path: Path | str,
    remote_name: str = "origin",
    url: str = "",
) -> dict[str, Any]:
    """Set or update a Git remote URL for a vault."""
    p = Path(vault_path)
    if not is_git_repo(p):
        return {"ok": False, "error": "Vault is not an initialized Git repository"}
    if not url.strip():
        return {"ok": False, "error": "Remote URL cannot be empty"}

    existing = [r["name"] for r in get_vault_remotes(p)]
    try:
        if remote_name in existing:
            subprocess.run(
                ["git", "remote", "set-url", remote_name, url.strip()],
                cwd=p,
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            subprocess.run(
                ["git", "remote", "add", remote_name, url.strip()],
                cwd=p,
                check=True,
                capture_output=True,
                text=True,
            )
        return {"ok": True, "message": f"Configured remote '{remote_name}'", "url": url.strip()}
    except subprocess.CalledProcessError as exc:
        return {"ok": False, "error": exc.stderr.strip() or str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _current_branch(vault_path: Path) -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=vault_path,
            check=True,
            capture_output=True,
            text=True,
        )
        branch = res.stdout.strip()
        return branch if branch and branch != "HEAD" else "main"
    except Exception:
        return "main"


def push_vault_git(
    vault_path: Path | str,
    remote: str = "origin",
    branch: str | None = None,
) -> dict[str, Any]:
    """Push local vault commits to the configured remote repository."""
    p = Path(vault_path)
    if not is_git_repo(p):
        return {"ok": False, "error": "Vault is not an initialized Git repository"}

    target_branch = branch or _current_branch(p)
    try:
        res = subprocess.run(
            ["git", "push", "-u", remote, target_branch],
            cwd=p,
            check=True,
            capture_output=True,
            text=True,
            timeout=30.0,
        )
        return {
            "ok": True,
            "message": f"Successfully pushed to {remote}/{target_branch}",
            "output": res.stdout.strip() or res.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Push to {remote} timed out after 30s"}
    except subprocess.CalledProcessError as exc:
        return {"ok": False, "error": exc.stderr.strip() or exc.stdout.strip() or str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def pull_vault_git(
    vault_path: Path | str,
    remote: str = "origin",
    branch: str | None = None,
) -> dict[str, Any]:
    """Pull remote commits into the local vault repository."""
    p = Path(vault_path)
    if not is_git_repo(p):
        return {"ok": False, "error": "Vault is not an initialized Git repository"}

    target_branch = branch or _current_branch(p)
    try:
        res = subprocess.run(
            ["git", "pull", "--rebase", remote, target_branch],
            cwd=p,
            check=True,
            capture_output=True,
            text=True,
            timeout=30.0,
        )
        return {
            "ok": True,
            "message": f"Successfully pulled from {remote}/{target_branch}",
            "output": res.stdout.strip() or res.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Pull from {remote} timed out after 30s"}
    except subprocess.CalledProcessError as exc:
        return {"ok": False, "error": exc.stderr.strip() or exc.stdout.strip() or str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

