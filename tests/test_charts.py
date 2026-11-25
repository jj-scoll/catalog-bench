"""The README charts are generated from results/ and committed, so they must be deterministic, valid SVG, and
say what the numbers say: medians per (system, writers), a label for every line, log positions in order."""

import json
import xml.etree.ElementTree as ET

import pytest

from bench import charts

SVG = "{http://www.w3.org/2000/svg}"
PIPELINE_NAMES = [
    "ducklake-aurora-prov-rate",
    "ducklake-aurora-sv2-rate",
    "iceberg-per-batch-s3tables-rate-small",
    "iceberg-per-trace-s3tables-rate",
    "files-s3-rate",
]


def _run(system, writers, rate, **extra):
    return {"system": system, "writers": writers, "commits_per_s": rate, **extra}


@pytest.fixture
def results_dir(tmp_path):
    root = tmp_path / "results"
    (root / "pipeline").mkdir(parents=True)
    files = {
        "ec2-postgres.json": [_run("postgres", w, r) for w in (1, 2, 64) for r in (1000.0 * w, 3000.0 * w, 2000.0 * w)],
        "ec2-lakekeeper.json": [_run("lakekeeper", 1, r) for r in (6.0, 7.0)] + [_run("lakekeeper", 64, 4.0)],
        # a second file for the same system pools into the same (system, writers) cell
        "ec2-run2-lakekeeper.json": [_run("lakekeeper", 1, 100.0), _run("lakekeeper", 1, 6.5)],
        "ec2-polaris.json": [_run("polaris", w, 4.1) for w in (1, 64)],
        "ec2-s3-tables.json": [_run("s3-tables", w, 1.9) for w in (1, 64)],
        "ec2-s3-tables-per-writer.json": [_run("s3-tables", w, 1.9 * w, tables_per_writer=1) for w in (1, 4, 64)],
    }
    for name, runs in files.items():
        (root / name).write_text(json.dumps({"results": runs, "descriptions": {}}))
    for i, name in enumerate(PIPELINE_NAMES):
        slow = "per-batch" in name
        row = {
            "commit_p50_ms": 6132.9 if slow else 90.0 + i,
            "finalize_max_ms": 27367.5 if slow else 80.0 + i,
            "read_trace_p50_ms": 2167.4 if slow else 340.0 + i,
            "batches_per_s": 1.13 if slow else 1.275,
            "target_batches_per_s": 1.25,
            "batches_committed": 900 if slow else 1800,
            "verified": True,
        }
        (root / "pipeline" / f"ec2-{name}.json").write_text(json.dumps({"results": [row]}))
    # a pipeline file is not a commit-rate file, and neither is anything else below the top level
    (root / "pipeline" / "ec2-other-rate.json").write_text(json.dumps({"results": [_run("ghost", 1, 5.0)]}))
    return root


def _texts(svg):
    return ["".join(t.itertext()) for t in ET.fromstring(svg).iter(f"{SVG}text")]


def test_medians_pool_every_top_level_file_per_system_and_writer_count(results_dir):
    medians = charts.load_commit_medians(results_dir)
    assert medians["postgres"] == {1: 2000.0, 2: 4000.0, 64: 128000.0}
    assert medians["lakekeeper"] == {1: 6.75, 64: 4.0}  # median of 6, 6.5, 7, 100
    assert "ghost" not in medians


def test_the_table_per_writer_runs_are_a_series_of_their_own(results_dir):
    medians = charts.load_commit_medians(results_dir)
    assert medians["s3-tables"] == {1: 1.9, 64: 1.9}
    assert medians["s3-tables-per-writer"] == {1: 1.9, 4: 7.6, 64: 121.6}


def test_log_positions_are_monotonic_and_hit_both_ends():
    values = [1, 2, 4, 8, 16, 32, 64]
    xs = [charts.log_pos(v, 1, 64, 100.0, 700.0) for v in values]
    assert xs[0] == pytest.approx(100.0) and xs[-1] == pytest.approx(700.0)
    assert xs == sorted(xs)
    steps = [b - a for a, b in zip(xs, xs[1:])]
    assert steps == pytest.approx([100.0] * 6)  # log2: every doubling is the same distance
    ys = [charts.log_pos(v, 1, 100_000, 440.0, 72.0) for v in (1, 5, 160, 80_000)]
    assert ys == sorted(ys, reverse=True)  # a bigger rate sits higher on the page


@pytest.mark.parametrize("ms, text", [(93.9, "94 ms"), (340.6, "341 ms"), (999.7, "1.0 s"), (6132.9, "6.1 s"),
                                      (9406.7, "9.4 s"), (27367.5, "27 s")])
def test_durations_read_in_human_units(ms, text):
    assert charts.human_ms(ms) == text


