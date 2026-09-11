.PHONY: run frontend check ruff database lint api start-all stop-all status clean-cache worker worker-start worker-stop worker-restart backup restore verify-backup test test-integration
.PHONY: docker-buildx-prepare docker-buildx-clean docker-buildx-reset
.PHONY: docker-push docker-push-latest docker-release docker-build-local tag export-docs

# Get version from pyproject.toml
VERSION := $(shell grep -m1 version pyproject.toml | cut -d'"' -f2)

# Image names for both registries
DOCKERHUB_IMAGE := lfnovo/open_notebook
GHCR_IMAGE := ghcr.io/lfnovo/open-notebook

# Build platforms
PLATFORMS := linux/amd64,linux/arm64

database:
	docker compose up -d surrealdb

run:
	@echo "⚠️  Warning: Starting frontend only. For full functionality, use 'make start-all'"
	cd frontend && npm run dev

frontend:
	cd frontend && npm run dev

lint:
	uv run python -m mypy .

ruff:
	ruff check . --fix

# === Docker Build Setup ===
docker-buildx-prepare:
	@docker buildx inspect multi-platform-builder >/dev/null 2>&1 || \
		docker buildx create --use --name multi-platform-builder --driver docker-container
	@docker buildx use multi-platform-builder

docker-buildx-clean:
	@echo "🧹 Cleaning up buildx builders..."
	@docker buildx rm multi-platform-builder 2>/dev/null || true
	@docker ps -a | grep buildx_buildkit | awk '{print $$1}' | xargs -r docker rm -f 2>/dev/null || true
	@echo "✅ Buildx cleanup complete!"

docker-buildx-reset: docker-buildx-clean docker-buildx-prepare
	@echo "✅ Buildx reset complete!"

# === Docker Build Targets ===

# Build production image for local platform only (no push)
docker-build-local:
	@echo "🔨 Building production image locally ($(shell uname -m))..."
	docker build \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(DOCKERHUB_IMAGE):local \
		.
	@echo "✅ Built $(DOCKERHUB_IMAGE):$(VERSION) and $(DOCKERHUB_IMAGE):local"
	@echo "Run with: docker run -p 5055:5055 -p 3000:3000 $(DOCKERHUB_IMAGE):local"

# Build and push version tags ONLY (no latest) for both regular and single images
docker-push: docker-buildx-prepare
	@echo "📤 Building and pushing version $(VERSION) to both registries..."
	@echo "🔨 Building regular image..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(GHCR_IMAGE):$(VERSION) \
		--push \
		.
	@echo "🔨 Building single-container image..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		-f Dockerfile.single \
		-t $(DOCKERHUB_IMAGE):$(VERSION)-single \
		-t $(GHCR_IMAGE):$(VERSION)-single \
		--push \
		.
	@echo "✅ Pushed version $(VERSION) to both registries (latest NOT updated)"
	@echo "  📦 Docker Hub:"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION)"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION)-single"
	@echo "  📦 GHCR:"
	@echo "    - $(GHCR_IMAGE):$(VERSION)"
	@echo "    - $(GHCR_IMAGE):$(VERSION)-single"

# Update v1-latest tags to current version (both regular and single images)
docker-push-latest: docker-buildx-prepare
	@echo "📤 Updating v1-latest tags to version $(VERSION)..."
	@echo "🔨 Building regular image with latest tag..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		-t $(DOCKERHUB_IMAGE):$(VERSION) \
		-t $(DOCKERHUB_IMAGE):v1-latest \
		-t $(GHCR_IMAGE):$(VERSION) \
		-t $(GHCR_IMAGE):v1-latest \
		--push \
		.
	@echo "🔨 Building single-container image with latest tag..."
	docker buildx build --pull \
		--platform $(PLATFORMS) \
		--progress=plain \
		-f Dockerfile.single \
		-t $(DOCKERHUB_IMAGE):$(VERSION)-single \
		-t $(DOCKERHUB_IMAGE):v1-latest-single \
		-t $(GHCR_IMAGE):$(VERSION)-single \
		-t $(GHCR_IMAGE):v1-latest-single \
		--push \
		.
	@echo "✅ Updated v1-latest to version $(VERSION)"
	@echo "  📦 Docker Hub:"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION) → v1-latest"
	@echo "    - $(DOCKERHUB_IMAGE):$(VERSION)-single → v1-latest-single"
	@echo "  📦 GHCR:"
	@echo "    - $(GHCR_IMAGE):$(VERSION) → v1-latest"
	@echo "    - $(GHCR_IMAGE):$(VERSION)-single → v1-latest-single"

