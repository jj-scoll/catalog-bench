import os
import shutil
from dataclasses import dataclass

import duckdb
import psycopg

from bench.config import DATA_PATH, PG


@dataclass
class System:
    """DuckLake, default settings: metadata in Postgres, Parquet data on local disk, one single-row INSERT per commit."""

    attach: str = f"ATTACH 'ducklake:postgres:{PG} dbname=ducklake' AS lake (DATA_PATH '{DATA_PATH}')"
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
        return f"DuckDB {duckdb.__version__}, ducklake extension {ext}, format {fmt}"


class _Conn:
    def __init__(self, conn):
        self.conn = conn

    def commit_one(self, writer, seq):
        self.conn.execute("INSERT INTO lake.commits VALUES (?, ?)", (writer, seq))

    def close(self):
        self.conn.close()
