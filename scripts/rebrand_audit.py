#!/usr/bin/env python3
"""Classify tracked legacy-name references during the Deeper Notebook rebrand."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.persisted_queue_inventory import (
        production_queue_inventory,
        production_queue_occurrence_inventory,
    )
except ModuleNotFoundError:
    from persisted_queue_inventory import (
        production_queue_inventory,
        production_queue_occurrence_inventory,
    )

CATEGORIES = (
    "compatibility_alias",
    "upstream_reference",
    "historical_reference",
    "migration_documentation",
    "unexpected_active_identity",
)
ALLOWLIST_SCHEMA_VERSION = 6  # digests fold in the intra-line ordinal
LEGACY_PATTERNS = (
    "Open Notebook Plus",
    "Open Notebook",
    "Open notebook+",
    "OpenNotebook",
    "open-notebook-Plus",
    "open-notebook-plus",
    "open_notebook",
    "OPEN_NOTEBOOK_",
    "ONP_",
    "/onp/",
    "onpFetch",
    "--onp-",
    "onp-theme",
    "components/onp",
    "/api/onp",
)
_SAFE_NESTED_APPROVALS = frozenset(
    {
        ("Open Notebook Plus", "Open Notebook"),
    }
)
_CATEGORY_OVERRIDES = {
    ("desktop/tests/test_data_root_conflict_recovery.py", "open-notebook-plus"): (
        "compatibility_alias"
    ),
    ("desktop/tests/test_data_root_conflict_recovery.py", "Open Notebook Plus"): (
        "compatibility_alias"
    ),
    ("desktop/tests/test_emergency_log.py", "open-notebook-plus"): (
        "compatibility_alias"
    ),
    ("tests/test_product_identity.py", "OpenNotebook"): ("compatibility_alias"),
    (
        "frontend/src/components/deeper-notebook/ThemeSwitcher.tsx",
        "onp-theme",
    ): "compatibility_alias",
    (
        "frontend/src/components/deeper-notebook/ThemeSwitcher.test.tsx",
        "onp-theme",
    ): "compatibility_alias",
    ("tests/test_task5_brand_namespace.py", "onp-theme"): ("compatibility_alias"),
    ("tests/test_local_model_benchmarks.py", "open-notebook-plus"): (
        "compatibility_alias"
    ),
    ("desktop/__init__.py", "open-notebook-plus"): "historical_reference",
    ("desktop/paths.py", "Open Notebook Plus"): "migration_documentation",
    ("deeper_notebook/logging.py", "OPEN_NOTEBOOK_"): ("migration_documentation"),
    ("deeper_notebook/logging.py", "ONP_"): "migration_documentation",
    ("scripts/persisted_queue_inventory.py", "open_notebook"): (
        "migration_documentation"
    ),
}

OccurrenceKey = tuple[str, str, str, int | None, int, str]


@dataclass(frozen=True)
class Rationale:
    path: str
    pattern: str
    source: str
    line: int | None
    column: int
    context_sha256: str
    category: str
    explanation: str
    compatibility_contract: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "pattern": self.pattern,
            "source": self.source,
            "line": self.line,
            "column": self.column,
            "context_sha256": self.context_sha256,
            "category": self.category,
            "explanation": self.explanation,
            "compatibility_contract": self.compatibility_contract,
        }


@dataclass(frozen=True)
class Approval:
    category: str
    rationale: Rationale


Allowlist = Mapping[OccurrenceKey, Approval]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "persisted_queue_identifiers",
        "compatibility_contracts",
        "entries",
    }
)
_ENTRY_FIELDS = frozenset(
    {
        "path",
        "pattern",
        "source",
        "line",
        "column",
        "context_sha256",
        "category",
        "rationale",
    }
)
_RATIONALE_FIELDS = frozenset(
    {
        "path",
        "pattern",
        "source",
        "line",
        "column",
        "context_sha256",
        "category",
        "explanation",
        "compatibility_contract",
    }
)
_COMPATIBILITY_CONTRACT_FIELDS = frozenset(
    {
        "kind",
        "owner",
        "retention_reason",
        "proof",
        "scope",
        "coverage_sha256",
    }
)
_COMPATIBILITY_SCOPE_FIELDS = frozenset({"paths", "patterns", "sources"})
COMPATIBILITY_CONTRACT_KINDS = frozenset(
    {
        "env_alias",
        "import_shim",
        "data_migration",
        "installer_upgrade",
        "legacy_api_route",
        "persisted_identifier",
        "public_symbol",
        "external_protocol",
        "legacy_artifact_probe",
        "regression_fixture",
    }
)
_STATIC_COMPATIBILITY_PROOFS = {
    "static:env-alias-contract-fixture-v1": "env_alias",
    "static:regression-fixture-contract-v1": "regression_fixture",
}
_PROVEN_LEGACY_TEST_FIXTURE_PATHS = frozenset(
    {
        "desktop/tests/conftest.py",
        "desktop/tests/test_app_migration.py",
        "desktop/tests/test_auto_register.py",
        "desktop/tests/test_bootstrap.py",
        "desktop/tests/test_data_root_final_window_race.py",
        "desktop/tests/test_first_run.py",
        "desktop/tests/test_launcher.py",
        "desktop/tests/test_launcher_prefs.py",
        "desktop/tests/test_llamacpp_provider.py",
        "desktop/tests/test_next_rewrites_patcher.py",
        "desktop/tests/test_release_manifest.py",
        "desktop/tests/test_v0_8_68_launch_race.py",
        "desktop/tests/test_window.py",
        "frontend/src/components/chat/CitationPill.test.tsx",
        "frontend/src/components/deeper-notebook/ArtifactRail.test.tsx",
        "frontend/src/lib/task6-active-brand.test.ts",
        "tests/test_deeper_notebook_router.py",
        "tests/test_digest_builder.py",
        "tests/test_evidence_studio_artifact_api.py",
        "tests/test_logging_config.py",
        "tests/test_local_model_benchmarks.py",
        "tests/test_podcast_suggest.py",
        "tests/test_product_identity.py",
        "tests/test_repair_desktop_db_script.py",
        "tests/test_studio_router.py",
        "tests/test_task5_brand_namespace.py",
        "tests/test_task6_active_product.py",
        "tests/test_v0_7_141_bootstrap.py",
        "tests/test_v0_7_142_singleton.py",
        "tests/test_v0_7_201_audit_sweep.py",
        "tests/test_v0_8_6_launcher_prefs_api.py",
    }
)
_VISIBLE_IDENTITY_PATTERNS = frozenset(
    {
        "Open Notebook Plus",
        "Open Notebook",
        "Open notebook+",
        "OpenNotebook",
        "open-notebook-Plus",
        "open-notebook-plus",
    }
)
_UPSTREAM_REFERENCE_PATHS = frozenset(
    {
        "Makefile",
        ".github/ISSUE_TEMPLATE/installation_issue.yml",
        ".github/workflows/build-and-release.yml",
        ".github/workflows/build-dev.yml",
        "examples/docker-compose-full-local.yml",
        "examples/docker-compose-ollama.yml",
        "examples/docker-compose-single.yml",
        "examples/docker-compose-speaches.yml",
        "examples/easypanel/meta.yaml",
    }
)
_DEFAULT_COMPATIBILITY_CONTRACTS = {
    "env-alias-v1": {
        "kind": "env_alias",
        "owner": "runtime-configuration",
        "retention_reason": (
            "Existing operator environments need a deprecation window while "
            "canonical settings take precedence."
        ),
        "proof": (
            "tests/test_environment_aliases.py::test_all_four_precedence_positions"
        ),
    },
    "python-import-shim-v1": {
        "kind": "import_shim",
        "owner": "python-package-compatibility",
        "retention_reason": (
            "Installed integrations may import the former package until the "
            "documented compatibility window closes."
        ),
        "proof": (
            "tests/test_python_import_compatibility.py::"
            "test_every_pre_move_legacy_import_resolves_to_canonical_object"
        ),
    },
    "python-symbol-compat-v1": {
        "kind": "public_symbol",
        "owner": "python-api-compatibility",
        "retention_reason": (
            "Existing extensions may catch the former public exception base "
            "while the canonical symbol migration is staged."
        ),
        "proof": (
            "tests/test_python_import_compatibility.py::"
            "test_legacy_exception_name_is_an_alias_of_the_canonical_base"
        ),
    },
    "database-record-identifier-v1": {
        "kind": "persisted_identifier",
        "owner": "database-migrations",
        "retention_reason": (
            "Existing SurrealDB records retain stable record identifiers "
            "until a dedicated idempotent data migration is shipped."
        ),
        "proof": (
            "tests/test_product_identity.py::"
            "test_persisted_database_identifier_contract_has_exact_inventory"
        ),
    },
    "runtime-record-identifier-v1": {
        "kind": "persisted_identifier",
        "owner": "database-domain-models",
        "retention_reason": (
            "Domain singleton records must remain addressable under their "
            "persisted identifiers until an idempotent record migration ships."
        ),
        "proof": (
            "tests/test_product_identity.py::"
            "test_runtime_record_contract_has_exact_behavioral_inventory"
        ),
    },
    "surreal-namespace-identifier-v1": {
        "kind": "persisted_identifier",
        "owner": "database-runtime",
        "retention_reason": (
            "Existing SurrealDB installations must retain their namespace and "
            "database names until a coordinated data migration is available."
        ),
        "proof": (
            "tests/test_product_identity.py::"
            "test_surreal_namespace_contract_has_exact_runtime_scope"
        ),
    },
    "persisted-queue-identifier-v1": {
        "kind": "persisted_identifier",
        "owner": "background-command-queue",
        "retention_reason": (
            "Queued jobs and workers must retain their persisted application "
            "identifier until a coordinated queue migration is complete."
        ),
        "proof": (
            "tests/test_persisted_queue_identifiers.py::"
            "test_persisted_queue_identifier_allowlist_matches_exact_ast_inventory"
        ),
    },
    "data-root-migration-v1": {
        "kind": "data_migration",
        "owner": "desktop-migration",
        "retention_reason": (
            "Existing installations need their legacy application and data "
            "roots detected without overwriting either location."
        ),
        "proof": (
            "desktop/tests/test_data_root_migration.py::"
            "test_rerun_after_success_is_idempotent"
        ),
    },
    "installer-upgrade-v1": {
        "kind": "installer_upgrade",
        "owner": "desktop-release",
        "retention_reason": (
            "Existing desktop installations require stable upgrade identity "
            "while visible package names become canonical."
        ),
        "proof": (
            "desktop/tests/test_release_manifest.py::"
            "test_installer_rebrands_visible_identity_but_pins_upgrade_app_id"
        ),
    },
    "legacy-api-route-v1": {
        "kind": "legacy_api_route",
        "owner": "api-routing",
        "retention_reason": (
            "Existing clients and registered callbacks require the hidden "
            "legacy route to reach the canonical handler."
        ),
        "proof": (
            "tests/test_task6_active_product.py::"
            "test_canonical_namespace_is_documented_and_legacy_alias_is_hidden"
        ),
    },
    "external-format-v1": {
        "kind": "external_protocol",
        "owner": "artifact-import",
        "retention_reason": (
            "Previously exported research bundles remain readable while new "
            "artifacts use the canonical format identifier."
        ),
        "proof": (
            "tests/test_task6_active_product.py::"
            "test_research_bundle_writes_canonical_format_and_reads_legacy_format"
        ),
    },
    "desktop-bridge-v1": {
        "kind": "external_protocol",
        "owner": "desktop-webview-bridge",
        "retention_reason": (
            "Installed frontend bundles may still read legacy injected window "
            "properties while canonical bridge consumers roll out."
        ),
        "proof": (
            "desktop/tests/test_window.py::"
            "test_desktop_bridge_producer_consumer_contract_is_canonical_first"
        ),
    },
    "compose-service-identifier-v1": {
        "kind": "external_protocol",
        "owner": "container-runtime",
        "retention_reason": (
            "Operator support commands must retain the real Compose service "
            "identifier while visible product branding remains canonical."
        ),
        "proof": (
            "tests/test_product_identity.py::"
            "test_installation_issue_log_command_names_existing_compose_service"
        ),
    },
    "legacy-artifact-probe-v1": {
        "kind": "legacy_artifact_probe",
        "owner": "desktop-release",
        "retention_reason": (
            "Upgrade verification must detect retired application artifacts "
            "without launching or mutating unrelated installations."
        ),
        "proof": (
            "desktop/tests/test_release_manifest.py::"
            "test_compatibility_jobs_probe_real_readiness_then_leave_no_sidecars"
        ),
    },
    "export-directory-fallback-v1": {
        "kind": "legacy_artifact_probe",
        "owner": "filesystem-export",
        "retention_reason": (
            "Existing export directories remain discoverable only when the "
            "canonical directory does not already exist."
        ),
        "proof": (
            "tests/test_filesystem_router.py::"
            "test_fs_home_falls_back_to_existing_legacy_exports_without_moving"
        ),
    },
    "frontend-env-alias-v1": {
        "kind": "env_alias",
        "owner": "frontend-runtime-configuration",
        "retention_reason": (
            "Existing frontend deployments keep legacy feature settings while "
            "canonical settings take deterministic precedence."
        ),
        "proof": (
            "frontend/src/lib/features.test.ts::"
            "continues to support legacy Plus flags when canonical flags are absent"
        ),
    },
    "launcher-pref-env-migration-v1": {
        "kind": "env_alias",
        "owner": "desktop-launcher-preferences",
        "retention_reason": (
            "Existing launcher preference files must normalize deprecated keys "
            "through the central registry before canonical persistence."
        ),
        "proof": (
            "desktop/tests/test_launcher_prefs.py::"
            "test_legacy_launcher_pref_remains_accepted_and_is_canonicalized"
        ),
    },
    "theme-storage-migration-v1": {
        "kind": "persisted_identifier",
        "owner": "frontend-theme-storage",
        "retention_reason": (
            "Existing browser profiles retain their selected theme while the "
            "canonical storage key becomes primary."
        ),
        "proof": (
            "frontend/src/components/deeper-notebook/ThemeSwitcher.test.tsx::"
            "migrates legacy theme storage into the canonical key"
        ),
    },
    "benchmark-history-fallback-v1": {
        "kind": "persisted_identifier",
        "owner": "local-model-benchmarks",
        "retention_reason": (
            "Existing benchmark history remains readable while every new "
            "write targets the canonical filename."
        ),
        "proof": (
            "tests/test_local_model_benchmarks.py::"
            "test_legacy_benchmark_filename_is_read_but_new_writes_are_canonical"
        ),
    },
    "container-log-fallback-v1": {
        "kind": "legacy_artifact_probe",
        "owner": "runtime-logging",
        "retention_reason": (
            "Existing container log mounts remain readable only when the "
            "canonical log directory is absent."
        ),
        "proof": (
            "tests/test_logging_config.py::"
            "test_existing_legacy_container_log_dir_is_deprecated_fallback"
        ),
    },
    "podcast-profile-identifier-v1": {
        "kind": "persisted_identifier",
        "owner": "podcast-profiles",
        "retention_reason": (
            "Existing saved podcast profiles retain their former selectable "
            "identity until records receive a dedicated migration."
        ),
        "proof": (
            "tests/test_podcast_suggest.py::"
            "test_suggest_medium_volume_matches_canonical_and_legacy_local_profile_names"
        ),
    },
    "central-legacy-identity-v1": {
        "kind": "persisted_identifier",
        "owner": "product-identity",
        "retention_reason": (
            "Centralized former identifiers remain explicit so compatibility "
            "readers can reject stale visible branding elsewhere."
        ),
        "proof": (
            "tests/test_task6_active_product.py::"
            "test_active_product_code_has_no_stale_visible_brand_labels"
        ),
    },
    "legacy-test-fixture-v1": {
        "kind": "regression_fixture",
        "owner": "compatibility-tests",
        "retention_reason": (
            "Synthetic former identities exercise migration readers while "
            "production surfaces remain canonical."
        ),
        "proof": (
            "tests/test_task6_active_product.py::"
            "test_active_product_code_has_no_stale_visible_brand_labels"
        ),
    },
    "rebrand-audit-regression-v1": {
        "kind": "regression_fixture",
        "owner": "rebrand-audit",
        "retention_reason": (
            "The audit test suite must retain synthetic former identities to "
            "prove unsafe compatibility entries are rejected."
        ),
        "proof": (
            "tests/test_product_identity.py::"
            "test_allowlist_rejects_compatibility_for_active_docs_ui_and_defaults"
        ),
    },
    "frontend-canonical-help-regression-v1": {
        "kind": "regression_fixture",
        "owner": "frontend-settings",
        "retention_reason": (
            "The settings regression explicitly rejects deprecated environment "
            "labels from every visible help surface."
        ),
        "proof": (
            "frontend/src/components/settings/SmartRoutingPanel.test.tsx::"
            "names only canonical Deeper Notebook environment variables"
        ),
    },
}
_KIND_PROOF_PATHS = {
    "env_alias": frozenset(
        {
            "tests/test_environment_aliases.py",
            "frontend/src/lib/features.test.ts",
            "frontend/src/lib/features-build-contract.test.ts",
            "desktop/tests/test_launcher_prefs.py",
        }
    ),
    "import_shim": frozenset({"tests/test_python_import_compatibility.py"}),
    "data_migration": frozenset(
        {
            "desktop/tests/test_data_root_migration.py",
            "desktop/tests/test_data_root_conflict_recovery.py",
            "desktop/tests/test_emergency_log.py",
        }
    ),
    "installer_upgrade": frozenset({"desktop/tests/test_release_manifest.py"}),
    "legacy_api_route": frozenset({"tests/test_task6_active_product.py"}),
    "persisted_identifier": frozenset(
        {
            "tests/test_persisted_queue_identifiers.py",
            "tests/test_product_identity.py",
            "tests/test_domain.py",
            "tests/test_task6_active_product.py",
            "tests/test_local_model_benchmarks.py",
            "tests/test_podcast_suggest.py",
            "frontend/src/components/deeper-notebook/ThemeSwitcher.test.tsx",
        }
    ),
    "public_symbol": frozenset(
        {
            "tests/test_python_import_compatibility.py",
            "tests/test_v0_7_139.py",
        }
    ),
    "external_protocol": frozenset(
        {
            "desktop/tests/test_window.py",
            "tests/test_product_identity.py",
            "tests/test_task6_active_product.py",
        }
    ),
    "legacy_artifact_probe": frozenset(
        {
            "desktop/tests/test_release_manifest.py",
            "tests/test_filesystem_router.py",
            "tests/test_logging_config.py",
        }
    ),
    "regression_fixture": frozenset(
        {
            "frontend/src/components/settings/SmartRoutingPanel.test.tsx",
            "tests/test_product_identity.py",
            "tests/test_task6_active_product.py",
        }
    ),
}
_KIND_SCOPE_EXACT_PATHS = {
    "env_alias": frozenset(
        {
            "deeper_notebook/environment.py",
            "tests/test_environment_aliases.py",
            "frontend/package.json",
            "frontend/scripts/verify-feature-env-build.mjs",
            "frontend/src/lib/features.ts",
            "frontend/src/lib/features.test.ts",
            "frontend/src/lib/features-build-contract.test.ts",
            "frontend/tests/build-contract/package.json",
            "desktop/tests/test_launcher_prefs.py",
        }
    ),
    "import_shim": frozenset(
        {
            ".codex-scanignore",
            "desktop/bootstrap.py",
            "desktop/build/package_layout.py",
            "desktop/build/pyinstaller.spec",
            "desktop/build/verify_package_contents.py",
            "desktop/launcher.py",
            "pyproject.toml",
            "tests/fixtures/legacy_import_modules.txt",
            "tests/test_environment_aliases.py",
            "tests/test_package_artifact_contract.py",
            "tests/test_python_import_compatibility.py",
        }
    ),
    "public_symbol": frozenset(
        {
            "deeper_notebook/exceptions.py",
            "tests/test_python_import_compatibility.py",
            "tests/test_v0_7_139.py",
        }
    ),
    "persisted_identifier": frozenset(
        {
            "api/command_service.py",
            "api/podcast_service.py",
            "api/routers/chat.py",
            "api/routers/commands.py",
            "api/routers/embedding.py",
            "api/routers/embedding_rebuild.py",
            "api/routers/sources.py",
            "api/routers/studio/common.py",
            "api/routers/transformations.py",
            "commands/embedding_commands.py",
            "commands/example_commands.py",
            "commands/podcast_commands.py",
            "commands/prompt_optimizer_commands.py",
            "commands/source_commands.py",
            "commands/source_visual_commands.py",
            "commands/studio_commands.py",
            ".github/workflows/test.yml",
            "desktop/db_repair.py",
            "desktop/launcher.py",
            "desktop/memory/_register.py",
            "desktop/memory/client.py",
            "desktop/memory/memory_commands.py",
            "desktop/memory/surreal_store.py",
            "desktop/memory/tests/test_register.py",
            "deeper_notebook/database/migrations/1.surrealql",
            "deeper_notebook/database/migrations/1_down.surrealql",
            "deeper_notebook/database/migrations/5.surrealql",
            "deeper_notebook/database/migrations/11.surrealql",
            "deeper_notebook/database/migrations/11_down.surrealql",
            "deeper_notebook/database/migrations/18.surrealql",
            "deeper_notebook/database/migrations/18_down.surrealql",
            "deeper_notebook/identity.py",
            "deeper_notebook/ai/models.py",
            "deeper_notebook/domain/content_settings.py",
            "deeper_notebook/domain/notebook.py",
            "deeper_notebook/source_visuals/queue.py",
            "deeper_notebook/domain/provider_config.py",
            "deeper_notebook/domain/transformation.py",
            "deeper_notebook/graphs/source.py",
            "deeper_notebook/research/graph.py",
            "deeper_notebook/local_models/benchmarks.py",
            "deeper_notebook/podcasts/profile_names.py",
            "frontend/src/components/deeper-notebook/ThemeSwitcher.tsx",
            "frontend/src/components/deeper-notebook/ThemeGallery.test.tsx",
            "frontend/src/components/deeper-notebook/ThemeSwitcher.test.tsx",
            "frontend/src/components/providers/ThemeProvider.test.tsx",
            "frontend/src/lib/theme-storage.ts",
            "frontend/src/lib/stores/theme-store.test.ts",
            "frontend/src/lib/theme-script.test.ts",
            "frontend/src/lib/theme-script.ts",
            "tests/test_local_model_benchmarks.py",
            "tests/test_persisted_queue_identifiers.py",
            "scripts/repair_desktop_db.sh",
            "tests/integration/conftest.py",
            "tests/test_domain.py",
            "tests/test_embedding_commands.py",
            "tests/test_evidence_studio_artifact_api.py",
            "tests/test_research_graph.py",
            "tests/test_podcast_suggest.py",
            "tests/test_task5_brand_namespace.py",
            "tests/test_task6_active_product.py",
            "tests/test_v0_8_68_prompt_optimizer.py",
        }
    ),
    "external_protocol": frozenset(
        {
            ".github/ISSUE_TEMPLATE/installation_issue.yml",
            "api/routers/exports.py",
            "desktop/first_run/static/memory_injection.js",
            "desktop/first_run/static/voice_injection.js",
            "desktop/tests/test_window.py",
            "desktop/window.py",
            "deeper_notebook/studio/exporters/research_bundle.py",
            "frontend/src/lib/desktop-version.test.ts",
            "frontend/src/lib/desktop-version.ts",
            "tests/test_v0_7_210_version_and_reaper.py",
            "tests/test_task6_active_product.py",
        }
    ),
    "legacy_artifact_probe": frozenset(
        {
            ".github/workflows/build-desktop.yml",
            "api/routers/filesystem.py",
            "deeper_notebook/logging.py",
            "desktop/build/deeper-notebook.iss",
            "desktop/build/pyinstaller.spec",
            "tests/test_filesystem_router.py",
            "tests/test_logging_config.py",
            "desktop/tests/test_release_manifest.py",
        }
    ),
    "legacy_api_route": frozenset(
        {
            "api/main.py",
            "api/routers/deeper_notebook.py",
            "deeper_notebook/identity.py",
            "desktop/build/pyinstaller.spec",
            "frontend/src/lib/api/deeper-notebook.test.ts",
            "frontend/src/lib/api/deeper-notebook.ts",
            "frontend/src/lib/api/onp.ts",
            "frontend/src/lib/task6-active-brand.test.ts",
            "tests/test_gmail_router.py",
            "tests/test_knowledge_engine_api.py",
            "tests/test_overlay_api.py",
            "tests/test_task6_active_product.py",
            "tests/test_vault_api.py",
        }
    ),
    "data_migration": frozenset(
        {
            "desktop/tests/test_emergency_log.py",
            "scripts/backup_restore.py",
            "tests/test_backup_restore_v0_7_126.py",
            "scripts/repair_desktop_db.sh",
        }
    ),
    "installer_upgrade": frozenset(
        {
            "desktop/tests/test_data_root_conflict_recovery.py",
            "scripts/repair_desktop_db.sh",
        }
    ),
}
_KIND_SCOPE_PREFIXES = {
    "import_shim": ("open_notebook/",),
    "data_migration": (
        "desktop/tests/test_data_root_",
        "desktop/data_root.py",
        "desktop/app_migration.py",
    ),
    "installer_upgrade": (
        ".github/workflows/",
        "desktop/build/",
        "desktop/tests/test_release_manifest.py",
        "desktop/app_migration.py",
        "desktop/window.py",
    ),
    "regression_fixture": (
        "fixtures/",
        "tests/",
        "desktop/tests/",
    ),
}
_AUDIT_METADATA_PATHS = frozenset({"scripts/rebrand-allowlist.json"})
_PINNED_SELECTOR_INVENTORY_SHA256 = (
    "3a27e4c4e0be198e9ec8af92d8343d98e593825b3e42a8754d1d4f1aad92e305"
)
_SEMANTIC_SELECTOR_PATHS = frozenset(
    {
        "frontend/src/lib/api/deeper-notebook.ts",
        "frontend/src/lib/api/onp.ts",
        "frontend/src/lib/features.ts",
        "frontend/src/lib/theme-storage.ts",
        "tests/test_persisted_queue_identifiers.py",
        "tests/test_product_identity.py",
    }
)
_MIN_EXPLANATION_CHARS = 48
_MIN_EXPLANATION_WORDS = 8
_GENERIC_EXPLANATIONS = frozenset(
    {
        "compatibility behavior is intentionally preserved.",
        "historical product name retained for accuracy.",
        ("historical reference retained for accuracy and migration compatibility."),
        "this legacy reference is retained for compatibility.",
    }
)
_MECHANICAL_LOCATOR_RE = re.compile(
    r"\s*(?:this\s+)?occurrence\s+at\s+"
    r"(?:[\w.@+ -]+/)*[\w.@+ -]+:\d+:\d+"
    r"\s+is\s+individually\s+pinned(?:\s+to\s+its\s+audited\s+context)?[.!]?",
    flags=re.IGNORECASE,
)
_STRUCTURAL_LOCATOR_RE = re.compile(
    r"(?:[\w.@+ -]+/)+[\w.@+ -]+:\d+:\d+|"
    r"\b(?:line|column)\s+\d+\b|"
    r"\b[0-9a-f]{64}\b",
    flags=re.IGNORECASE,
)


def _humanize(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    return " ".join(word for word in re.split(r"[^A-Za-z0-9]+", value) if word).lower()


def _scrub_structural_terms(value: str) -> str:
    scrubbed = value
    for pattern in sorted(LEGACY_PATTERNS, key=len, reverse=True):
        scrubbed = re.sub(
            re.escape(pattern),
            "former identifier",
            scrubbed,
            flags=re.IGNORECASE,
        )
    for category in CATEGORIES:
        scrubbed = re.sub(
            re.escape(category),
            "approved classification",
            scrubbed,
            flags=re.IGNORECASE,
        )
    scrubbed = _STRUCTURAL_LOCATOR_RE.sub("", scrubbed)
    return " ".join(scrubbed.split()).strip(" .,:;-")


def semantic_explanation_key(explanation: str) -> str:
    """Normalize semantic meaning without locator-only differentiation."""
    normalized = _MECHANICAL_LOCATOR_RE.sub("", explanation)
    normalized = _scrub_structural_terms(normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized.casefold())
    return " ".join(normalized.split())


def _source_role(relative_path: str) -> str:
    path = Path(relative_path)
    if path.name == "Makefile":
        return "The build automation manifest"
    if relative_path == "desktop/__init__.py":
        return "The desktop package wrapper"
    if path.name in {"CHANGELOG.md", "CHANGELOG"}:
        return "The product changelog"
    if path.name == "MEMORY.md":
        return "The project memory history"
    if relative_path.startswith(".github/workflows/"):
        return f"The {_humanize(path.stem)} workflow"
    if relative_path.startswith(".github/ISSUE_TEMPLATE/"):
        return f"The {_humanize(path.stem)} issue template"
    if path.name.startswith("Dockerfile"):
        return (
            "The single-image container build definition"
            if path.name != "Dockerfile"
            else "The multi-stage container build definition"
        )
    if path.name == "docker-compose.yml":
        return "The container orchestration definition"
    if path.name == ".env.example":
        return "The environment configuration example"

    stem_role = _humanize(path.stem or path.name) or "project"
    semantic_parents = [
        _humanize(part)
        for part in path.parts[:-1]
        if part
        not in {
            ".",
            ".github",
            "src",
            "docs",
            "tests",
            "test",
        }
    ]
    qualifier = " ".join(part for part in semantic_parents[-3:] if part)
    subject = " ".join(part for part in (qualifier, stem_role) if part)
    if relative_path.startswith("tests/") or "/tests/" in relative_path:
        return f"The {subject} regression suite"
    if path.suffix == ".py":
        return f"The {subject} Python module"
    if path.suffix in {".ts", ".tsx", ".js", ".mjs"}:
        return f"The {subject} client module"
    if path.suffix in {".yml", ".yaml"}:
        return f"The {subject} configuration"
    if path.suffix == ".md":
        if "spec" in path.stem:
            return f"The {subject} specification"
        if "plan" in path.stem:
            return f"The {subject} plan"
        if "report" in path.stem:
            return f"The {subject} report"
        return f"The {subject} document"
    return f"The {subject} project artifact"


def _markdown_scope(lines: list[str], line_number: int) -> str | None:
    headings: list[tuple[int, str]] = []
    local_label: str | None = None
    in_fence = False
    for line in lines[:line_number]:
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", line)
        if match:
            level = len(match.group(1))
            headings = [heading for heading in headings if heading[0] < level]
            headings.append((level, _scrub_structural_terms(match.group(2))))
            local_label = None
            continue
        bold_match = re.match(
            r"^\s*(?:\d+\.\s*)?\*\*(.+?)\*\*\s*:?(?:\s+.*)?$",
            line,
        )
        if bold_match:
            local_label = _scrub_structural_terms(bold_match.group(1))
    if not headings:
        return f'the "{local_label}" section' if local_label else None
    hierarchy_parts = [heading for _level, heading in headings[-3:]]
    if local_label:
        hierarchy_parts.append(local_label)
    hierarchy = " / ".join(hierarchy_parts)
    return f'the "{hierarchy}" section'


def _python_scope(source: str, line_number: int) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    scopes: list[tuple[int, int, str]] = []
    branches: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            start = node.lineno
            end = getattr(node, "end_lineno", node.lineno)
            if start <= line_number <= end:
                condition = _scrub_structural_terms(ast.unparse(node.test))
                branches.append((start, end, condition))
            continue
        if not isinstance(
            node,
            (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        start = min(
            [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
        )
        end = getattr(node, "end_lineno", node.lineno)
        if start <= line_number <= end:
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            scopes.append((start, end, f"the `{node.name}` {kind}"))
    if branches:
        ordered = sorted(
            branches,
            key=lambda branch: (-(branch[1] - branch[0]), branch[0]),
        )
        conditions = list(dict.fromkeys(branch[2] for branch in ordered))
        return "the branch where " + " and ".join(conditions)
    if scopes:
        return min(
            scopes,
            key=lambda scope: (scope[1] - scope[0], -scope[0]),
        )[2]
    return None


def _workflow_scope(lines: list[str], line_number: int) -> str | None:
    for line in reversed(lines[:line_number]):
        match = re.match(r"^\s*-\s+name:\s*(.+?)\s*$", line)
        if match:
            return f'the "{_scrub_structural_terms(match.group(1))}" workflow step'
    return None


def _generic_scope(lines: list[str], line_number: int) -> str | None:
    symbol_patterns = (
        r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"^\s*(?:function\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{",
        r"^\s*(?:describe|test|it)\s*\(\s*['\"](.+?)['\"]",
        r"^\s*(?:Task|Step)\s+\d+[.: -]+\s*(.+?)\s*$",
    )
    for line in reversed(lines[:line_number]):
        for pattern in symbol_patterns:
            match = re.match(pattern, line)
            if match:
                name = _scrub_structural_terms(match.group(1))
                return f'the "{name}" operation'
    return None


def _semantic_scope(
    relative_path: str,
    lines: list[str],
    line_number: int,
) -> str:
    suffix = Path(relative_path).suffix
    if suffix == ".md":
        return _markdown_scope(lines, line_number) or "the document overview"
    if suffix == ".py":
        source = "\n".join(lines)
        return _python_scope(source, line_number) or "the module overview"
    if suffix in {".yml", ".yaml"}:
        return (
            _workflow_scope(lines, line_number)
            or _generic_scope(lines, line_number)
            or "the configuration block"
        )
    return _generic_scope(lines, line_number) or "the active artifact section"


def _alias_purpose(
    line: str,
    *,
    pattern: str,
    column: int,
) -> str | None:
    start = column - 1
    if pattern in {"OPEN_NOTEBOOK_", "ONP_"}:
        variable_match = re.match(r"[A-Z][A-Z0-9_]+", line[start:])
        variable = variable_match.group(0) if variable_match else pattern
        suffix = variable.removeprefix(pattern)
        setting = _humanize(suffix) or "product setting"
        form = "long-form" if pattern == "OPEN_NOTEBOOK_" else "short-form"
        base = (
            f"the deprecated {form} {setting} setting is named so operators "
            "can recognize and migrate that specific fallback"
        )
        return f"{base}; {_statement_semantics(line, pattern, column)}"
    if pattern == "open_notebook":
        lowered = line.casefold()
        if "app=" in lowered or '"app"' in lowered or "'app'" in lowered:
            return (
                "the persisted command application namespace remains explicit "
                "so queued registrations and submissions keep routing "
                f"correctly; {_statement_semantics(line, pattern, column)}"
            )
        if "namespace" in lowered:
            return (
                "the persisted database namespace remains explicit so existing "
                "records continue to resolve during upgrades; "
                f"{_statement_semantics(line, pattern, column)}"
            )
        if "database" in lowered:
            return (
                "the persisted database name remains explicit so existing "
                "records continue to resolve during upgrades; "
                f"{_statement_semantics(line, pattern, column)}"
            )
        if re.search(r"\b(?:from|import)\s+", line):
            return (
                "the compatibility Python package is imported so downstream "
                "extensions keep their established module boundary; "
                f"{_statement_semantics(line, pattern, column)}"
            )
    if pattern in {"Open Notebook Plus", "Open Notebook", "Open notebook+"}:
        if "http" in line or "](" in line or "spec" in line.casefold():
            return (
                "the archived product wording identifies the historical design "
                "or source link that maintainers still need to trace; "
                f"{_statement_semantics(line, pattern, column)}"
            )
    return None


def _pattern_role(pattern: str, line: str) -> str:
    roles = {
        "Open Notebook Plus": "former full desktop product title",
        "Open Notebook": "former base product title",
        "Open notebook+": "former stylized plus title",
        "OpenNotebook": "former compact product title",
        "open-notebook-Plus": "former mixed-case package slug",
        "open-notebook-plus": "former lowercase package slug",
        "/onp/": "legacy API path segment",
        "onpFetch": "legacy client fetch helper",
        "--onp-": "legacy command-line option prefix",
        "components/onp": "legacy component directory",
        "/api/onp": "legacy API namespace",
    }
    if pattern in roles:
        return roles[pattern]
    if pattern == "open_notebook":
        lowered = line.casefold()
        if "namespace" in lowered:
            return "persisted database namespace"
        if "database" in lowered:
            return "persisted database name"
        if "app" in lowered:
            return "persisted command application namespace"
        if re.search(r"\b(?:from|import)\s+", line):
            return "compatibility Python package"
        return "legacy underscored identifier"
    if pattern == "OPEN_NOTEBOOK_":
        return "deprecated long-form environment prefix"
    if pattern == "ONP_":
        return "deprecated short-form environment prefix"
    return "legacy compatibility role"


def _statement_semantics(line: str, pattern: str, column: int) -> str:
    start = column - 1
    end = start + len(pattern)
    if pattern in {"OPEN_NOTEBOOK_", "ONP_"}:
        variable_match = re.match(r"[A-Z][A-Z0-9_]+", line[start:])
        if variable_match:
            end = start + len(variable_match.group(0))
    role = _pattern_role(pattern, line)
    window_start = max(0, start - 100)
    window_end = min(len(line), end + 100)
    marked = line[window_start:start] + f" current {role} " + line[end:window_end]
    marked = _scrub_structural_terms(marked)
    marked = re.sub(r"[`*_#|<>{}\[\]();]+", " ", marked)
    marked = " ".join(marked.split()).strip(" .,:;-")
    if len(marked) > 180:
        marked = marked[:177].rsplit(" ", 1)[0] + "..."
    return (
        f"the statement uses that role while expressing {marked}"
        if marked
        else "the statement assigns that role to the current migration case"
    )


def _normalized_line_purpose(
    line: str,
    *,
    pattern: str,
    column: int,
) -> str:
    alias_purpose = _alias_purpose(line, pattern=pattern, column=column)
    if alias_purpose:
        return alias_purpose

    return _statement_semantics(line, pattern, column)


def _role_specific_purpose(
    relative_path: str,
    pattern: str,
    line: str,
) -> str | None:
    if relative_path == "desktop/__init__.py":
        return (
            "the archived desktop design specification keeps its former "
            "filename so maintainers can trace packaging decisions made "
            "before the rename"
        )
    if relative_path == "README.upstream.md":
        return (
            "the original project terminology remains visible so inherited "
            "documentation stays attributable to its upstream source"
        )
    if relative_path == "deeper_notebook/identity.py" and pattern == "open_notebook":
        return (
            "the declared legacy Python package name remains available so "
            "compatibility imports resolve after the package rename"
        )
    if relative_path == "scripts/rebrand_audit.py":
        role = _pattern_role(pattern, line)
        return (
            f"the scanner enumerates the {role} form so active legacy remnants "
            "cannot evade the identity audit; "
            f"{_statement_semantics(line, pattern, line.index(pattern) + 1)}"
        )
    return None


def _nearby_semantic_context(
    lines: list[str],
    line_number: int,
) -> str | None:
    neighbors: list[tuple[str, str]] = []
    for label, indexes in (
        ("the preceding context", range(line_number - 2, -1, -1)),
        ("the following context", range(line_number, len(lines))),
    ):
        for index in indexes:
            candidate = _scrub_structural_terms(lines[index])
            candidate = re.sub(r"[`*_#|<>{}\[\]();]+", " ", candidate)
            candidate = " ".join(candidate.split()).strip(" .,:;-")
            if not candidate:
                continue
            if len(candidate) > 110:
                candidate = candidate[:107].rsplit(" ", 1)[0] + "..."
            neighbors.append((label, candidate))
            break
    if not neighbors:
        return None
    return "; ".join(f"{label} concerns {value}" for label, value in neighbors)


def _semantic_context_label(
    lines: list[str],
    line_number: int,
) -> str | None:
    current_line = lines[line_number - 1]
    current_indent = len(current_line) - len(current_line.lstrip())
    label_patterns = (
        r"^\s*(?:\d+\.\s*)?\*\*(.+?)\*\*\s*:?\s*$",
        r"^\s*(?:Task|Step)\s+\d+[.: -]+\s*(.+?)\s*$",
    )
    for line in reversed(lines[max(0, line_number - 120) : line_number]):
        if re.search(r"(?:\"rationale\"\s*:\s*|rationale\s*=\s*)_rationale\(", line):
            return "the rationale binding"
        function_match = re.match(
            r"^(\s*)(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)",
            line,
        )
        if function_match and len(function_match.group(1)) < current_indent:
            return _scrub_structural_terms(function_match.group(2))
        class_match = re.match(
            r"^(\s*)class\s+([A-Za-z_][A-Za-z0-9_]*)",
            line,
        )
        if class_match and len(class_match.group(1)) < current_indent:
            return _scrub_structural_terms(class_match.group(2))
        for pattern in label_patterns:
            match = re.match(pattern, line)
            if match:
                return _scrub_structural_terms(match.group(1))
    return None


def semantic_explanation_for_occurrence(
    root: Path,
    occurrence: Mapping[str, object],
    category: str,
) -> str:
    """Explain why one exact legacy occurrence remains using source semantics."""
    relative_path = occurrence["path"]
    pattern = occurrence["pattern"]
    source = occurrence["source"]
    line_number = occurrence.get("line")
    column = occurrence["column"]
    if not isinstance(relative_path, str) or not isinstance(pattern, str):
        raise ValueError("occurrence path and pattern must be strings")
    if not isinstance(source, str) or not isinstance(column, int):
        raise ValueError("occurrence source and column must be valid")

    role = _source_role(relative_path)
    if source == "path":
        scope = "the installed or persisted artifact identity"
        purpose = (
            "the legacy artifact name remains discoverable for upgrade and "
            "compatibility checks"
        )
    else:
        if not isinstance(line_number, int):
            raise ValueError("content occurrence requires a line number")
        lines = (root / relative_path).read_text(encoding="utf-8").splitlines()
        if not 1 <= line_number <= len(lines):
            raise ValueError("occurrence line is outside the source file")
        scope = _semantic_scope(relative_path, lines, line_number)
        current_line = lines[line_number - 1]
        purpose = _role_specific_purpose(
            relative_path,
            pattern,
            current_line,
        ) or _normalized_line_purpose(
            current_line,
            pattern=pattern,
            column=column,
        )
        nearby_context = _nearby_semantic_context(lines, line_number)
        if nearby_context:
            purpose = f"{purpose}, while {nearby_context}"
        context_label = _semantic_context_label(lines, line_number)
        if context_label and context_label.casefold() not in scope.casefold():
            purpose = f"{purpose}; the local example is introduced by {context_label}"

    templates = {
        "compatibility_alias": (
            "{role} keeps the legacy behavior in {scope} because {purpose}."
        ),
        "historical_reference": (
            "{role} preserves the historical record in {scope} because {purpose}."
        ),
        "migration_documentation": (
            "{role} documents the upgrade boundary in {scope} because {purpose}."
        ),
        "upstream_reference": (
            "{role} retains inherited terminology in {scope} because {purpose}."
        ),
    }
    try:
        explanation = templates[category].format(
            role=role,
            scope=scope,
            purpose=purpose,
        )
    except KeyError as exc:
        raise ValueError(f"invalid rationale category: {category}") from exc
    explanation = explanation.replace(
        relative_path,
        "the related project artifact",
    )
    return _scrub_structural_terms(explanation).rstrip(".") + "."


_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_context(context: str) -> str:
    """Collapse whitespace so an approval survives reformatting.

    Approvals used to pin the RAW line text, so reindenting changed the digest
    and invalidated a still-correct approval. A repo-wide `ruff format` (700+
    files) invalidated them wholesale, after which `--regenerate` aborted and
    could not rebuild — which is what kept a formatter un-adoptable.
    """
    return _WHITESPACE_RUN.sub(" ", context).strip()


def occurrence_ordinal(pattern: str, context: str, column: int) -> int:
    """Which match of `pattern` on this line the given column refers to.

    This is what lets absolute position leave the key WITHOUT losing what
    position was really encoding. `column` was never merely location: when one
    line contains the same legacy token twice, it is the only thing separating
    the approved occurrence from the unapproved one. An ordinal keeps that
    distinction while being immune to indentation, which shifts every absolute
    column on a line by the same amount.
    """
    index = 0
    cursor = context.find(pattern)
    while cursor != -1 and cursor < column - 1:
        index += 1
        cursor = context.find(pattern, cursor + len(pattern))
    return index


def context_sha256(context: str) -> str:
    """Digest for context with no intra-line ambiguity — i.e. ordinal 0.

    Deliberately identical to `occurrence_digest` for the FIRST occurrence on a
    line. That equivalence is load-bearing: whole-path approvals have no line to
    disambiguate, and the overwhelming majority of line approvals cover the only
    occurrence on their line, so those keep working untouched. Only a genuine
    second-occurrence approval has to say so explicitly.
    """
    return hashlib.sha256(
        f"0\x00{normalize_context(context)}".encode("utf-8")
    ).hexdigest()


def occurrence_digest(*, pattern: str, context: str, column: int) -> str:
    """Digest identifying WHICH occurrence on a line, independent of layout."""
    ordinal = occurrence_ordinal(pattern, context, column)
    return hashlib.sha256(
        f"{ordinal}\x00{normalize_context(context)}".encode("utf-8")
    ).hexdigest()


def occurrence_anchor(
    path: str,
    source: str,
    line: int | None,
    column: int,
) -> str:
    """Return the human-readable anchor every approval reason must name."""
    line_label = "path" if line is None else str(line)
    return f"{path}@{source}:{line_label}:{column}"


def _occurrence_key(
    *,
    path: str,
    pattern: str,
    source: str,
    line: int | None,
    column: int,
    context: str,
) -> OccurrenceKey:
    # Path-sourced occurrences have no line to disambiguate, so the context IS
    # the path. Line-sourced ones fold in the ordinal so position can leave the
    # lookup key safely.
    digest = (
        context_sha256(context)
        if line is None
        else occurrence_digest(pattern=pattern, context=context, column=column)
    )
    return (path, pattern, source, line, column, digest)


_CONTENT_INDEX_CACHE: dict[int, dict[tuple[str, str, str, str], str]] = {}


def _content_index(allowlist: Allowlist) -> dict[tuple[str, str, str, str], str]:
    """Index approvals by (path, pattern, source, digest) — position dropped."""
    cached = _CONTENT_INDEX_CACHE.get(id(allowlist))
    if cached is not None:
        return cached
    index: dict[tuple[str, str, str, str], str] = {}
    for (path, pattern, source, _line, _column, digest), approval in allowlist.items():
        index.setdefault((path, pattern, source, digest), approval.category)
    _CONTENT_INDEX_CACHE[id(allowlist)] = index
    return index


def classify_match(key: OccurrenceKey, allowlist: Allowlist) -> str:
    """Return the occurrence's category or flag it as active identity.

    Falls back to a position-independent match. An edit ABOVE an approved line
    used to invalidate a still-correct approval; that ratchet fired repeatedly
    and is what blocks adopting a formatter.

    Safe because the digest folds in the intra-line ordinal: two occurrences of
    the same token on one line hash differently, so an approval for one cannot
    silently cover the other. The fallback still requires the same file, the
    same pattern, the same source and the same normalized content — it drops
    only WHERE on the page that content sits.
    """
    approval = allowlist.get(key)
    if approval is not None:
        return approval.category
    path, pattern, source, _line, _column, digest = key
    fallback = _content_index(allowlist).get((path, pattern, source, digest))
    return fallback if fallback is not None else "unexpected_active_identity"


def patterns_for_path(path: str, allowlist: Allowlist) -> tuple[str, ...]:
    """Return the scanner's immutable, built-in legacy-pattern policy."""
    del path, allowlist
    return LEGACY_PATTERNS


