"""Install the presets shipped with the plugin (assets/) into the user's Painter shelf.

assets/<type>/<file> is copied to <user shelf>/<type>/<file> (e.g. export-presets, templates).
A file is written when it is missing, or when the copy on disk is still exactly the one this
plugin installed last time (so plugin updates refresh it). A file the user edited - or one that
was already there and differs - is never overwritten; that is reported in the log instead.
"""

import hashlib
import json
import os
import shutil

from PySide6 import QtCore

import substance_painter.logging as sp_log
import substance_painter.resource as sp_resource

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
_SETTINGS = ("EM", "em_variant_exporter")
_KEY = "assets/installed"   # {relative path: sha1 of the file we wrote}


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _bundled_files():
    for root, dirs, files in os.walk(ASSETS_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            src = os.path.join(root, name)
            yield os.path.relpath(src, ASSETS_DIR).replace("\\", "/"), src


def install():
    """Returns (installed, updated, kept) lists of relative paths. Safe to call repeatedly."""
    if not os.path.isdir(ASSETS_DIR):
        return [], [], []
    shelf = sp_resource.Shelves.user_shelf()
    settings = QtCore.QSettings(*_SETTINGS)
    try:
        record = json.loads(settings.value(_KEY, "") or "{}")
    except (TypeError, ValueError):
        record = {}

    installed, updated, kept = [], [], []
    for rel, src in _bundled_files():
        dst = os.path.join(shelf.path(), *rel.split("/"))
        src_hash = _sha1(src)
        if os.path.exists(dst):
            dst_hash = _sha1(dst)
            if dst_hash == src_hash:
                record[rel] = src_hash   # already up to date
                continue
            if record.get(rel) != dst_hash:
                kept.append(rel)          # edited by the user, or not ours: leave it
                continue
            updated.append(rel)
        else:
            installed.append(rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        record[rel] = src_hash

    settings.setValue(_KEY, json.dumps(record))
    if installed or updated:
        shelf.refresh()
    for rel in installed:
        sp_log.info(f"[Variant Exporter] installed {rel} into the '{shelf.name()}' shelf")
    for rel in updated:
        sp_log.info(f"[Variant Exporter] updated {rel} in the '{shelf.name()}' shelf")
    for rel in kept:
        sp_log.warning(f"[Variant Exporter] {rel} in the '{shelf.name()}' shelf differs from the "
                       f"plugin's version and was left untouched (delete it to reinstall)")
    return installed, updated, kept


DEFAULT_PROJECT_TEMPLATE = "templates/!EM-Pipeline.spt"


def set_default_project_template():
    """Preselect our template in Painter's New Project dialog. Painter keeps the dialog's
    choice in its own settings (new_project/last_template); we point it at the installed copy
    each time the plugin starts, so the user can still pick another one for a given project."""
    path = os.path.join(sp_resource.Shelves.user_shelf().path(),
                        *DEFAULT_PROJECT_TEMPLATE.split("/")).replace("\\", "/")
    if not os.path.exists(path):
        return False
    painter = QtCore.QSettings("Adobe", "Adobe Substance 3D Painter")
    if painter.value("new_project/last_template") != path:
        painter.setValue("new_project/last_template", path)
        painter.sync()
        sp_log.info(f"[Variant Exporter] default new-project template: {path}")
    return True


def install_safely():
    """install() that never breaks plugin start-up (e.g. shelf services not ready yet)."""
    try:
        install()
        set_default_project_template()
        return True
    except Exception as e:   # noqa: BLE001 - retried on GraphicalUserInterfaceStarted
        sp_log.warning(f"[Variant Exporter] presets not installed yet: {e}")
        return False
