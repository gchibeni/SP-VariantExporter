"""Crash diagnostics: a lifecycle trace and Python's fault handler.

Both are written under %LOCALAPPDATA%/em_variant_exporter/ (next to the per-project settings),
so a crash while Painter is quitting can be traced after the fact:

- trace.log: one line per plugin lifecycle step (start, project events, close...), flushed at
  once so the last line shows how far the plugin got before Painter went down.
- crash-trace.txt: the Python stack of every thread at the moment of a native crash (access
  violation...), written by `faulthandler`. Empty when the crash happens after Python itself
  has been shut down - which is a finding in itself.
"""

import faulthandler
import os
import sys
import time

DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "em_variant_exporter")
TRACE = os.path.join(DIR, "trace.log")
CRASH = os.path.join(DIR, "crash-trace.txt")
_MAX_TRACE_BYTES = 256 * 1024   # the trace is truncated once it grows past this

_state = {}


def start():
    try:
        os.makedirs(DIR, exist_ok=True)
        if os.path.exists(TRACE) and os.path.getsize(TRACE) > _MAX_TRACE_BYTES:
            os.remove(TRACE)
        _state["trace"] = open(TRACE, "a", encoding="utf-8")
        if not faulthandler.is_enabled():
            # The file object must stay alive for as long as the handler is enabled.
            _state["crash"] = open(CRASH, "a", encoding="utf-8")
            faulthandler.enable(file=_state["crash"], all_threads=True)
    except OSError:
        pass
    trace(f"---- plugin start (pid {os.getpid()}, Python {sys.version.split()[0]})")


def trace(msg):
    f = _state.get("trace")
    if f is None:
        return
    try:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        f.flush()
    except (OSError, ValueError):
        pass


def stop():
    # The fault handler stays on: a crash while Painter exits is exactly what it is here for.
    # Python disables it by itself when the interpreter shuts down.
    trace("plugin closed")
