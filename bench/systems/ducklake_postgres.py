import os
import shutil
from dataclasses import dataclass

import duckdb
import psycopg

from bench.config import DATA_PATH, PG

# Rows a single insert may keep inline in the catalog. Unset leaves the DuckLake default; 0 writes a Parquet file for
# every commit.
INLINE_ROWS = os.environ.get("BENCH_DUCKLAKE_INLINE_ROWS")
_INLINE = f", DATA_INLINING_ROW_LIMIT {int(INLINE_ROWS)}" if INLINE_ROWS else ""


@dataclass
class System:
    """DuckLake, default settings: metadata in Postgres, one single-row INSERT per commit. DuckLake keeps a row this small inline in the catalog, so no Parquet file is written unless BENCH_DUCKLAKE_INLINE_ROWS=0 turns inlining off; the version line says when it was set."""

    attach: str = f"ATTACH 'ducklake:postgres:{PG} dbname=ducklake' AS lake (DATA_PATH '{DATA_PATH}'{_INLINE})"
    name = "ducklake-postgres"

    def _open(self):
        os.makedirs(DATA_PATH, exist_ok=True)
        conn = duckdb.connect()
        conn.execute(self.attach)
        return conn

    def setup(self):
        # Start every run from an empty lake: old snapshots and data files change commit cost.
        with psycopg.connect(f"{PG} dbname=postgres", autocommit=True) as pg:
            pg.execute("DROP DATABASE IF EXISTS ducklake WITH (FORCE)")
            pg.execute("CREATE DATABASE ducklake")
        shutil.rmtree(DATA_PATH, ignore_errors=True)
        with self._open() as c:
            c.execute("CREATE OR REPLACE TABLE lake.commits (writer INTEGER, seq INTEGER)")

    def connect(self):
        return _Conn(self._open())

    def count(self):
        with self._open() as c:
            return c.execute("SELECT count(*) FROM lake.commits").fetchone()[0]

    def version(self):
        with self._open() as c:
            ext = c.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'ducklake'").fetchone()[0]
            fmt = c.execute("SELECT value FROM lake.options() WHERE option_name = 'version'").fetchone()[0]
        return f"DuckDB {duckdb.__version__}, ducklake extension {ext}, format {fmt}" + (f", inlining limit {INLINE_ROWS} rows" if INLINE_ROWS else "")


class _Conn:
    def __init__(self, conn):
        self.conn = conn

    def commit_one(self, writer, seq):
        self.conn.execute("INSERT INTO lake.commits VALUES (?, ?)", (writer, seq))

    def close(self):
        self.conn.close()