def _allowlist_root(path: Path) -> Path:
    if path.parent.name == "scripts":
        return path.parent.parent.resolve()
    return path.parent.resolve()


def _validate_compatibility_proof(
    root: Path,
    proof: str,
    kind: str,
) -> None:
    static_kind = _STATIC_COMPATIBILITY_PROOFS.get(proof)
    if static_kind is not None:
        if static_kind != kind:
            raise ValueError("static compatibility proof does not prove contract kind")
        return
    if proof.startswith("static:"):
        raise ValueError(
            "compatibility contract requires a closed kind-specific static proof"
        )
    if proof.count("::") != 1:
        raise ValueError(
            "compatibility contract requires a tracked proof reference "
            "formatted as path::test_name or a validated static contract ID"
        )
    relative_path, symbol = proof.split("::", 1)
    if relative_path not in _KIND_PROOF_PATHS[kind]:
        raise ValueError("tracked proof reference path does not prove contract kind")
    proof_path = Path(relative_path)
    if (
        not relative_path
        or not symbol
        or proof_path.is_absolute()
        or ".." in proof_path.parts
    ):
        raise ValueError("compatibility contract requires a tracked proof reference")
    target = root / proof_path
    if not target.is_file():
        raise ValueError("compatibility contract requires a tracked proof reference")
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative_path],
        check=False,
        capture_output=True,
    )
    if tracked.returncode != 0:
        raise ValueError("compatibility contract requires a tracked proof reference")
    source = target.read_text(encoding="utf-8")
    if target.suffix == ".py":
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ValueError("compatibility contract proof must be parseable") from exc
        symbols = {
            node.name
            for node in ast.walk(tree)
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            )
        }
        if symbol not in symbols:
            raise ValueError(
                "compatibility contract requires a tracked proof reference"
            )
        if not (symbol.startswith("test_") or symbol.startswith("Test")):
            raise ValueError(
                "compatibility contract proof must reference a behavioral test"
            )
    else:
        matching_lines = [line for line in source.splitlines() if symbol in line]
        if not matching_lines:
            raise ValueError(
                "compatibility contract requires a tracked proof reference"
            )
        if not any(
            re.search(r"\b(?:it|test|describe)\s*\(", line) for line in matching_lines
        ):
            raise ValueError(
                "compatibility contract proof must reference a behavioral test"
            )


