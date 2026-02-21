from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL


class DatabaseConnector:
    """Create and reuse a DB2 SQLAlchemy engine backed by values from .env."""

    def __init__(self, env_path: str | os.PathLike[str] = ".env") -> None:
        self.env_path = Path(env_path)
        load_dotenv(dotenv_path=self.env_path)

        self.username = os.getenv("DB_USERNAME")
        self.password = os.getenv("DB_PASSWORD")
        self.host = os.getenv("DB_HOST")
        self.port = os.getenv("DB_PORT")
        self.database = os.getenv("DB_NAME")

        missing = [key for key, value in self._env_values().items() if not value]
        if missing:
            missing_keys = ", ".join(missing)
            raise ValueError(f"Missing required database environment variables: {missing_keys}")

        self._engine = create_engine(self._build_connection_url())

    def _env_values(self) -> dict[str, str | None]:
        return {
            "DB_USERNAME": self.username,
            "DB_PASSWORD": self.password,
            "DB_HOST": self.host,
            "DB_PORT": self.port,
            "DB_NAME": self.database,
        }

    @staticmethod
    def _normalize_identifier(value: str, label: str) -> str:
        candidate = value.strip().upper()
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", candidate):
            raise ValueError(
                f"Invalid {label} '{value}'. Only letters, digits, and underscores are allowed."
            )
        return candidate

    def _build_connection_url(self) -> URL:
        try:
            port = int(self.port or "")
        except ValueError as exc:
            raise ValueError("DB_PORT must be a valid integer.") from exc

        return URL.create(
            "db2+ibm_db",
            username=self.username,
            password=self.password,
            host=self.host,
            port=port,
            database=self.database,
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    def test_connection(self) -> bool:
        """Open a connection and run a trivial DB2 query to validate connectivity."""
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM SYSIBM.SYSDUMMY1"))
        return True

    def execute_query(
        self, query: str, params: Mapping[str, Any] | None = None
    ) -> pd.DataFrame:
        """Run SQL and return the result set as a pandas DataFrame."""
        kwargs: dict[str, Any] = {}
        if params is not None:
            kwargs["params"] = dict(params)
        return pd.read_sql(text(query), self.engine, **kwargs)

    def get_table_names(self, schema: str) -> list[str]:
        schema_name = self._normalize_identifier(schema, "schema")
        query = """
            SELECT tabname
            FROM SYSCAT.TABLES
            WHERE tabschema = :schema_name
              AND type IN ('T', 'N', 'V')
            ORDER BY tabname
        """
        df = self.execute_query(query, params={"schema_name": schema_name})
        if df.empty:
            return []
        df.columns = [column.lower() for column in df.columns]
        return [str(value).upper() for value in df["tabname"].dropna().tolist()]

    def table_exists(self, schema: str, table_name: str) -> bool:
        schema_name = self._normalize_identifier(schema, "schema")
        table = self._normalize_identifier(table_name, "table")
        query = """
            SELECT 1
            FROM SYSCAT.TABLES
            WHERE tabschema = :schema_name
              AND tabname = :table_name
              AND type IN ('T', 'N', 'V')
            FETCH FIRST 1 ROW ONLY
        """
        df = self.execute_query(query, params={"schema_name": schema_name, "table_name": table})
        return not df.empty

    def dispose(self) -> None:
        """Release pooled DB connections."""
        self.engine.dispose()
