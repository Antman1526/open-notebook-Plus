"""v0.8.126 — SurrealDB 2.x rejects `ORDER BY field` when `field` is not in
the SELECT projection ("Missing order idiom"). Found live twice
(capture roots, MCP notebook list). This scans literal queries in the
backend for that shape so it cannot come back silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("deeper_notebook", "api", "commands")

# A literal SELECT with an explicit (non-*) projection, no subquery close
# paren between FROM and ORDER BY (nested `select * from (...) order by`
# shapes project everything and are fine), and no template braces.
_ORDERED = re.compile(
    r"SELECT\s+(?!\*)(?!VALUE)([^;\"{}()]*?)\s+FROM\s+[A-Za-z_][\w:]*[^;\"(){}]*?ORDER\s+BY\s+([A-Za-z_][\w.]*)",
    re.IGNORECASE | re.DOTALL,
)


def _projected_names(projection: str) -> set[str]:
    names: set[str] = set()
    for part in projection.split(","):
        part = part.strip()
        if not part:
            continue
        alias = re.split(r"\s+AS\s+", part, flags=re.IGNORECASE)
        names.add(alias[-1].strip())
        names.add(alias[0].strip())
    return names


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] if not line.lstrip().startswith(("'", '"')) else line for line in text.splitlines())


def _violations() -> list[str]:
    out: list[str] = []
    for d in SCAN_DIRS:
        for path in (ROOT / d).rglob("*.py"):
            raw = path.read_text(errors="ignore")
            if "import sqlite3" in raw:
                # Anki/SQLite exports use SQL, not SurrealQL.
                continue
            text = _strip_comments(raw)
            for m in _ORDERED.finditer(text):
                projection, order_field = m.group(1), m.group(2)
                if "count()" in projection or "__" in projection:
                    # aggregate, or a runtime-substituted placeholder
                    continue
                if order_field not in _projected_names(projection):
                    line = text[: m.start()].count("\n") + 1
                    out.append(f"{path.relative_to(ROOT)}:{line} orders by `{order_field}` but projects `{projection.strip()[:60]}`")
    return out


def test_no_literal_query_orders_by_an_unprojected_field():
    assert _violations() == []


def test_scanner_catches_the_live_shape():
    sample = 'repo_query("SELECT path FROM capture_inbox_root ORDER BY created ASC")'
    m = _ORDERED.search(sample)
    assert m is not None
    assert m.group(2) not in _projected_names(m.group(1))


def test_runtime_guard_catches_unprojected_order_by():
    from deeper_notebook.database.repository import check_query_ordered_projection

    bad_query = "SELECT title, content FROM source ORDER BY created DESC"
    warning = check_query_ordered_projection(bad_query)
    assert warning is not None
    assert "ORDER BY `created` is not in SELECT projection" in warning


def test_runtime_guard_allows_projected_order_by():
    from deeper_notebook.database.repository import check_query_ordered_projection

    good_query = "SELECT title, created FROM source ORDER BY created DESC"
    assert check_query_ordered_projection(good_query) is None


def test_runtime_guard_allows_aliased_projection():
    from deeper_notebook.database.repository import check_query_ordered_projection

    aliased_query = (
        "SELECT title, time::now() AS created_at FROM source ORDER BY created_at"
    )
    assert check_query_ordered_projection(aliased_query) is None


def test_runtime_guard_allows_wildcards_and_values():
    from deeper_notebook.database.repository import check_query_ordered_projection

    assert check_query_ordered_projection("SELECT * FROM source ORDER BY created") is None
    assert (
        check_query_ordered_projection("SELECT VALUE id FROM source ORDER BY created")
        is None
    )


@pytest.mark.asyncio
async def test_repo_query_strict_mode_raises(monkeypatch):
    from deeper_notebook.database.repository import repo_query

    monkeypatch.setenv("DEEPER_NOTEBOOK_STRICT_QUERY_GUARD", "1")
    with pytest.raises(
        ValueError, match="ORDER BY `created` is not in SELECT projection"
    ):
        await repo_query("SELECT id FROM source ORDER BY created")
