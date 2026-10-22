"""SP Variant Exporter - Adobe Substance 3D Painter plugin.

Exports every variation (child layer / group) of the selected group(s) one by
one, with numbered file names (_0001, _0002...), in one or several sizes.
"""

import sys as _sys

# Painter reloads a plugin (Python menu: untick / tick) by re-running this file, but keeps
# its sub-modules cached: drop them so an updated plugin is loaded entirely fresh.
for _name in [n for n in _sys.modules if n.startswith(__name__ + ".")]:
    del _sys.modules[_name]

import shiboken6  # noqa: E402 - after the cache purge above
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

import substance_painter.event as sp_event
import substance_painter.project as sp_project
import substance_painter.ui as sp_ui

from . import assets_install, core, diag, dock_placement, self_update, ui

_state = {}


def _batch_window():
    """The Variant Exporter window, if open (None once Qt has deleted it)."""
    win = _state.get("batch")
    try:
        return win if win is not None and win.isVisible() else None
    except RuntimeError:
        return None


def _panels():
    win = _batch_window()
    return [p for p in (_state.get("panel"), win.panel if win else None) if p is not None]


def _update_menu():
    action = _state.get("action")
    if action is not None:
        action.setEnabled(sp_project.is_open())


def _on_project_opened(_evt):
    diag.trace("project opened")
    panel = _state.get("panel")
    if panel:
        panel.on_project_opened()
    _update_menu()


def _on_project_closed(_evt):
    diag.trace("project about to close")
    win = _batch_window()
    if win:
        win.close()   # saves its settings while the project is still readable
    panel = _state.get("panel")
    if panel:
        panel.on_project_closed()
    _update_menu()


def _on_layer_stack_changed(_evt):
    for panel in _panels():
        panel.on_layer_stack_changed()


def _on_project_about_to_save(_evt):
    # Make sure pending edits are written into the saved file (the batch window if it's
    # open: the docked panel is greyed out meanwhile).
    win = _batch_window()
    panel = win.panel if win else _state.get("panel")
    if panel:
        panel.save_settings()


def _open_window(tab=ui.VariantExporterWindow.VARIATIONS):
    """Open the Variant Exporter window (or bring it up) on the given tab."""
    win = _batch_window()
    if win is None:
        dock_panel = _state.get("panel")
        if dock_panel:
            dock_panel.save_settings()     # the window loads the very same settings
            dock_panel.setEnabled(False)   # one editor at a time
        win = ui.VariantExporterWindow()
        win.closed.connect(_on_batch_closed)
        _state["batch"] = win
    win.show_tab(tab)


def _on_batch_closed():
    diag.trace("window closed")
    _state.pop("batch", None)
    dock_panel = _state.get("panel")
    if dock_panel:
        dock_panel.setEnabled(True)
        dock_panel.on_project_opened()   # pick up what was changed in the window


def _on_gui_started(_evt):
    diag.trace("GUI started")
    dock_placement.on_gui_started()
    if not _state.get("assets_installed"):
        _state["assets_installed"] = assets_install.install_safely()


_EVENTS = [
    (sp_event.GraphicalUserInterfaceStarted, _on_gui_started),
    (sp_event.LayerStacksModelDataChanged, _on_layer_stack_changed),
    (sp_event.ProjectOpened, _on_project_opened),
    (sp_event.ProjectCreated, _on_project_opened),
    (sp_event.ProjectAboutToClose, _on_project_closed),
    (sp_event.ProjectAboutToSave, _on_project_about_to_save),
]


class _QuitWatcher(QtCore.QObject):
    """Receives QApplication.aboutToQuit. A QObject (deleted in close_plugin) rather than a
    plain function: PySide reported disconnecting the function as done while the connection
    stayed alive, so the handler ran a second time after the plugin was already closed."""

    def on_about_to_quit(self):
        diag.trace("Qt aboutToQuit")
        # Painter closes the Python plugins *before* emitting aboutToQuit, so normally there
        # is nothing left to do here. Should the order ever change, tear down while Qt and
        # Python are both still alive: leftover widgets destroyed after Python crash Painter.
        if not _state.get("closed"):
            close_plugin()


def _delete(widget):
    """shiboken-delete a widget if it still exists (Painter may have deleted it already)."""
    if widget is None:
        return
    try:
        if shiboken6.isValid(widget):
            sp_ui.delete_ui_element(widget)
    except RuntimeError:
        pass


def start_plugin():
    diag.start()
    _state.clear()
    # Bundled export presets / templates -> the user's shelf (retried once the GUI is up).
    _state["assets_installed"] = assets_install.install_safely()
    panel = ui.VariantExporterPanel()
    _state["panel"] = panel
    # Only the panel is kept: Painter rebuilds the dock while applying its layout at start-up
    # (the one returned here gets deleted), see dock_placement.
    sp_ui.add_dock_widget(panel)
    dock_placement.attach(panel)
    for evt, cb in _EVENTS:
        sp_event.DISPATCHER.connect(evt, cb)
    panel.export_requested.connect(lambda: _open_window(ui.VariantExporterWindow.EXPORT))
    action = QtGui.QAction("Export Variants…", sp_ui.get_main_window())
    action.triggered.connect(lambda: _open_window(ui.VariantExporterWindow.VARIATIONS))
    separator = QtGui.QAction(sp_ui.get_main_window())
    separator.setSeparator(True)
    sp_ui.add_action(sp_ui.ApplicationMenu.File, separator)   # line above our entry
    sp_ui.add_action(sp_ui.ApplicationMenu.File, action)
    _state["action"] = action
    _state["separator"] = separator
    _update_menu()
    self_update.start()   # reload in place when the installer updates the files
    watcher = _QuitWatcher()
    QtWidgets.QApplication.instance().aboutToQuit.connect(watcher.on_about_to_quit)
    _state["quit_watcher"] = watcher
    diag.trace("plugin started")


def close_plugin():
    diag.trace("close_plugin begin" + (" (again)" if _state.get("closed") else ""))
    _state["closed"] = True
    watcher = _state.pop("quit_watcher", None)
    if watcher is not None:
        # A real QObject slot: disconnecting it works, unlike for a plain function. Deleted
        # later rather than now: this may be running inside that very slot (quit path), and
        # deleting the receiver while it is delivering the signal is unsafe. Kept referenced
        # so Python doesn't free it first either.
        _state["old_quit_watcher"] = watcher
        try:
            QtWidgets.QApplication.instance().aboutToQuit.disconnect(watcher.on_about_to_quit)
        except (RuntimeError, TypeError):
            pass
        try:
            watcher.deleteLater()
        except RuntimeError:
            pass
    self_update.stop()
    win = _state.pop("batch", None)   # (closing it pops the key too: take it first)
    if win is not None:
        try:
            if shiboken6.isValid(win):
                win.close()   # saves its settings; Qt would only delete it later: do it now
        except RuntimeError:
            pass
        _delete(win)
    for key in ("action", "separator"):   # removes them from the File menu
        _delete(_state.pop(key, None))
    dock = dock_placement.current_dock()
    dock_placement.detach()
    for evt, cb in _EVENTS:
        try:
            sp_event.DISPATCHER.disconnect(evt, cb)
        except Exception:
            pass
    panel = _state.pop("panel", None)
    if panel is not None:
        try:
            panel.shutdown()
        except RuntimeError:
            pass
    # The panel first (deleting the dock first would delete the panel with it), then the
    # dock it was living in, unless Painter already dropped it along with the panel.
    _delete(panel)
    _delete(dock)
    diag.trace("close_plugin end")
    diag.stop()
