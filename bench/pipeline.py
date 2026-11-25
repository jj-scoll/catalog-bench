"""Pipeline workload: a load arrives as traces, each split into batches. Workers write one batch at a time, a finaliser
closes each trace, and readers then read whole traces. One trace is then reprocessed with fewer batches under a new
version, which must replace the old rows.

Usage: python -m bench.pipeline --options files ducklake --traces 4 --batches 200 --rows 1000 --workers 16 --rate 1.25 --out results/pipeline-local.json

What is timed, per option:
    commit          one write_batch call: the batch is durable and acknowledged
    behind          how late a batch started against its scheduled time (open loop only, --rate above 0)
    visible after   from the acknowledgement until a separate reader first sees the batch
    fresh read      how long that first successful read took
    finalize        closing a trace, including replacing an older version
    read trace      a reader loading every current row of one trace

Delivery is at least once: a failed write is delivered again, up to three times in all, and --redeliver-every
delivers a batch twice even though the first delivery succeeded. Afterwards every trace is read back and compared row
by row with what was acknowledged.
"""

import argparse
import json
import multiprocessing as mp
import os
import platform
import queue
import time
from collections import Counter
from datetime import UTC, datetime

import duckdb
import pyarrow as pa

DELIVERIES = 3
SCHEMA = pa.schema([
    ("trace", pa.int32()), ("version", pa.int32()), ("batch", pa.int32()), ("attempt", pa.int32()), ("row", pa.int32()),
    ("amount", pa.float64()), ("code", pa.string()), ("note", pa.string()),
])


def make_rows(trace, version, batch, attempt, n):
    """n output rows for one delivery of one batch, about 100 bytes each before compression."""
    return pa.table({
        "trace": [trace] * n, "version": [version] * n, "batch": [batch] * n, "attempt": [attempt] * n,
        "row": list(range(n)),
        "amount": [(batch * 31 + r) * 0.01 for r in range(n)],
        "code": [f"{trace:06d}-{batch:06d}-{r:08d}" for r in range(n)],
        "note": [f"row {r} of batch {batch}, " + "x" * (r % 60) for r in range(n)],
    }, schema=SCHEMA)


