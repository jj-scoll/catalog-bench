"""Draw the README charts as plain SVG text from results/, in a light and a dark variant.

    uv run python -m bench.charts

No plotting library: the output is small, deterministic and diffable, and every label is real <text>."""

import json
import math
import statistics
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).parent.parent
WIDTH = 860
FONT = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

# Ink and chrome per variant. The dark values are chosen for GitHub's dark page (#0d1117), not flipped.
THEMES = {
    False: {"ink": "#0b0b0b", "ink2": "#52514e", "grid": "#e1e0d9", "axis": "#c3c2b7", "reference": "#898781",
            "warning": "#ec835a",
            "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]},
    True: {"ink": "#f0efec", "ink2": "#c3c2b7", "grid": "#262c36", "axis": "#484f58", "reference": "#898781",
           "warning": "#ec835a",
           "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9"]},
}

# Series key -> (label, colour slot). The slot belongs to the system, so a missing system never repaints the
# others. None is the reference style: grey and dashed.
SERIES = {
    "postgres": ("Postgres (baseline)", None),
    "ducklake-postgres": ("DuckLake", 0),
    "ducklake-postgres-no-inlining": ("DuckLake, inlining off", 1),
    "iceberg-sql-postgres": ("Iceberg SQL catalog", 2),
    "lakekeeper": ("Lakekeeper", 3),
    "polaris": ("Polaris", 4),
    "s3-tables": ("S3 Tables, shared table", 5),
    "s3-tables-per-writer": ("S3 Tables, table per writer", 6),
}

PIPELINE_ROWS = [
    ("ducklake-aurora-prov-rate", "DuckLake, Aurora provisioned"),
    ("ducklake-aurora-sv2-rate", "DuckLake, Aurora Serverless v2"),
    ("iceberg-per-batch-s3tables-rate-small", "Iceberg per batch, S3 Tables"),
    ("iceberg-per-trace-s3tables-rate", "Iceberg per trace, S3 Tables"),
    ("files-s3-rate", "Parquet files on S3"),
]
PIPELINE_PANELS = [
    ("commit_p50_ms", "Write one batch (median)"),
    ("finalize_max_ms", "Close a trace (slowest)"),
    ("read_trace_p50_ms", "Read one trace back (median)"),
]
ON_SCHEDULE_BATCHES_PER_S = 1.2  # the target is 1.25; below this the run did not keep up


def log_pos(value, lo, hi, start, end):
    """Where `value` falls between pixel `start` (at `lo`) and `end` (at `hi`) on a log scale."""
    return start + (end - start) * (math.log(value) - math.log(lo)) / (math.log(hi) - math.log(lo))


def human_ms(ms):
    if ms < 999.5:
        return f"{ms:.0f} ms"
    seconds = ms / 1000
    return f"{seconds:.1f} s" if seconds < 9.95 else f"{seconds:.0f} s"


def spread_labels(wanted, gap, lo, hi):
    """Move labels as little as possible so neighbours are at least `gap` apart and all stay within [lo, hi].
    Labels that crowd each other become one evenly spaced group centred on where they wanted to be."""
    order = sorted(range(len(wanted)), key=lambda i: wanted[i])
    groups = [[i] for i in order]

    def top(group):
        centre = sum(wanted[i] for i in group) / len(group)
        span = gap * (len(group) - 1)
        return min(max(centre - span / 2, lo), max(lo, hi - span))

    merged = True
    while merged:
        merged = False
        for k in range(len(groups) - 1):
            if top(groups[k]) + gap * len(groups[k]) > top(groups[k + 1]) + 1e-9:
                groups[k:k + 2] = [groups[k] + groups[k + 1]]
                merged = True
                break
    placed = [0.0] * len(wanted)
    for group in groups:
        for n, i in enumerate(group):
            placed[i] = top(group) + gap * n
    return placed


def load_commit_medians(results_dir):
    """{series: {writers: median commits/s}} over every top-level results file. The S3 Tables runs that gave
    each writer its own table carry `tables_per_writer` and are a series of their own."""
    samples = {}
    for path in sorted(Path(results_dir).glob("*.json")):
        for run in json.loads(path.read_text())["results"]:
            series = run["system"] + ("-per-writer" if run.get("tables_per_writer") else "")
            samples.setdefault(series, {}).setdefault(run["writers"], []).append(run["commits_per_s"])
    return {series: {w: statistics.median(rates) for w, rates in sorted(cells.items())}
            for series, cells in samples.items()}


def load_pipeline(pipeline_dir):
    rows = []
    for name, label in PIPELINE_ROWS:
        path = Path(pipeline_dir) / f"ec2-{name}.json"
        if path.exists():
            rows.append({"name": name, "label": label, **json.loads(path.read_text())["results"][0]})
    return rows


def _n(value):
    return f"{value:.1f}"


