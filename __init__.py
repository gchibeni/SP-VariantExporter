"""SP Variant Exporter - Adobe Substance 3D Painter plugin.

Exports every variation (child layer / group) of the selected group(s) one by
one, with numbered file names (_0001, _0002...), in one or several sizes.
"""

import sys as _sys

# Painter reloads a plugin (Python menu: untick / tick) by re-running this file, but keeps
# its sub-modules cached: drop them so an updated plugin is loaded entirely fresh.
for _name in [n for n in _sys.modules if n.startswith(__name__ + ".")]:
    del _sys.modules[_name]

from PySide6 import QtGui, QtWidgets  # noqa: E402 - after the cache purge above

import substance_painter.event as sp_event
import substance_painter.project as sp_project
import substance_painter.ui as sp_ui

from . import assets_install, core, dock_placement, self_update, ui

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
    panel = _state.get("panel")
    if panel:
        panel.on_project_opened()
    _update_menu()


def _on_project_closed(_evt):
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
    _state.pop("batch", None)
    dock_panel = _state.get("panel")
    if dock_panel:
        dock_panel.setEnabled(True)
        dock_panel.on_project_opened()   # pick up what was changed in the window


def _on_gui_started(_evt):
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


def _on_about_to_quit():
    # Painter is quitting: tear everything down while Qt and Python are both still alive.
    # Leftover widgets / connections otherwise get destroyed after Python, which crashed
    # Painter on exit. close_plugin() is safe to run again if Painter calls it later.
    close_plugin()


def start_plugin():
    # Bundled export presets / templates -> the user's shelf (retried once the GUI is up).
    _state["assets_installed"] = assets_install.install_safely()
    panel = ui.VariantExporterPanel()
    _state["panel"] = panel
    _state["dock"] = sp_ui.add_dock_widget(panel)
    dock_placement.attach(_state["dock"])
    for evt, cb in _EVENTS:
        sp_event.DISPATCHER.connect(evt, cb)
    panel.export_requested.connect(lambda: _open_window(ui.VariantExporterWindow.EXPORT))
    action = QtGui.QAction("Export Variants\u2026", sp_ui.get_main_window())
    action.triggered.connect(lambda: _open_window(ui.VariantExporterWindow.VARIATIONS))
    separator = QtGui.QAction(sp_ui.get_main_window())
    separator.setSeparator(True)
    sp_ui.add_action(sp_ui.ApplicationMenu.File, separator)   # line above our entry
    sp_ui.add_action(sp_ui.ApplicationMenu.File, action)
    _state["action"] = action
    _state["separator"] = separator
    _update_menu()
    self_update.start()   # reload in place when the installer updates the files
    QtWidgets.QApplication.instance().aboutToQuit.connect(_on_about_to_quit)


def close_plugin():
    try:
        QtWidgets.QApplication.instance().aboutToQuit.disconnect(_on_about_to_quit)
    except (RuntimeError, TypeError):
        pass
    self_update.stop()
    win = _batch_window()
    if win:
        win.close()
    _state.pop("batch", None)
    for key in ("action", "separator"):   # removes them from the File menu
        item = _state.pop(key, None)
        if item is not None:
            try:
                sp_ui.delete_ui_element(item)
            except RuntimeError:
                pass
    dock_placement.detach()
    for evt, cb in _EVENTS:
        try:
            sp_event.DISPATCHER.disconnect(evt, cb)
        except Exception:
            pass
    panel = _state.get("panel")
    if panel is not None:
        try:
            panel.shutdown()
        except RuntimeError:
            pass
    # Delete the panel before its dock: deleting the dock first would delete the panel with it.
    for key in ("panel", "dock"):
        widget = _state.pop(key, None)
        if widget is not None:
            try:
                sp_ui.delete_ui_element(widget)
            except RuntimeError:
                pass