def test_crowded_labels_are_spread_apart_without_reordering_or_leaving_the_plot():
    wanted = [20.0, 388.0, 390.0, 395.0, 420.0, 300.0]
    placed = charts.spread_labels(wanted, gap=16.0, lo=72.0, hi=440.0)
    order = sorted(range(len(wanted)), key=wanted.__getitem__)
    ys = [placed[i] for i in order]
    assert all(b - a >= 16.0 - 1e-6 for a, b in zip(ys, ys[1:]))
    assert all(72.0 <= y <= 440.0 for y in ys)
    assert placed[5] == pytest.approx(300.0)  # a label with room stays on its line


def test_main_writes_both_variants_of_both_charts_as_parseable_svg(results_dir, tmp_path):
    out = tmp_path / "img"
    charts.main(results_dir, out)
    names = sorted(p.name for p in out.iterdir())
    assert names == ["commit-rate-dark.svg", "commit-rate.svg", "pipeline-dark.svg", "pipeline.svg"]
    for name in names:
        text = (out / name).read_text()
        root = ET.fromstring(text)
        assert root.tag == f"{SVG}svg"
        assert root.get("viewBox").startswith("0 0 860 ")
        assert root.get("width") is None and root.get("height") is None  # the README sizes it
        assert root.find(f"{SVG}title").text and root.find(f"{SVG}desc").text
        assert "/home/" not in text
    assert (out / "commit-rate.svg").read_text() != (out / "commit-rate-dark.svg").read_text()
    assert (out / "pipeline.svg").read_text() != (out / "pipeline-dark.svg").read_text()


def test_output_is_deterministic(results_dir, tmp_path):
    charts.main(results_dir, tmp_path / "a")
    charts.main(results_dir, tmp_path / "b")
    for name in ("commit-rate.svg", "pipeline-dark.svg"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


@pytest.mark.parametrize("dark", [False, True])
def test_every_system_present_gets_a_readable_label(results_dir, dark):
    texts = _texts(charts.commit_rate_svg(charts.load_commit_medians(results_dir), dark=dark))
    for label in ("Postgres (baseline)", "Lakekeeper", "Polaris", "S3 Tables, shared table",
                  "S3 Tables, table per writer"):
        assert texts.count(label) == 1, label
    assert "DuckLake" not in texts  # absent from the data, so absent from the chart
    assert "Acknowledged commits per second, by number of writers" in texts


def test_an_unknown_system_is_still_drawn_and_labelled_by_its_name():
    texts = _texts(charts.commit_rate_svg({"new-catalog": {1: 3.0, 64: 9.0}}))
    assert "new-catalog" in texts


def test_commit_rate_labels_do_not_overlap(results_dir):
    root = ET.fromstring(charts.commit_rate_svg(charts.load_commit_medians(results_dir)))
    ys = sorted(float(t.get("y")) for t in root.iter(f"{SVG}text") if t.get("class") == "series-label")
    assert len(ys) == 5
    assert all(b - a >= 15.0 for a, b in zip(ys, ys[1:]))


@pytest.mark.parametrize("dark", [False, True])
def test_pipeline_chart_has_three_panels_five_rows_and_a_value_on_every_bar(results_dir, dark):
    rows = charts.load_pipeline(results_dir / "pipeline")
    assert [r["name"] for r in rows] == PIPELINE_NAMES
    svg = charts.pipeline_svg(rows, dark=dark)
    texts = _texts(svg)
    for expected in ("One pipeline flow at 1.25 batches per second", "Write one batch (median)",
                     "Close a trace (slowest)", "Read one trace back (median)", "DuckLake, Aurora provisioned",
                     "DuckLake, Aurora Serverless v2", "Iceberg per batch, S3 Tables",
                     "Iceberg per trace, S3 Tables", "Parquet files on S3", "6.1 s", "27 s", "2.2 s", "90 ms"):
        assert expected in texts, expected
    bars = [p for p in ET.fromstring(svg).iter(f"{SVG}path") if p.get("class") == "bar"]
    assert len(bars) == 15


def test_only_the_row_that_fell_behind_schedule_says_so(results_dir):
    rows = charts.load_pipeline(results_dir / "pipeline")
    behind = [t for t in _texts(charts.pipeline_svg(rows)) if "behind schedule" in t]
    assert behind == ["behind schedule: 1.13 batches/s"]
    for row in rows:
        row["batches_per_s"] = 1.27
    assert not [t for t in _texts(charts.pipeline_svg(rows)) if "behind schedule" in t]


def test_longer_bars_mean_longer_durations(results_dir):
    rows = charts.load_pipeline(results_dir / "pipeline")
    root = ET.fromstring(charts.pipeline_svg(rows))
    bars = [p for p in root.iter(f"{SVG}path") if p.get("class") == "bar"]
    by_ms = sorted(bars, key=lambda p: float(p.get("data-ms")))
    lengths = [float(p.get("data-length")) for p in by_ms]
    assert lengths == sorted(lengths) and lengths[0] > 0
