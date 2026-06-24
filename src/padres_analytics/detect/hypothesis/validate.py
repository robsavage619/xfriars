"""Spec validation — the trust boundary between the LLM and the database.

``filter_sql`` and ``derived_expr`` are SQL fragments authored by an LLM. They
are untrusted data, never trusted commands. Before a spec touches the DB this
module:

* allowlists ``table`` against the live schema,
* allowlists every column reference against that table's columns,
* tokenizes the SQL fragments and rejects anything outside a numeric-expression
  whitelist (no string literals, no statements, no subqueries, no functions
  beyond a tiny numeric set),
* bounds the rolling window.

The connection used here must be read-only; this module only inspects schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from padres_analytics.detect.hypothesis.spec import ALLOWED_LENSES, HypothesisSpec
from padres_analytics.detect.sql import resolve_table

if TYPE_CHECKING:
    import duckdb

# Functions a fragment may call — numeric, side-effect-free only.
_ALLOWED_FUNCS: frozenset[str] = frozenset(
    {"abs", "coalesce", "greatest", "least", "nullif", "round", "floor", "ceil"}
)
# SQL keywords that are legal inside a boolean/arithmetic fragment.
_ALLOWED_KEYWORDS: frozenset[str] = frozenset(
    {"and", "or", "not", "is", "null", "in", "between", "true", "false"}
)
# Whole-word tokens that must never appear — statement/DDL/exfiltration surface.
_FORBIDDEN_KEYWORDS: frozenset[str] = frozenset(
    {
        "select",
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "attach",
        "detach",
        "copy",
        "pragma",
        "call",
        "install",
        "load",
        "export",
        "import",
        "union",
        "exec",
        "system",
        "read_csv",
        "read_parquet",
        "read_json",
        "glob",
        "with",
        "case",
        "from",
        "where",
        "join",
        "having",
        "group",
        "order",
        "limit",
        "values",
        "set",
    }
)
# Characters permitted in a numeric SQL fragment (note: no quotes, no ':', ';').
_ALLOWED_CHARS = re.compile(r"^[A-Za-z0-9_\s.,()+\-*/<>=!%]*$")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FORBIDDEN_SUBSTR = (";", "--", "/*", "*/", "'", '"', "`", "\\", "::", "[", "]", "{", "}")


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating one spec."""

    ok: bool
    code: str  # ok | unknown_table | unknown_column | illegal_sql | bad_lens | bad_window
    reason: str


def _columns(conn: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    """Lower-cased column names for ``table`` (main. then hist.), or empty."""
    src = resolve_table(conn, table)
    try:
        info = conn.execute(f"PRAGMA table_info('{src}')").fetchall()
    except Exception:
        return set()
    return {str(r[1]).lower() for r in info}


def _check_fragment(fragment: str, columns: set[str]) -> str | None:
    """Return a rejection reason for an unsafe SQL fragment, or None if clean.

    The fragment must be a pure numeric/boolean expression over known columns:
    only column refs, numeric literals, whitelisted operators, and a small set of
    numeric functions. Anything else — string literals, statements, subqueries,
    unknown identifiers — is rejected.
    """
    if not fragment.strip():
        return None

    lowered = fragment.lower()
    for bad in _FORBIDDEN_SUBSTR:
        if bad in fragment:
            return f"illegal token {bad!r}"
    if not _ALLOWED_CHARS.match(fragment):
        return "contains a disallowed character"

    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", lowered):
            return f"forbidden keyword {kw!r}"

    for ident in _IDENT.findall(fragment):
        low = ident.lower()
        if low in _ALLOWED_KEYWORDS or low in _ALLOWED_FUNCS or low in columns:
            continue
        return f"unknown identifier {ident!r} (not a column, keyword, or allowed function)"

    return None


def validate(conn: duckdb.DuckDBPyConnection, spec: HypothesisSpec) -> ValidationResult:
    """Validate a hypothesis spec against the schema and the SQL whitelist.

    Args:
        conn: Read-only connection (with hist attached) used only to inspect schema.
        spec: The LLM-proposed spec.

    Returns:
        A :class:`ValidationResult`. ``ok=True`` means the spec is safe to scan.
    """
    columns = _columns(conn, spec.table)
    if not columns:
        return ValidationResult(False, "unknown_table", f"table {spec.table!r} not in schema")

    if spec.value_col.lower() not in columns:
        return ValidationResult(
            False, "unknown_column", f"value_col {spec.value_col!r} not in {spec.table!r}"
        )

    for field, fragment in (
        ("derived_expr", spec.derived_expr or ""),
        ("filter_sql", spec.filter_sql),
    ):
        reason = _check_fragment(fragment, columns)
        if reason is not None:
            return ValidationResult(False, "illegal_sql", f"{field}: {reason}")

    bad_lenses = [lens for lens in spec.lenses if lens not in ALLOWED_LENSES]
    if bad_lenses:
        return ValidationResult(False, "bad_lens", f"unsupported lenses: {bad_lenses}")
    if not spec.to_metric_spec().lenses:
        return ValidationResult(False, "bad_lens", "no valid lens requested")

    if spec.window is not None and not (1 <= spec.window.days <= 120):
        return ValidationResult(False, "bad_window", "window.days out of [1, 120]")

    return ValidationResult(True, "ok", "valid")
