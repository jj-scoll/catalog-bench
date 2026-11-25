import json

from bench.site import build, main


def _run(option="ducklake", **extra):
    return {"option": option, "traces": 6, "batches_per_trace": 300, "rows_per_batch": 5000, "workers": 16,
            "target_batches_per_s": 1.25, "batches_per_s": 1.27, "behind_max_ms": 0.1, "commit_p50_ms": 90.0,
            "commit_p95_ms": 200.0, "finalize_max_ms": 80.0, "finalize_retries": 0, "read_trace_p50_ms": 300.0,
            "read_trace_after_maintain_p50_ms": None, "maintain_seconds": None, "redelivered": 36, "write_errors": 0,
            "batches_failed": 0, "rows_missing": 0, "rows_duplicated": 0, "rows_stale": 0, "verified": True,
            "reprocess_verified": True, **extra}


def _write(results, name, runs, **doc):
    directory = results / "pipeline"
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(json.dumps({"workload": "pipeline", "environment": "lab", "results": runs, **doc}))


def _shapes(results):
    return {s["id"]: s for s in build(results)["pipeline"]["shapes"]}


def test_no_pipeline_directory_gives_an_empty_section_and_leaves_the_ramp_alone(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"environment": "lab", "results": [
        {"system": "cat", "writers": 1, "target_seconds": 30.0, "commits_per_s": 10.0}]}))
    data = build(tmp_path)
    assert data["pipeline"]["shapes"] == [] and data["pipeline"]["skipped"] == []
    assert [e["label"] for e in data["environments"]] == ["lab"]


def test_pipeline_files_never_join_the_commit_ramp(tmp_path):
    _write(tmp_path, "ec2-ducklake-aurora-sv2-rate.json", [_run()])
    data = build(tmp_path)
    assert data["environments"] == [] and data["skipped"] == []


def test_backing_and_shape_come_from_the_file_name(tmp_path):
    _write(tmp_path, "ec2-ducklake-aurora-sv2-big.json", [_run(traces=1, batches_per_trace=9000, target_batches_per_s=None)])
    _write(tmp_path, "ec2-ducklake-aurora-prov-rate.json", [_run()])
    _write(tmp_path, "ec2-ducklake-local-postgres-rate.json", [_run()])
    _write(tmp_path, "ec2-files-s3-rate.json", [_run("files")])
    _write(tmp_path, "ec2-iceberg-per-batch-s3tables-rate-small.json", [_run("iceberg-per-batch", traces=3)])
    _write(tmp_path, "ec2-iceberg-per-batch-s3tables-half-rate.json", [_run("iceberg-per-batch")])
    _write(tmp_path, "ec2-iceberg-per-batch-s3tables-ceiling-small.json", [_run("iceberg-per-batch")])
    _write(tmp_path, "ec2-iceberg-per-trace-sql-ceiling.json", [_run("iceberg-per-trace")])
    shapes = _shapes(tmp_path)
    assert list(shapes) == ["rate", "ceiling", "big", "rate-small", "half-rate", "ceiling-small"]
    assert [(r["option"], r["backing"], r["backing_label"]) for r in shapes["rate"]["runs"]] == [
        ("ducklake", "aurora-prov", "Aurora PostgreSQL, provisioned"),
        ("ducklake", "local-postgres", "Postgres on the runner"),
        ("files", "s3", "S3, no catalog"),
    ]
    assert shapes["big"]["runs"][0]["backing_label"] == "Aurora Serverless v2"
    assert shapes["big"]["runs"][0]["file"] == "ec2-ducklake-aurora-sv2-big.json"
    assert shapes["rate-small"]["runs"][0]["backing_label"] == "Amazon S3 Tables"
    assert shapes["ceiling"]["runs"][0]["backing_label"] == "Iceberg SQL catalog on Postgres"
    assert {"half-rate", "ceiling-small"} <= set(shapes)


