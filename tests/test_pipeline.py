"""The pipeline workload, driven with the plain-files option on a temporary directory, and with deliberately
broken variants of it to show that verification catches lost, duplicated and stale rows."""

import time
from dataclasses import dataclass

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bench.config import PG
from bench.options import OPTIONS
from bench.options.files import Option as Files
from bench.pipeline import run

SMALL = dict(traces=2, batches=6, rows=20, workers=3, probe_timeout=1)


class _Wrapped:
    """A connection that forwards to the files option's connection unless a subclass overrides a call."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)


class _Appending(_Wrapped):
    def read_trace(self, trace):  # a redelivered batch shows up twice, as it would after two appending commits
        table = self.inner.read_trace(trace)
        again = table.filter(pa.compute.equal(table["batch"], 1))
        return pa.concat_tables([table, again])


class _Dropping(_Wrapped):
    def write_batch(self, trace, version, batch, attempt, rows, errors):
        if batch != 2:
            self.inner.write_batch(trace, version, batch, attempt, rows, errors)


class _Unversioned(_Wrapped):
    def read_trace(self, trace):  # reads every file under the trace, whatever version wrote it
        return pq.read_table(f"{self.inner.root}/output/{trace}", filesystem=self.inner.fs, columns=["version", "batch", "row"])


class _FailsOnce(_Wrapped):
    def write_batch(self, trace, version, batch, attempt, rows, errors):
        if batch == 3 and attempt == 0:
            raise OSError("first delivery fails")
        self.inner.write_batch(trace, version, batch, attempt, rows, errors)


class _OneNumberPerDelivery(_Wrapped):
    def write_batch(self, trace, version, batch, attempt, rows, errors):
        if batch == 3 and attempt == 0:
            raise OSError("first delivery fails")
        open(f"{self.inner.root}/seen-{trace}-{version}-{batch}-{attempt}", "x").close()  # raises if the number was used
        self.inner.write_batch(trace, version, batch, attempt, rows, errors)


class _AlwaysFails(_Wrapped):
    def write_batch(self, trace, version, batch, attempt, rows, errors):
        if batch == 3:
            raise OSError("never lands")
        self.inner.write_batch(trace, version, batch, attempt, rows, errors)


class _Slow(_Wrapped):
    def read_batch(self, trace, version, batch):  # a batch becomes readable 0.3 s after it was written
        written = self.inner.fs.get_file_info(self.inner.key("output", trace, batch)).mtime
        return self.inner.read_batch(trace, version, batch) if written and time.time() - written.timestamp() > 0.3 else 0


@dataclass
class Variant(Files):
    conn: type = _Wrapped

    def connect(self):
        return self.conn(super().connect())


def files(tmp_path, conn=None):
    return Variant(data=str(tmp_path / "data"), conn=conn) if conn else Files(data=str(tmp_path / "data"))


def test_a_clean_run_commits_every_batch_and_reads_back_exactly_what_was_written(tmp_path):
    r = run(files(tmp_path), **SMALL)
    assert r["option"] == "files"
    assert r["batches_committed"] == 12 and r["batches_failed"] == 0
    assert r["rows_missing"] == r["rows_duplicated"] == r["rows_stale"] == 0
    assert r["verified"] and r["reprocess_verified"]
    assert r["commit_p50_ms"] > 0 and r["finalize_max_ms"] > 0 and r["read_trace_p50_ms"] > 0
    assert r["fresh_samples"] > 0 and r["fresh_timeouts"] == 0


def test_a_redelivered_batch_is_counted_and_does_not_break_an_option_that_overwrites(tmp_path):
    r = run(files(tmp_path), **SMALL, redeliver_every=3)
    assert r["redelivered"] == 4
    assert r["verified"] and r["rows_duplicated"] == 0


def test_rows_appended_twice_fail_verification(tmp_path):
    r = run(files(tmp_path, _Appending), **SMALL)
    assert r["rows_duplicated"] == 2 * 20 and not r["verified"]


def test_an_acknowledged_batch_that_is_not_there_fails_verification(tmp_path):
    r = run(files(tmp_path, _Dropping), **SMALL)
    assert r["rows_missing"] == 2 * 20 and not r["verified"]


def test_rows_left_over_from_the_old_version_fail_the_reprocess_check(tmp_path):
    r = run(files(tmp_path, _Unversioned), **SMALL)
    assert r["verified"], "the first load has nothing stale in it"
    assert r["reprocess_rows_stale"] == 3 * 20 and not r["reprocess_verified"]  # the reprocess writes 3 of 6 batches


def test_a_failed_write_is_delivered_again_and_the_run_still_verifies(tmp_path):
    r = run(files(tmp_path, _FailsOnce), **SMALL)
    assert r["write_errors"] >= 2 and r["batches_failed"] == 0 and r["verified"]
    assert "OSError" in r["error_types"]


def test_a_retry_and_a_duplicate_delivery_of_one_batch_never_share_an_attempt_number(tmp_path):
    # An appending store keeps one delivery per batch by its attempt number, so two deliveries with one number are
    # both kept. This happened: a failed first delivery was retried as attempt 1, the number of the duplicate delivery.
    r = run(files(tmp_path, _OneNumberPerDelivery), redeliver_every=1, **SMALL)
    assert "FileExistsError" not in r["error_types"] and r["verified"]


def test_a_batch_that_fails_three_deliveries_is_reported_as_failed(tmp_path):
    r = run(files(tmp_path, _AlwaysFails), **SMALL)
    assert r["batches_failed"] == 2 and r["batches_committed"] == 10
    assert not r["verified"], "a run that lost batches is not a clean run"


def test_an_open_loop_run_keeps_to_its_rate_and_reports_how_far_behind_it_fell(tmp_path):
    r = run(files(tmp_path), traces=1, batches=10, rows=20, workers=2, rate=20)
    assert 0.4 < r["load_seconds"] < 2
    assert r["batches_per_s"] < 25
    assert r["behind_p95_ms"] is not None and r["behind_max_ms"] < 500


def test_time_until_a_batch_is_readable_is_measured(tmp_path):
    r = run(files(tmp_path, _Slow), traces=1, batches=4, rows=20, workers=1, rate=2)
    assert r["fresh_samples"] > 0
    assert r["visible_after_p50_ms"] >= 300


def test_every_registered_option_says_what_it_measures_and_has_the_interface():
    assert {"files", "ducklake", "iceberg-per-batch", "iceberg-per-trace"} <= set(OPTIONS)
    for name, cls in OPTIONS.items():
        assert cls.name == name and cls.__doc__
        for method in ("setup", "connect", "version"):
            assert callable(getattr(cls, method, None)), f"{name} is missing {method}()"


@pytest.mark.parametrize("name", ["ducklake", "iceberg-per-batch", "iceberg-per-trace"])
def test_each_option_passes_the_whole_flow_locally(name, tmp_path):
    if name == "ducklake":  # the only option that cannot run without a service: its catalog is the Postgres in compose.yaml
        try:
            psycopg.connect(f"{PG} dbname=postgres", connect_timeout=2).close()
        except psycopg.OperationalError:
            pytest.skip("Postgres is not running")
    option = OPTIONS[name].local(tmp_path)
    r = run(option, **SMALL, redeliver_every=4)
    assert r["batches_failed"] == 0
    assert r["verified"], {k: r[k] for k in ("rows_missing", "rows_duplicated", "rows_stale", "error_types")}
    assert r["reprocess_verified"], {k: r[k] for k in ("reprocess_rows_missing", "reprocess_rows_duplicated", "reprocess_rows_stale")}
