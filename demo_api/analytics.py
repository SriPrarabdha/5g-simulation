from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb


class ParquetAnalytics:
    """Read-only DuckDB analytics over immutable campaign Parquet artifacts."""

    def __init__(self, artifact_root: str | Path) -> None:
        self.artifact_root = Path(artifact_root).resolve()

    def _validated(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.artifact_root / candidate
        candidate = candidate.resolve()
        if candidate != self.artifact_root and self.artifact_root not in candidate.parents:
            raise ValueError("artifact path escapes the configured root")
        if candidate.suffix != ".parquet" or not candidate.is_file():
            raise ValueError("a readable Parquet artifact is required")
        return candidate

    def describe(self, path: str | Path) -> dict[str, Any]:
        candidate = self._validated(path)
        with duckdb.connect(":memory:") as connection:
            columns = connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(candidate)]).fetchall()
            row_count = connection.execute("SELECT count(*) FROM read_parquet(?)", [str(candidate)]).fetchone()[0]
        return {
            "path": str(candidate.relative_to(self.artifact_root)),
            "rows": int(row_count),
            "columns": [{"name": row[0], "type": row[1]} for row in columns],
        }

    def telemetry_window(self, path: str | Path, *, limit: int = 1000) -> list[dict[str, Any]]:
        candidate = self._validated(path)
        limit = max(1, min(int(limit), 10_000))
        with duckdb.connect(":memory:") as connection:
            cursor = connection.execute(
                "SELECT * FROM read_parquet(?) ORDER BY window_start DESC LIMIT ?", [str(candidate), limit]
            )
            return cursor.to_arrow_table().to_pylist()
