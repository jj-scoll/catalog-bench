"""Usage: python -m bench.run --systems postgres ducklake-postgres --writers 1 2 4 8 --seconds 10 --out results/local.json"""

import argparse
import json
import os
import platform
from datetime import UTC, datetime, timezone

from bench.harness import Noop, run
from bench.systems import SYSTEMS


def main():
    ap = argparse.ArgumentParser()
    configured = [n for n, s in SYSTEMS.items() if os.environ.get(getattr(s, "requires_env", "HOME"))]
    ap.add_argument("--systems", nargs="+", choices=SYSTEMS, default=configured)
    ap.add_argument("--writers", nargs="+", type=int, default=[1, 2, 4, 8, 16])
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--environment", default="local", help="where this ran, shown on the site")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    doc = {
        "environment": args.environment,
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": {"cpus": os.cpu_count(), "platform": platform.platform(), "python": platform.python_version()},
        "versions": {},
        "descriptions": {name: SYSTEMS[name].__doc__ for name in args.systems},
        "harness_ceiling": [],
        "results": [],
    }
    for w in args.writers:
        ceiling = run(Noop(), w, min(args.seconds, 2))
        doc["harness_ceiling"].append({"writers": w, "commits_per_s": ceiling["commits_per_s"]})
    for name in args.systems:
        for w in args.writers:
            result = run(SYSTEMS[name](), w, args.seconds)
            doc["results"].append(result)
            print(f"{name:20} writers={w:<3} {result['commits_per_s']:>10.1f} commits/s  "
                  f"p50={result['p50_ms']:.2f}ms p99={result['p99_ms']:.2f}ms errors={result['errors']} verified={result['verified']}")
        doc["versions"][name] = SYSTEMS[name]().version()  # after its runs: a fresh server has nothing to ask until setup()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(doc, f, indent=1)


if __name__ == "__main__":
    main()
