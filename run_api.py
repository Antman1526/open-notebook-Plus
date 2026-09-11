#!/usr/bin/env python3
"""
Startup script for Deeper Notebook API server.
"""

import os
import sys
from pathlib import Path

import uvicorn

# Add the current directory to Python path so imports work
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

if __name__ == "__main__":
    # Default configuration
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "5055"))
    reload = os.getenv("API_RELOAD", "true").lower() == "true"

    print(f"Starting Deeper Notebook API server on {host}:{port}")
    print(f"Reload mode: {reload}")

    uvicorn.run(
        "api.main:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=[str(current_dir)] if reload else None,
        # v0.8.126 — bound graceful shutdown so a reload (triggered by a
        # file change) doesn't leave the old process holding the port
        # while an in-flight request is still running.
        timeout_graceful_shutdown=int(os.getenv("API_GRACEFUL_SHUTDOWN_SEC", "20")),
    )
