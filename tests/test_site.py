import json

from bench.site import build, main


def _run(system, writers, rate, **extra):
    return {"system": system, "writers": writers, "target_seconds": 30.0, "seconds": 30.0, "commits": int(rate * 30),
            "errors": 0, "commits_per_s": rate, "p50_ms": 10.0, "verified": True, **extra}


def _write(directory, name, results, environment="lab", **doc):
    (directory / name).write_text(json.dumps({"environment": environment, "results": results, **doc}))


def _system(data, name, environment=0):
    return next(s for s in data["environments"][environment]["systems"] if s["name"] == name)


def test_peak_is_the_median_of_repetitions_at_the_best_writer_count(tmp_path):
    _write(tmp_path, "a.json", [_run("cat", 1, r) for r in (90, 100, 120)] + [_run("cat", 4, r) for r in (80, 300, 200)])
    peak = _system(build(tmp_path), "cat")["peak"]
    assert (peak["writers"], peak["commits_per_s"], peak["min"], peak["max"], peak["reps"]) == (4, 200, 80, 300, 3)


def test_repetitions_may_be_spread_over_several_files_of_one_environment(tmp_path):
    _write(tmp_path, "ec2-1.json", [_run("cat", 1, 100)])
    _write(tmp_path, "ec2-2.json", [_run("cat", 1, 300)])
    data = build(tmp_path)
    assert len(data["environments"]) == 1
    assert _system(data, "cat")["points"][0]["reps"] == 2


def test_environments_and_run_lengths_never_share_a_ranking(tmp_path):
    _write(tmp_path, "a.json", [_run("cat", 1, 100)], environment="laptop")
    _write(tmp_path, "b.json", [_run("cat", 1, 900)], environment="ec2")
    _write(tmp_path, "c.json", [_run("cat", 1, 5, target_seconds=5.0)], environment="ec2")
    envs = build(tmp_path)["environments"]
    assert sorted((e["label"], e["target_seconds"]) for e in envs) == [("ec2", 5), ("ec2", 30), ("laptop", 30)]
    assert len({e["id"] for e in envs}) == 3
    assert all(len(e["systems"][0]["points"]) == 1 for e in envs)


def test_short_or_unrepeated_runs_are_marked_not_comparable(tmp_path):
    _write(tmp_path, "long.json", [_run("cat", 1, r) for r in (1, 2, 3)], environment="long")
    _write(tmp_path, "once.json", [_run("cat", 1, 1)], environment="once")
    _write(tmp_path, "short.json", [_run("cat", 1, r, target_seconds=5.0) for r in (1, 2, 3)], environment="short")
    envs = {e["label"]: e for e in build(tmp_path)["environments"]}
    assert envs["long"]["comparable"] is True and envs["long"]["caveats"] == []
    assert envs["once"]["comparable"] is False and "repetition" in envs["once"]["caveats"][0]
    assert envs["short"]["comparable"] is False and "30" in envs["short"]["caveats"][0]


def test_fairness_catalog_share_and_failure_rate(tmp_path):
    _write(tmp_path, "a.json", [
        _run("cat", 4, 2.0, per_writer_commits=[1, 1, 2, 10], p50_ms=600.0, catalog_p50_ms=30.0, max_ms=2800.0, commits=14, errors=2),
        _run("cat", 4, 2.0, per_writer_commits=[0, 3, 3, 400], p50_ms=600.0, catalog_p50_ms=30.0, max_ms=5000.0, commits=406, errors=0, verified=False),
    ])
    system = _system(build(tmp_path), "cat")
    point = system["points"][0]
    assert (point["writer_min"], point["writer_max"]) == (0, 400)
    assert point["catalog_share"] == 0.05
    assert point["max_ms"] == 5000.0
    assert point["error_rate"] == 2 / 422
    assert point["verified"] is False and system["verified"] is False


def test_missing_and_unknown_keys_do_not_break_the_build(tmp_path):
    _write(tmp_path, "sparse.json", [
        {"system": "cat", "writers": 2, "commits_per_s": 7.0, "seconds": 10.0, "some_new_metric": [1, 2]},
        {"system": "cat"},  # unusable: no rate
        "not a result",
    ], something_new={"a": 1})
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "list.json").write_text("[1, 2]")
    data = build(tmp_path)
    point = _system(data, "cat")["points"][0]
    assert point["commits_per_s"] == 7.0
    assert point["writer_min"] is None and point["catalog_share"] is None and point["error_rate"] is None
    assert point["verified"] is None
    assert sorted(s["file"] for s in data["skipped"]) == ["broken.json", "list.json"]


def test_a_different_client_or_table_layout_is_a_separate_line(tmp_path):
    _write(tmp_path, "a.json", [_run("cat", 1, 100), _run("cat", 1, 150, client="go"), _run("cat", 1, 900, tables_per_writer=1)])
    names = [s["name"] for s in build(tmp_path)["environments"][0]["systems"]]
    assert len(names) == 3 and len(set(names)) == 3
    assert all(n.startswith("cat") for n in names)


def test_descriptions_versions_and_error_samples_reach_the_system(tmp_path):
    _write(tmp_path, "a.json", [_run("cat", 1, 5, errors=3, error_samples={"Boom: conflict": 3})],
           versions={"cat": "Cat 1.2"}, descriptions={"cat": "A catalog."}, machine={"cpus": 4})
    data = build(tmp_path)
    system = _system(data, "cat")
    assert (system["version"], system["description"]) == ("Cat 1.2", "A catalog.")
    assert system["error_samples"] == [{"message": "Boom: conflict", "count": 3}]
    assert data["environments"][0]["machines"] == [{"cpus": 4}]


def test_a_line_states_only_the_versions_of_the_files_it_came_from(tmp_path):
    _write(tmp_path, "a.json", [_run("cat", 1, 100)], versions={"cat": "Cat 1.2, 1 table"})
    _write(tmp_path, "b.json", [_run("cat", 1, 900, tables_per_writer=1)], versions={"cat": "Cat 1.2, one table per writer"})
    data = build(tmp_path)
    assert _system(data, "cat")["version"] == "Cat 1.2, 1 table"
    assert _system(data, "cat (tables per writer: 1)")["version"] == "Cat 1.2, one table per writer"


def test_main_writes_site_data_json(tmp_path):
    _write(tmp_path, "a.json", [_run("cat", 1, 100)])
    out = tmp_path / "site" / "data.json"
    main(["--results", str(tmp_path), "--out", str(out)])
    assert json.loads(out.read_text())["environments"][0]["systems"][0]["name"] == "cat"
