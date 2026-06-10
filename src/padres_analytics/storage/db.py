"""DuckDB connection helpers."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from padres_analytics.config import DUCKDB_PATH

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


class TradesDbNotFoundError(FileNotFoundError):
    """Raised when the savage-trade-evaluator trades.db cannot be located."""


@contextmanager
def connect(
    path: Path | None = None,
    read_only: bool = False,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a padres.db connection scoped to a ``with`` block.

    Args:
        path: Database path. Defaults to config.DUCKDB_PATH.
        read_only: Open in read-only mode.

    Yields:
        An open DuckDB connection, closed on context exit.
    """
    target = path or DUCKDB_PATH
    if not read_only:
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(target), read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


def attach_trades(conn: duckdb.DuckDBPyConnection) -> None:
    """Attach savage-trade-evaluator's trades.db as ``hist`` (read-only).

    Path resolution order:
    1. PADRES_TRADES_DB_PATH environment variable
    2. Default sibling-project path

    Args:
        conn: An open DuckDB connection to attach trades.db onto.

    Raises:
        TradesDbNotFoundError: If trades.db cannot be located.
    """
    env_path = os.environ.get("PADRES_TRADES_DB_PATH")
    if env_path:
        trades_path = Path(env_path)
    else:
        # Default: sibling project at the same directory level
        trades_path = (
            Path(__file__).resolve().parents[4]
            / "savage-trade-evaluator"
            / "data"
            / "duckdb"
            / "trades.db"
        )

    if not trades_path.exists():
        raise TradesDbNotFoundError(
            f"trades.db not found at {trades_path}. "
            "Set PADRES_TRADES_DB_PATH to the correct absolute path."
        )

    conn.execute(f"ATTACH '{trades_path}' AS hist (READ_ONLY)")
    logger.debug("Attached trades.db as hist from %s", trades_path)