def _scope_path_allowed(kind: str, path: str) -> bool:
    if kind == "regression_fixture":
        return (
            path.startswith("tests/")
            or path.startswith("desktop/tests/")
            or path.startswith("fixtures/")
            or path.startswith("frontend/tests/")
            or (path.startswith("frontend/src/") and ".test." in Path(path).name)
        )
    if path in _KIND_SCOPE_EXACT_PATHS.get(kind, frozenset()):
        return True
    return any(path.startswith(prefix) for prefix in _KIND_SCOPE_PREFIXES.get(kind, ()))


def _validate_contract_scope(
    kind: str,
    scope: Mapping[str, object],
) -> None:
    if frozenset(scope) != _COMPATIBILITY_SCOPE_FIELDS:
        raise ValueError(
            "compatibility contract scope must use exact structured fields"
        )
    for field in ("paths", "patterns", "sources"):
        values = scope.get(field)
        if (
            not isinstance(values, list)
            or not values
            or values != sorted(set(values))
            or not all(isinstance(value, str) and value for value in values)
        ):
            raise ValueError(
                "compatibility contract scope values must be sorted unique strings"
            )
    paths = scope["paths"]
    patterns = scope["patterns"]
    sources = scope["sources"]
    assert isinstance(paths, list)
    assert isinstance(patterns, list)
    assert isinstance(sources, list)
    if not all(_scope_path_allowed(kind, path) for path in paths):
        raise ValueError(
            "compatibility contract scope exceeds its kind-specific boundary"
        )
    if not set(patterns).issubset(LEGACY_PATTERNS):
        raise ValueError(
            "compatibility contract scope patterns must be built-in patterns"
        )
    if not set(sources).issubset({"path", "content"}):
        raise ValueError("compatibility contract scope sources must be path or content")
    if kind == "env_alias" and not set(patterns).issubset({"OPEN_NOTEBOOK_", "ONP_"}):
        raise ValueError("env_alias scope contains a non-environment pattern")
    if kind == "import_shim" and set(patterns) != {"open_notebook"}:
        raise ValueError("import_shim scope must use the legacy package pattern")
    if kind == "public_symbol" and set(patterns) != {"OpenNotebook"}:
        raise ValueError("public_symbol scope must use the exported legacy symbol")