# Full release: push version AND update latest tags
docker-release: docker-push-latest
	@echo "✅ Full release complete for version $(VERSION)"

tag:
	@version=$$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'); \
	echo "Creating tag v$$version"; \
	git tag "v$$version"; \
	git push origin "v$$version"


# v0.7.140 — Both `dev` and `full` previously referenced compose files
# (`docker-compose.dev.yml`, `docker-compose.full.yml`) that do not
# exist in the repo. Only `docker-compose.yml` ships. Running either
# target failed with:
#     open docker-compose.dev.yml: no such file or directory
# Switched both to the actual file. Until/unless the dev / full
# variants are reintroduced they're aliases of the same single
# compose file.
dev:
	docker compose -f docker-compose.yml up --build

full:
	docker compose -f docker-compose.yml up --build


api:
	uv run --env-file .env run_api.py

# v0.7.126 — Backup + restore targets.
#
# `make backup`           — snapshot the data directory to a timestamped
#                            tarball at backups/onp-backup-YYYYMMDD-HHMMSS.tar.gz
# `make backup OUT=path`  — write to a specific path
# `make verify-backup BUNDLE=path`
#                          — re-hash everything inside a bundle against its
#                            manifest, without touching the data dir
# `make restore BUNDLE=path`
#                          — restore from a bundle. REFUSES if data dir is
#                            non-empty unless FORCE=1 is also set.
#
# All three honor DEEPER_NOTEBOOK_DATA_DIR so users with a custom install path don't
# need to think about which directory to back up.

OUT ?= backups/onp-backup-$(shell date +%Y%m%d-%H%M%S).tar.gz

backup:
	@mkdir -p $(dir $(OUT))
	@echo "Creating backup at $(OUT)..."
	@uv run --env-file .env python scripts/backup_restore.py backup --output $(OUT)
	@echo "✅ Backup complete: $(OUT)"

verify-backup:
	@if [ -z "$(BUNDLE)" ]; then \
		echo "Usage: make verify-backup BUNDLE=path/to/backup.tar.gz"; \
		exit 1; \
	fi
	@uv run --env-file .env python scripts/backup_restore.py restore $(BUNDLE) --verify-only

restore:
	@if [ -z "$(BUNDLE)" ]; then \
		echo "Usage: make restore BUNDLE=path/to/backup.tar.gz [FORCE=1]"; \
		echo ""; \
		echo "DANGER: restoring overwrites the current data directory."; \
		echo "        Run \`make verify-backup BUNDLE=...\` first to confirm integrity."; \
		exit 1; \
	fi
	@if [ "$(FORCE)" = "1" ]; then \
		echo "⚠️  FORCE=1 — will overwrite any existing data."; \
		uv run --env-file .env python scripts/backup_restore.py restore $(BUNDLE) --force; \
	else \
		uv run --env-file .env python scripts/backup_restore.py restore $(BUNDLE); \
	fi
	@echo "✅ Restore complete from: $(BUNDLE)"

## v0.7.129 — test runners.
## `make test`             runs the hermetic backend suite (no external deps).
## `make test-integration` runs the SurrealDB-backed integration suite. You
##                         MUST have SurrealDB up — `make database` first —
##                         and the test fixtures will mint a throwaway
##                         namespace (onp_test_<uuid>) so your real data is
##                         untouched.

test:
	uv run pytest tests/ -v --ignore=tests/integration

test-integration:
	@echo "Running integration tests against SurrealDB at $${SURREAL_URL:-ws://localhost:8000/rpc}..."
	@echo "Tests use a throwaway namespace; your real data is not touched."
	SURREAL_INTEGRATION=1 uv run --env-file .env pytest tests/integration/ -v -m integration_surreal

