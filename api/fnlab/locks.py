"""One computation at a time per heavy, cached function.

The dashboard runs a page's sections at the same time. When two sections need the same expensive model (expected
points, zone forecast, engine), the second waits for the first and then reuses its cached result, instead of
computing it again in parallel."""
from __future__ import annotations

import functools
import threading


def serialized(fn):
    lock = threading.RLock()

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with lock:
            return fn(*args, **kwargs)
    return wrapper
