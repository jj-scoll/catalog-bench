import json
import os
import sqlite3
import sys

from bench import run as bench_run
from tests.test_harness import SqliteSystem


class FreshSystem(SqliteSystem):
    """Like a catalog on a brand-new server: nothing exists until setup() has run."""

    name = "fresh-fake"

    def __init__(self):
        super().__init__(os.environ["FRESH_FAKE_PATH"])

    def version(self):
        with sqlite3.connect(self.path) as c:
            c.execute("SELECT count(*) FROM commits")  # raises until setup() has created the table
        return "fresh 1.0"


def test_a_system_is_asked_for_its_version_only_after_it_has_been_set_up(tmp_path, monkeypatch):
    out = tmp_path / "out.json"
    monkeypatch.setenv("FRESH_FAKE_PATH", str(tmp_path / "db"))
    monkeypatch.setitem(bench_run.SYSTEMS, "fresh-fake", FreshSystem)
    monkeypatch.setattr(sys, "argv", ["bench.run", "--systems", "fresh-fake", "--writers", "1", "--seconds", "0.2", "--out", str(out)])

    bench_run.main()

    doc = json.loads(out.read_text())
    assert doc["versions"] == {"fresh-fake": "fresh 1.0"}
    assert doc["results"][0]["verified"] is True