## v0.7.139 — Live model benchmark harness.
##
## Exercises every configured language Model against three real probes
## (notebook chat, Studio JSON-outline, podcast multi-speaker turn) and
## writes a ranked benchmark-report.md.
##
## Requires services running:
##   1. make database     (SurrealDB)
##   2. make api          (FastAPI :5055)
##   3. make worker       (surreal-commands worker)
## Then:
##   make benchmark-models
##
## Single-model run:
##   DEEPER_NOTEBOOK_BENCHMARK_ONLY="My OpenAI gpt-4o-mini" make benchmark-models
##
## Custom per-call timeout (default 90s):
##   DEEPER_NOTEBOOK_BENCHMARK_PER_CALL_TIMEOUT_SEC=180 make benchmark-models
.PHONY: benchmark-models
benchmark-models:
	@echo "Benchmarking models at $${DEEPER_NOTEBOOK_BENCHMARK_API_BASE:-http://localhost:5055}..."
	@echo "Requires API + worker + SurrealDB to be running. Use \`make status\` to verify."
	uv run --env-file .env python scripts/benchmark_models.py --output benchmark-report.md
	@echo ""
	@echo "Report: ./benchmark-report.md"

.PHONY: worker worker-start worker-stop worker-restart

worker: worker-start

worker-start:
	@echo "Starting surreal-commands worker..."
	DEEPER_NOTEBOOK_WORKER_PROCESS=1 uv run --env-file .env surreal-commands-worker --import-modules commands

worker-stop:
	@echo "Stopping surreal-commands worker..."
	pkill -f "surreal-commands-worker" || true

worker-restart: worker-stop
	@sleep 2
	@$(MAKE) worker-start

# === Service Management ===
start-all:
	@echo "🚀 Starting Deeper Notebook (Database + API + Worker + Frontend)..."
	@echo "📊 Starting SurrealDB..."
	# v0.7.140 — was docker-compose.dev.yml (didn't exist).
	@docker compose -f docker-compose.yml up -d surrealdb
	# v0.7.140 — poll SurrealDB /health instead of a flat sleep 3s.
	# Cold-start with a fresh volume can exceed 3s on slower disks;
	# the polling loop bails after 30s with a clear error rather
	# than letting the API fail its first migration silently.
	@echo -n "   Waiting for SurrealDB"
	@for i in $$(seq 1 30); do \
		if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then \
			echo " ✓"; break; \
		fi; \
		echo -n "."; \
		sleep 1; \
		if [ "$$i" = "30" ]; then \
			echo ""; echo "❌ SurrealDB did not become ready within 30s"; \
			docker compose -f docker-compose.yml logs surrealdb | tail -20; \
			exit 1; \
		fi; \
	done
	@echo "🔧 Starting API backend..."
	# v0.7.140 — added --env-file .env (was missing). Without it,
	# run_api.py didn't see DEEPER_NOTEBOOK_ENCRYPTION_KEY +
	# SURREAL_URL / SURREAL_PASSWORD from .env (which the worker
	# line below already loads correctly). Symptom: API came up
	# but credential decryption failed on the first auth call.
	@uv run --env-file .env run_api.py &
	@sleep 3
	@echo "⚙️ Starting background worker..."
	@DEEPER_NOTEBOOK_WORKER_PROCESS=1 uv run --env-file .env surreal-commands-worker --import-modules commands &
	@sleep 2
	@echo "🌐 Starting Next.js frontend..."
	@echo "✅ All services started!"
	@echo "📱 Frontend: http://localhost:3000"
	@echo "🔗 API: http://localhost:5055"
	@echo "📚 API Docs: http://localhost:5055/docs"
	cd frontend && npm run dev

stop-all:
	@echo "🛑 Stopping all Deeper Notebook services..."
	@pkill -f "next dev" || true
	@pkill -f "surreal-commands-worker" || true
	@pkill -f "run_api.py" || true
	@pkill -f "uvicorn api.main:app" || true
	@docker compose down
	@echo "✅ All services stopped!"

status:
	@echo "📊 Deeper Notebook Service Status:"
	@echo "Database (SurrealDB):"
	@docker compose ps surrealdb 2>/dev/null || echo "  ❌ Not running"
	@echo "API Backend:"
	@pgrep -f "run_api.py\|uvicorn api.main:app" >/dev/null && echo "  ✅ Running" || echo "  ❌ Not running"
	@echo "Background Worker:"
	@pgrep -f "surreal-commands-worker" >/dev/null && echo "  ✅ Running" || echo "  ❌ Not running"
	@echo "Next.js Frontend:"
	@pgrep -f "next dev" >/dev/null && echo "  ✅ Running" || echo "  ❌ Not running"

