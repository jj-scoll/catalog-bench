"""Storage options for the pipeline workload. Every module in this package that defines an `Option` class is
registered by its `name`.

To add one, copy files.py. An Option needs:

    name                  short id used on the command line
    docstring             what the option does at each step; shown with the results
    setup()               start empty, removing anything a previous run left
    connect()             called once per process; return an object with
                            write_batch(trace, version, batch, attempt, rows, errors)
                                                  store one delivery of one batch durably; raise on failure
                            finalize(trace, version)
                                                  close the trace at this version, replacing any older version
                            read_trace(trace)     pyarrow table with version, batch and row for every current row
                            read_batch(trace, version, batch)
                                                  how many rows of that batch a reader can see right now
                            close()
    version()             versions of everything involved, as one string
    maintain()            optional: compaction or cleanup, timed and followed by a second round of reads
    local(directory)      classmethod returning an instance that runs with no services, used by the tests

A batch may be delivered more than once, with a higher attempt each time. read_trace must return each row once.
Option instances are pickled into each process, so keep connections out of __init__.
"""

import importlib
import pkgutil

OPTIONS = {}
for _mod in pkgutil.iter_modules(__path__):
    _cls = getattr(importlib.import_module(f"{__name__}.{_mod.name}"), "Option", None)
    if _cls:
        OPTIONS[_cls.name] = _cls
