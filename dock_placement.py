"""Keep the panel docked where the user left it - by default tabbed with Painter's Properties.

Painter only restores a plugin dock if its saved layout knows it; when it doesn't, the dock
comes back floating (sometimes behind other windows). So we remember the placement ourselves.
"""

import json

from PySide6 import QtCore, QtWidgets

import substance_painter.ui as sp_ui

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


def save(dock):
    if not _state.get("tracking"):
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


def attach(dock):
    """Restore the placement now (deferred) and keep saving it whenever the user moves it."""
    _state["dock"] = dock
    on_change = lambda *_: save(dock)   # noqa: E731
    dock.dockLocationChanged.connect(on_change)
    dock.topLevelChanged.connect(on_change)
    _state["on_change"] = on_change
    QtCore.QTimer.singleShot(0, lambda: _restore_if_alive(dock))
    _start_tracking_later()


def on_gui_started():
    """Painter applies its own layout late during start-up: put the panel back afterwards."""
    dock = _state.get("dock")
    if dock is not None:
        _restore_if_alive(dock)
        _start_tracking_later()


def _restore_if_alive(dock):
    try:
        restore(dock)
    except RuntimeError:   # dock already deleted (plugin closed)
        pass


def detach():
    _state["tracking"] = False
    # Disconnect: Painter's teardown moves the dock one last time, and calling into Python
    # then crashes it.
    dock, on_change = _state.get("dock"), _state.pop("on_change", None)
    if dock is not None and on_change is not None:
        for signal in ("dockLocationChanged", "topLevelChanged"):
            try:
                getattr(dock, signal).disconnect(on_change)
            except (RuntimeError, TypeError):
                pass
    timer = _state.pop("timer", None)
    if timer is not None:
        timer.stop()
    _state.pop("dock", None)