def _text(x, y, content, fill, size=12, anchor="start", weight=None, cls=None):
    extra = (f' font-weight="{weight}"' if weight else "") + (f' class="{cls}"' if cls else "")
    return (f'<text x="{_n(x)}" y="{_n(y)}" font-size="{size}" fill="{fill}" text-anchor="{anchor}"{extra}>'
            f"{escape(content)}</text>")


def _document(height, title, subtitle, desc, theme, body):
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" role="img" '
        f'aria-labelledby="chart-title chart-desc" font-family="{FONT}">',
        f'<title id="chart-title">{escape(title)}</title>',
        f'<desc id="chart-desc">{escape(desc)}</desc>',
        _text(16, 28, title, theme["ink"], size=17, weight=600),
        _text(16, 49, subtitle, theme["ink2"], size=12.5),
        *body,
        "</svg>",
    ]) + "\n"


def _rate(value):
    return f"{value:,.0f}" if value >= 100 else f"{value:.1f}" if value >= 10 else f"{value:.2f}"


def _decade(value):
    return f"{value / 1000:.0f}K" if value >= 1000 else f"{value:.0f}"


def commit_rate_svg(medians, dark=False):
    theme = THEMES[dark]
    title = "Acknowledged commits per second, by number of writers"
    subtitle = "Median of three 60-second runs per point. EC2 c6id.4xlarge, Python clients. Both axes are log scales."
    left, right, top, bottom, height = 56, 618, 86, 452, 500
    order = [s for s in SERIES if s in medians] + sorted(s for s in medians if s not in SERIES)
    writers = sorted({w for s in order for w in medians[s]})
    rates = [r for s in order for r in medians[s].values()]
    x_lo, x_hi = writers[0], max(writers[-1], writers[0] * 2)
    y_lo, y_hi = 10 ** math.floor(math.log10(min(rates))), 10 ** math.ceil(math.log10(max(rates)))
    if y_lo == y_hi:
        y_hi *= 10

    def x(w):
        return log_pos(w, x_lo, x_hi, left, right)

    def y(rate):
        return log_pos(rate, y_lo, y_hi, bottom, top)

    body = [_text(left - 40, top - 16, "commits per second", theme["ink2"], size=11.5)]
    for exp in range(round(math.log10(y_lo)), round(math.log10(y_hi)) + 1):
        tick = 10.0 ** exp
        colour = theme["axis"] if tick == y_lo else theme["grid"]
        body.append(f'<line x1="{left}" x2="{right}" y1="{_n(y(tick))}" y2="{_n(y(tick))}" stroke="{colour}"/>')
        body.append(_text(left - 10, y(tick) + 4, _decade(tick), theme["ink2"], size=11.5, anchor="end"))
    for w in writers:
        body.append(f'<line x1="{_n(x(w))}" x2="{_n(x(w))}" y1="{bottom}" y2="{bottom + 5}" stroke="{theme["axis"]}"/>')
        body.append(_text(x(w), bottom + 20, str(w), theme["ink2"], size=11.5, anchor="middle"))
    body.append(_text((left + right) / 2, bottom + 40, "writers committing at once", theme["ink2"], size=11.5,
                      anchor="middle"))

    ends = [(max(medians[s]), medians[s][max(medians[s])]) for s in order]
    label_ys = spread_labels([y(rate) for _, rate in ends], gap=17.0, lo=top, hi=bottom)
    summary = []
    for series, (end_w, end_rate), label_y in zip(order, ends, label_ys):
        label, slot = SERIES.get(series, (series, None))
        colour = theme["reference"] if slot is None else theme["series"][slot]
        dash = ' stroke-dasharray="6 4"' if slot is None else ""
        points = [(w, rate) for w, rate in medians[series].items()]
        path = " ".join(f"{'M' if i == 0 else 'L'}{_n(x(w))} {_n(y(rate))}" for i, (w, rate) in enumerate(points))
        body.append(f'<g><path d="{path}" fill="none" stroke="{colour}" stroke-width="2" stroke-linejoin="round" '
                    f'stroke-linecap="round"{dash}/>')
        for w, rate in points:
            body.append(f'<circle cx="{_n(x(w))}" cy="{_n(y(rate))}" r="3" fill="{colour}">'
                        f"<title>{escape(label)}, {w} writers: {_rate(rate)} commits/s</title></circle>")
        # a leader in the line's own colour ties the label to its line where labels had to move apart
        body.append(f'<path d="M{_n(x(end_w) + 6)} {_n(y(end_rate))} L{_n(right + 22)} {_n(label_y)} '
                    f'H{_n(right + 34)}" fill="none" stroke="{colour}" stroke-width="1.5"{dash}/>')
        body.append(_text(right + 40, label_y + 4, label, theme["ink"], cls="series-label") + "</g>")
        first_w = min(medians[series])
        summary.append(f"{label}: {_rate(medians[series][first_w])} at {first_w} "
                       f"writer{'s' if first_w != 1 else ''}, {_rate(end_rate)} at {end_w}")
    desc = ("Line chart of median acknowledged commits per second against the number of concurrent writers, "
            "both on log scales. " + ". ".join(summary) + ".")
    return _document(height, title, subtitle, desc, theme, body)