# === Documentation Export ===
export-docs:
	@echo "📚 Exporting documentation..."
	@uv run python scripts/export_docs.py
	@echo "✅ Documentation export complete!"

# === Desktop build (mirrors .github/workflows/build-desktop.yml) ===
#
# Builds the macOS .app + .dmg locally. Mirrors CI exactly so what you build
# here is what GitHub Actions would build on a tag push.
#
# One-shot:
#   make build-mac                      # full build, produces dist/Deeper Notebook.app + .dmg
#
# Iterative (re-run individual stages):
#   make build-mac-venv                 # set up .build-venv with pinned deps
#   make build-mac-frontend             # Next.js build into frontend/.next
#   make build-mac-runtimes             # fetch surreal/node/uv/python-build-standalone
#   make build-mac-pyinstaller          # run pyinstaller spec
#   make build-mac-dmg                  # wrap .app into .dmg via hdiutil
#
# Tear-down:
#   make build-mac-clean                # remove dist/, build/, .build-venv/
#   make build-mac-distclean            # also remove the fetched runtimes
#
# Override the Python interpreter (default: python3.12 from PATH):
#   make build-mac BUILD_PYTHON=/opt/homebrew/bin/python3.12
#
# To install the .app after building:
#   make build-mac-install              # copies dist/Deeper\ Notebook.app → /Applications

.PHONY: build-mac build-mac-test build-mac-venv build-mac-frontend build-mac-runtimes build-mac-pyinstaller build-mac-dmg build-mac-clean build-mac-distclean build-mac-install

BUILD_PYTHON ?= python3.12
BUILD_VENV   := .build-venv
BUILD_PIP    := $(BUILD_VENV)/bin/pip
BUILD_PY     := $(BUILD_VENV)/bin/python
BUILD_PYINSTALLER := $(BUILD_VENV)/bin/pyinstaller

# Detect CPU arch (arm64 vs x86_64) — drives the DMG filename.
# Canonical artifact contract: dist/Deeper-Notebook-mac-<arch>.dmg
BUILD_ARCH := $(shell uname -m)

# v0.8.67k — codesigning identity for the bundle re-seal. Defaults to '-'
# (ad-hoc, no cert needed) so nothing changes for a plain build. Ad-hoc
# signatures give the app a NEW identity on every rebuild, which makes macOS
# reset its TCC (Files & Folders) permissions each time — the cause of the
# iCloud/Desktop "scandir wedge" seen in the field. Set a STABLE identity to
# fix that: run `bash scripts/create-signing-identity.sh` once, then build with
#   make build-mac DEEPER_NOTEBOOK_CODESIGN_IDENTITY="Deeper Notebook Local"
DEEPER_NOTEBOOK_CODESIGN_IDENTITY ?= -

build-mac: build-mac-test build-mac-lock build-mac-venv build-mac-frontend build-mac-runtimes build-mac-pyinstaller build-mac-dmg
	@echo ""
	@echo "✅ macOS build complete:"
	@echo "    dist/Deeper Notebook.app"
	@echo "    dist/Deeper-Notebook-mac-$(BUILD_ARCH).dmg"
	@echo ""
	@echo "Run with:  open 'dist/Deeper Notebook.app'"
	@echo "Tail logs: tail -F ~/.deeper-notebook/logs/*.log"

