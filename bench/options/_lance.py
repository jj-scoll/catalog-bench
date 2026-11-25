"""Lance, written with the lance Python library and read with DuckDB's lance extension."""

import lance

EXTENSION, SUFFIX, CONFLICT = "lance", ".lance", ()


def create(path, schema):
    lance.write_dataset(schema.empty_table(), path, schema=schema)


def append(path, rows):
    lance.write_dataset(rows, path, mode="append")


def delete(path, predicate):
    lance.dataset(path).delete(predicate)


def replace(path, trace, version, rows):  # two commits: Lance has no overwrite limited to a filter
    append(path, rows)
    delete(path, f"trace = {trace} AND version < {version}")


def scan(path):
    return f"'{path}'"


def compact(path):
    lance.dataset(path).optimize.compact_files()


def version():
    return f"lance {lance.__version__}"