def test_a_run_tag_in_the_file_name_is_not_the_option_the_backing_or_the_shape(tmp_path):
    new = ["vortex", "delta-per-trace", "delta-per-batch", "lance-per-trace", "lance-per-batch"]
    _write(tmp_path, "ec2-files-s3-rate.json", [_run("files")])
    for option in ["files", *new]:
        _write(tmp_path, f"ec2-run3-{option}-s3-rate.json", [_run(option)])
    _write(tmp_path, "ec2-run3-lance-per-batch-s3-ceiling.json", [_run("lance-per-batch", target_batches_per_s=None)])
    _write(tmp_path, "ec2-run9-ducklake-newstore-rate.json", [_run()])
    shapes = _shapes(tmp_path)
    assert list(shapes) == ["rate", "ceiling"]
    rate = shapes["rate"]["runs"]
    assert [(r["option"], r["backing"], r["backing_label"], r["run"]) for r in rate] == [
        ("delta-per-batch", "s3", "S3, no catalog", "run3"),
        ("delta-per-trace", "s3", "S3, no catalog", "run3"),
        ("ducklake", "newstore", "newstore", "run9"),
        ("files", "s3", "S3, no catalog", None),  # the earlier run comes first
        ("files", "s3", "S3, no catalog", "run3"),
        ("lance-per-batch", "s3", "S3, no catalog", "run3"),
        ("lance-per-trace", "s3", "S3, no catalog", "run3"),
        ("vortex", "s3", "S3, no catalog", "run3"),
    ]
    assert all("one instance" in r["note"] for r in rate if r["run"] == "run3")
    assert [r["note"] for r in rate if r["run"] != "run3"] == [None, None]
    (ceiling,) = shapes["ceiling"]["runs"]
    assert (ceiling["option"], ceiling["backing"], ceiling["run"], ceiling["note"]) == ("lance-per-batch", "s3", "run3", None)


def test_shape_load_is_read_from_the_runs_and_falls_back_to_the_known_shape(tmp_path):
    _write(tmp_path, "ec2-ducklake-polaris-rate.json", [_run(traces=5, batches_per_trace=10, target_batches_per_s=2.0)])
    _write(tmp_path, "ec2-ducklake-lakekeeper-burst.json", [{"option": "ducklake", "batches_per_s": 3.0}])
    _write(tmp_path, "ec2-ducklake-polaris-ceiling.json", [_run(traces=4, batches_per_trace=500, target_batches_per_s=None)])
    shapes = _shapes(tmp_path)
    assert (shapes["rate"]["traces"], shapes["rate"]["batches_per_trace"], shapes["rate"]["target_batches_per_s"]) == (5, 10, 2.0)
    assert (shapes["burst"]["traces"], shapes["burst"]["batches_per_trace"], shapes["burst"]["target_batches_per_s"]) == (6, 1500, 12.5)
    assert (shapes["ceiling"]["traces"], shapes["ceiling"]["target_batches_per_s"]) == (4, None)
    assert all(isinstance(s["name"], str) and s["name"] for s in shapes.values())


def test_unknown_backing_and_shape_keep_what_the_file_name_says(tmp_path):
    _write(tmp_path, "ec2-ducklake-newstore-soak.json", [_run()])
    shape = build(tmp_path)["pipeline"]["shapes"][0]
    assert shape["id"] == "soak"
    assert (shape["runs"][0]["backing"], shape["runs"][0]["backing_label"]) == ("newstore", "newstore")


def test_kept_up_compares_lateness_with_the_limit_and_is_unknown_when_unpaced(tmp_path):
    _write(tmp_path, "ec2-a-sql-rate.json", [_run("a", behind_max_ms=0.4)])
    _write(tmp_path, "ec2-b-sql-rate.json", [_run("b", behind_max_ms=49679.0, batches_per_s=1.13)])
    _write(tmp_path, "ec2-c-sql-rate.json", [_run("c", target_batches_per_s=None, behind_max_ms=None)])
    _write(tmp_path, "ec2-d-sql-rate.json", [_run("d", behind_max_ms=None, batches_per_s=0.5)])
    data = build(tmp_path)["pipeline"]
    assert [r["kept_up"] for r in data["shapes"][0]["runs"]] == [True, False, None, False]
    assert data["behind_limit_ms"] > 0.4


def test_all_verified_needs_zero_bad_rows_and_a_verified_reprocess(tmp_path):
    _write(tmp_path, "ec2-a-sql-rate.json", [_run("a")])
    _write(tmp_path, "ec2-b-sql-rate.json", [_run("b", rows_duplicated=3)])
    _write(tmp_path, "ec2-c-sql-rate.json", [_run("c", reprocess_verified=False)])
    _write(tmp_path, "ec2-d-sql-rate.json", [_run("d", verified=False)])
    _write(tmp_path, "ec2-e-sql-rate.json", [_run("e", verified_after_maintain=False)])
    _write(tmp_path, "ec2-f-sql-rate.json", [{"option": "f"}])
    runs = build(tmp_path)["pipeline"]["shapes"][0]["runs"]
    assert [r["all_verified"] for r in runs] == [True, False, False, False, False, None]