# Stage 0: precondition — fast unit suite. Catches regressions before we
# spend 15+ min on a build that's going to be DOA. Runs desktop tests in the
# prepared Python 3.12 desktop build environment. P2-MED-12 audit fix.
build-mac-test: build-mac-venv
	@# v0.8.85 — preflight: the backend gate includes a repair-script test that
	@# CANNOT pass while the app or its SurrealDB is running. Before this check
	@# it failed ~5 minutes into the suite with an opaque assertion; now the
	@# build refuses up front with the actual remedy.
	@if pgrep -f '/Applications/Deeper Notebook.app/Contents/MacOS' >/dev/null 2>&1 \
	  || pgrep -f 'surreal-darwin' >/dev/null 2>&1; then \
	  echo "❌ Deeper Notebook (or its SurrealDB sidecar) is running."; \
	  echo "   Quit the app fully, then re-run make build-mac."; \
	  exit 1; \
	fi
	@echo "🧪 Running unit tests (precondition for build-mac)…"
	# v0.8.66 (audit I-M1) — DON'T pipe to `tail`: a piped recipe's exit status
	# is the LAST command's (tail, always 0), so a failing test suite could NOT
	# fail the build (the "Stage 0 precondition" was toothless). Run pytest
	# directly so its non-zero exit aborts `build-mac`.
	@$(BUILD_PY) -m pytest desktop/tests/ desktop/memory/tests/ -q
	@# v0.8.67k — ALSO gate the backend suite. Previously the precondition ran
	@# only desktop/tests/, so a regression in api/ or deeper_notebook/ (e.g. the
	@# chat-stream overflow handling) could ship in a build with zero coverage.
	@# Run the backend suite via the repo .venv (uv run, py3.12) the same way
	@# `make test` does; integration tests (need a live SurrealDB) stay excluded.
	@#
	@# v0.8.85 — retry failures ONCE. Three timing-scaled tests (projection
	@# budget, logseq scaling, plus frontend cousins) flake under heavy machine
	@# load (Backblaze syncing build artifacts pushed load past 20) and cost
	@# three consecutive builds in one day; each passed in isolation. A rerun
	@# of ONLY the failed subset keeps the gate honest — a deterministic
	@# failure still fails twice — while a load blip no longer kills a 25-min
	@# build. `--last-failed-no-failures none` makes the retry a no-op when
	@# the first pass was green.
	@echo "🧪 Running backend tests (precondition for build-mac)…"
	@uv run pytest tests/ -q --ignore=tests/integration || \
	  { echo "⚠️  Backend failures — retrying only the failed tests once…"; \
	    uv run pytest tests/ -q --ignore=tests/integration \
	      --last-failed --last-failed-no-failures none; }

# v0.7.141 — Stage 0.5: regenerate desktop/requirements.lock from
# pyproject.toml BEFORE the bundle venv installs against it.
#
# Why this exists: prior to v0.7.141, desktop/requirements.lock was
# hand-maintained — last touched in commit 90fbf8e (pre-v0.7.124).
# Any dep added to pyproject.toml between regen and bundle build
# was silently dropped from the bundle. v0.7.124 added
# `prometheus-client>=0.20.0` but the lockfile was never refreshed,
# so the bundled venv installed without it, the API crashed at
# import time ("ModuleNotFoundError: No module named 'prometheus_client'"),
# and the launcher timed out waiting for /readyz.
#
# The user-facing symptom: the desktop app opens, shows
# a splash, then silently quits after ~3 minutes with no UI ever
# appearing.
#
# Now: every `make build-mac` regenerates the lock from current
# pyproject.toml AND desktop/requirements.txt first. The header in
# desktop/requirements.lock already documents the canonical command —
# we just promote it to a Makefile target so it actually runs.
#
# v0.7.154 — Added `desktop/requirements.txt` as a second compile
# input. Background: v0.7.141 introduced this target compiling
# only from pyproject.toml, which silently DROPPED any dep that
# was declared only in desktop/requirements.txt (CI's
# "installs on top of the upstream pyproject.toml" path). The
# casualty was `llama-cpp-python>=0.3.16,<0.4` (desktop/requirements.txt:18,
# pinned for CVE-2024-42479) — the lockfile shipped without it,
# the bundled venv installed without it, and every local-GGUF chat
# attempt got `ModuleNotFoundError: No module named 'llama_cpp'`
# at llama_cpp.server spawn (visible in llamacpp_chat_stderr.log
# now that v0.7.151 captures stderr). Passing BOTH files to
# `uv pip compile` merges the dep sets exactly the way
# `pip install -r requirements.txt` would have at runtime.
build-mac-lock:
	@echo "🔒 Regenerating desktop/requirements.lock from pyproject.toml + desktop/requirements.txt..."
	@uv pip compile pyproject.toml desktop/requirements.txt --python-version 3.12 --universal \
		-o desktop/requirements.lock --quiet
	@echo "   Lockfile: $$(wc -l < desktop/requirements.lock) pinned packages"

# Stage 1: isolated build venv with pinned deps (separate from .venv used for tests).
build-mac-venv:
	@if [ ! -d "$(BUILD_VENV)" ]; then \
		echo "🐍 Creating $(BUILD_VENV) with $(BUILD_PYTHON)..."; \
		$(BUILD_PYTHON) -m venv $(BUILD_VENV); \
	fi
	@echo "📦 Installing desktop build deps..."
	@$(BUILD_PIP) install --upgrade pip > /dev/null
	@$(BUILD_PIP) install -r desktop/requirements.txt
	@$(BUILD_PIP) install -e .

