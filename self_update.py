"""Reload the plugin in place when the installer updates it while Painter is open.

Every start also writes "<plugins folder>/.Variant Exporter.loaded-<process id>": the installer
waits for it to know the plugin is running in that Painter, without poking Painter's menus.
The file is removed when the plugin closes (and stale ones left by a crashed Painter are
cleaned up at the next start).

After replacing the plugin files, the installer writes "<plugins folder>/.Variant Exporter.reload".
The running plugin checks for it every couple of seconds; when it appears, it removes it (the
installer takes that as "picked up") and restarts itself on the new files - the same way
Painter's own plugin reloading works. Dot-named files are ignored by Painter's plugin scan.
"""

import glob
import importlib
import os
import sys
import time
import traceback

from PySide6 import QtCore

import substance_painter.logging as sp_log
import substance_painter_plugins

PACKAGE = __package__   # the plugin's folder / module name ("Variant Exporter")
PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKER = os.path.join(PLUGINS_DIR, f".{PACKAGE}.reload")
LOADED = os.path.join(PLUGINS_DIR, f".{PACKAGE}.loaded-{os.getpid()}")
CHECK_INTERVAL_MS = 2000
_STALE_AFTER_S = 24 * 3600   # "loaded" notes older than this belong to a Painter that is gone

_state = {}


def start():
    _remove_marker()   # a fresh start already runs the latest files
    _remove_stale_loaded_notes()
    try:   # "running in this Painter" note for the installer
        from .version import __version__
        with open(LOADED, "w", encoding="utf-8") as f:
            f.write(__version__)
    except OSError:
        pass
    timer = QtCore.QTimer()
    timer.setInterval(CHECK_INTERVAL_MS)
    timer.timeout.connect(_check)
    timer.start()
    _state["timer"] = timer


def stop():
    timer = _state.pop("timer", None)
    if timer is not None:
        timer.stop()
    try:
        os.remove(LOADED)
    except OSError:
        pass


def _remove_marker():
    try:
        os.remove(MARKER)
    except OSError:
        pass


def _remove_stale_loaded_notes():
    now = time.time()
    for path in glob.glob(os.path.join(PLUGINS_DIR, f".{PACKAGE}.loaded-*")):
        try:
            if path != LOADED and now - os.path.getmtime(path) > _STALE_AFTER_S:
                os.remove(path)
        except OSError:
            pass


def _check():
    if os.path.exists(MARKER):
        _remove_marker()
        stop()
        # Outside the timer callback: the reload deletes this module's timer and widgets.
        QtCore.QTimer.singleShot(0, _reload)


def _reload():
    try:
        module = sys.modules.get(PACKAGE)
        if module is not None:
            substance_painter_plugins.close_plugin(module)
        for name in [n for n in sys.modules if n == PACKAGE or n.startswith(PACKAGE + ".")]:
            del sys.modules[name]
        importlib.invalidate_caches()
        module = importlib.import_module(PACKAGE)
        substance_painter_plugins.start_plugin(module)
        version = getattr(sys.modules.get(PACKAGE + ".version"), "__version__", "?")
        sp_log.info(f"[Variant Exporter] updated to v{version} (reloaded in place)")
    except Exception:
        sp_log.error(f"[Variant Exporter] reload after update failed - restart Substance 3D "
                     f"Painter:\n{traceback.format_exc()}")
