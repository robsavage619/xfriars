"""Validator is the trust boundary — these tests are the security contract."""

from __future__ import annotations

import duckdb
import pytest

from padres_analytics.detect.hypothesis.spec import HypothesisSpec
from padres_analytics.detect.hypothesis.validate import validate


@pytest.fixture()
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute(
        """
        CREATE TABLE statcast_batter_exitvelo_barrels (
            player_id INTEGER, player_name VARCHAR, year INTEGER,
            brl_percent DOUBLE, attempts INTEGER, avg_hit_speed DOUBLE
        )
        """
    )
    c.execute("INSERT INTO statcast_batter_exitvelo_barrels VALUES (1, 'X', 2026, 12.0, 200, 90.0)")
    return c


def _spec(**kw: object) -> HypothesisSpec:
    base: dict[str, object] = {
        "id": "t",
        "label": "T",
        "rationale": "test",
        "table": "statcast_batter_exitvelo_barrels",
        "value_col": "brl_percent",
    }
    base.update(kw)
    return HypothesisSpec.model_validate(base)


def test_clean_spec_passes(conn: duckdb.DuckDBPyConnection) -> None:
    assert validate(conn, _spec(filter_sql="attempts >= 100")).ok


def test_clean_derived_expr_passes(conn: duckdb.DuckDBPyConnection) -> None:
    r = validate(
        conn, _spec(derived_expr="brl_percent - avg_hit_speed", metric_type="differential")
    )
    assert r.ok


def test_unknown_table_rejected(conn: duckdb.DuckDBPyConnection) -> None:
    assert validate(conn, _spec(table="secrets")).code == "unknown_table"


def test_unknown_value_col_rejected(conn: duckdb.DuckDBPyConnection) -> None:
    assert validate(conn, _spec(value_col="ssn")).code == "unknown_column"


def test_unknown_identifier_in_filter_rejected(conn: duckdb.DuckDBPyConnection) -> None:
    assert validate(conn, _spec(filter_sql="salary > 100")).code == "illegal_sql"


@pytest.mark.parametrize(
    "fragment",
    [
        "attempts >= 100; DROP TABLE statcast_batter_exitvelo_barrels",
        "attempts >= 100 -- comment",
        "attempts >= (SELECT MAX(attempts) FROM statcast_batter_exitvelo_barrels)",
        "attempts >= 100 UNION SELECT 1",
        "attempts >= '100'",
        'attempts >= "100"',
        "1=1 OR pg_sleep(10)",
        "attempts >= 100 /* x */",
        "attempts::varchar = 'x'",
        "read_csv('/etc/passwd')",
    ],
)
def test_injection_fragments_rejected(conn: duckdb.DuckDBPyConnection, fragment: str) -> None:
    assert validate(conn, _spec(filter_sql=fragment)).code == "illegal_sql"


def test_allowed_function_passes(conn: duckdb.DuckDBPyConnection) -> None:
    assert validate(conn, _spec(filter_sql="abs(brl_percent) >= 5")).ok


def test_bad_lens_rejected(conn: duckdb.DuckDBPyConnection) -> None:
    assert validate(conn, _spec(lenses=["telepathy"])).code == "bad_lens"


def test_window_bounds(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError):
        _spec(window={"days": 999})
    assert validate(conn, _spec(window={"days": 15})).ok