def _coverage_identity(entry: Mapping[str, object]) -> str:
    return "|".join(
        str(entry[field])
        for field in (
            "path",
            "pattern",
            "source",
            "line",
            "column",
            "context_sha256",
        )
    )


def compatibility_coverage_digest(
    entries: list[Mapping[str, object]],
    contract_id: str,
) -> str:
    identities = sorted(
        _coverage_identity(entry)
        for entry in entries
        if isinstance(entry.get("rationale"), dict)
        and entry["rationale"].get("compatibility_contract") == contract_id
    )
    encoded = json.dumps(
        identities,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _materialize_compatibility_contracts(
    entries: list[dict[str, object]],
    contract_ids: set[str],
) -> dict[str, dict[str, object]]:
    contracts: dict[str, dict[str, object]] = {}
    for contract_id in sorted(contract_ids):
        base = _DEFAULT_COMPATIBILITY_CONTRACTS[contract_id]
        owned_entries = [
            entry
            for entry in entries
            if isinstance(entry.get("rationale"), dict)
            and entry["rationale"].get("compatibility_contract") == contract_id
        ]
        contract: dict[str, object] = dict(base)
        contract["scope"] = {
            "paths": sorted({str(entry["path"]) for entry in owned_entries}),
            "patterns": sorted({str(entry["pattern"]) for entry in owned_entries}),
            "sources": sorted({str(entry["source"]) for entry in owned_entries}),
        }
        contract["coverage_sha256"] = compatibility_coverage_digest(
            owned_entries,
            contract_id,
        )
        contracts[contract_id] = contract
    return contracts


def _compatibility_is_forbidden(
    root: Path,
    path: str,
    pattern: str,
    source: str,
    line: int | None,
    contract_kind: str | None = None,
    canonical_contract: str | None = None,
) -> bool:
    path_obj = Path(path)
    if path_obj.suffix.lower() in {".md", ".mdx", ".rst"}:
        return True
    if "/locales/" in f"/{path}":
        return True
    if path.startswith("frontend/src/") and ".test." not in Path(path).name:
        if canonical_contract not in {
            "desktop-bridge-v1",
            "frontend-env-alias-v1",
            "legacy-api-route-v1",
            "theme-storage-migration-v1",
        }:
            return True
        return False
    if (
        path.startswith("tests/")
        or path.startswith("desktop/tests/")
        or ".test." in path
    ):
        return False
    if contract_kind == "persisted_identifier":
        return False
    if source != "content" or line is None:
        return False
    target = root / path_obj
    try:
        context = target.read_text(encoding="utf-8").splitlines()[line - 1]
    except (OSError, UnicodeError, IndexError):
        return False
    return bool(
        re.search(
            r"\bdefault(?:s|ed)?\b|default_",
            context,
            re.IGNORECASE,
        )
    )


def _selector_key(
    occurrence: Mapping[str, object],
) -> OccurrenceKey | None:
    path = occurrence.get("path")
    pattern = occurrence.get("pattern")
    source = occurrence.get("source")
    line = occurrence.get("line")
    column = occurrence.get("column")
    digest = occurrence.get("context_sha256")
    if (
        not isinstance(path, str)
        or not isinstance(pattern, str)
        or not isinstance(source, str)
        or (line is not None and not isinstance(line, int))
        or not isinstance(column, int)
        or not isinstance(digest, str)
    ):
        return None
    return (path, pattern, source, line, column, digest)


def _selector_occurrences_for_path(
    root: Path,
    relative_path: str,
) -> list[dict[str, object]]:
    target = root / relative_path
    if not target.is_file():
        return []
    occurrences: list[dict[str, object]] = []
    for pattern, start, _end in _pattern_occurrences(
        relative_path,
        LEGACY_PATTERNS,
    ):
        occurrences.append(
            {
                "path": relative_path,
                "pattern": pattern,
                "source": "path",
                "line": None,
                "column": start + 1,
                "context_sha256": context_sha256(relative_path),
            }
        )
    lines = _text_lines(target)
    if lines is None:
        return occurrences
    for line_number, context in enumerate(lines, start=1):
        for pattern, start, _end in _pattern_occurrences(
            context,
            LEGACY_PATTERNS,
        ):
            occurrences.append(
                {
                    "path": relative_path,
                    "pattern": pattern,
                    "source": "content",
                    "line": line_number,
                    "column": start + 1,
                    "context_sha256": occurrence_digest(
                        pattern=pattern, context=context, column=start + 1
                    ),
                }
            )
    identities = {
        (
            occurrence["path"],
            occurrence["source"],
            occurrence.get("line"),
            occurrence["column"],
            occurrence["context_sha256"],
            occurrence["pattern"],
        )
        for occurrence in occurrences
    }
    return [
        occurrence
        for occurrence in occurrences
        if not any(
            occurrence["pattern"] == nested
            and (
                occurrence["path"],
                occurrence["source"],
                occurrence.get("line"),
                occurrence["column"],
                occurrence["context_sha256"],
                broader,
            )
            in identities
            for broader, nested in _SAFE_NESTED_APPROVALS
        )
    ]


def _pinned_selector_inventory(root: Path) -> dict[OccurrenceKey, str]:
    """Load the reviewed exact selector set only when its digest is unchanged."""
    allowlist_path = root / "scripts" / "rebrand-allowlist.json"
    try:
        payload = json.loads(allowlist_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return {}
    selectors: dict[OccurrenceKey, str] = {}
    encoded_identities: list[list[object]] = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or entry.get("category") != "compatibility_alias"
        ):
            continue
        rationale = entry.get("rationale")
        contract = (
            rationale.get("compatibility_contract")
            if isinstance(rationale, dict)
            else None
        )
        key = _selector_key(entry)
        if not isinstance(contract, str) or key is None:
            return {}
        if key[0] in _SEMANTIC_SELECTOR_PATHS:
            continue
        if contract == "persisted-queue-identifier-v1" and not key[0].startswith(
            "tests/"
        ):
            continue
        selectors[key] = contract
        encoded_identities.append([*key, contract])
    encoded_identities.sort(
        key=lambda identity: tuple(
            "" if value is None else str(value) for value in identity
        )
    )
    digest = hashlib.sha256(
        json.dumps(
            encoded_identities,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if digest != _PINNED_SELECTOR_INVENTORY_SHA256:
        return {}
    return selectors


def _frontend_semantic_selectors(
    root: Path,
) -> dict[OccurrenceKey, str]:
    selectors: dict[OccurrenceKey, str] = {}

    features_path = "frontend/src/lib/features.ts"
    features_target = root / features_path
    if features_target.is_file():
        source = features_target.read_text(encoding="utf-8")
        suffixes = {
            "EVIDENCE_STUDIO",
            "MODEL_FLEET",
            "RESEARCH_RUNS",
            "VISUAL_REFRESH",
        }
        expected_names = {
            f"NEXT_PUBLIC_{prefix}_{suffix}"
            for prefix in ("DN", "ONP")
            for suffix in suffixes
        }
        references = re.findall(
            r"process\.env\.(NEXT_PUBLIC_(?:DN|ONP)_[A-Z0-9_]+)",
            source,
        )
        if (
            expected_names.issubset(references)
            and all(references.count(name) == 1 for name in expected_names)
            and {name for name in references if name.split("_")[2] == "ONP"}
            == {name for name in expected_names if name.split("_")[2] == "ONP"}
        ):
            for occurrence in _selector_occurrences_for_path(
                root,
                features_path,
            ):
                key = _selector_key(occurrence)
                if key is not None and occurrence["pattern"] == "ONP_":
                    line = source.splitlines()[int(occurrence["line"]) - 1]
                    if any(
                        f"process.env.NEXT_PUBLIC_ONP_{suffix}" in line
                        for suffix in suffixes
                    ):
                        selectors[key] = "frontend-env-alias-v1"

    theme_path = "frontend/src/lib/theme-storage.ts"
    theme_target = root / theme_path
    if theme_target.is_file():
        source = theme_target.read_text(encoding="utf-8")
        required_operations = {
            "const LEGACY_THEME_KEY = 'onp-theme'",
            "storage.getItem(LEGACY_THEME_KEY)",
            "storage.setItem(CANONICAL_THEME_KEY, legacy)",
            "storage.setItem(LEGACY_THEME_KEY, theme)",
        }
        if all(operation in source for operation in required_operations):
            lines = source.splitlines()
            for occurrence in _selector_occurrences_for_path(root, theme_path):
                key = _selector_key(occurrence)
                if (
                    key is not None
                    and occurrence["pattern"] == "onp-theme"
                    and lines[int(occurrence["line"]) - 1].strip()
                    == "const LEGACY_THEME_KEY = 'onp-theme'"
                ):
                    selectors[key] = "theme-storage-migration-v1"

    canonical_api_path = "frontend/src/lib/api/deeper-notebook.ts"
    canonical_target = root / canonical_api_path
    if canonical_target.is_file():
        source = canonical_target.read_text(encoding="utf-8")
        exact_export = "export const onpFetch = deeperNotebookFetch"
        if source.splitlines().count(exact_export) == 1:
            for occurrence in _selector_occurrences_for_path(
                root,
                canonical_api_path,
            ):
                key = _selector_key(occurrence)
                if key is not None and occurrence["pattern"] == "onpFetch":
                    line = source.splitlines()[int(occurrence["line"]) - 1]
                    if line == exact_export:
                        selectors[key] = "legacy-api-route-v1"

    legacy_api_path = "frontend/src/lib/api/onp.ts"
    legacy_target = root / legacy_api_path
    if legacy_target.is_file():
        source = legacy_target.read_text(encoding="utf-8")
        exact_module_alias = (
            "/** @deprecated Use deeperNotebookFetch from ./deeper-notebook. */\n"
            "export {\n"
            "  deeperNotebookFetch,\n"
            "  deeperNotebookFetch as onpFetch,\n"
            "} from './deeper-notebook'\n"
        )
        if source == exact_module_alias:
            for occurrence in _selector_occurrences_for_path(
                root,
                legacy_api_path,
            ):
                key = _selector_key(occurrence)
                if key is not None and occurrence["pattern"] in {
                    "/api/onp",
                    "onpFetch",
                }:
                    selectors[key] = "legacy-api-route-v1"
    return selectors


def _source_visual_environment_alias_selectors(
    root: Path,
) -> dict[OccurrenceKey, str]:
    """Select only the reviewed Source Visuals alias-contract regression."""
    selectors: dict[OccurrenceKey, str] = {}
    relative_path = "tests/test_environment_aliases.py"
    target = root / relative_path
    if not target.is_file():
        return selectors
    lines = target.read_text(encoding="utf-8").splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line
            == "def test_source_visuals_setting_registers_all_aliases_with_precedence_and_warnings("
        ),
        None,
    )
    if start is None:
        return selectors
    end = next(
        (
            index
            for index, line in enumerate(lines[start + 1 :], start=start + 1)
            if line.startswith("def ")
        ),
        len(lines),
    )
    expected_lines = {
        '        ("OPEN_NOTEBOOK_SOURCE_VISUALS_ENABLED", "0", True),',
        '        ("ONP_SOURCE_VISUALS_ENABLED", "1", True),',
        '        "OPEN_NOTEBOOK_SOURCE_VISUALS_ENABLED",',
        '        "ONP_SOURCE_VISUALS_ENABLED",',
        '        "OPEN_NOTEBOOK_SOURCE_VISUALS_ENABLED": "0",',
        '        "ONP_SOURCE_VISUALS_ENABLED": "1",',
    }
    if any(lines.count(expected) != 1 for expected in expected_lines):
        return selectors
    for occurrence in _selector_occurrences_for_path(root, relative_path):
        key = _selector_key(occurrence)
        line_number = occurrence.get("line")
        if (
            key is not None
            and occurrence["pattern"] in {"OPEN_NOTEBOOK_", "ONP_"}
            and isinstance(line_number, int)
            and line_number - 1 < end
            and lines[line_number - 1] in expected_lines
        ):
            selectors[key] = "env-alias-v1"
    return selectors


def compatibility_selector_inventory(
    root: Path,
) -> dict[OccurrenceKey, str]:
    """Return the closed exact occurrence inventory for compatibility code."""
    root = root.resolve()
    selectors = _pinned_selector_inventory(root)
    semantic_groups: list[tuple[list[dict[str, object]], str]] = [
        (
            production_queue_occurrence_inventory(root),
            "persisted-queue-identifier-v1",
        ),
        (
            _selector_occurrences_for_path(
                root,
                "tests/test_persisted_queue_identifiers.py",
            ),
            "persisted-queue-identifier-v1",
        ),
        (
            _selector_occurrences_for_path(
                root,
                "tests/test_product_identity.py",
            ),
            "rebrand-audit-regression-v1",
        ),
    ]
    semantic = _frontend_semantic_selectors(root)
    semantic.update(_backend_legacy_api_route_selectors(root))
    semantic.update(_source_visual_environment_alias_selectors(root))
    for occurrences, contract in semantic_groups:
        for occurrence in occurrences:
            key = _selector_key(occurrence)
            if key is not None:
                semantic[key] = contract
    for key, contract in semantic.items():
        previous = selectors.setdefault(key, contract)
        if previous != contract:
            raise ValueError(
                "compatibility selector collision requires explicit review"
            )
    return selectors


def compatibility_contract_for_occurrence(
    occurrence: Mapping[str, object],
    *,
    root: Path | None = None,
    selectors: Mapping[OccurrenceKey, str] | None = None,
) -> str | None:
    """Return the contract only for a construct-exact occurrence identity."""
    key = _selector_key(occurrence)
    if key is None:
        return None
    if selectors is None:
        selector_root = (
            root.resolve() if root is not None else Path(__file__).resolve().parents[1]
        )
        selectors = compatibility_selector_inventory(selector_root)
    contract = selectors.get(key)
    if contract is not None:
        return contract
    # Without this the approval survives a reflow but its COMPATIBILITY CONTRACT
    # does not, and load_allowlist aborts with "compatibility entry must use its
    # exact canonical compatibility contract". Measured that way.
    path, pattern, source, _line, _column, digest = key
    for (s_path, s_pattern, s_source, _l, _c, s_digest), value in selectors.items():
        if (s_path, s_pattern, s_source, s_digest) == (path, pattern, source, digest):
            return value
    return None


def load_allowlist(path: Path) -> dict[OccurrenceKey, Approval]:
    """Load approvals pinned to exact path/content occurrences."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("allowlist must be an object")
    if frozenset(payload) != _TOP_LEVEL_FIELDS:
        raise ValueError(
            "allowlist must contain exactly the schema_version, "
            "persisted_queue_identifiers, compatibility_contracts, and "
            "entries fields"
        )
    if payload.get("schema_version") != ALLOWLIST_SCHEMA_VERSION:
        raise ValueError(f"allowlist schema_version must be {ALLOWLIST_SCHEMA_VERSION}")
    persisted_identifiers = payload.get("persisted_queue_identifiers")
    if not isinstance(persisted_identifiers, list):
        raise ValueError("allowlist persisted_queue_identifiers must be a list")
    queue_identifier_fields = {
        "registration": frozenset(
            {"kind", "path", "symbol", "callee", "app", "command"}
        ),
        "submission": frozenset(
            {
                "kind",
                "path",
                "symbol",
                "callee",
                "app",
                "command",
                "invocation",
            }
        ),
        "lookup": frozenset({"kind", "path", "symbol", "callee", "command_id"}),
    }
    for identifier in persisted_identifiers:
        if not isinstance(identifier, dict):
            raise ValueError("each persisted queue identifier must be an object")
        expected_fields = queue_identifier_fields.get(identifier.get("kind"))
        if (
            expected_fields is None
            or frozenset(identifier) != expected_fields
            or not all(
                isinstance(value, str) and value for value in identifier.values()
            )
        ):
            raise ValueError(
                "persisted queue identifiers must use the exact registration, "
                "submission, or lookup field schema"
            )
    raw_contracts = payload.get("compatibility_contracts")
    if not isinstance(raw_contracts, dict):
        raise ValueError("allowlist compatibility_contracts must be an object")
    contracts: dict[str, dict[str, object]] = {}
    root = _allowlist_root(path)
    selectors: Mapping[OccurrenceKey, str] | None = None
    for contract_id, contract in raw_contracts.items():
        if (
            not isinstance(contract_id, str)
            or not contract_id
            or not isinstance(contract, dict)
            or frozenset(contract) != _COMPATIBILITY_CONTRACT_FIELDS
        ):
            raise ValueError(
                "compatibility contracts must use the exact structured fields"
            )
        kind = contract.get("kind")
        owner = contract.get("owner")
        retention_reason = contract.get("retention_reason")
        proof = contract.get("proof")
        scope = contract.get("scope")
        coverage_sha256 = contract.get("coverage_sha256")
        if not all(
            isinstance(value, str) and value
            for value in (
                kind,
                owner,
                retention_reason,
                proof,
                coverage_sha256,
            )
        ) or not isinstance(scope, dict):
            raise ValueError(
                "compatibility contracts must use the exact structured fields"
            )
        assert isinstance(kind, str)
        assert isinstance(retention_reason, str)
        assert isinstance(proof, str)
        assert isinstance(coverage_sha256, str)
        if kind not in COMPATIBILITY_CONTRACT_KINDS:
            raise ValueError(
                "compatibility contract kind must be a closed compatibility "
                "contract kind"
            )
        if len(retention_reason.split()) < 8:
            raise ValueError(
                "compatibility contract requires a meaningful retention reason"
            )
        if not _SHA256_RE.fullmatch(coverage_sha256):
            raise ValueError(
                "compatibility contract coverage digest must be 64 lowercase hex chars"
            )
        _validate_compatibility_proof(root, proof, kind)
        contracts[contract_id] = contract
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("allowlist must contain an 'entries' list")

    allowlist: dict[OccurrenceKey, Approval] = {}
    explanations: set[str] = set()
    referenced_contracts: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each allowlist entry must be an object")
        if frozenset(entry) != _ENTRY_FIELDS:
            raise ValueError(
                "allowlist entries must contain exactly the documented fields"
            )
        allowlisted_path = entry.get("path")
        pattern = entry.get("pattern")
        source = entry.get("source")
        line = entry.get("line")
        column = entry.get("column")
        digest = entry.get("context_sha256")
        category = entry.get("category")
        rationale = entry.get("rationale")
        if not all(
            isinstance(value, str)
            for value in (
                allowlisted_path,
                pattern,
                source,
                digest,
                category,
            )
        ):
            raise ValueError(
                "allowlist path, pattern, source, context_sha256, and category "
                "must be strings"
            )
        if pattern not in LEGACY_PATTERNS:
            raise ValueError(
                "allowlist pattern must be one of the scanner's built-in "
                "legacy patterns"
            )
        if source not in {"path", "content"}:
            raise ValueError("allowlist source must be 'path' or 'content'")
        if source == "content" and (
            not isinstance(line, int) or isinstance(line, bool) or line < 1
        ):
            raise ValueError("content approvals require a positive line")
        if source == "path" and line is not None:
            raise ValueError("path approvals require line=null")
        if not isinstance(column, int) or isinstance(column, bool) or column < 1:
            raise ValueError("allowlist column must be a positive integer")
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("allowlist context_sha256 must be 64 lowercase hex chars")
        if not isinstance(rationale, dict):
            raise ValueError("allowlist rationale must be an object")
        if frozenset(rationale) != _RATIONALE_FIELDS:
            raise ValueError(
                "allowlist rationale must contain exactly path, pattern, "
                "source, line, column, context_sha256, category, and explanation"
            )
        rationale_location = (
            rationale.get("path"),
            rationale.get("source"),
            rationale.get("line"),
            rationale.get("column"),
            rationale.get("context_sha256"),
        )
        if rationale_location != (
            allowlisted_path,
            source,
            line,
            column,
            digest,
        ):
            raise ValueError(
                "allowlist rationale location and context hash must exactly "
                "match its occurrence"
            )
        if rationale.get("pattern") != pattern:
            raise ValueError(
                "allowlist rationale pattern must exactly match its occurrence"
            )
        if rationale.get("category") != category:
            raise ValueError(
                "allowlist rationale category must exactly match its occurrence"
            )
        explanation = rationale.get("explanation")
        if not isinstance(explanation, str):
            raise ValueError("allowlist rationale explanation must be a string")
        normalized_explanation = " ".join(explanation.split())
        casefolded_explanation = normalized_explanation.casefold()
        if (
            len(normalized_explanation) < _MIN_EXPLANATION_CHARS
            or len(normalized_explanation.split()) < _MIN_EXPLANATION_WORDS
            or casefolded_explanation in _GENERIC_EXPLANATIONS
        ):
            raise ValueError(
                "allowlist rationale requires a meaningful explanation of at "
                f"least {_MIN_EXPLANATION_CHARS} characters and "
                f"{_MIN_EXPLANATION_WORDS} words"
            )
        if (
            allowlisted_path.casefold() in casefolded_explanation
            or pattern.casefold() in casefolded_explanation
            or category.casefold() in casefolded_explanation
            or digest.casefold() in casefolded_explanation
            or _STRUCTURAL_LOCATOR_RE.search(normalized_explanation)
            or _MECHANICAL_LOCATOR_RE.search(normalized_explanation)
        ):
            raise ValueError(
                "allowlist rationale explanation must not repeat structural "
                "path, locator, hash, pattern, or category fields"
            )
        duplicate_key = semantic_explanation_key(normalized_explanation)
        if duplicate_key in explanations:
            raise ValueError(
                "allowlist rationale contains a duplicate semantic explanation"
            )
        explanations.add(duplicate_key)
        if category not in CATEGORIES[:-1]:
            raise ValueError(f"invalid allowlist category: {category}")
        compatibility_contract = rationale.get("compatibility_contract")
        if category == "compatibility_alias":
            if (
                not isinstance(compatibility_contract, str)
                or compatibility_contract not in contracts
            ):
                raise ValueError(
                    "compatibility_alias requires a structured compatibility contract"
                )
            if selectors is None:
                selectors = compatibility_selector_inventory(root)
            canonical_contract = compatibility_contract_for_occurrence(
                entry,
                root=root,
                selectors=selectors,
            )
            if canonical_contract != compatibility_contract:
                raise ValueError(
                    "compatibility entry must use its exact canonical "
                    "compatibility contract"
                )
            canonical_definition = _DEFAULT_COMPATIBILITY_CONTRACTS.get(
                canonical_contract
            )
            if (
                canonical_definition is None
                or contracts[compatibility_contract]["kind"]
                != canonical_definition["kind"]
            ):
                raise ValueError(
                    "compatibility entry kind must match its canonical "
                    "compatibility contract kind"
                )
            if _compatibility_is_forbidden(
                root,
                allowlisted_path,
                pattern,
                source,
                line,
                str(contracts[compatibility_contract]["kind"]),
                canonical_contract,
            ):
                raise ValueError(
                    "active UI, documentation, or default identifier cannot "
                    "use compatibility_alias"
                )
            contract_scope = contracts[compatibility_contract]["scope"]
            assert isinstance(contract_scope, dict)
            if (
                allowlisted_path not in contract_scope["paths"]
                or pattern not in contract_scope["patterns"]
                or source not in contract_scope["sources"]
            ):
                raise ValueError(
                    "compatibility occurrence is outside its exact contract scope"
                )
            referenced_contracts.add(compatibility_contract)
        elif compatibility_contract is not None:
            raise ValueError(
                "non-compatibility rationale cannot reference a compatibility contract"
            )
        if "*" in allowlisted_path:
            raise ValueError("allowlist paths must be exact; wildcards are disallowed")
        key: OccurrenceKey = (
            allowlisted_path,
            pattern,
            source,
            line,
            column,
            digest,
        )
        if key in allowlist:
            raise ValueError(f"duplicate allowlist entry: {key}")
        allowlist[key] = Approval(
            category=category,
            rationale=Rationale(
                path=allowlisted_path,
                pattern=pattern,
                source=source,
                line=line,
                column=column,
                context_sha256=digest,
                category=category,
                explanation=normalized_explanation,
                compatibility_contract=compatibility_contract,
            ),
        )
    unused_contracts = set(contracts) - referenced_contracts
    if unused_contracts:
        raise ValueError(
            "allowlist contains unused compatibility contracts: "
            f"{sorted(unused_contracts)}"
        )
    for contract_id, contract in contracts.items():
        kind = contract["kind"]
        scope = contract["scope"]
        assert isinstance(kind, str)
        assert isinstance(scope, dict)
        _validate_contract_scope(kind, scope)
        expected_digest = contract["coverage_sha256"]
        actual_digest = compatibility_coverage_digest(entries, contract_id)
        if expected_digest != actual_digest:
            raise ValueError(
                "compatibility contract coverage digest does not match its "
                f"exact occurrences: {contract_id}"
            )
    return allowlist


def _tracked_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [
        raw_path.decode("utf-8", errors="surrogateescape")
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    ]


def _text_lines(path: Path) -> list[str] | None:
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _pattern_occurrences(
    value: str,
    patterns: tuple[str, ...],
) -> list[tuple[str, int, int]]:
    occurrences: list[tuple[str, int, int]] = []
    for pattern in patterns:
        if not pattern:
            continue
        start = 0
        while (match_start := value.find(pattern, start)) != -1:
            match_end = match_start + len(pattern)
            occurrences.append((pattern, match_start, match_end))
            start = match_start + 1
    return sorted(
        occurrences,
        key=lambda occurrence: (
            occurrence[1],
            -(occurrence[2] - occurrence[1]),
            occurrence[0],
        ),
    )


def audit_repository(
    root: Path,
    allowlist: Allowlist,
    *,
    selectors: Mapping[OccurrenceKey, str] | None = None,
) -> dict[str, object]:
    """Scan tracked path names and UTF-8 text contents for legacy references."""
    root = root.resolve()
    selector_inventory = (
        selectors if selectors is not None else compatibility_selector_inventory(root)
    )
    categorized: dict[str, list[dict[str, object]]] = {
        category: [] for category in CATEGORIES
    }
    matched_allowlist: set[OccurrenceKey] = set()

    def audited_category(
        key: OccurrenceKey,
        occurrence: Mapping[str, object],
    ) -> str:
        category = classify_match(key, allowlist)
        approval = allowlist.get(key)
        if approval is None or category != "compatibility_alias":
            return category
        canonical_contract = compatibility_contract_for_occurrence(
            occurrence,
            root=root,
            selectors=selector_inventory,
        )
        if canonical_contract != approval.rationale.compatibility_contract:
            return "unexpected_active_identity"
        return category

    def record(
        *,
        path: str,
        pattern: str,
        source: str,
        line: int | None,
        column: int,
        context: str,
    ) -> None:
        key = _occurrence_key(
            path=path,
            pattern=pattern,
            source=source,
            line=line,
            column=column,
            context=context,
        )
        entry: dict[str, object] = {
            "path": path,
            "pattern": pattern,
            "source": source,
            "column": column,
            "context_sha256": key[-1],
        }
        if line is not None:
            entry["line"] = line
        category = audited_category(key, entry)
        categorized[category].append(entry)
        if key in allowlist and category != "unexpected_active_identity":
            matched_allowlist.add(key)

    for relative_path in _tracked_paths(root):
        # The allowlist contains hashes of audited source lines. Auditing its
        # own serialized entries would make those hashes self-referential and
        # impossible to stabilize. Its structure and every field are instead
        # validated by ``load_allowlist`` before repository scanning begins.
        if relative_path in _AUDIT_METADATA_PATHS:
            continue
        patterns = patterns_for_path(relative_path, allowlist)
        for pattern, start, _end in _pattern_occurrences(relative_path, patterns):
            record(
                path=relative_path,
                pattern=pattern,
                source="path",
                line=None,
                column=start + 1,
                context=relative_path,
            )

        absolute_path = root / relative_path
        if not absolute_path.is_file():
            continue
        lines = _text_lines(absolute_path)
        if lines is None:
            continue
        for line_number, line in enumerate(lines, start=1):
            occurrences = _pattern_occurrences(line, patterns)
            allowed_contexts = [
                (pattern, start, end)
                for pattern, start, end in occurrences
                if audited_category(
                    _occurrence_key(
                        path=relative_path,
                        pattern=pattern,
                        source="content",
                        line=line_number,
                        column=start + 1,
                        context=line,
                    ),
                    {
                        "path": relative_path,
                        "pattern": pattern,
                        "source": "content",
                        "line": line_number,
                        "column": start + 1,
                        "context_sha256": occurrence_digest(
                            pattern=pattern, context=line, column=start + 1
                        ),
                    },
                )
                != "unexpected_active_identity"
            ]
            for pattern, start, end in occurrences:
                if any(
                    (context_pattern, pattern) in _SAFE_NESTED_APPROVALS
                    and context_start <= start
                    and end <= context_end
                    for context_pattern, context_start, context_end in allowed_contexts
                ):
                    continue
                record(
                    path=relative_path,
                    pattern=pattern,
                    source="content",
                    line=line_number,
                    column=start + 1,
                    context=line,
                )

    stale = [
        {
            "path": key[0],
            "pattern": key[1],
            "source": key[2],
            "line": key[3],
            "column": key[4],
            "context_sha256": key[5],
            "category": approval.category,
            "rationale": approval.rationale.as_dict(),
        }
        for key, approval in allowlist.items()
        if key not in matched_allowlist
    ]
    return {
        "schema_version": ALLOWLIST_SCHEMA_VERSION,
        "categories": categorized,
        "summary": {
            category: len(matches) for category, matches in categorized.items()
        },
        "stale_allowlist": stale,
    }


def _without_safe_nested_matches(
    matches: list[dict[str, object]],
) -> list[dict[str, object]]:
    identities = {
        (
            match["path"],
            match["source"],
            match.get("line"),
            match["column"],
            match["context_sha256"],
            match["pattern"],
        )
        for match in matches
    }
    return [
        match
        for match in matches
        if not any(
            match["pattern"] == nested
            and (
                match["path"],
                match["source"],
                match.get("line"),
                match["column"],
                match["context_sha256"],
                broader,
            )
            in identities
            for broader, nested in _SAFE_NESTED_APPROVALS
        )
    ]


def regenerate_allowlist(
    root: Path,
    allowlist_path: Path,
) -> dict[str, object]:
    """Regenerate exact approvals and semantic rationales deterministically."""
    root = root.resolve()
    payload = json.loads(allowlist_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("allowlist must be an object")
    persisted_identifiers = payload.get("persisted_queue_identifiers")
    entries = payload.get("entries")
    if not isinstance(persisted_identifiers, list) or not isinstance(
        entries,
        list,
    ):
        raise ValueError(
            "allowlist regeneration requires persisted identifiers and entries"
        )
    persisted_identifiers = production_queue_inventory(root)
    selectors = compatibility_selector_inventory(root)

    category_sets: dict[tuple[str, str], set[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("allowlist regeneration entries must be objects")
        path = entry.get("path")
        pattern = entry.get("pattern")
        category = entry.get("category")
        if not all(isinstance(value, str) for value in (path, pattern, category)):
            raise ValueError(
                "allowlist regeneration entries require path, pattern, and category"
            )
        category_sets.setdefault((path, pattern), set()).add(category)
    split_category_policy_keys = {
        (".github/ISSUE_TEMPLATE/installation_issue.yml", "open_notebook"),
        ("docs/recreation/PROJECT-DEEP-DIVE.md", "Open Notebook Plus"),
    }
    split_category_policy = {
        (
            entry["path"],
            entry["pattern"],
            entry["source"],
            entry.get("line"),
            entry["column"],
            entry["context_sha256"],
        ): entry["category"]
        for entry in entries
        if (entry["path"], entry["pattern"]) in split_category_policy_keys
    }
    ambiguous = {
        key: categories
        for key, categories in category_sets.items()
        if len(categories) != 1 and key not in split_category_policy_keys
    }
    if ambiguous:
        raise ValueError(f"ambiguous category policies: {ambiguous}")
    category_policy = {
        key: next(iter(categories))
        for key, categories in category_sets.items()
        if key not in split_category_policy_keys
    }
    category_policy.update(_CATEGORY_OVERRIDES)

    report = audit_repository(root, {}, selectors=selectors)
    raw_matches = report["categories"]["unexpected_active_identity"]
    assert isinstance(raw_matches, list)
    matches = _without_safe_nested_matches(raw_matches)
    generated_entries: list[dict[str, object]] = []
    semantic_keys: set[str] = set()
    used_contracts: set[str] = set()
    uncontracted_groups: dict[tuple[str, str], int] = {}
    for match in sorted(
        matches,
        key=lambda item: (
            item["path"],
            item["source"],
            item.get("line") or 0,
            item["column"],
            item["pattern"],
        ),
    ):
        policy_key = (match["path"], match["pattern"])
        compose_service_occurrence = (
            compatibility_contract_for_occurrence(
                match,
                root=root,
                selectors=selectors,
            )
            == "compose-service-identifier-v1"
        )
        if policy_key in split_category_policy_keys:
            split_key = (
                match["path"],
                match["pattern"],
                match["source"],
                match.get("line"),
                match["column"],
                match["context_sha256"],
            )
            category = split_category_policy.get(split_key)
            if category is None:
                raise ValueError(
                    "ambiguous split-category occurrence requires exact review: "
                    f"{match}"
                )
        else:
            category = (
                "compatibility_alias"
                if compose_service_occurrence
                else category_policy.get(policy_key)
            )
        match_path = Path(str(match["path"]))
        if category is None:
            if (
                compatibility_contract_for_occurrence(
                    match,
                    root=root,
                    selectors=selectors,
                )
                is not None
            ):
                category = "compatibility_alias"
            elif match_path.suffix.lower() in {".md", ".mdx", ".rst"}:
                category = "migration_documentation"
            elif match["path"] == "scripts/rebrand_audit.py":
                category = "migration_documentation"
            elif str(match["path"]).startswith("output/"):
                category = "historical_reference"
            else:
                raise ValueError(
                    f"unclassified occurrence requires explicit review: {match}"
                )
        if category == "compatibility_alias":
            if match_path.suffix.lower() in {".md", ".mdx", ".rst"}:
                category = "migration_documentation"
            elif match["path"] == "scripts/rebrand_audit.py":
                category = "migration_documentation"
            elif str(match["path"]).startswith("output/"):
                category = "historical_reference"
            elif (
                match["path"] in _UPSTREAM_REFERENCE_PATHS
                and match["pattern"] == "open_notebook"
                and not compose_service_occurrence
            ):
                category = "upstream_reference"
        compatibility_contract: str | None = None
        if category == "compatibility_alias":
            compatibility_contract = (
                "compose-service-identifier-v1"
                if compose_service_occurrence
                else compatibility_contract_for_occurrence(
                    match,
                    root=root,
                    selectors=selectors,
                )
            )
            if compatibility_contract is None:
                path_group = str(match["path"]).split("/", 1)[0]
                group_key = (path_group, str(match["pattern"]))
                uncontracted_groups[group_key] = (
                    uncontracted_groups.get(group_key, 0) + 1
                )
                continue
            used_contracts.add(compatibility_contract)
        explanation = semantic_explanation_for_occurrence(
            root,
            match,
            category,
        )
        semantic_key = semantic_explanation_key(explanation)
        if semantic_key in semantic_keys:
            raise ValueError(
                f"semantic rationale generation produced a duplicate: {explanation}"
            )
        semantic_keys.add(semantic_key)
        line = match.get("line")
        rationale = {
            "path": match["path"],
            "pattern": match["pattern"],
            "source": match["source"],
            "line": line,
            "column": match["column"],
            "context_sha256": match["context_sha256"],
            "category": category,
            "explanation": explanation,
            "compatibility_contract": compatibility_contract,
        }
        generated_entries.append(
            {
                "path": match["path"],
                "pattern": match["pattern"],
                "source": match["source"],
                "line": line,
                "column": match["column"],
                "context_sha256": match["context_sha256"],
                "category": category,
                "rationale": rationale,
            }
        )
    if uncontracted_groups:
        grouped = [
            {
                "path_group": path_group,
                "pattern": pattern,
                "count": count,
            }
            for (path_group, pattern), count in sorted(
                uncontracted_groups.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        raise ValueError(
            "uncontracted compatibility groups require review: "
            + json.dumps(grouped, sort_keys=True)
        )
    generated: dict[str, object] = {
        "schema_version": ALLOWLIST_SCHEMA_VERSION,
        "persisted_queue_identifiers": persisted_identifiers,
        "compatibility_contracts": _materialize_compatibility_contracts(
            generated_entries,
            used_contracts,
        ),
        "entries": generated_entries,
    }
    allowlist_path.write_text(
        json.dumps(generated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return generated


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the script's parent repository)",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path(__file__).with_name("rebrand-allowlist.json"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail for unexpected active identity or stale allowlist entries",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="rewrite exact approvals with deterministic semantic rationales",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.regenerate:
        regenerate_allowlist(args.root.resolve(), args.allowlist)
    allowlist = load_allowlist(args.allowlist)
    report = audit_repository(args.root.resolve(), allowlist)
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.check:
        categories = report["categories"]
        assert isinstance(categories, dict)
        if categories["unexpected_active_identity"] or report["stale_allowlist"]:
            return 1
    return 0


def _backend_legacy_api_route_selectors(
    root: Path,
) -> dict[OccurrenceKey, str]:
    """Select only the reviewed hidden legacy API route compatibility seams."""
    selectors: dict[OccurrenceKey, str] = {}
    legacy_api_namespace = "/" + "api/" + "onp"
    legacy_path_fragment = "/" + "onp" + "/"
    exact_lines = {
        "api/main.py": {
            (
                "    # polls "
                f"{legacy_api_namespace}/gmail/status on mount; first cold call against the"
            ): {legacy_api_namespace, legacy_path_fragment},
        },
        "tests/test_vault_api.py": {
            (
                "    assert test_client.get("
                f'"{legacy_api_namespace}/vaults").status_code == 404'
            ): {legacy_api_namespace, legacy_path_fragment},
        },
    }
    for path, lines in exact_lines.items():
        target = root / path
        if not target.is_file():
            continue
        source_lines = target.read_text(encoding="utf-8").splitlines()
        for expected_line, patterns in lines.items():
            if source_lines.count(expected_line) != 1:
                continue
            for occurrence in _selector_occurrences_for_path(root, path):
                key = _selector_key(occurrence)
                line_number = occurrence.get("line")
                if (
                    key is not None
                    and occurrence["pattern"] in patterns
                    and isinstance(line_number, int)
                    and source_lines[line_number - 1] == expected_line
                ):
                    selectors[key] = "legacy-api-route-v1"
    main_path = "api/main.py"
    main_target = root / main_path
    if main_target.is_file():
        main_lines = main_target.read_text(encoding="utf-8").splitlines()
        expected_router_line = "    " + "gmail_router.router,"
        for occurrence in _selector_occurrences_for_path(root, main_path):
            key = _selector_key(occurrence)
            line_number = occurrence.get("line")
            if (
                key is not None
                and occurrence["pattern"] == legacy_api_namespace
                and isinstance(line_number, int)
                and line_number > 1
                and main_lines[line_number - 2] == expected_router_line
                and main_lines[line_number - 1]
                == f'    prefix="{legacy_api_namespace}",'
            ):
                selectors[key] = "legacy-api-route-v1"
    return selectors


if __name__ == "__main__":
    raise SystemExit(main())
