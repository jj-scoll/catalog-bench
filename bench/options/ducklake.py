import time
from dataclasses import dataclass

import duckdb
import psycopg
import pyarrow.fs as pafs

from bench.config import PG, PIPELINE_DATA
from bench.pipeline import SCHEMA

FINALIZE_ATTEMPTS = 20


@dataclass
class Option:
    """DuckLake with default settings, metadata in Postgres. Each batch is one transaction that inserts its output and
    error rows. Closing a trace is one transaction that deletes the trace's older versions and records the new one;
    DuckLake aborts a delete that races an insert into the same table, so the finaliser retries. Readers take the
    recorded version and one delivery per batch. Maintenance merges adjacent data files."""

    pg: str = PG
    db: str = "pipeline_ducklake"
    data: str = f"{PIPELINE_DATA}/ducklake/"
    name = "ducklake"

    @classmethod
    def local(cls, directory):
        return cls(db="pipeline_ducklake_test", data=f"{directory}/ducklake/")

    def _open(self):
        conn = duckdb.connect()
        if self.data.startswith("s3://"):
            conn.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain)")
        conn.execute(f"ATTACH 'ducklake:postgres:{self.pg} dbname={self.db}' AS lake (DATA_PATH '{self.data}')")
        return conn

    def setup(self):
        with psycopg.connect(f"{self.pg} dbname=postgres", autocommit=True) as pg:
            pg.execute(f"DROP DATABASE IF EXISTS {self.db} WITH (FORCE)")
            pg.execute(f"CREATE DATABASE {self.db}")
        fs, root = pafs.FileSystem.from_uri(self.data)
        fs.create_dir(root, recursive=True)
        fs.delete_dir_contents(root)
        with self._open() as c:
            c.register("empty", SCHEMA.empty_table())
            c.execute("CREATE TABLE lake.output AS FROM empty")
            c.execute("CREATE TABLE lake.errors AS FROM empty")
            c.execute("CREATE TABLE lake.control (trace INTEGER, version INTEGER)")

    def connect(self):
        return _Conn(self._open())

    def maintain(self):
        with self._open() as c:
            c.execute("CALL ducklake_merge_adjacent_files('lake')")

    def version(self):
        with self._open() as c:
            ext = c.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'ducklake'").fetchone()[0]
            fmt = c.execute("SELECT value FROM lake.options() WHERE option_name = 'version'").fetchone()[0]
        with psycopg.connect(f"{self.pg} dbname=postgres") as pg:
            server = pg.execute("SHOW server_version").fetchone()[0]
        return f"DuckDB {duckdb.__version__}, ducklake extension {ext}, format {fmt}, PostgreSQL {server}"


class _Conn:
    def __init__(self, conn):
        self.conn, self.retries = conn, 0

    def _transaction(self, statements):
        self.conn.execute("BEGIN")
        try:
            for sql, params in statements:
                self.conn.execute(sql, params)
            self.conn.execute("COMMIT")
        except Exception:
            try:
                self.conn.execute("ROLLBACK")
            except duckdb.Error:
                pass  # a failed COMMIT has already ended the transaction; the error worth reporting is the first one
            raise

    def write_batch(self, trace, version, batch, attempt, rows, errors):
        self.conn.register("rows_in", rows)
        self.conn.register("errors_in", errors)
        self._transaction([("INSERT INTO lake.output FROM rows_in", None), ("INSERT INTO lake.errors FROM errors_in", None)])

    def finalize(self, trace, version):
        statements = [
            ("DELETE FROM lake.output WHERE trace = ? AND version < ?", (trace, version)),
            ("DELETE FROM lake.errors WHERE trace = ? AND version < ?", (trace, version)),
            ("INSERT INTO lake.control VALUES (?, ?)", (trace, version)),
        ]
        for attempt in range(FINALIZE_ATTEMPTS):
            try:
                return self._transaction(statements)
            except duckdb.TransactionException:
                if attempt == FINALIZE_ATTEMPTS - 1:
                    raise
                self.retries += 1
                time.sleep(min(2.0, 0.05 * 2**attempt))

    def read_trace(self, trace):
        return self.conn.sql(
            "SELECT version, batch, row FROM lake.output "
            "WHERE trace = ? AND version = (SELECT max(version) FROM lake.control WHERE trace = ?) "
            "QUALIFY attempt = max(attempt) OVER (PARTITION BY batch)",
            params=(trace, trace),
        ).to_arrow_table()

    def read_batch(self, trace, version, batch):
        return self.conn.execute(
            "SELECT count(*) FROM lake.output WHERE trace = ? AND version = ? AND batch = ?", (trace, version, batch)
        ).fetchone()[0]

    def close(self):
        self.conn.close()
