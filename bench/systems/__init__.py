"""Systems under test. Every module in this package that defines a `System` class is registered by its `name`.

To add one, copy postgres.py. A System needs:

    name                  short id used on the command line and the site
    docstring             one line saying exactly what is measured; shown on the site
    setup()               create an empty target table, dropping any previous one
    connect()             called once per writer process; return an object with
                            commit_one(writer, seq)  durably commit one row, raise on failure
                            close()
    count()               rows currently in the table, used to verify acknowledged commits
    version()             versions of everything involved, as one string

System instances are pickled into each writer process, so keep connections out of __init__.
"""

import importlib
import pkgutil

SYSTEMS = {}
for _mod in pkgutil.iter_modules(__path__):
    _cls = getattr(importlib.import_module(f"{__name__}.{_mod.name}"), "System", None)
    if _cls:
        SYSTEMS[_cls.name] = _cls