# Stage 2: Next.js standalone build → frontend/.next/standalone (consumed by PyInstaller spec).
build-mac-frontend:
	@echo "⚛️  Building Next.js frontend..."
	@if [ ! -d "frontend/node_modules" ]; then cd frontend && npm ci; fi
	@cd frontend && npm run build

# Stage 3: fetch surreal binary, node, uv, python-build-standalone tarball into desktop/bin/.
# Idempotent — fetch_runtimes.py skips files already present.
build-mac-runtimes:
	@echo "⬇️  Fetching bundled runtimes (surreal / node / uv / python-standalone)..."
	@$(BUILD_PY) desktop/build/fetch_runtimes.py

# Stage 4: PyInstaller — produces dist/Deeper Notebook.app from desktop/build/pyinstaller.spec.
#
# v0.7.146 — Re-seal the bundle with `codesign --force --deep --sign -`
# AFTER PyInstaller finishes. Background:
#
#   macOS auto-applies an ad-hoc signature to arm64 Mach-O binaries the
#   first time they're written. PyInstaller produces the .app in
#   multiple write phases (COLLECT, then BUNDLE wraps it), and ANY file
#   modification under the bundle after macOS seals it — including
#   Spotlight indexing writing extended attributes, or PyInstaller's
#   own multi-pass writes — invalidates the seal. The Gatekeeper
#   verdict on the user's first rebuild was:
#
#     spctl -a -vvv "Deeper Notebook.app"
#     → a sealed resource is missing or invalid
#
#   When a Gatekeeper seal is broken, macOS silently kills the binary
#   at launch: no error dialog, no crash report, no stderr output. The
#   user double-clicks and nothing happens.
#
#   The fix is to do an explicit final codesign at the END of all
#   PyInstaller work, so the seal reflects the bundle's true final
#   contents. `--deep` re-signs every nested Mach-O (Python framework,
#   dylibs, helper binaries). `--force` overwrites any prior signature.
#   `--sign -` means ad-hoc (no developer cert required for local dev).
build-mac-pyinstaller:
	@echo "🔧 Running PyInstaller (this is the slow step, ~5-10 min)..."
	@$(BUILD_PYINSTALLER) desktop/build/pyinstaller.spec --noconfirm
	@echo "🔏 Re-sealing bundle (codesign --force --deep --sign $(DEEPER_NOTEBOOK_CODESIGN_IDENTITY))..."
	@codesign --force --deep --sign "$(DEEPER_NOTEBOOK_CODESIGN_IDENTITY)" "dist/Deeper Notebook.app"
	@echo "   Verifying seal..."
	@spctl -a -vvv "dist/Deeper Notebook.app" 2>&1 | sed 's/^/   /' || \
		echo "   ⚠️  spctl rejected the bundle (expected for ad-hoc on first-launch Gatekeeper);" && \
		echo "   the seal itself is valid, run codesign -v to confirm."
	@codesign --verify --deep --strict "dist/Deeper Notebook.app"

# Stage 5: wrap the .app into a .dmg via hdiutil. Unsigned — first launch needs
# right-click → Open OR `xattr -dr com.apple.quarantine dist/Deeper\ Notebook.app`.
build-mac-dmg:
	@echo "💾 Building .dmg..."
	@bash desktop/build/post_build_mac.sh

# Smoke an already-built app without installing, copying, or removing it.
# Every input is caller-owned so this target is safe for local and CI probes.
.PHONY: smoke-mac-app smoke-installed-mac-app
SMOKE_EXECUTABLE ?=
SMOKE_READINESS_FILE ?=
SMOKE_ARTIFACT ?=
SMOKE_RECEIPT ?=
SMOKE_ARTIFACT_SHA256 ?=
SMOKE_ENVIRONMENT_FILE ?=
SMOKE_EXPECTED_FEATURE ?=
SMOKE_TIMEOUT_SECONDS ?= 90
export SMOKE_EXECUTABLE SMOKE_READINESS_FILE SMOKE_ARTIFACT SMOKE_RECEIPT
export SMOKE_ARTIFACT_SHA256 SMOKE_ENVIRONMENT_FILE SMOKE_EXPECTED_FEATURE
export SMOKE_TIMEOUT_SECONDS
unexport SMOKE_ENVIRONMENT

