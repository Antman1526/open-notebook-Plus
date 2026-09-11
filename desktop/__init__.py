"""Deeper Notebook desktop wrapper.

See the historical desktop design specification at
docs/superpowers/specs/2026-05-09-open-notebook-plus-desktop-design.md.
"""

# v0.7.210 — synced from "0.1.0" (set at project start, never
# updated) to the actual current release. The version string is
# now displayed on the launch splash, the system-tray About line,
# and the API /api/version endpoint. Test
# `tests/test_v0_7_210_version_and_reaper.py::
# test_version_matches_latest_changelog_release` (v0.8.70) now actually
# enforces that this equals the newest `## vX.Y.Z` header in
# desktop/CHANGELOG.md, so future bumps can't drift. In-progress work lives
# under `## Unreleased` and does NOT advance this — bump it only when a real
# `## v` release is cut.
#
# This is the DESKTOP app version (window, /api/version, update notifier, and
# the macOS bundle CFBundleShortVersionString). It is a separate track from
# pyproject.toml's `version`, which versions the upstream/Docker image — see
# the note there. Don't conflate them.
__version__ = "0.8.128"
