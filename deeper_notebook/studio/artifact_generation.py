"""Compatibility facade for the legacy Evidence Studio generation module.

New code should import ``deeper_notebook.studio.generation``. This module keeps
the legacy function names and router monkeypatch targets stable for one release
cycle while forwarding execution to the split implementation.
"""

from __future__ import annotations

from deeper_notebook.ai.models import Model
from deeper_notebook.ai.provision import provision_langchain_model
from deeper_notebook.domain.notebook import (
    Notebook,
    Source,
    StudioArtifact,
    StudioWorkflowRun,
)
from deeper_notebook.local_models.inventory import enumerate_models
from deeper_notebook.local_models.role_routing import recommend_model_roles

from .generation import (
    ArtifactGenerationRequest,
    context,
    persistence,
    prompts,
    service,
)
from .generation.service import generate_artifact

persist_artifact_exports = persistence.persist_artifact_exports
persist_single_export = persistence.persist_single_export
is_export_stale = persistence.is_export_stale
producible_export_formats = persistence.producible_export_formats
canonical_export_format = persistence.canonical_export_format

# Legacy helper names remain importable while call sites migrate gradually.
_ARTIFACT_TYPE_INSTRUCTIONS = prompts._ARTIFACT_TYPE_INSTRUCTIONS
_ARTIFACT_TYPE_MODEL_ROLE = prompts._ARTIFACT_TYPE_MODEL_ROLE
_artifact_instruction = prompts.artifact_instruction
_artifact_model_role = prompts.artifact_model_role
_configured_model_dir = context.configured_model_dir
_ensure_artifact_sources_ready = context.ensure_artifact_sources_ready
_sources_not_ready_exception = context.sources_not_ready_exception
_artifact_sources = context.artifact_sources
_notebook_record_exists = context.notebook_record_exists
_citation_preview = context.citation_preview
_artifact_context = context.artifact_context
_artifact_not_ready_sources = context.artifact_not_ready_sources
_resolve_artifact_model_route = context.resolve_artifact_model_route
_env_int = context.env_int
_set_workflow_step_status = service._set_workflow_step_status
_active_workflow_run_for_artifact = service._active_workflow_run_for_artifact
_has_generated_output = service._has_generated_output
_snapshot_artifact_revision = service._snapshot_artifact_revision


def _sync_generation_dependencies() -> None:
    """Apply the router's legacy dependency injection to split modules."""
    context.Notebook = Notebook
    context.Source = Source
    context.Model = Model
    context.provision_langchain_model = provision_langchain_model
    context.enumerate_models = enumerate_models
    context.recommend_model_roles = recommend_model_roles
    service.StudioArtifact = StudioArtifact
    service.StudioWorkflowRun = StudioWorkflowRun
    persistence.persist_artifact_exports = persist_artifact_exports
    for name in ("export_slide_deck", "export_infographic"):
        if name in globals():
            setattr(persistence, name, globals()[name])


async def generate_studio_artifact(artifact_id: str) -> StudioArtifact:
    """Legacy adapter preserving router dependency-injection and test seams."""
    _sync_generation_dependencies()
    return await generate_artifact(
        ArtifactGenerationRequest(artifact_id=artifact_id, source_ids=[])
    )


def __getattr__(name: str):
    """Forward unlisted private helpers during the compatibility window."""
    for module in (prompts, context, persistence, service):
        try:
            return getattr(module, name)
        except AttributeError:
            continue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ArtifactGenerationRequest",
    "canonical_export_format",
    "generate_artifact",
    "generate_studio_artifact",
    "is_export_stale",
    "persist_artifact_exports",
    "persist_single_export",
    "producible_export_formats",
]
