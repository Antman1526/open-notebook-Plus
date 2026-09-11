"""Surreal-commands integration for Deeper Notebook.

v0.7.24 — configure loguru file logging for the worker process.

The desktop launcher spawns `surreal_commands.cli.worker
--import-modules commands` as a separate Python process. That worker
is where podcast / source / embed jobs actually run — most operations
that take minutes and most things that fail in interesting ways.
Without a configured file sink, all worker output went to stderr,
and the launcher pipes worker stderr to DEVNULL in non-debug mode.
Net effect since v0.7.14: every worker job failure in production was
silently discarded; the README's `tail ~/.deeper-notebook/logs/*.log`
story worked for the API process but not the worker that does the
long-running work.

`configure_logging("worker")` at package import time means the moment
`surreal_commands` imports this package (its first action after
starting), the worker.log sink is installed alongside api.log and
launcher.log.

Wrapped in try/except so any import-time logging failure doesn't
prevent the worker from booting — defense-in-depth only.
"""

import os

from deeper_notebook.environment import apply_product_environment

# The surreal-commands worker imports this package before command modules.
_NORMALIZED_PRODUCT_ENVIRONMENT = apply_product_environment(os.environ)

try:
    from deeper_notebook.logging import configure_logging

    configure_logging("worker")
except Exception:
    # Logging setup is best-effort at import time. Even if it fails,
    # the worker must still start so we don't lose job processing.
    pass

# v0.8.127 — Start the worker heartbeat, but ONLY in the actual worker
# process. This module is also imported by the API process ("Commands
# imported in API process" — see api/main.py's command_registry
# healthz check), so `is_worker_process()` gates the thread start on
# an explicit env flag / argv signal rather than firing on every
# import. See deeper_notebook/worker_heartbeat.py for detection + the
# heartbeat itself; /healthz/deep reads it back to report a `worker`
# subsystem.
try:
    from deeper_notebook.worker_heartbeat import is_worker_process, start_heartbeat

    if is_worker_process():
        start_heartbeat()
except Exception:
    # Best-effort, same rationale as the logging setup above — a
    # heartbeat failure must never prevent the worker from booting.
    pass

from .embedding_commands import (
    embed_insight_command,
    embed_note_command,
    embed_source_command,
    rebuild_embeddings_command,
)
from .example_commands import analyze_data_command, process_text_command
from .podcast_commands import generate_podcast_command
from .prompt_optimizer_commands import optimize_prompt_command  # v0.8.68
from .source_commands import process_source_command
from .source_visual_commands import extract_source_visual_command
from .studio_commands import generate_studio_artifact_command

# v0.7.47 — memory_commands.py is RUNTIME-COPIED into this package by
# desktop/app.py:_phase_register_memory_commands during launcher
# startup. It's not a build-time file. Pick it up via a guarded import
# so the worker registers `memory_extract_turn` and
# `memory_summarize_session` once the launcher has placed it.
#
# Before v0.7.47, these commands were never registered with the worker
# — `surreal_commands.cli.worker --import-modules commands` only ran
# `__import__("commands")` which executes THIS file. memory_commands.py
# sitting next to us on disk was never imported. Every chat turn that
# fired `memory_extract_turn` queued a SurrealDB row with status="new"
# that nobody ever picked up. The whole v0.4 memory layer was a
# silent feature outage.
#
# Guarded import handles:
#   - Fresh installs before _phase_register_memory_commands runs
#   - No-memory builds where the module was deliberately omitted
#   - Tests / dev environments without the desktop launcher
_memory_extract_turn = None
_memory_summarize_session = None
try:
    from .memory_commands import (  # noqa: F401
        memory_extract_turn as _memory_extract_turn,
    )
    from .memory_commands import (
        memory_summarize_session as _memory_summarize_session,
    )
except ImportError:
    # memory_commands.py not present yet — the file is copied in by
    # the launcher BEFORE the worker spawns, so under normal operation
    # this branch only fires in dev/test environments without the
    # launcher path. Silently skip; the worker will run fine without
    # the memory layer.
    pass

__all__ = [
    # Embedding commands
    "embed_note_command",
    "embed_insight_command",
    "embed_source_command",
    "rebuild_embeddings_command",
    # Other commands
    "generate_podcast_command",
    "generate_studio_artifact_command",
    "process_source_command",
    "extract_source_visual_command",
    "process_text_command",
    "analyze_data_command",
]
# v0.7.47 — memory commands appended to __all__ only when actually
# imported, so `from commands import *` doesn't fail on no-memory builds.
if _memory_extract_turn is not None:
    __all__.extend(["memory_extract_turn", "memory_summarize_session"])
    # Bind to module namespace so the @command decorators stay
    # discoverable via `commands.memory_extract_turn` etc.
    memory_extract_turn = _memory_extract_turn
    memory_summarize_session = _memory_summarize_session