def pipeline_svg(rows, dark=False):
    theme = THEMES[dark]
    title = "One pipeline flow at 1.25 batches per second"
    subtitle = "Single run each, 16 workers, 5,000 rows per batch, batch data on S3. Shorter bars are faster."
    label_x, panel_x, pitch, scale_w = 16, 232, 208, 138
    row_top, row_h, bar_h = 98, 46, 16
    values = [row[key] for row in rows for key, _ in PIPELINE_PANELS]
    lo, hi = 10 ** math.floor(math.log10(min(values))), 10 ** math.ceil(math.log10(max(values)))
    if lo == hi:
        hi *= 10
    exps = list(range(round(math.log10(lo)), round(math.log10(hi)) + 1))
    rows_bottom = row_top + row_h * len(rows)
    body = []

    for r, row in enumerate(rows):
        mid = row_top + row_h * r + row_h / 2
        behind = row["batches_per_s"] < ON_SCHEDULE_BATCHES_PER_S
        body.append(_text(label_x, mid - 3 if behind else mid + 4, row["label"], theme["ink"]))
        if behind:
            body.append(f'<path d="M{label_x} {_n(mid + 15)} l5 -9 l5 9 Z" fill="{theme["warning"]}"/>')
            body.append(_text(label_x + 15, mid + 15, f"behind schedule: {row['batches_per_s']:.2f} batches/s",
                              theme["ink2"], size=11.5))
    for p, (key, panel_title) in enumerate(PIPELINE_PANELS):
        x0 = panel_x + pitch * p
        body.append(_text(x0, row_top - 16, panel_title, theme["ink"], weight=600))
        for n, exp in enumerate(exps):
            gx = log_pos(10.0 ** exp, lo, hi, x0, x0 + scale_w)
            colour = theme["axis"] if n == 0 else theme["grid"]
            body.append(f'<line x1="{_n(gx)}" x2="{_n(gx)}" y1="{row_top - 4}" y2="{rows_bottom + 4}" '
                        f'stroke="{colour}"/>')
            if n % 2 == 0 or len(exps) <= 3:  # every other decade: the labels are wider than a decade
                tick = f"{10 ** exp} ms" if exp < 3 else f"{10 ** (exp - 3)} s"
                body.append(_text(gx, rows_bottom + 20, tick, theme["ink2"], size=11,
                                  anchor="start" if n == 0 else "middle"))
        for r, row in enumerate(rows):
            length = log_pos(row[key], lo, hi, 0, scale_w)
            y0 = row_top + row_h * r + (row_h - bar_h) / 2
            end, c = x0 + length, min(4.0, length)  # rounded at the data end, square at the baseline
            body.append(
                f'<path class="bar" data-ms="{row[key]:.1f}" data-length="{_n(length)}" fill="{theme["series"][0]}" '
                f'd="M{x0} {_n(y0)} H{_n(end - c)} Q{_n(end)} {_n(y0)} {_n(end)} {_n(y0 + c)} V{_n(y0 + bar_h - c)} '
                f'Q{_n(end)} {_n(y0 + bar_h)} {_n(end - c)} {_n(y0 + bar_h)} H{x0} Z">'
                f"<title>{escape(row['label'])}, {escape(panel_title.lower())}: {human_ms(row[key])}</title></path>")
            body.append(_text(end + 6, y0 + bar_h / 2 + 4, human_ms(row[key]), theme["ink"]))

    notes = [f"All three panels share one log scale starting at {human_ms(lo)}."]
    usual = Counter(row.get("batches_committed") for row in rows).most_common(1)[0][0] if rows else None
    for row in rows:
        if usual and row.get("batches_committed") not in (None, usual):
            notes.append(f"{row['label']} ran {row['batches_committed']:,} batches; the others ran {usual:,}.")
    body.append(_text(label_x, rows_bottom + 46, " ".join(notes), theme["ink2"], size=11.5))
    desc = "Three panels of horizontal bars on a shared log scale. " + " ".join(
        f"{row['label']}: " + ", ".join(f"{t.lower()} {human_ms(row[k])}" for k, t in PIPELINE_PANELS)
        + (f", fell behind schedule at {row['batches_per_s']:.2f} batches per second"
           if row["batches_per_s"] < ON_SCHEDULE_BATCHES_PER_S else "") + "."
        for row in rows)
    return _document(rows_bottom + 62, title, subtitle, desc, theme, body)


def main(results_dir=ROOT / "results", out_dir=ROOT / "docs" / "img"):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    medians = load_commit_medians(results_dir)
    rows = load_pipeline(Path(results_dir) / "pipeline")
    for dark in (False, True):
        suffix = "-dark" if dark else ""
        (out_dir / f"commit-rate{suffix}.svg").write_text(commit_rate_svg(medians, dark=dark))
        (out_dir / f"pipeline{suffix}.svg").write_text(pipeline_svg(rows, dark=dark))


if __name__ == "__main__":
    main()
