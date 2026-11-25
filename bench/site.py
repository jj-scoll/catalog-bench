"""Usage: python -m bench.site  (reads results/*.json, writes site/data.json)

Runs are grouped by environment label and run length, then by system (plus client and
tables-per-writer where a result states them) and writer count. Runs that share all of
those are repetitions. Unknown keys are ignored and missing ones become null.
"""

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from statistics import median

MIN_SECONDS, MIN_REPS = 30, 2  # anything less is shown, but labelled as not comparable
VARIANTS = {"client": "client: {}", "tables_per_writer": "tables per writer: {}"}


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _median(runs, key):
    values = [v for r in runs if (v := _num(r.get(key))) is not None]
    return median(values) if values else None


def _point(writers, runs):
    rates = [r["commits_per_s"] for r in runs]
    per_writer = [n for r in runs if isinstance(r.get("per_writer_commits"), list) for n in r["per_writer_commits"] if _num(n) is not None]
    counted = [(c, e) for r in runs if (c := _num(r.get("commits"))) is not None and (e := _num(r.get("errors"))) is not None]
    attempts = sum(c + e for c, e in counted)
    verified = [r["verified"] for r in runs if isinstance(r.get("verified"), bool)]
    p50, catalog = _median(runs, "p50_ms"), _median(runs, "catalog_p50_ms")
    slowest = [v for r in runs if (v := _num(r.get("max_ms"))) is not None]
    return {
        "writers": writers,
        "reps": len(runs),
        "commits_per_s": median(rates),
        "min": min(rates),
        "max": max(rates),
        "p50_ms": p50,
        "p99_ms": _median(runs, "p99_ms"),
        "max_ms": max(slowest, default=None),
        "catalog_p50_ms": catalog,
        "catalog_share": catalog / p50 if catalog is not None and p50 else None,
        "writer_min": min(per_writer, default=None),
        "writer_max": max(per_writer, default=None),
        "error_rate": sum(e for _, e in counted) / attempts if attempts else None,
        "verified": all(verified) if verified else None,
    }


def _system(name, runs, docs):
    by_writers = {}
    for r in runs:
        by_writers.setdefault(r["writers"], []).append(r)
    points = [_point(w, by_writers[w]) for w in sorted(by_writers)]
    base = runs[0]["system"]
    errors = sum((Counter({m: n for m, n in r["error_samples"].items() if _num(n)}) for r in runs if isinstance(r.get("error_samples"), dict)), Counter())

    def stated(key):  # the distinct things the result files say about this system, in file order
        found = [d[key][base] for d in docs if isinstance(d.get(key), dict) and isinstance(d[key].get(base), str)]
        return "; ".join(dict.fromkeys(s.strip() for s in found)) or None

    verified = [p["verified"] for p in points if p["verified"] is not None]
    return {
        "name": name,
        "system": base,
        "client": runs[0].get("client"),
        "description": stated("descriptions"),
        "version": stated("versions"),
        "peak": max(points, key=lambda p: p["commits_per_s"]),
        "verified": all(verified) if verified else None,
        "points": points,
        "error_samples": [{"message": m, "count": n} for m, n in errors.most_common(5)],
    }


def build(results_dir):
    groups, skipped = {}, []  # (environment label, run length) -> {"docs": [...], "runs": {series name: [...]}}
    for path in sorted(Path(results_dir).glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except ValueError as e:
            skipped.append({"file": path.name, "reason": f"not valid JSON: {e}"})
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("results"), list):
            skipped.append({"file": path.name, "reason": "no list of results"})
            continue
        label = str(doc.get("environment") or path.stem)
        for r in doc["results"]:
            if not (isinstance(r, dict) and isinstance(r.get("system"), str) and _num(r.get("writers")) and _num(r.get("commits_per_s")) is not None):
                continue
            r = {**{k: doc[k] for k in VARIANTS if k in doc}, **r}
            seconds = _num(r.get("target_seconds")) or _num(r.get("seconds"))
            variant = ", ".join(text.format(r[k]) for k, text in VARIANTS.items() if isinstance(r.get(k), (str, int)))
            group = groups.setdefault((label, round(seconds) if seconds else None), {"docs": {}, "runs": {}, "sources": {}})
            group["docs"][path.name] = doc
            series = f"{r['system']} ({variant})" if variant else r["system"]
            group["runs"].setdefault(series, []).append(r)
            group["sources"].setdefault(series, {})[path.name] = doc

    environments = []
    for (label, seconds), group in sorted(groups.items(), key=lambda g: (g[0][0], g[0][1] or 0)):
        docs = list(group["docs"].values())
        systems = sorted((_system(n, runs, list(group["sources"][n].values())) for n, runs in group["runs"].items()), key=lambda s: -s["peak"]["commits_per_s"])
        reps = min(p["reps"] for s in systems for p in s["points"])
        caveats = []
        if seconds is None or seconds < MIN_SECONDS:
            caveats.append(f"each run lasted {seconds} seconds, under the {MIN_SECONDS} needed" if seconds else "run length not recorded")
        if reps < MIN_REPS:
            caveats.append("a single repetition, so there is no spread to judge noise by")
        ceiling = {}
        for d in docs:
            for c in d["harness_ceiling"] if isinstance(d.get("harness_ceiling"), list) else []:
                if isinstance(c, dict) and _num(c.get("writers")) and _num(c.get("commits_per_s")):
                    ceiling.setdefault(c["writers"], []).append(c["commits_per_s"])
        environments.append({
            "id": re.sub(r"[^a-z0-9]+", "-", f"{label} {seconds}s".lower()).strip("-"),
            "label": label,
            "target_seconds": seconds,
            "reps": reps,
            "comparable": not caveats,
            "caveats": caveats,
            "files": sorted(group["docs"]),
            "ran_at": sorted({d["ran_at"] for d in docs if isinstance(d.get("ran_at"), str)}),
            "machines": list({json.dumps(d["machine"], sort_keys=True): d["machine"] for d in docs if isinstance(d.get("machine"), dict)}.values()),
            "harness_ceiling": [{"writers": w, "commits_per_s": median(v)} for w, v in sorted(ceiling.items())],
            "systems": systems,
        })
    return {"repository": os.environ.get("GITHUB_REPOSITORY"), "environments": environments, "skipped": skipped}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default="site/data.json")
    args = ap.parse_args(argv)
    data = build(args.results)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1))
    for env in data["environments"]:
        print(f"{env['label']} ({env['target_seconds']} s x {env['reps']}): {len(env['systems'])} systems, "
              f"{'comparable' if env['comparable'] else 'not comparable'}")
    for s in data["skipped"]:
        print(f"skipped {s['file']}: {s['reason']}")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
