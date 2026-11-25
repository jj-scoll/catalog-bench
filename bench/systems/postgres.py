from dataclasses import dataclass

import psycopg

from bench.config import PG


@dataclass
class System:
    """Baseline, not a catalog: one single-row INSERT per autocommit transaction into a plain Postgres table."""

    dsn: str = f"{PG} dbname=postgres"
    name = "postgres"

    def setup(self):
        with psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute("DROP TABLE IF EXISTS commits")
            c.execute("CREATE TABLE commits (writer INTEGER, seq INTEGER)")

    def connect(self):
        return _Conn(psycopg.connect(self.dsn, autocommit=True))

    def count(self):
        with psycopg.connect(self.dsn) as c:
            return c.execute("SELECT count(*) FROM commits").fetchone()[0]

    def version(self):
        with psycopg.connect(self.dsn) as c:
            return f"PostgreSQL {c.execute('SHOW server_version').fetchone()[0]}, psycopg {psycopg.__version__}"


class _Conn:
    def __init__(self, conn):
        self.conn = conn

    def commit_one(self, writer, seq):
        self.conn.execute("INSERT INTO commits VALUES (%s, %s)", (writer, seq))

    def close(self):
        self.conn.close()