smoke-mac-app:
	@uv run python desktop/build/package_smoke.py --make-smoke-inputs

smoke-installed-mac-app: smoke-mac-app

# Read-only release proof for an already-built staged or installed app. Every
# path belongs to the caller; these targets never copy, remove, or alter an
# application bundle. The installed default is an input path only.
.PHONY: smoke-release-mac-app smoke-release-installed-mac-app
RELEASE_SMOKE_EXECUTABLE ?=
RELEASE_SMOKE_ARTIFACT ?=
RELEASE_SMOKE_OUTPUT_ROOT ?=
RELEASE_SMOKE_UV_CACHE_DIR ?=
RELEASE_SMOKE_PLAYWRIGHT_MODULE ?=
RELEASE_SMOKE_EXPECTED_ARTIFACT_SHA256 ?=
RELEASE_SMOKE_INSTALLED_EXECUTABLE ?= /Applications/Deeper Notebook.app/Contents/MacOS/Deeper Notebook
export RELEASE_SMOKE_EXECUTABLE RELEASE_SMOKE_ARTIFACT RELEASE_SMOKE_OUTPUT_ROOT
export RELEASE_SMOKE_UV_CACHE_DIR RELEASE_SMOKE_PLAYWRIGHT_MODULE
export RELEASE_SMOKE_EXPECTED_ARTIFACT_SHA256 RELEASE_SMOKE_INSTALLED_EXECUTABLE
RELEASE_SMOKE_EXPECTED_HASH_ARGUMENT = $(if $(strip $(RELEASE_SMOKE_EXPECTED_ARTIFACT_SHA256)), --expected-artifact-sha256 "$${RELEASE_SMOKE_EXPECTED_ARTIFACT_SHA256}")

smoke-release-mac-app:
	@test -d "$${RELEASE_SMOKE_UV_CACHE_DIR}" || { echo "RELEASE_SMOKE_UV_CACHE_DIR must name an existing directory: $${RELEASE_SMOKE_UV_CACHE_DIR}" >&2; exit 1; }
	@UV_OFFLINE=1 UV_CACHE_DIR="$${RELEASE_SMOKE_UV_CACHE_DIR}" uv run python desktop/build/package_release_smoke.py --executable "$${RELEASE_SMOKE_EXECUTABLE}" --artifact "$${RELEASE_SMOKE_ARTIFACT}" --output-root "$${RELEASE_SMOKE_OUTPUT_ROOT}" --uv-cache-dir "$${RELEASE_SMOKE_UV_CACHE_DIR}" --playwright-module "$${RELEASE_SMOKE_PLAYWRIGHT_MODULE}"$(RELEASE_SMOKE_EXPECTED_HASH_ARGUMENT)

smoke-release-installed-mac-app:
	@test -d "$${RELEASE_SMOKE_UV_CACHE_DIR}" || { echo "RELEASE_SMOKE_UV_CACHE_DIR must name an existing directory: $${RELEASE_SMOKE_UV_CACHE_DIR}" >&2; exit 1; }
	@UV_OFFLINE=1 UV_CACHE_DIR="$${RELEASE_SMOKE_UV_CACHE_DIR}" uv run python desktop/build/package_release_smoke.py --executable "$${RELEASE_SMOKE_EXECUTABLE:-$${RELEASE_SMOKE_INSTALLED_EXECUTABLE}}" --artifact "$${RELEASE_SMOKE_ARTIFACT}" --output-root "$${RELEASE_SMOKE_OUTPUT_ROOT}" --uv-cache-dir "$${RELEASE_SMOKE_UV_CACHE_DIR}" --playwright-module "$${RELEASE_SMOKE_PLAYWRIGHT_MODULE}"$(RELEASE_SMOKE_EXPECTED_HASH_ARGUMENT)