def test_failed_runs_missing_keys_and_broken_files_are_tolerated(tmp_path):
    _write(tmp_path, "ec2-ducklake-sql-rate.json", [{"option": "ducklake", "failed": "RuntimeError: no catalog"}, "junk", {"surprise": 1}])
    (tmp_path / "pipeline" / "broken.json").write_text("{")
    (tmp_path / "pipeline" / "list.json").write_text("[]")
    data = build(tmp_path)["pipeline"]
    (run,) = data["shapes"][0]["runs"]
    assert run["failed"] == "RuntimeError: no catalog"
    assert run["commit_p50_ms"] is None and run["kept_up"] is None and run["all_verified"] is None
    assert sorted(s["file"] for s in data["skipped"]) == ["broken.json", "list.json"]


def test_options_carry_the_descriptions_and_versions_the_files_state(tmp_path):
    _write(tmp_path, "ec2-ducklake-sql-rate.json", [_run()], descriptions={"ducklake": "One transaction\nper batch."}, versions={"ducklake": "v1"})
    _write(tmp_path, "ec2-ducklake-sql-burst.json", [_run()], descriptions={"ducklake": "One transaction\nper batch."}, versions={"ducklake": "v2"},
           machine={"cpus": 16}, ran_at="2026-01-02T00:00:00+00:00")
    data = build(tmp_path)["pipeline"]
    assert data["options"] == [{"name": "ducklake", "description": "One transaction\nper batch.", "versions": ["v2", "v1"]}]
    assert data["environments"] == ["lab"] and data["machines"] == [{"cpus": 16}] and data["ran_at"] == ["2026-01-02T00:00:00+00:00"]


def test_a_file_of_repeats_gives_one_row_per_repeat_under_its_own_shape(tmp_path):
    short = {"traces": 2, "target_batches_per_s": None, "batches_per_trace": 250}
    _write(tmp_path, "ec2-run4-iceberg-duckdb-per-batch-s3tables-rate-short.json", [_run("iceberg-duckdb-per-batch", traces=2) for _ in range(3)])
    _write(tmp_path, "ec2-run4-iceberg-duckdb-per-batch-s3tables-ceiling-short.json", [_run("iceberg-duckdb-per-batch", **short) for _ in range(3)])
    _write(tmp_path, "ec2-run4-files-s3-ceiling-short.json", [_run("files", **short) for _ in range(3)])
    _write(tmp_path, "ec2-iceberg-per-batch-s3tables-ceiling-small.json", [_run("iceberg-per-batch")])
    shapes = _shapes(tmp_path)
    assert list(shapes) == ["ceiling-small", "rate-short", "ceiling-short"]
    assert (shapes["rate-short"]["name"], shapes["ceiling-short"]["name"]) == ("Steady rate, two traces", "Ceiling, two traces")
    assert (shapes["ceiling-short"]["traces"], shapes["ceiling-short"]["batches_per_trace"], shapes["ceiling-short"]["target_batches_per_s"]) == (2, 250, None)
    rows = [(r["option"], r["backing_label"], r["run"], r["note"]) for r in shapes["ceiling-short"]["runs"]]
    assert rows == [("files", "S3, no catalog", "run4", "one of three repeats")] * 3 + [
        ("iceberg-duckdb-per-batch", "Amazon S3 Tables", "run4", "one of three repeats")] * 3
    assert len(shapes["rate-short"]["runs"]) == 3 and shapes["ceiling-small"]["runs"][0]["note"] is None


def test_main_writes_the_pipeline_section(tmp_path, capsys):
    _write(tmp_path, "ec2-ducklake-sql-rate.json", [_run()])
    out = tmp_path / "site" / "data.json"
    main(["--results", str(tmp_path), "--out", str(out)])
    assert json.loads(out.read_text())["pipeline"]["shapes"][0]["runs"][0]["option"] == "ducklake"
    assert "pipeline" in capsys.readouterr().out
