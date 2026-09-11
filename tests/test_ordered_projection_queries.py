"""v0.8.126 — SurrealDB 2.x rejects `ORDER BY field` when `field` is not in
the SELECT projection ("Missing order idiom"). Found live twice
(capture roots, MCP notebook list). This scans literal queries in the
backend for that shape so it cannot come back silently.
"""

from __future__ import annotations

import re
from pathlib import Path

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
