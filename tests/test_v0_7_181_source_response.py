"""v0.7.181 — SourceResponse / SourceListResponse shape reconciliation.

Background: a deferred-list audit (see desktop/CHANGELOG.md
v0.7.178) flagged that the detail endpoint
(`GET /sources/{source_id}` → `SourceResponse`) and the list
endpoint (`GET /sources` → `list[SourceListResponse]`) returned
different shapes for the same resource. The most visible gap:
`insights_count` was present on the LIST endpoint but absent from
the DETAIL endpoint — so a "source has 3 insights" badge in the
sidebar would disappear when the user clicked into the source.

v0.7.181 reconciles by:
  1. Adding `insights_count: int = 0` to SourceResponse (default
     for backward compat with the POST/PUT/retry construction
     sites that build a fresh SourceResponse with no insights).
  2. Tightening `processing_info` type from bare `dict` to
     `dict[str, Any]` (matching the list endpoint's already-tight
     annotation).
  3. Wiring a fast count query into the detail endpoint so the
     reported `insights_count` matches the list endpoint's.

The intentional asymmetries (full_text and notebooks list-only
omission) are preserved and now documented inline in api/models.py.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read_source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_source_response_includes_insights_count():
    """v0.7.181: SourceResponse must have `insights_count`. Without
    it the detail endpoint can't report 'this source has N
    transformations' to the client — UX gap with the list view."""
    from api.models import SourceResponse

    fields = SourceResponse.model_fields
    assert "insights_count" in fields, (
        "v0.7.181 regression: SourceResponse no longer declares "
        "`insights_count`. The detail endpoint will silently drop "
        "this field, breaking the insights badge on every source "
        "detail page."
    )
    # And the default is 0 so creation-time constructions stay
    # backward-compatible.
    assert fields["insights_count"].default == 0


def test_processing_info_type_tightened_on_source_response():
    """v0.7.181: SourceResponse.processing_info must be the tight
    `dict[str, Any]` type, not bare `dict`. Tightening matches
    SourceListResponse and gives Pydantic real serialization
    semantics."""
    src = _read_source("api/models.py")
    # Find SourceResponse class and its end (next `class ` declaration).
    idx = src.find("class SourceResponse(BaseModel):")
    assert idx != -1
    next_class = src.find("\nclass ", idx + 1)
    assert next_class != -1, "couldn't locate end of SourceResponse"
    region = src[idx:next_class]
    # Inside SourceResponse: processing_info should be dict[str, Any]
    assert "processing_info: Optional[dict[str, Any]]" in region, (
        "v0.7.181 regression: SourceResponse.processing_info has "
        "reverted to a looser annotation. Should be "
        "`Optional[dict[str, Any]]` to match SourceListResponse."
    )


def test_get_source_endpoint_computes_insights_count():
    """v0.7.181: the detail endpoint must compute insights_count
    via a fast aggregate query and pass it through to
    SourceResponse. Without the wire-up the new field defaults
    to 0 on every request — silently wrong instead of broken
    loudly."""
    src = _read_source("api/routers/sources.py")
    # Find the get_source handler.
    idx = src.find("async def get_source(source_id: str):")
    next_fn = src.find("\ndef ", idx + 1)
    region = src[idx : next_fn] if next_fn != -1 else src[idx : idx + 5000]

    # The aggregate query is present.
    assert "FROM source_insight" in region, (
        "v0.7.181 regression: get_source endpoint no longer "
        "queries source_insight for the count. The endpoint will "
        "return insights_count=0 (the default) on every source — "
        "always wrong instead of always right."
    )

    # And the value is wired into the response.
    assert "insights_count=insights_count" in region, (
        "v0.7.181 regression: get_source endpoint queries the "
        "count but doesn't pass it to SourceResponse. The "
        "response will report 0 regardless of actual count."
    )


def test_detail_insights_count_normalizes_surreal_aggregate_shapes():
    """Surreal aggregate rows may be scalar or ``{"count": n}`` objects."""
    from api.routers.sources import _normalize_insights_count

    assert _normalize_insights_count([3]) == 3
    assert _normalize_insights_count([{"count": 4}]) == 4
    assert _normalize_insights_count([{"count": "not-a-count"}]) == 0
    assert _normalize_insights_count([]) == 0


def test_list_endpoint_still_includes_insights_count():
    """v0.7.181 forward-guard: don't accidentally drop the
    list endpoint's insights_count while reconciling. The list
    endpoint was the source-of-truth; the detail endpoint had
    to catch up to it, not the other way around."""
    from api.models import SourceListResponse

    assert "insights_count" in SourceListResponse.model_fields, (
        "v0.7.181 regression: SourceListResponse no longer "
        "declares `insights_count`. The list endpoint will drop "
        "this field — breaks the sidebar badge that was the "
        "original source of truth."
    )


def test_full_text_remains_list_only_omitted():
    """v0.7.181: the `full_text` asymmetry is INTENTIONAL —
    keep that way. SourceResponse carries the body; the list
    endpoint does NOT (would explode payload size on bulk list
    responses). If anyone proposes adding full_text to the list
    response in the name of 'symmetry', this test stops them."""
    from api.models import SourceListResponse, SourceResponse

    assert "full_text" in SourceResponse.model_fields
    assert "full_text" not in SourceListResponse.model_fields, (
        "v0.7.181: don't add full_text to SourceListResponse. A "
        "bulk list call for 50 sources would carry 50 source "
        "bodies — potentially megabytes per row. The asymmetry "
        "is intentional and documented in api/models.py."
    )
