"""Closed-loop commit benchmark: N writer processes each commit one row at a time for a fixed period."""

import multiprocessing as mp
import time
from collections import Counter


class Noop:
    """Commits nothing. Its rate is the ceiling the harness loop itself can drive."""

    name = "noop"

    def setup(self): ...
    def connect(self): return self
    def commit_one(self, writer, seq): ...
    def close(self): ...
    def count(self): return 0
    def version(self): return ""


def _writer(system, writer, seconds, barrier, out):
    try:
        conn = system.connect()
        latencies, catalog, errors, seq = [], [], Counter(), 0
        barrier.wait()
        began = time.monotonic()
        deadline = began + seconds
        while (start := time.monotonic()) < deadline:
            try:
                in_catalog = conn.commit_one(writer, seq)
                latencies.append(time.monotonic() - start)
                if in_catalog is not None:
                    catalog.append(in_catalog)
            except Exception as e:
                errors[f"{type(e).__name__}: {str(e)[:200]}"] += 1
            seq += 1
        conn.close()
        out.put((latencies, errors, None, time.monotonic() - began, catalog))
    except Exception as e:
        barrier.abort()  # release the other writers so the run fails fast
        out.put(([], Counter(), f"{type(e).__name__}: {e}", 0.0, []))


def _percentile_ms(ordered, pct):
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))] * 1000


def run(system, writers, seconds):
    """Run one measurement and return a JSON-serialisable result.

    A commit counts when the client was told it succeeded. Afterwards the rows are counted:
    every acknowledged commit must be present, and any surplus must be explainable by
    commits that errored after landing.
    """
    system.setup()
    ctx = mp.get_context("spawn")
    barrier, out = ctx.Barrier(writers), ctx.Queue()
    procs = [ctx.Process(target=_writer, args=(system, w, seconds, barrier, out)) for w in range(writers)]
    for p in procs:
        p.start()
    results = [out.get() for _ in procs]
    for p in procs:
        p.join()
    failures = [f for _, _, f, _, _ in results if f and "BrokenBarrierError" not in f]
    if failures:
        raise RuntimeError(f"{system.name}: writer failed to start: {failures[0]}")

    latencies = sorted(lat for lats, _, _, _, _ in results for lat in lats)
    elapsed = max(t for _, _, _, t, _ in results)  # a commit in flight at the deadline extends the run
    catalog = sorted(c for _, _, _, _, cs in results for c in cs)
    samples = sum((e for _, e, _, _, _ in results), Counter())
    commits, errors = len(latencies), sum(samples.values())
    error_types = {}  # error_samples keeps only the five commonest messages; this covers every kind of failure
    for message, n in samples.most_common():
        error_types.setdefault(message.split(":")[0], {"count": 0, "example": message})["count"] += n
    rows = system.count()
    return {
        "system": system.name,
        "writers": writers,
        "target_seconds": seconds,
        "seconds": elapsed,
        "commits": commits,
        "errors": errors,
        "error_samples": dict(samples.most_common(5)),
        "error_types": error_types,
        "commits_per_s": commits / elapsed,
        "per_writer_commits": sorted(len(lats) for lats, _, _, _, _ in results),
        "p50_ms": _percentile_ms(latencies, 50) if latencies else None,
        "p95_ms": _percentile_ms(latencies, 95) if latencies else None,
        "p99_ms": _percentile_ms(latencies, 99) if latencies else None,
        "max_ms": latencies[-1] * 1000 if latencies else None,
        "catalog_p50_ms": _percentile_ms(catalog, 50) if catalog else None,  # share of p50 spent in the catalog call
        "mean_ms": sum(latencies) / commits * 1000 if latencies else None,
        "rows_found": rows,
        "verified": commits <= rows <= commits + errors,
    }
