# Adding a system or a pipeline option

## Add a system

A system is one entry in the commit-rate workload. Copy `bench/systems/postgres.py` to a new file in the same directory and edit it. Any module there with a `System` class is registered automatically. The interface is five things, documented at the top of `bench/systems/__init__.py`:

| Member | Purpose |
|---|---|
| `name` and docstring | Identifier, and one line saying exactly what is measured |
| `setup()` | Start from an empty table. Leftover snapshots and files change commit cost, so clean thoroughly |
| `connect()` | Called once per writer process. Returns an object with `commit_one(writer, seq)` and `close()` |
| `count()` | Rows in the table, for verification |
| `version()` | Versions of everything involved |

If the system needs a service, add it to `compose.yaml`.

Read the [rules for a fair entry](methodology.md#rules-for-a-fair-entry) before publishing numbers.

## Add a pipeline option

An option is one way of storing the data in the [pipeline workload](pipeline.md). Copy `bench/options/files.py`; the interface is at the top of `bench/options/__init__.py`. Each option's docstring states what it does at every step, and that text is saved with the results.
