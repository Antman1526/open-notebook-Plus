# Roadmap

**Status: 2026-08-20 · desktop `0.8.114` · server/container `1.8.5`**

> Actionable items live in [`TODO.md`](TODO.md). This file carries the strategy
> and the reasoning; that one is the list you pick work from.

Why this file exists: an inventory on 2026-08-19 found no roadmap or backlog
anywhere in the repository — only retrospective per-feature plans under
`docs/superpowers/plans/`. Answering "what needs building?" required
reconstructing it from feature-flag defaults, CI config, and §4 of
`recreation/PROJECT-DEEP-DIVE.md`. For a project with 49 API routers and 5,754
tests, that reconstruction cost was the most consequential missing artifact.

Everything below is either measured or explicitly marked as a judgement call.
Where a number appears, it came from running something, and the command is
named so it can be re-run rather than trusted.

---

## 1. Decisions waiting on a human

These are not engineering problems. They are choices only the owner can make,
and each is currently costing something while it stays open.

### 1.1 Revoke the leaked Google API key — **urgent**

A live-format key (`AIza…`, 39 chars) sits in git history and has been public on
GitHub. Purging history does **not** un-leak it: forks and clones retain it, and
it may be indexed. Rotation is the only remediation. A `git-filter-repo`
redaction was staged and is described in the commit history, but it is hygiene,
not a fix.

`history.txt` — a SurrealDB dump carrying Fernet-encrypted credential blobs — is
in history for the same reason and was staged for the same purge.

### 1.2 Source visuals — **RESOLVED: enabled by default** (updated 2026-08-24)

`source_visuals_enabled()` now defaults to `True` and resolves through the
registered `SOURCE_VISUALS_ENABLED` setting (`deeper_notebook/feature_flags.py:37`,
`deeper_notebook/environment.py:187`). Behind that flag:

| Piece | Size |
|---|---|
| `deeper_notebook/database/migrations/46.surrealql` | `source_visual_cache` / `_claim` / `_operation` |
| `api/routers/source_visuals.py` | 3 endpoints |
| `frontend/.../source-gallery/` | 9 components |
| Visual proof matrix | 8 cells × 3 themes × 4 viewports |

The subsystem is on by default; the 2026-08-21 release smoke exercised both the
default and the Source-Visuals-off configuration (see `Docs/CURRENT-SNAPSHOT.md`
in the knowledge workspace).

`research_runs_enabled()` likewise defaults to `True` now; it gates one
`ArtifactRail` surface rather than a subsystem, and the main Guided Research
workspace is live in the notebook page.

### 1.3 Adopt a formatter — **DONE in v0.8.111**

`ruff format` is enabled in `.pre-commit-config.yaml` and the tree is fully
formatted (1,126 files; the formatter is now a no-op).

It stayed off for three releases because §2.1's allowlist pinned approvals to
line and column, so a reformat invalidated them wholesale. §2.1 removed that
blocker; adoption then took one reviewed pass:

* **701 files** reformatted.
* **~1,046 pins** relocated automatically by `make repair-rebrand-pins`.
* **38 re-approvals** reviewed line by line and recorded in
  `docs/verification/2026-08-19-formatter-adoption-review.md`. One proposed
  mapping was **wrong** — it would have moved a regex approval onto an unrelated
  `assert` — and the review caught it. That is why the review exists.
* **2 approvals** carried onto the occurrence the audit itself named, where
  substring nesting made ordinal mapping ambiguous.

**The hazard worth remembering:** ruff JOINS adjacent string literals, and two
tests that scan production source for the legacy token write `"open_" "notebook"`
deliberately so they do not trip their own scan. Joining reintroduces the exact
token the codebase is designed not to contain. Both sites are guarded with
`# fmt: skip` / `# fmt: off` and carry a comment; **do not remove those guards**,
and grep for other adjacent-literal splits before adding new ones.

---

## 2. Engineering work, ranked by payoff per unit of risk

### 2.1 Re-key the rebrand allowlist — **DONE in v0.8.109**

Approvals are pinned to `(path, pattern, source, line, column, sha256(RAW
line))`. Every positional component moves when a file is edited, so:

* an edit *above* an approved line invalidates a still-correct approval — this
  ratchet fired three separate times during one working session, once relocating
  **416 pins** for a single edit; and
* a repo-wide reformat invalidates approvals wholesale, after which
  `--regenerate` aborts rather than rebuilding.

**Shipped 2026-08-19 (v0.8.109), on the second attempt.** The digest now hashes
whitespace-normalized content with the **intra-line ordinal folded in**, so
position leaves the lookup key while the distinction it carried — which
occurrence on a line is approved — does not. Measured: a 702-file `ruff format`
now leaves 1,044 pins auto-relocated and 49 genuinely changed, versus
invalidating everything and refusing to regenerate. Adopting a formatter is now
a bounded 49-entry review.

`make repair-rebrand-pins` remains for ordinary line shifts.

The first attempt, and why it was reverted: Normalizing whitespace in the digest and
falling back to `(path, pattern, source, digest)` got `--check` passing and cut
reformat damage from *everything* to five entries. It was reverted because
dropping `column` is a real weakening, and the suite already proves it:
`test_exact_context_does_not_hide_distinct_same_line_active_occurrence` puts the
legacy token on one line **twice** — one occurrence approved, one not — and
asserts the unapproved one is still flagged.

So `column` carries intra-line occurrence identity. The correct fix replaces
absolute column with an **occurrence ordinal within the line** (1st vs 2nd match
of that pattern): stable under reindentation, still distinguishing the two.
That ordinal must be stored in the allowlist *and* computed scanner-side, and
`classify_match` currently receives only the key — not the source line — so it
cannot derive one. Threading it through changes `_occurrence_key`'s arity and
ripples across ~55 assertions in `tests/test_product_identity.py`.

