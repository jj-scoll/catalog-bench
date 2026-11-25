import sqlite3
import time
from dataclasses import dataclass

import pytest

from bench.harness import run


@dataclass
class SqliteSystem:
    """Stand-in catalog backed by a SQLite file, so the harness can be tested without services."""

    path: str
    lossy: bool = False  # acknowledge commits without writing them

    name = "sqlite-fake"

    def setup(self):
        with sqlite3.connect(self.path) as c:
            c.execute("DROP TABLE IF EXISTS commits")
            c.execute("CREATE TABLE commits (writer INTEGER, seq INTEGER)")

    def connect(self):
        return _Conn(sqlite3.connect(self.path, timeout=30), self.lossy)

    def count(self):
        with sqlite3.connect(self.path) as c:
            return c.execute("SELECT count(*) FROM commits").fetchone()[0]


class _Conn:
    def __init__(self, conn, lossy):
        self.conn, self.lossy = conn, lossy

    def commit_one(self, writer, seq):
        if self.lossy:
            return
        self.conn.execute("INSERT INTO commits VALUES (?, ?)", (writer, seq))
        self.conn.commit()

    def close(self):
        self.conn.close()


def test_counts_acknowledged_commits_and_verifies_them(tmp_path):
    result = run(SqliteSystem(str(tmp_path / "db")), writers=3, seconds=0.5)

    assert result["system"] == "sqlite-fake"
    assert result["writers"] == 3
    assert result["commits"] > 0
    assert result["rows_found"] == result["commits"]
    assert result["verified"] is True
    assert result["commits_per_s"] == result["commits"] / result["seconds"]
    assert 0 < result["p50_ms"] <= result["p95_ms"] <= result["p99_ms"] <= result["max_ms"]
    assert 0 < result["mean_ms"] <= result["max_ms"]


def test_flags_a_system_that_acknowledges_commits_it_did_not_keep(tmp_path):
    result = run(SqliteSystem(str(tmp_path / "db"), lossy=True), writers=2, seconds=0.3)

    assert result["commits"] > 0
    assert result["rows_found"] == 0
    assert result["verified"] is False


class BrokenSystem(SqliteSystem):
    def connect(self):
        raise ConnectionError("catalog unreachable")


def test_a_writer_that_cannot_connect_fails_the_run_instead_of_hanging(tmp_path):
    with pytest.raises(RuntimeError, match="catalog unreachable"):
        run(BrokenSystem(str(tmp_path / "db")), writers=2, seconds=0.3)


class FlakySystem(SqliteSystem):
    def connect(self):
        return _FlakyConn(sqlite3.connect(self.path, timeout=30), False)


class _FlakyConn(_Conn):
    def commit_one(self, writer, seq):
        if seq % 2:
            raise TimeoutError("commit conflict, retries exhausted")
        super().commit_one(writer, seq)


def test_failed_commits_are_counted_and_their_messages_recorded(tmp_path):
    result = run(FlakySystem(str(tmp_path / "db")), writers=2, seconds=0.3)

    assert result["errors"] > 0
    assert result["verified"] is True
    assert result["error_samples"] == {"TimeoutError: commit conflict, retries exhausted": result["errors"]}


def test_failed_commits_are_grouped_by_exception_type_with_one_message_each(tmp_path):
    result = run(FlakySystem(str(tmp_path / "db")), writers=2, seconds=0.3)

    assert result["error_types"] == {
        "TimeoutError": {"count": result["errors"], "example": "TimeoutError: commit conflict, retries exhausted"}
    }


class SlowSystem(SqliteSystem):
    def connect(self):
        return _SlowConn(sqlite3.connect(self.path, timeout=30), False)


class _SlowConn(_Conn):
    def commit_one(self, writer, seq):
        time.sleep(0.5)
        super().commit_one(writer, seq)


def test_rate_uses_the_time_actually_taken_when_commits_outlast_the_period(tmp_path):
    result = run(SlowSystem(str(tmp_path / "db")), writers=2, seconds=0.2)

    assert result["target_seconds"] == 0.2
    assert result["seconds"] >= 0.5
    assert result["commits_per_s"] == result["commits"] / result["seconds"]


def test_reports_how_many_commits_each_writer_got(tmp_path):
    result = run(SqliteSystem(str(tmp_path / "db")), writers=3, seconds=0.3)

    assert len(result["per_writer_commits"]) == 3
    assert sum(result["per_writer_commits"]) == result["commits"]


class SplitSystem(SqliteSystem):
    def connect(self):
        return _SplitConn(sqlite3.connect(self.path, timeout=30), False)


class _SplitConn(_Conn):
    def commit_one(self, writer, seq):
        super().commit_one(writer, seq)
        return 0.001  # seconds of this commit spent in the catalog call


def test_reports_catalog_call_time_when_a_system_provides_it(tmp_path):
    split = run(SplitSystem(str(tmp_path / "a")), writers=1, seconds=0.2)
    plain = run(SqliteSystem(str(tmp_path / "b")), writers=1, seconds=0.2)

    assert split["catalog_p50_ms"] == 1.0
    assert plain["catalog_p50_ms"] is None
