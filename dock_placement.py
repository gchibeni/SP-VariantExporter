"""Keep the panel docked where the user left it - by default tabbed with Painter's Properties.

Painter only restores a plugin dock if its saved layout knows it; when it doesn't, the dock
comes back floating (sometimes behind other windows). So we remember the placement ourselves.

The QDockWidget returned by add_dock_widget() is not the one the panel lives in for long:
Painter rebuilds it while applying its layout at start-up (the original is deleted, the panel
is re-parented into a new one). So the dock is always looked up from the panel's parent, never
kept, and the signals are watched through a small QObject owned by the dock itself: it goes
away with the dock, so nothing can call into Python through a dead connection.
"""

import json

import shiboken6
from PySide6 import QtCore, QtWidgets

import substance_painter.ui as sp_ui

from . import diag

PROPERTIES_DOCK = "Tool"   # objectName of Painter's Properties dock
_SETTINGS = ("EM", "em_variant_exporter")
_KEY = "dock/placement"
_TRACKING_DELAY_MS = 2000  # ignore Painter's own start-up shuffling before tracking moves

_state = {}


def _settings():
    return QtCore.QSettings(*_SETTINGS)


def _load():
    try:
        return json.loads(_settings().value(_KEY, "") or "null")
    except (TypeError, ValueError):
        return None


def _valid(obj):
    return obj is not None and shiboken6.isValid(obj)


def current_dock():
    """The QDockWidget the panel lives in right now (None if the panel is gone or undocked)."""
    panel = _state.get("panel")
    if not _valid(panel):
        return None
    try:
        parent = panel.parentWidget()
    except RuntimeError:
        return None
    if isinstance(parent, QtWidgets.QDockWidget) and _valid(parent):
        return parent
    return None


class _Tracker(QtCore.QObject):
    """Saves the placement whenever the dock it belongs to is moved. Owned by that dock."""

    def __init__(self, dock):
        super().__init__(dock)
        dock.dockLocationChanged.connect(self._changed)
        dock.topLevelChanged.connect(self._changed)

    def _changed(self, *_):
        if _state.get("tracking"):
            dock = self.parent()
            if _valid(dock):
                save(dock)


def _track(dock):
    """Make sure the current dock is being watched (Painter may have replaced it)."""
    tracker = _state.get("tracker")
    if _valid(tracker) and tracker.parent() is dock:
        return
    _state["tracker"] = _Tracker(dock)


def save(dock):
    if not _state.get("tracking") or not _valid(dock):
        return
    mw = sp_ui.get_main_window()
    if dock.isFloating():
        g = dock.geometry()
        data = {"floating": True, "geometry": [g.x(), g.y(), g.width(), g.height()]}
    else:
        tabs = [d.objectName() for d in mw.tabifiedDockWidgets(dock) if d.objectName()]
        data = {"floating": False, "area": mw.dockWidgetArea(dock).value,
                "tab_with": tabs[0] if tabs else ""}
    _settings().setValue(_KEY, json.dumps(data))


def restore(dock):
    mw = sp_ui.get_main_window()
    data = _load()
    if data and data.get("floating"):
        dock.setFloating(True)
        if data.get("geometry"):
            dock.setGeometry(*data["geometry"])
        dock.show()
        dock.raise_()
        return
    find = lambda name: mw.findChild(QtWidgets.QDockWidget, name) if name else None
    sibling = find(data["tab_with"]) if data else find(PROPERTIES_DOCK)
    if data and not data.get("tab_with"):
        sibling = None   # it was alone in its dock area
    if sibling is not None and (sibling is dock or sibling.isFloating() or not sibling.isVisible()):
        sibling = find(PROPERTIES_DOCK)
    dock.setFloating(False)
    if sibling is not None and sibling is not dock and not sibling.isFloating():
        mw.tabifyDockWidget(sibling, dock)
    else:
        area = QtCore.Qt.DockWidgetArea(data["area"]) if data else QtCore.Qt.RightDockWidgetArea
        mw.addDockWidget(area, dock)
    dock.show()


def _start_tracking_later():
    timer = _state.get("timer")
    if timer is None:
        timer = QtCore.QTimer()
        timer.setSingleShot(True)
        timer.setInterval(_TRACKING_DELAY_MS)
        timer.timeout.connect(lambda: _state.__setitem__("tracking", True))
        _state["timer"] = timer
    _state["tracking"] = False
    timer.start()


def attach(panel):
    """Restore the placement now (deferred) and keep saving it whenever the user moves it."""
    _state["panel"] = panel
    _refresh()
    QtCore.QTimer.singleShot(0, _restore_current)
    _start_tracking_later()


def on_gui_started():
    """Painter applies its own layout late during start-up: put the panel back afterwards."""
    _restore_current()
    _start_tracking_later()


def _refresh():
    """Pick up the dock the panel currently lives in; returns it (or None)."""
    dock = current_dock()
    if dock is not None:
        _track(dock)
    return dock


def _restore_current():
    dock = _refresh()
    if dock is None:
        diag.trace("dock placement: no dock to restore")
        return
    try:
        restore(dock)
    except RuntimeError:   # dock deleted meanwhile (plugin closed)
        pass


def detach():
    _state["tracking"] = False
    # The tracker lives inside the dock: delete it now so Painter's own teardown (which moves
    # docks one last time) can no longer reach into Python through it.
    tracker = _state.pop("tracker", None)
    if _valid(tracker):
        try:
            shiboken6.delete(tracker)
        except RuntimeError:
            pass
    timer = _state.pop("timer", None)
    if timer is not None:
        timer.stop()
    _state.pop("panel", None)