Two traps for whoever does it, both hit during the attempt:

* The hashing is **duplicated** in `scripts/persisted_queue_inventory.py`, which
  `rebrand_audit` imports and therefore cannot import back. Normalizing only one
  side made 30 compatibility entries resolve to a null contract.
* `_PINNED_SELECTOR_INVENTORY_SHA256` and each contract's `coverage_sha256` are
  computed over the entry keys. Change a key and all of them go stale; an empty
  pinned inventory looks like success right up until nothing is classified.

**Payoff:** unblocks the formatter *and* ends the ratchet.

Until then, `make repair-rebrand-pins` performs the repair in the required order
(relocate pins by content digest → inventory digest → coverage digests →
regenerate). It only moves a pin when the file still contains a line matching
the recorded digest; anything whose content genuinely changed is reported for
review rather than silently re-approved.

### 2.2 Retire source-shape tests — lower priority than it looks

469 assertions across 88 files that grep exact source text. Long assumed to be
what blocks a formatter; **measured on 2026-08-19 to not be**. They remain
brittle in principle and worth replacing with behavioural assertions
opportunistically, but rewriting them buys far less than §2.1.

### 2.3 `act()` warnings — three clusters, do them per-cluster

~100 warnings, every test passing. Grouped in PROJECT-DEEP-DIVE §4.6a:
`GuidedTipsProvider` (16, a `MutationObserver` firing outside `act`), Radix
internals in `Tooltip`/`Popper`/`Presence` (20, third-party), and assorted
unawaited async state. No shared fix. Worth doing when already working in a
given area; not worth a dedicated 25-file sweep immediately after the suite was
made deterministic.

### 2.4 Open architecture questions

Neither urgent nor forced, listed in full under "Areas for Review" in
PROJECT-DEEP-DIVE: the five near-identical tool-binding blocks (§2.1 there), the
module-global pooled HTTP client (§2.3), whether ~92 `# nosec` SurrealQL sites
justify a typed query builder (§4.4), and what could now be deleted.

---

## 2.5 Retrieval was silently broken — resolved 2026-08-20 (v0.8.114)

Worth recording because of *how* it hid rather than what it was.

`fn::vector_search` returned zero results for every query, and had since
migration 21 — HTTP 200, empty list, no error, no log line, against a fully
embedded corpus. Three independent defects, each of which alone still returned
nothing: an MTREE-arity KNN operator (`<|100|>`) against HNSW indexes; a
SurrealDB 2.6.5 behaviour where a KNN predicate sharing a WHERE clause with a
function call over the same indexed field matches nothing; and `ORDER BY` being
ignored on a statement carrying `GROUP BY`. Fixed by migration 49.

The third defect turned out to also affect `fn::text_search`, where it had been
present since migration 1 — an unordered `LIMIT` returns an arbitrary slice, so
text search had been answering with mediocre matches rather than the best ones.
Fixed by migration 50; live top score for "architecture" went 2.586 → 6.363.

**The lesson is §1.2 of TODO.md.** Every surrounding signal read healthy:
sources reported embedded, chunk counts were non-zero, the query vector was a
well-formed 768-dim array, the index reported matching DIMENSION and TYPE, and
`REBUILD INDEX` returned OK. The 5,600-test backend suite was green. The only
thing that would have caught it is asking the database for results and checking
that some came back — which is what `tests/integration/` does, and which nothing
runs, because it is gated behind `SURREAL_INTEGRATION=1`.

---

## 3. Product gaps

* **Model-config health covers chat and embedding only.** TTS/STT/tools defaults
  can still dangle silently. Deliberate — listing every slot trains people to
  ignore the panel — but revisit if a support question ever traces to one.
* **Auto-route degrades silently.** With no benchmark history it now falls back
  to the configured default rather than dying (v0.8.100). Whether the toggle
  should *say* "nothing to route between yet" is unresolved.
* **Cold start is ~47s** (down from ~106s after freeing disk). Now dominated by
  a 4.9 GB GGUF cold-mmap, i.e. filesystem-bound rather than code-bound. Keep
  the volume off ~90% full; there is little left to win in code.
* **`source_visuals_enabled()` registration — RESOLVED 2026-08-20** (stale
  entry corrected 2026-08-24): the flag now resolves through `resolve_env`, and
  `SOURCE_VISUALS_ENABLED` is registered in `environment.SETTINGS`
  (`deeper_notebook/environment.py:187`), bringing alias handling and
  deprecation policy with it. `docs/TODO.md` §3.2 records the completion; this
  roadmap entry had not been updated to match.

---

## 4. What is deliberately *not* on this list

* **TODO debt.** Two `TODO` occurrences repo-wide, both an FSM enum value
  (`AgentState.TODO`). For ~180k LOC that is unusual and worth preserving.
* **Reconciling the two version tracks.** `desktop/__init__.py` (app) and
  `pyproject.toml` (server/container) version different artifacts and are
  intentionally unreconciled. It confuses every reader; it is still correct.
* **`pillow < 12`.** ~24 CVEs, accepted because moviepy 2.2.1 — verified the
  latest release — still pins `pillow<12.0`. Genuinely unresolvable upstream;
  re-check when moviepy moves.

---

## Re-running the evidence

```bash
make security-scan                      # Bandit HIGH gate + pip-audit
uv run python scripts/rebrand_audit.py --check
uv run pytest tests/ desktop/tests/ -q
cd frontend && npx vitest run
```