def _worker(option, jobs, done, probes, go, base, rows, probe_every):
    try:
        conn = option.connect()
    except Exception as e:
        done.put(("failed", f"{type(e).__name__}: {e}"))
        return
    done.put(("ready",))
    go.wait()
    while (job := jobs.get()) is not None:
        trace, version, batch, attempt, offset = job
        data = make_rows(trace, version, batch, attempt, rows)
        errors = make_rows(trace, version, batch, attempt, max(1, rows // 100))
        due = base.value + offset
        if (wait := due - time.monotonic()) > 0:
            time.sleep(wait)
        began = time.monotonic()
        try:
            conn.write_batch(trace, version, batch, attempt, data, errors)
            ended = time.monotonic()
            if probe_every and batch % probe_every == 0:
                probes.put((trace, version, batch, ended))
            done.put(("ok", job, began - due, ended - began))
        except Exception as e:
            done.put(("error", job, f"{type(e).__name__}: {str(e)[:200]}"))
    conn.close()


def _next(done, procs):
    """The next report from a worker. A worker that died without reporting would otherwise hang the run."""
    while True:
        try:
            return done.get(timeout=10)
        except queue.Empty:
            if dead := [p.exitcode for p in procs if not p.is_alive()]:
                for p in procs:
                    p.kill()
                raise RuntimeError(f"a worker process died, exit code {dead[0]}") from None


def _prober(option, probes, out, rows, timeout):
    """Reads each sampled batch from a connection of its own until it is all there."""
    conn = option.connect()
    visible, reads, skipped, timeouts = [], [], 0, 0
    while (probe := probes.get()) is not None:
        trace, version, batch, acked = probe
        if time.monotonic() - acked > 0.05:  # it waited behind another probe, so its delay would be this reader's, not the option's
            skipped += 1
            continue
        while True:
            start = time.monotonic()
            try:
                found = conn.read_batch(trace, version, batch)
            except Exception:
                found = 0
            if found >= rows:
                visible.append(start - acked)
                reads.append(time.monotonic() - start)
                break
            if time.monotonic() - acked > timeout:
                timeouts += 1
                break
            time.sleep(0.01)
    conn.close()
    out.put((visible, reads, skipped, timeouts))


def _load(option, plan, *, rows, workers, rate, redeliver_every, probe_every, probe_timeout):
    """Deliver every (trace, version, batch) in plan to the workers and finalise each trace once its batches are in."""
    ctx = mp.get_context("spawn")
    jobs, done, probes, probed = ctx.Queue(), ctx.Queue(), ctx.Queue(), ctx.Queue()
    go, base = ctx.Event(), ctx.Value("d", 0.0)
    procs = [ctx.Process(target=_worker, args=(option, jobs, done, probes, go, base, rows, probe_every)) for _ in range(workers)]
    prober = ctx.Process(target=_prober, args=(option, probes, probed, rows, probe_timeout))
    for p in procs + [prober]:
        p.start()
    for _ in procs:
        if (message := _next(done, procs))[0] == "failed":
            for p in procs + [prober]:
                p.kill()
            raise RuntimeError(f"{option.name}: worker failed to start: {message[1]}")

    outstanding, redelivered = Counter(), 0  # jobs not yet reported, per (trace, version)
    for i, (trace, version, batch) in enumerate(plan):
        offset = i / rate if rate else 0.0
        jobs.put((trace, version, batch, 0, offset))
        outstanding[trace, version] += 1
        if redeliver_every and i % redeliver_every == redeliver_every - 1:
            jobs.put((trace, version, batch, 1, offset))
            outstanding[trace, version] += 1
            redelivered += 1

    finaliser = option.connect()
    commits, behind, finalize, errors, acked, failed = [], [], [], Counter(), {}, 0
    base.value = began = time.monotonic() + 0.1
    go.set()
    while outstanding:
        kind, job, *rest = _next(done, procs)
        trace, version, batch, attempt, _ = job
        if kind == "ok":
            behind.append(max(0.0, rest[0]))
            commits.append(rest[1])
            acked.setdefault((trace, version), set()).add(batch)
        else:
            errors[rest[0]] += 1
            if attempt + 1 < DELIVERIES:
                jobs.put((trace, version, batch, attempt + 1, 0.0))
                outstanding[trace, version] += 1
        outstanding[trace, version] -= 1
        if not outstanding[trace, version]:
            del outstanding[trace, version]
            wanted = {b for t, v, b in plan if (t, v) == (trace, version)}
            failed += len(wanted - acked.get((trace, version), set()))
            start = time.monotonic()
            try:
                finaliser.finalize(trace, version)
            except Exception as e:
                errors[f"finalize: {type(e).__name__}: {str(e)[:200]}"] += 1
            finalize.append(time.monotonic() - start)
    ended = time.monotonic()
    finalize_retries = getattr(finaliser, "retries", 0)
    finaliser.close()
    for _ in procs:
        jobs.put(None)
    probes.put(None)
    visible, reads, skipped, timeouts = probed.get()
    for p in procs + [prober]:
        p.join()
    return {
        "seconds": ended - began, "commits": commits, "behind": behind, "finalize": finalize, "errors": errors,
        "acked": acked, "failed": failed, "redelivered": redelivered, "finalize_retries": finalize_retries,
        "visible": visible, "reads": reads, "probes_skipped": skipped, "probe_timeouts": timeouts,
    }


def _check(conn, trace, version, batches, rows):
    """Read one trace and compare it with the batches that were acknowledged at this version."""
    start = time.monotonic()
    table = conn.read_trace(trace)
    seconds = time.monotonic() - start
    with duckdb.connect() as db:  # in DuckDB because a large trace is tens of millions of rows
        db.register("got", table)
        db.register("wanted", pa.table({"batch": pa.array(sorted(batches), pa.int32())}))
        total, in_place, expected = db.execute(
            "SELECT count(*), count(*) FILTER (ok), count(DISTINCT (batch, row)) FILTER (ok) FROM "
            "(SELECT batch, row, version = ? AND row < ? AND batch IN (FROM wanted) AS ok FROM got)",
            (version, rows),
        ).fetchone()
    return {
        "seconds": seconds,
        "missing": len(batches) * rows - expected,
        "duplicated": in_place - expected,
        "stale": total - in_place,
    }


def _ms(values, pct=None):
    if not values:
        return None
    ordered = sorted(values)
    return (ordered[-1] if pct is None else ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))]) * 1000