# Convenience: copy the built .app to /Applications.
build-mac-install:
	@if [ ! -d "dist/Deeper Notebook.app" ]; then \
		echo "❌ dist/Deeper Notebook.app not found. Run 'make build-mac' first."; \
		exit 1; \
	fi
	@echo "📥 Installing to /Applications..."
	@# v0.8.67e — quit a running instance BEFORE deleting its bundle. Deleting
	@# the .app while it's running orphaned SurrealDB/uvicorn/llama sidecars and
	@# left zombie Next.js frontend servers on stale ports (the app's webview
	@# then showed "This page couldn't load"). Graceful quit first, wait, then
	@# force-kill any stragglers so the cp lands on a clean slate.
	@echo "⏹  Quitting any running Deeper Notebook first…"
	@osascript -e 'quit app "Deeper Notebook"' 2>/dev/null || true
	@for i in $$(seq 1 20); do pgrep -f '/Applications/Deeper Notebook.app/Contents/MacOS' >/dev/null 2>&1 || break; sleep 1; done
	@pkill -9 -f '/Applications/Deeper Notebook.app' 2>/dev/null || true
	@pkill -9 -f 'surreal-darwin' 2>/dev/null || true
	@pkill -9 -f 'llama_cpp.server' 2>/dev/null || true
	@pkill -9 -f 'surreal_commands.cli.worker' 2>/dev/null || true
	@sleep 2
	@rm -rf "/Applications/Deeper Notebook.app"
	@cp -R "dist/Deeper Notebook.app" /Applications/
	@xattr -dr com.apple.quarantine "/Applications/Deeper Notebook.app" || true
	@echo "✅ Installed. Launch with: open '/Applications/Deeper Notebook.app'"

# Remove PyInstaller artifacts and the build venv. Keeps fetched runtimes
# (downloading them again is the slowest step).
build-mac-clean:
	@echo "🧹 Removing dist/, build/, $(BUILD_VENV)/..."
	@rm -rf dist build $(BUILD_VENV)
	@echo "✅ Build artifacts cleaned. Runtimes in desktop/bin/ kept (use build-mac-distclean to wipe those too)."

# Nuclear option — also remove the fetched runtime binaries.
build-mac-distclean: build-mac-clean
	@echo "🧹 Removing desktop/bin/ (surreal / node / uv / python-standalone)..."
	@rm -rf desktop/bin
	@echo "✅ Distclean complete. Next build will re-download ~500 MB of runtimes."

# === Cleanup ===
clean-cache:
	@echo "🧹 Cleaning cache directories..."
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".mypy_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".ruff_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -type f -delete 2>/dev/null || true
	@find . -name "*.pyo" -type f -delete 2>/dev/null || true
	@find . -name "*.pyd" -type f -delete 2>/dev/null || true
	@echo "✅ Cache directories cleaned!"

# ---------------------------------------------------------------------------
# v0.8.86 — security scans. Bandit was uninstalled and pip-audit died in
# ensurepip for the entire life of the Phase 2A receipt; both now run.
#  - bandit: fails on HIGH-severity findings in project code (currently zero;
#    the vendored Node runtime under desktop/bin is excluded as third-party).
#  - pip-audit: audits the desktop lockfile. Runs via the Homebrew
#    interpreter because uv-managed pythons ship without a working ensurepip
#    (pip-audit builds a temp venv with it). Findings are REPORTED, not
#    build-failing: accepted residuals are documented in
#    docs/verification/2026-08-16-security-scan.md and re-triaged there.
# Network required (PyPI advisory DB) — deliberately NOT part of build-mac.
# v0.8.109 — repair the allowlist after an edit shifts pinned lines. The failure
# cascades through three gates that each report something different, and the
# repair order matters (relocate -> inventory digest -> coverage digests ->
# regenerate). See ROADMAP §2.1 for why this is needed at all.
.PHONY: repair-rebrand-pins
repair-rebrand-pins:
	@uv run python scripts/repair_rebrand_pins.py

.PHONY: security-scan
security-scan:
	@echo "🔍 Bandit (fails on HIGH severity in project code)…"
	@uvx bandit -r deeper_notebook api desktop \
	  -x "desktop/bin,desktop/tests,desktop/memory/tests" \
	  -q --severity-level high
	@echo "✅ Bandit: no HIGH-severity findings."
	@echo "🔍 pip-audit over desktop/requirements.lock…"
	@uvx --python /opt/homebrew/bin/python3.12 pip-audit \
	  -r desktop/requirements.lock --no-deps || true
	@echo "ℹ️  pip-audit findings above are triaged in docs/verification/2026-08-16-security-scan.md"