def run(option, *, traces, batches, rows, workers, rate=0.0, redeliver_every=0, probe_every=1, probe_timeout=120.0):
    """Run the whole flow once against a freshly set-up option and return a JSON-serialisable result."""
    option.setup()
    knobs = dict(rows=rows, workers=workers, rate=rate, redeliver_every=redeliver_every, probe_every=probe_every, probe_timeout=probe_timeout)
    load = _load(option, [(t, 1, b) for t in range(traces) for b in range(batches)], **knobs)

    reader = option.connect()
    checks = [_check(reader, t, 1, load["acked"].get((t, 1), set()), rows) for t in range(traces)]

    # Reprocess trace 0 with half its batches: the rows of the batches that are not rewritten must disappear.
    again = _load(option, [(0, 2, b) for b in range(max(1, batches // 2))], **knobs)
    redo = _check(reader, 0, 2, again["acked"].get((0, 2), set()), rows)
    reader.close()

    maintain_seconds, after = None, []
    if hasattr(option, "maintain"):
        start = time.monotonic()
        option.maintain()
        maintain_seconds = time.monotonic() - start
        reader = option.connect()
        after = [_check(reader, 0, 2, again["acked"].get((0, 2), set()), rows)]
        after += [_check(reader, t, 1, load["acked"].get((t, 1), set()), rows) for t in range(1, traces)]
        reader.close()

    def total(key, items):
        return sum(c[key] for c in items)

    samples = load["errors"] + again["errors"]
    error_types = {}
    for message, n in samples.most_common():
        kind = message.split(":")[1].strip() if message.startswith("finalize:") else message.split(":")[0]
        error_types.setdefault(kind, {"count": 0, "example": message})["count"] += n
    committed = sum(len(b) for b in load["acked"].values())
    clean = lambda items: not (total("missing", items) or total("duplicated", items) or total("stale", items))
    return {
        "option": option.name,
        "traces": traces, "batches_per_trace": batches, "rows_per_batch": rows, "workers": workers,
        "target_batches_per_s": rate or None, "redelivered": load["redelivered"],
        "load_seconds": load["seconds"],
        "batches_committed": committed, "batches_failed": load["failed"],
        "batches_per_s": len(load["commits"]) / load["seconds"],
        "write_errors": sum(n for m, n in load["errors"].items() if not m.startswith("finalize:")),
        "finalize_errors": sum(n for m, n in samples.items() if m.startswith("finalize:")),
        "error_types": error_types,
        "commit_p50_ms": _ms(load["commits"], 50), "commit_p95_ms": _ms(load["commits"], 95),
        "commit_p99_ms": _ms(load["commits"], 99), "commit_max_ms": _ms(load["commits"]),
        "behind_p95_ms": _ms(load["behind"], 95) if rate else None, "behind_max_ms": _ms(load["behind"]) if rate else None,
        "fresh_samples": len(load["visible"]), "fresh_skipped": load["probes_skipped"], "fresh_timeouts": load["probe_timeouts"],
        "visible_after_p50_ms": _ms(load["visible"], 50), "visible_after_p95_ms": _ms(load["visible"], 95),
        "visible_after_max_ms": _ms(load["visible"]),
        "fresh_read_p50_ms": _ms(load["reads"], 50), "fresh_read_p95_ms": _ms(load["reads"], 95),
        "finalize_retries": load["finalize_retries"] + again["finalize_retries"],
        "finalize_p50_ms": _ms(load["finalize"], 50), "finalize_max_ms": _ms(load["finalize"]),
        "read_trace_p50_ms": _ms([c["seconds"] for c in checks], 50), "read_trace_max_ms": _ms([c["seconds"] for c in checks]),
        "rows_missing": total("missing", checks), "rows_duplicated": total("duplicated", checks), "rows_stale": total("stale", checks),
        "verified": clean(checks) and not load["failed"],
        "reprocess_seconds": again["seconds"], "reprocess_finalize_ms": _ms(again["finalize"]),
        "reprocess_read_ms": redo["seconds"] * 1000,
        "reprocess_rows_missing": redo["missing"], "reprocess_rows_duplicated": redo["duplicated"], "reprocess_rows_stale": redo["stale"],
        "reprocess_verified": clean([redo]) and not again["failed"],
        "maintain_seconds": maintain_seconds,
        "read_trace_after_maintain_p50_ms": _ms([c["seconds"] for c in after], 50),
        "verified_after_maintain": clean(after) if after else None,
    }


def main():
    from bench.options import OPTIONS

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--options", nargs="+", choices=OPTIONS, required=True)
    ap.add_argument("--traces", type=int, default=4)
    ap.add_argument("--batches", type=int, default=100, help="batches per trace")
    ap.add_argument("--rows", type=int, default=1000, help="rows per batch")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rate", type=float, default=0.0, help="batches per second, open loop; 0 runs as fast as the workers go")
    ap.add_argument("--redeliver-every", type=int, default=0, help="deliver every Nth batch twice")
    ap.add_argument("--probe-every", type=int, default=1, help="probe freshness on every Nth batch; 0 turns it off")
    ap.add_argument("--environment", default="local")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    doc = {
        "workload": "pipeline",
        "environment": args.environment,
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": {"cpus": os.cpu_count(), "platform": platform.platform(), "python": platform.python_version()},
        "versions": {}, "descriptions": {name: OPTIONS[name].__doc__ for name in args.options}, "results": [],
    }
    for name in args.options:
        option = OPTIONS[name]()
        try:
            r = run(option, traces=args.traces, batches=args.batches, rows=args.rows, workers=args.workers, rate=args.rate,
                    redeliver_every=args.redeliver_every, probe_every=args.probe_every)
        except Exception as e:  # one option failing must not lose the others
            print(f"{name:20} FAILED {type(e).__name__}: {e}")
            doc["results"].append({"option": name, "failed": f"{type(e).__name__}: {str(e)[:500]}"})
            continue
        doc["results"].append(r)
        doc["versions"][name] = option.version()
        print(f"{name:20} {r['batches_per_s']:7.2f} batches/s  commit p50={r['commit_p50_ms']:.0f}ms p95={r['commit_p95_ms']:.0f}ms  "
              f"visible p95={r['visible_after_p95_ms'] or 0:.0f}ms  finalize max={r['finalize_max_ms']:.0f}ms  "
              f"read p50={r['read_trace_p50_ms']:.0f}ms  verified={r['verified']} reprocess={r['reprocess_verified']}")
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:  # after every option, so a later failure keeps the earlier results
            json.dump(doc, f, indent=1)


if __name__ == "__main__":
    main()
