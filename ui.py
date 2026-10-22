"""UI of the variant exporter: a dock panel listing the variations, and an export dialog."""

import json
import os
import time
import traceback

from PySide6 import QtCore, QtGui, QtWidgets

import substance_painter.export as sp_export
import substance_painter.logging as sp_log
import substance_painter.project as sp_project
import substance_painter.textureset as sp_textureset
import substance_painter.ui as sp_ui

from . import core, theme as th
from .version import __version__

METADATA_CONTEXT = "em_variant_exporter"
METADATA_KEY = "settings_json"   # whole settings as JSON text (older versions: "settings" dict)
# Local copy of each project's settings (keyed by project uuid): survives closing Painter
# without re-saving the .spp, where the project metadata copy would be lost.
LOCAL_STORE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                           "em_variant_exporter", "projects")
PROJECT_SIZE = "Texture set size"
TEMPLATE_DEFAULT = "Template default"
TITLE = "Variant Exporter"
DEFAULT_EXPORT_PRESET = "!EM-PDP"   # shipped in assets/export-presets
ICON_SIZE = 14


def _size_label(size):
    if not size:
        return "Native"   # texture set resolution
    return f"{size // 1024}K" if size >= 1024 else str(size)


def _section_title(text):
    label = QtWidgets.QLabel(text.upper())
    label.setProperty("role", "section")
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * 0.85)
    label.setFont(font)
    return label


def _label(text="", role="muted"):
    label = QtWidgets.QLabel(text)
    label.setProperty("role", role)
    return label


def _themed_root(widget):
    """Apply the EM Hub theme to a top-level panel / dialog and everything inside it."""
    widget.setProperty("emRoot", True)
    widget.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    widget.setStyleSheet(th.stylesheet())


class _IconButton(QtWidgets.QPushButton):
    """Push button that lays out its icon + text itself: Qt's built-in icon/text gap is tight
    and the pair sits slightly off-centre with our padding. Here they are centred as a group
    with a comfortable gap, and both follow the hover / disabled colours."""

    ICON = 15
    GAP = 8

    def __init__(self, text, icon_name, primary=False):
        super().__init__()
        self._primary = primary
        self._hover = False
        self._icon_name = icon_name
        self._icon_label = QtWidgets.QLabel()
        self._text_label = QtWidgets.QLabel(text)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(self.GAP)
        lay.addStretch(1)
        for label in (self._icon_label, self._text_label):
            label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
            label.setAlignment(QtCore.Qt.AlignVCenter)
            lay.addWidget(label, 0, QtCore.Qt.AlignVCenter)
        lay.addStretch(1)
        self._pixmaps = {}
        self._refresh()

    # QPushButton's own text stays empty: route text through our label.
    def setText(self, text):
        self._text_label.setText(text)
        self.updateGeometry()

    def text(self):
        return self._text_label.text()

    def sizeHint(self):
        hint = super().sizeHint()
        width = self.layout().sizeHint().width()
        return QtCore.QSize(max(hint.width(), width), max(hint.height(), 32))

    def minimumSizeHint(self):
        return QtCore.QSize(self.layout().minimumSize().width(), 32)

    def _colors(self):
        if self._primary:
            return ("#ffffff" if self.isEnabled() else th.GRAY_500)
        if not self.isEnabled():
            return th.GRAY_400
        return th.TEXT if self._hover else th.GRAY_700

    def set_icon(self, name):
        self._icon_name = name
        self._pixmaps = {}
        self._refresh()

    def _refresh(self):
        color = self._colors()
        if color not in self._pixmaps:
            self._pixmaps[color] = th.pixmap(self._icon_name, color, self.ICON)
        self._icon_label.setPixmap(self._pixmaps[color])
        self._text_label.setStyleSheet(
            f"color: {color}; background: transparent; font-weight: 600; padding: 0;")

    def enterEvent(self, event):
        self._hover = True
        self._refresh()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._refresh()
        super().leaveEvent(event)

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.EnabledChange:
            self._refresh()
        super().changeEvent(event)


def _button(text, icon_name=None, primary=False):
    btn = _IconButton(text, icon_name, primary) if icon_name else QtWidgets.QPushButton(text)
    if primary:
        btn.setProperty("variant", "primary")
    btn.setCursor(QtCore.Qt.PointingHandCursor)
    return btn


def _tool_button(text, icon_name=None, tooltip=""):
    btn = QtWidgets.QToolButton()
    btn.setText(text)
    btn.setToolTip(tooltip)
    btn.setAutoRaise(True)
    btn.setCursor(QtCore.Qt.PointingHandCursor)
    if icon_name:
        btn.setIcon(th.icon(icon_name, th.GRAY_500, active_color=th.TEXT))
        btn.setIconSize(QtCore.QSize(14, 14))
        btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon if text
                               else QtCore.Qt.ToolButtonIconOnly)
    return btn


class _WheelGuard(QtCore.QObject):
    """Mouse wheel over a dropdown / number field that isn't focused scrolls the page instead
    of silently changing the value (e.g. resetting Start to 0000 while scrolling past it)."""

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Wheel and not obj.hasFocus():
            area = obj.parentWidget()
            while area is not None and not isinstance(area, QtWidgets.QAbstractScrollArea):
                area = area.parentWidget()
            if area is not None:
                QtWidgets.QApplication.sendEvent(area.viewport(), event)
            return True
        return False


def _guard_wheel(*widgets):
    for w in widgets:
        w.setFocusPolicy(QtCore.Qt.StrongFocus)
        # One guard per widget, owned by it: no Python object outlives Painter's widgets
        # (a shared global filter got called during Painter's shutdown and crashed it).
        guard = _WheelGuard(w)
        w.installEventFilter(guard)


def _guard_wheel_in(root):
    _guard_wheel(*root.findChildren(QtWidgets.QComboBox),
                 *root.findChildren(QtWidgets.QAbstractSpinBox))


def _half():
    """Container whose width comes only from its layout stretch: two of them in a row are
    exactly half each, whatever they contain (so stacked rows line up)."""
    w = QtWidgets.QWidget()
    w.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
    return w


def _card(widget):
    """Rounded card around a list/table: views don't clip their rows to a stylesheet radius,
    so the rounding lives on this frame and the view inside stays square."""
    frame = QtWidgets.QFrame()
    frame.setObjectName("emCard")
    lay = QtWidgets.QVBoxLayout(frame)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.addWidget(widget)
    return frame


def _separator():
    line = QtWidgets.QFrame()
    line.setObjectName("emSeparator")
    line.setFrameShape(QtWidgets.QFrame.NoFrame)
    return line


def _equal_row(*widgets):
    """Row whose widgets share the width equally and grow with the window."""
    row = QtWidgets.QHBoxLayout()
    for w in widgets:
        w.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        row.addWidget(w, 1)
    return row


def _form():
    """Form layout whose labels sit centred on their field (Painter's style puts them at the top)."""
    form = QtWidgets.QFormLayout()
    form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    form.setFormAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
    form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
    return form


def _center_form_labels(root):
    """Make every form label as tall as its field, text centred: QFormLayout otherwise pins the
    label to the top of the row, a few pixels above the middle of our taller fields."""
    for form in root.findChildren(QtWidgets.QFormLayout):
        for i in range(form.rowCount()):
            label = form.itemAt(i, QtWidgets.QFormLayout.LabelRole)
            field = form.itemAt(i, QtWidgets.QFormLayout.FieldRole)
            if label is None or field is None or label.widget() is None:
                continue
            label.widget().setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            label.widget().setMinimumHeight(field.sizeHint().height())


def _with_steppers(spin):
    """Spin box with our own down / up chevron buttons beside it (Qt's built-in arrows
    can't be laid out cleanly in a rounded field)."""
    spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
    box = QtWidgets.QWidget()
    box.setObjectName("emStepper")
    lay = QtWidgets.QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    lay.addWidget(spin, 1, QtCore.Qt.AlignVCenter)
    for name, step, tip in (("chevron-down", spin.stepDown, "Decrease"),
                            ("chevron-up", spin.stepUp, "Increase")):
        btn = _tool_button("", name, tip)
        btn.setAutoRepeat(True)
        btn.setFixedSize(26, 26)
        btn.clicked.connect(step)
        lay.addWidget(btn, 0, QtCore.Qt.AlignVCenter)
    return box


# =========================================================================== variations panel

class _VariationTree(QtWidgets.QTreeWidget):
    """Groups as headers, variations below with a checkbox and the number they will get."""

    changed = QtCore.Signal()        # check states
    order_changed = QtCore.Signal()  # drag & drop / reverse

    def __init__(self):
        super().__init__()
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.setIndentation(16)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setAlternatingRowColors(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.setDropIndicatorShown(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self._start = 1
        self._digits = 4
        self._count_skipped = True
        self._mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self._icons = {
            ("ball", False): th.icon("ball", th.GRAY_500),
            ("ball", True): th.icon("ball", th.GRAY_400),
            ("folder", False): th.icon("folder", th.GRAY_500),
            ("folder", True): th.icon("folder", th.GRAY_400),
            ("group", False): th.icon("folder", th.ACCENT),
        }
        self.setIconSize(QtCore.QSize(ICON_SIZE, ICON_SIZE))
        self.itemChanged.connect(self._on_item_changed)
        self.itemClicked.connect(self._on_item_clicked)

    def populate(self, grouped, skipped):
        """grouped: [(group node, [Variation])]"""
        self.blockSignals(True)
        self.clear()
        for group, children in grouped:
            top = QtWidgets.QTreeWidgetItem([group.get_name(), f"{len(children)}"])
            top.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable
                         | QtCore.Qt.ItemIsAutoTristate | QtCore.Qt.ItemIsSelectable
                         | QtCore.Qt.ItemIsDragEnabled | QtCore.Qt.ItemIsDropEnabled)
            top.setData(0, QtCore.Qt.UserRole, group.uid())
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            top.setForeground(1, QtGui.QColor(th.GRAY_500))
            top.setIcon(0, self._icons["group", False])
            top.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            top.setToolTip(0, "Check / uncheck every variation of this group.\n"
                              "Drag to change the group order.")
            self.addTopLevelItem(top)
            for var in children:
                item = QtWidgets.QTreeWidgetItem([var.name, ""])
                item.setData(0, QtCore.Qt.UserRole, var.uid)
                item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable
                              | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsDragEnabled)
                item.setCheckState(0, QtCore.Qt.Unchecked if var.uid in skipped
                                   else QtCore.Qt.Checked)
                item.setToolTip(0, f"{'Group' if var.is_group else 'Layer'}: {var.name}\n"
                                   "Drag to change its number.")
                item.setData(0, QtCore.Qt.UserRole + 1, "folder" if var.is_group else "ball")
                item.setFont(1, self._mono)
                item.setTextAlignment(1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                top.addChild(item)
            top.setExpanded(True)
        self.clearSelection()
        self.setCurrentItem(None)
        self.blockSignals(False)
        self._update_numbers()

    def set_numbering(self, start, digits):
        self._start, self._digits = start, digits
        self._update_numbers()

    def variation_items(self):
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            for j in range(top.childCount()):
                yield top.child(j)

    def variation_uids(self):
        return [it.data(0, QtCore.Qt.UserRole) for it in self.variation_items()]

    def group_uids(self):
        return [self.topLevelItem(i).data(0, QtCore.Qt.UserRole)
                for i in range(self.topLevelItemCount())]

    def reverse(self):
        self.blockSignals(True)
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            children = top.takeChildren()
            top.addChildren(children[::-1])
        self.blockSignals(False)
        self._update_numbers()
        self.order_changed.emit()

    def dropEvent(self, event):
        """Move rows ourselves: variations only within their own group, groups among groups."""
        dragged = self.selectedItems()[0] if self.selectedItems() else None
        target = self.itemAt(event.position().toPoint())
        if dragged is None or target is None or target is dragged:
            event.ignore()
            return
        pos = self.dropIndicatorPosition()
        src_parent = dragged.parent()
        if pos == QtWidgets.QAbstractItemView.OnItem:
            dest_parent, index = target, target.childCount()
        else:
            dest_parent = target.parent()
            siblings = dest_parent if dest_parent else self.invisibleRootItem()
            index = siblings.indexOfChild(target)
            if pos == QtWidgets.QAbstractItemView.BelowItem:
                index += 1
        if dest_parent is not src_parent:
            event.ignore()
            return
        container = src_parent if src_parent else self.invisibleRootItem()
        old = container.indexOfChild(dragged)
        if old < index:
            index -= 1
        # Ignore the action so Qt doesn't also remove the source row.
        event.setDropAction(QtCore.Qt.IgnoreAction)
        event.accept()
        if index == old:
            return
        self.blockSignals(True)
        container.takeChild(old)
        container.insertChild(index, dragged)
        dragged.setExpanded(True)
        self.blockSignals(False)
        self.setCurrentItem(dragged)
        self._update_numbers()
        self.order_changed.emit()

    def checked_uids(self):
        return [it.data(0, QtCore.Qt.UserRole) for it in self.variation_items()
                if it.checkState(0) == QtCore.Qt.Checked]

    def unchecked_uids(self):
        return [it.data(0, QtCore.Qt.UserRole) for it in self.variation_items()
                if it.checkState(0) != QtCore.Qt.Checked]

    def set_all(self, checked):
        state = QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked
        self.blockSignals(True)
        for it in self.variation_items():
            it.setCheckState(0, state)
        self.blockSignals(False)
        self._update_numbers()
        self.changed.emit()

    def numbers(self):
        """uid -> file number of every checked variation."""
        result, n = {}, self._start
        for it in self.variation_items():
            checked = it.checkState(0) == QtCore.Qt.Checked
            if checked:
                result[it.data(0, QtCore.Qt.UserRole)] = n
            if checked or self._count_skipped:
                n += 1
        return result

    def set_count_skipped(self, count_skipped):
        self._count_skipped = count_skipped
        self._update_numbers()

    def _update_numbers(self):
        self.blockSignals(True)
        n = self._start
        normal, muted = QtGui.QColor(th.TEXT), QtGui.QColor(th.GRAY_400)
        accent = QtGui.QColor(th.ACCENT_HOVER)
        for it in self.variation_items():
            number = f"_{str(n).zfill(self._digits)}"
            if it.checkState(0) == QtCore.Qt.Checked:
                it.setText(1, number)
                it.setForeground(0, normal)
                it.setForeground(1, accent)
                it.setIcon(0, self._icons[it.data(0, QtCore.Qt.UserRole + 1), False])
                n += 1
            else:
                it.setText(1, f"skip {number}" if self._count_skipped else "skip")
                if self._count_skipped:
                    n += 1
                it.setIcon(0, self._icons[it.data(0, QtCore.Qt.UserRole + 1), True])
                it.setForeground(0, muted)
                it.setForeground(1, muted)
        self.blockSignals(False)
        self.viewport().update()

    def _on_item_changed(self, _item, column):
        if column == 0:
            self._update_numbers()
            self.changed.emit()

    def _on_item_clicked(self, item, column):
        # Clicking anywhere on a variation row toggles it, not only the tiny checkbox.
        if column == 1 and item.parent() is not None:
            checked = item.checkState(0) == QtCore.Qt.Checked
            item.setCheckState(0, QtCore.Qt.Unchecked if checked else QtCore.Qt.Checked)


class _PaddedSpinBox(QtWidgets.QSpinBox):
    """Spin box whose zero padding follows what the user types: "001" = 3 digits, "00005" = 5.

    The arrows / mouse wheel step the value and keep the padding.
    """

    paddingChanged = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self._digits = 4
        self.setRange(0, 99999999)
        self.lineEdit().textEdited.connect(self._on_text_edited)

    def digits(self):
        return self._digits

    def set_padded(self, value, digits):
        self._digits = digits
        self.setValue(value)
        self.lineEdit().setText(self.textFromValue(value))

    def textFromValue(self, value):
        return str(value).zfill(self._digits)

    def valueFromText(self, text):
        return int(text) if text.isdigit() else self.value()

    def validate(self, text, pos):
        if text == "":
            return QtGui.QValidator.Intermediate, text, pos
        if text.isdigit() and len(text) <= 8:
            return QtGui.QValidator.Acceptable, text, pos
        return QtGui.QValidator.Invalid, text, pos

    def _on_text_edited(self, text):
        if text.isdigit() and len(text) != self._digits:
            self._digits = len(text)
            self.paddingChanged.emit()


class VariantExporterPanel(QtWidgets.QWidget):
    export_requested = QtCore.Signal()   # the Export button: show the Export tab
    _next_text = ""                       # set inside the window: the button reads "Next..."
    variations_changed = QtCore.Signal()  # list / ticks changed (the Export tab mirrors it)

    def __init__(self):
        super().__init__()
        self.setObjectName("em_variant_exporter_panel")
        self.setWindowTitle(TITLE)
        _themed_root(self)
        self._groups = []
        self._signature = None
        self._settings = core.ExportSettings()
        self._custom_order = []   # variation uids in user order, [] = layer stack order
        self._dialog = None
        self.exporting = False
        self._closing = False
        # Painter still reports the project as open during ProjectAboutToClose: track it ourselves.
        self._project_open = False
        # Every change is saved shortly after it happens (debounced).
        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.save_settings)
        # Layer stack events come in bursts: wait for them to settle before refreshing.
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self._auto_refresh)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # --- header
        row = QtWidgets.QHBoxLayout()
        row.addWidget(_section_title("Variations"))
        row.addStretch()
        self.pick_btn = _button("Use selected", "layers")
        self.pick_btn.setToolTip("Select groups and/or layers in the Layer stack, then click.\n"
                                 "Group: each layer or group directly inside it is one variation.\n"
                                 "Layer: the layer itself is one variation.")
        row.addWidget(self.pick_btn)
        self.clear_btn = QtWidgets.QPushButton()
        self.clear_btn.setObjectName("emSquare")
        self.clear_btn.setIcon(th.icon("x", th.GRAY_700, disabled_color=th.GRAY_400,
                                       active_color=th.TEXT))
        self.clear_btn.setIconSize(QtCore.QSize(14, 14))
        self.clear_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.clear_btn.setToolTip("Clear the list (forget the picked groups / layers)")
        row.addWidget(self.clear_btn)
        lay.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        self.summary_label = _label()
        row.addWidget(self.summary_label, 1)
        self.all_btn = _tool_button("All", tooltip="Check every variation")
        self.none_btn = _tool_button("None", tooltip="Uncheck every variation")
        for b in (self.all_btn, self.none_btn):
            row.addWidget(b)
        lay.addLayout(row)

        # --- list or empty-state hint
        self.views = QtWidgets.QStackedWidget()
        self.hint = _label("", "hint")
        self.hint.setAlignment(QtCore.Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.tree = _VariationTree()
        self.views.addWidget(self.hint)
        self.views.addWidget(self.tree)
        frame = QtWidgets.QFrame()
        frame.setObjectName("emCard")
        f_lay = QtWidgets.QVBoxLayout(frame)
        f_lay.setContentsMargins(6, 6, 6, 6)
        f_lay.addWidget(self.views)
        lay.addWidget(frame, 1)

        # --- numbering
        # Two equal halves, matching the Reverse / Reset row below:
        # [Start ____ v ^] [Skip counts]
        row = QtWidgets.QHBoxLayout()
        left_w, right_w = _half(), _half()
        left = QtWidgets.QHBoxLayout(left_w)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        left.addWidget(_label("Start"), 0, QtCore.Qt.AlignVCenter)
        # One field for both: "001" = start at 1 with 3 digits, "00005" = start at 5 with 5.
        self.start_spin = _PaddedSpinBox()
        self.start_spin.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.start_spin.setMinimumWidth(60)
        self.start_spin.setToolTip("First number, typed with the padding you want:\n"
                                   "001 = 001, 002, 003...\n00005 = 00005, 00006, 00007...\n"
                                   "Use the arrows to step it up or down.")
        self.start_box = _with_steppers(self.start_spin)
        left.addWidget(self.start_box, 1, QtCore.Qt.AlignVCenter)
        right = QtWidgets.QHBoxLayout(right_w)
        right.setContentsMargins(0, 0, 0, 0)
        self.count_skipped_cb = QtWidgets.QCheckBox("Skip counts")
        self.count_skipped_cb.setToolTip(
            "On: skipped variations still use up their number (0001, 0003, 0004...).\n"
            "Off: numbers stay consecutive (0001, 0002, 0003...).")
        right.addWidget(self.count_skipped_cb, 0, QtCore.Qt.AlignVCenter)
        right.addStretch(1)
        row.addWidget(left_w, 1)
        row.addWidget(right_w, 1)
        lay.addLayout(row)

        # --- order: buttons share the row and grow with the panel
        row = QtWidgets.QHBoxLayout()
        self.reverse_btn = _button("Reverse", "reverse")
        self.reverse_btn.setToolTip("Reverse the order of the variations.\n"
                                    "You can also drag variations to reorder them.")
        self.reset_order_btn = _button("Reset", "reset")
        self.reset_order_btn.setToolTip("Go back to the Layer stack order")
        for b in (self.reverse_btn, self.reset_order_btn):
            b.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
            row.addWidget(b, 1)
        lay.addLayout(row)

        # --- export
        self.export_btn = _button("Export…", "export", primary=True)
        self.export_btn.setMinimumHeight(34)
        self._export_row = QtWidgets.QHBoxLayout()
        self._export_row.setSpacing(FOOTER_SPACING)
        self._export_row.addWidget(self.export_btn)
        lay.addLayout(self._export_row)

        self.pick_btn.clicked.connect(self._pick_groups)
        self.clear_btn.clicked.connect(self._clear_groups)
        self.all_btn.clicked.connect(lambda: self.tree.set_all(True))
        self.none_btn.clicked.connect(lambda: self.tree.set_all(False))
        self.tree.changed.connect(self._update_summary)
        self.tree.changed.connect(self.schedule_save)
        self.count_skipped_cb.toggled.connect(self._on_count_skipped)
        self.reverse_btn.clicked.connect(self.tree.reverse)
        self.reset_order_btn.clicked.connect(self._reset_order)
        self.tree.order_changed.connect(self._on_order_changed)
        self.start_spin.valueChanged.connect(self._on_numbering)
        self.start_spin.paddingChanged.connect(self._on_numbering)
        self.export_btn.clicked.connect(self._open_export_dialog)

        _guard_wheel_in(self)
        self.on_project_opened()

    def showEvent(self, event):
        super().showEvent(event)
        # Square, as tall as "Use selected": sized once styled.
        side = self.pick_btn.sizeHint().height()
        self.clear_btn.setFixedSize(side, side)

    def _clear_groups(self):
        self._groups = []
        self._custom_order = []
        self.refresh_variations(skipped=set())
        self.save_settings()

    def shutdown(self):
        # Widgets emit change signals while being destroyed: never save from then on.
        self._closing = True
        self._refresh_timer.stop()
        self._dialog = None

    # ------------------------------------------------------------------ project state

    def on_project_opened(self):
        if not sp_project.is_open():
            self.on_project_closed()
            return
        self._project_open = True
        self._settings = self._load_settings()
        if not self._settings.export_dir:
            self._settings.export_dir = self._default_export_dir()
        s = self._settings
        self.start_spin.blockSignals(True)
        self.start_spin.set_padded(s.start_index, s.digits)
        self.start_spin.blockSignals(False)
        self.tree.set_numbering(s.start_index, s.digits)
        self.count_skipped_cb.blockSignals(True)
        self.count_skipped_cb.setChecked(s.count_skipped)
        self.count_skipped_cb.blockSignals(False)
        self.tree.set_count_skipped(s.count_skipped)
        self._custom_order = list(s.order_uids)
        self._groups = core.groups_from_uids(s.group_uids)
        self.refresh_variations(skipped=set(s.skipped_uids))
        if s.reverse_order and not s.order_uids:   # settings saved by the old checkbox
            self.tree.reverse()

    def on_project_closed(self):
        if self._project_open and sp_project.is_open():
            self.save_settings()   # still readable during ProjectAboutToClose: flush last edits
        self._project_open = False
        self._refresh_timer.stop()
        self._signature = None
        self._groups = []
        self._custom_order = []
        defaults = core.ExportSettings()
        self.start_spin.blockSignals(True)
        self.start_spin.set_padded(defaults.start_index, defaults.digits)
        self.start_spin.blockSignals(False)
        self.tree.clear()
        self._update_summary()

    def on_layer_stack_changed(self):
        if self._groups and not self.exporting:
            self._refresh_timer.start()

    def _auto_refresh(self):
        if self.exporting or not self._groups or not sp_project.is_open():
            return
        try:
            groups = core.groups_from_uids(core.source_uids(self._groups))
            signature = self._variation_signature(groups)
        except Exception:
            return
        if signature != self._signature:
            self.refresh_variations()

    @staticmethod
    def _variation_signature(groups):
        return tuple((g.uid(), g.get_name(), tuple((c.uid(), c.get_name()) for c in g.sub_layers()))
                     for g in groups)

    @staticmethod
    def _default_export_dir():
        path = sp_project.file_path()
        if path:
            return os.path.join(os.path.dirname(path), "export").replace("\\", "/")
        return sp_export.get_default_export_path()

    @staticmethod
    def _local_store_path():
        try:
            return os.path.join(LOCAL_STORE, f"{sp_project.get_uuid()}.json")
        except Exception:
            return None

    def _load_settings(self):
        """Newest of: the copy saved in the project, the local copy for this project."""
        candidates = []
        try:
            meta = sp_project.Metadata(METADATA_CONTEXT)
            text = meta.get(METADATA_KEY) if METADATA_KEY in meta.list() else None
            if text:
                candidates.append(json.loads(text))
            else:
                # Older saves: a plain dict whose saved_at Painter stored as a 32-bit float
                # (rounded by up to ~2 min), so it can't be compared: use it only as a fallback.
                legacy = meta.get("settings") if "settings" in meta.list() else None
                if legacy:
                    candidates.append(dict(legacy, saved_at=0.0))
        except Exception:
            sp_log.warning(f"[{TITLE}] could not read saved settings:\n{traceback.format_exc()}")
        path = self._local_store_path()
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    candidates.append(json.load(f))
            except (OSError, ValueError):
                pass
        if not candidates:
            return core.ExportSettings()
        newest = max(candidates, key=lambda d: d.get("saved_at", 0.0))
        return core.ExportSettings.from_dict(newest)

    def schedule_save(self, *_):
        if self._project_open and not self._closing:
            self._save_timer.start()

    def settings(self):
        """Current settings, with this panel's state written into them."""
        s = self._settings
        s.start_index, s.digits = self.numbering()
        s.reverse_order = False
        s.count_skipped = self.count_skipped_cb.isChecked()
        s.order_uids = list(self._custom_order)
        s.group_uids = core.source_uids(self._groups)
        s.skipped_uids = self.tree.unchecked_uids()
        return s

    def save_settings(self):
        self._save_timer.stop()
        if self._closing or not self._project_open or not sp_project.is_open():
            return
        s = self.settings()
        if self._dialog is not None and self._dialog.isVisible() and not self._dialog.loading:
            self._dialog.store(s)
        s.saved_at = time.time()
        data = s.to_dict()
        try:
            # Stored as JSON text: Painter keeps metadata numbers as 32-bit floats, which
            # mangled the save timestamp (and could pick an older copy on load).
            sp_project.Metadata(METADATA_CONTEXT).set(METADATA_KEY, json.dumps(data))
        except Exception:
            sp_log.warning(f"[{TITLE}] could not save settings:\n{traceback.format_exc()}")
        path = self._local_store_path()
        if path:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            except OSError:
                sp_log.warning(f"[{TITLE}] could not write {path}")

    # ------------------------------------------------------------------ variations

    @property
    def groups(self):
        return self._groups

    def stack(self):
        if self._groups:
            return self._groups[0].get_stack()
        return sp_textureset.get_active_stack()

    def selected_variations(self):
        """Checked variations, in the order shown in the list (= numbering order)."""
        by_uid = {v.uid: v for v in core.list_variations(self._groups)}
        return [by_uid[uid] for uid in self.tree.checked_uids() if uid in by_uid]

    def _pick_groups(self):
        try:
            groups = core.selected_groups()
        except Exception:
            sp_log.error(f"[{TITLE}] could not read the layer selection:\n{traceback.format_exc()}")
            return
        if not groups:
            QtWidgets.QMessageBox.information(
                self, TITLE, "Select groups and/or layers in the Layer stack first.\n"
                             "Group: each layer or group directly inside it is one variation.\n"
                             "Layer: the layer itself is one variation.")
            return
        self._groups = groups
        self._custom_order = []
        self.refresh_variations()
        self.save_settings()

    def refresh_variations(self, skipped=None):
        if skipped is None:
            skipped = set(self.tree.unchecked_uids())
        self._signature = None
        grouped = []
        if self._groups:
            try:
                self._groups = core.groups_from_uids(core.source_uids(self._groups))
                grouped = [(g, self._ordered(core.list_variations([g]))) for g in self._groups]
                self._signature = self._variation_signature(self._groups)
            except Exception:
                sp_log.error(f"[{TITLE}] could not read the variations:\n{traceback.format_exc()}")
                self._groups = []
                grouped = []
        self.tree.populate(grouped, skipped)
        self._update_summary()

    def _ordered(self, variations):
        """Apply the custom order; variations it doesn't know yet go last, in stack order."""
        if not self._custom_order:
            return variations
        rank = {uid: i for i, uid in enumerate(self._custom_order)}
        return sorted(variations, key=lambda v: rank.get(v.uid, len(rank)))

    def variation_numbers(self):
        return self.tree.numbers()

    def numbering(self):
        """(start, digits) from the Start field."""
        return self.start_spin.value(), self.start_spin.digits()

    def _on_count_skipped(self, checked):
        self.tree.set_count_skipped(checked)
        self.save_settings()

    def _on_order_changed(self):
        self._custom_order = self.tree.variation_uids()
        order = self.tree.group_uids()
        self._groups.sort(key=lambda g: order.index(g.uid()) if g.uid() in order else len(order))
        self._update_summary()
        self.save_settings()

    def _reset_order(self):
        self._custom_order = []
        self._groups = core.groups_from_uids(core.source_uids(self._groups))
        self.refresh_variations()
        self.save_settings()

    def _on_numbering(self, *_):
        self.tree.set_numbering(*self.numbering())
        if self._dialog is not None:
            self._dialog.update_example()
        if sp_project.is_open() and self._groups:
            self.save_settings()

    def _update_summary(self):
        total = sum(1 for _ in self.tree.variation_items())
        checked = len(self.tree.checked_uids())
        project = self._project_open
        has_groups = project and bool(self._groups)
        self.views.setCurrentIndex(1 if has_groups else 0)
        if not project:
            self.hint.setText("No project loaded\n\nOpen or create a project to export variations.")
        else:
            self.hint.setText("No variations yet.\n\nSelect the group(s) holding your variations,"
                              "\nor the variation layers themselves, in the Layer stack,"
                              "\nthen click \u201cUse selected\u201d.")
        for w in (self.pick_btn, self.start_box, self.count_skipped_cb):
            w.setEnabled(project)
        self.clear_btn.setEnabled(has_groups)
        for w in (self.all_btn, self.none_btn):
            w.setVisible(has_groups)
        # Reverse / Reset are always shown, just greyed out while there's nothing to order.
        self.reverse_btn.setEnabled(has_groups)
        self.reset_order_btn.setEnabled(has_groups and bool(self._custom_order))
        if has_groups:
            stack = self.stack()
            self.summary_label.setText(f"{checked} of {total} selected  ·  {stack}")
        else:
            self.summary_label.setText("")
        self.export_btn.setEnabled(has_groups)
        self.export_btn.setText(self._next_text or self.export_label())
        self.variations_changed.emit()

    def checked_count(self):
        return len(self.tree.checked_uids()) if self._project_open and self._groups else 0

    def export_label(self):
        n = self.checked_count()
        return f"Export {n} variation{'s' if n != 1 else ''}\u2026" if n else "Export\u2026"

    def use_next_button(self):
        """Inside the window the button only moves on to the Export tab: "Next..." + arrow."""
        self._next_text = "Next\u2026"
        self.export_btn.set_icon("arrow-right")
        self.export_btn.setText(self._next_text)

    def add_footer_widget(self, widget):
        self._export_row.addWidget(widget)

    def add_close_button(self, on_click):
        """Close button left of Export, sharing the row 50/50 (used inside the window)."""
        close_btn = _button("Close")
        close_btn.clicked.connect(on_click)
        for w in (close_btn, self.export_btn):
            w.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self._export_row.insertWidget(0, close_btn, 1)
        self._export_row.setStretch(1, 1)
        close_btn.setMinimumHeight(self.export_btn.minimumHeight())
        return close_btn

    def _open_export_dialog(self):
        self.export_requested.emit()


class VariantExporterWindow(QtWidgets.QDialog):
    """The Variant Exporter window: a Variations tab (the panel) and an Export tab (the
    export settings). Opened from File > Export Variants... or the docked panel's Export button.

    It shares the project's settings with the docked panel: the dock saves before this opens,
    this saves when it closes and the dock then reloads them.
    """

    VARIATIONS, EXPORT = 0, 1
    closed = QtCore.Signal()

    def __init__(self):
        super().__init__(sp_ui.get_main_window())
        self.setWindowTitle(f"{TITLE} v{__version__}")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setMinimumSize(560, 620)
        self.resize(640, 820)
        _themed_root(self)
        self.panel = VariantExporterPanel()
        self.panel.setObjectName("em_variant_exporter_window_panel")
        self.page = ExportPage(self.panel)
        self.panel._dialog = self.page   # the panel saves the page's fields with its own
        self.tabs = QtWidgets.QTabWidget()
        # Document mode: the tab bar spans the whole window, and expanding tabs share its width.
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(True)
        self.tabs.tabBar().setDrawBase(False)   # our stylesheet draws the line under the tabs
        self.tabs.addTab(self.panel, "Variations")
        self.tabs.addTab(self.page, "Export")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.addWidget(self.tabs)
        self._done = False
        self._tab = self.VARIATIONS
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.panel.export_requested.connect(lambda: self.show_tab(self.EXPORT))
        self.panel.add_close_button(self.close)
        self.panel.use_next_button()
        self._open_dir_btn = self.page.make_open_dir_button()
        self.panel.add_footer_widget(self._open_dir_btn)
        # Fill the Export page right away (the folder button needs the output directory).
        self.page.open_with(self.panel.settings())
        self.page.exporting_changed.connect(
            lambda busy: self.tabs.tabBar().setEnabled(not busy))

    def showEvent(self, event):
        super().showEvent(event)
        side = self.panel.export_btn.sizeHint().height()
        self._open_dir_btn.setFixedSize(side, side)

    def show_tab(self, index):
        if self.tabs.currentIndex() == index:
            self._on_tab_changed(index)   # make sure the page shows the current settings
        else:
            self.tabs.setCurrentIndex(index)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_tab_changed(self, index):
        if self._tab == self.EXPORT and index != self.EXPORT:
            # Leaving Export: keep its fields in the settings.
            self.page.store(self.panel.settings())
            self.panel.save_settings()
        if index == self.EXPORT:
            self.page.open_with(self.panel.settings())
        self._tab = index

    def closeEvent(self, event):
        if self.panel.exporting:
            event.ignore()
            return
        if not self._done:
            self._done = True
            if self.tabs.currentIndex() == self.EXPORT:
                self.page.store(self.panel.settings())
            self.panel.save_settings()
            self.panel.shutdown()
            self.closed.emit()
        # Accept directly: QDialog.closeEvent would call reject(), which (below) calls close()
        # again - Qt then sees a close already in progress and cancels it, so the window stayed.
        event.accept()

    def reject(self):
        # Esc: go through closeEvent so the settings are saved (QDialog would just hide).
        self.close()


# =========================================================================== export dialog

ROW_HEIGHT = 38
# Footer (Close / Export / folder) geometry shared by both tabs, so they line up exactly.
FOOTER_MARGIN = 10
FOOTER_SPACING = 8
CHECK_COLUMN_WIDTH = 40   # first (on/off) column of the Output maps and Sizes tables


def _fit_table(table):
    """Size a table to its rows so it never scrolls inside the (already scrolling) dialog."""
    table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
    height = (table.horizontalHeader().sizeHint().height() + table.rowCount() * ROW_HEIGHT
              + 2 * table.frameWidth() + 4)
    table.setFixedHeight(height)


class _CenteredCheckDelegate(QtWidgets.QStyledItemDelegate):
    """Checkbox-only cells: draw the box centred (Qt pins it to the left) and toggle it on a
    click anywhere in the cell."""

    def _check_rect(self, option):
        widget = option.widget
        style = widget.style() if widget else QtWidgets.QApplication.style()
        opt = QtWidgets.QStyleOptionViewItem(option)
        opt.features |= QtWidgets.QStyleOptionViewItem.HasCheckIndicator
        rect = style.subElementRect(QtWidgets.QStyle.SE_ItemViewItemCheckIndicator, opt, widget)
        rect.moveCenter(option.rect.center())
        return rect

    def paint(self, painter, option, index):
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget else QtWidgets.QApplication.style()
        state = QtCore.Qt.CheckState(index.data(QtCore.Qt.CheckStateRole) or 0)
        # Row background (zebra / hover) without Qt's own left-aligned box.
        opt.features &= ~QtWidgets.QStyleOptionViewItem.HasCheckIndicator
        opt.text = ""
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, widget)
        check = QtWidgets.QStyleOptionViewItem(option)
        check.rect = self._check_rect(option)
        check.state &= ~QtWidgets.QStyle.State_HasFocus
        if state == QtCore.Qt.Checked:
            check.state |= QtWidgets.QStyle.State_On
        elif state == QtCore.Qt.PartiallyChecked:
            check.state |= QtWidgets.QStyle.State_NoChange
        else:
            check.state |= QtWidgets.QStyle.State_Off
        style.drawPrimitive(QtWidgets.QStyle.PE_IndicatorItemViewItemCheck, check, painter, widget)

    def editorEvent(self, event, model, option, index):
        flags = index.flags()
        if not (flags & QtCore.Qt.ItemIsUserCheckable and flags & QtCore.Qt.ItemIsEnabled):
            return False
        kind = event.type()
        if kind == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
            state = QtCore.Qt.CheckState(index.data(QtCore.Qt.CheckStateRole) or 0)
            new = QtCore.Qt.Unchecked if state == QtCore.Qt.Checked else QtCore.Qt.Checked
            # Store the plain int: an enum object is accepted by setData but then reads back
            # as "unchecked" from QTableWidgetItem.checkState(), so the click wouldn't stick.
            return model.setData(index, new.value, QtCore.Qt.CheckStateRole)
        if kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
            return True   # swallow, so the release above is the only toggle
        return False


def _center_checks(table, *columns):
    delegate = _CenteredCheckDelegate(table)
    for column in columns:
        table.setItemDelegateForColumn(column, delegate)


class _FolderCell(QtWidgets.QWidget):
    """Editable subfolder name + a switch button (shown on hover) that fills in the map's
    default folder name (ALB, ROUGH...) or clears the field."""

    edited = QtCore.Signal()

    def __init__(self, text, default_name):
        super().__init__()
        self.setObjectName("emCell")   # transparent: lets the table's zebra show through
        self._default = default_name
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(4)
        self.edit = QtWidgets.QLineEdit(text)
        self.edit.setPlaceholderText("none")
        self.edit.setToolTip("Subfolder for this map inside the size folder (empty = none)")
        lay.addWidget(self.edit, 1, QtCore.Qt.AlignVCenter)
        # The switch sits inside the field, right-aligned (a trailing field action).
        self._icons = {on: th.icon("switch-on" if on else "switch-off",
                                   th.ACCENT if on else th.GRAY_500, size=18)
                       for on in (True, False)}
        self.button = self.edit.addAction(self._icons[False],
                                          QtWidgets.QLineEdit.TrailingPosition)
        self.button.triggered.connect(self._toggle)
        self.edit.textChanged.connect(self._on_text)
        self._on_text(text, emit=False)
        self.button.setVisible(False)

    def text(self):
        return self.edit.text().strip()

    def _toggle(self):
        self.edit.setText("" if self.text() else self._default)

    def _on_text(self, _text, emit=True):
        on = bool(self.text())
        self.button.setIcon(self._icons[on])
        self.button.setToolTip("Clear the subfolder" if on else f"Use subfolder {self._default}")
        if emit:
            self.edited.emit()

    # The switch only appears while the mouse is over the cell.
    def enterEvent(self, event):
        self.button.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.button.setVisible(False)
        super().leaveEvent(event)


class _HidePainterProgress(QtCore.QObject):
    """While installed, hides Painter's own export progress window (one pops up per export
    call): our dialog shows the progress instead. Use as a context manager."""

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Show and isinstance(obj, QtWidgets.QProgressDialog):
            obj.setWindowOpacity(0.0)            # invisible right away...
            QtCore.QTimer.singleShot(0, obj.hide)  # ...and hidden once it's done showing
        return False

    def __enter__(self):
        QtWidgets.QApplication.instance().installEventFilter(self)
        return self

    def __exit__(self, *_):
        QtWidgets.QApplication.instance().removeEventFilter(self)
        return False


class _OverwriteDialog(QtWidgets.QDialog):
    """Popup shown when files the export would write are already in the output folder:
    Cancel the export, Increment the numbers past the existing files, or Overwrite them."""

    CANCEL, INCREMENT, OVERWRITE = range(3)

    def __init__(self, parent, existing, numbered):
        """`existing`: the conflicting files; `numbered`: whether the export has numbered
        (per variation) files, i.e. whether Increment makes sense."""
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(460)
        _themed_root(self)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        title = _label("Overwrite files?", "")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        lay.addWidget(title)
        text = _label("There's already output files with conflicting names in the destination.", "")
        text.setWordWrap(True)
        lay.addWidget(text)

        files = QtWidgets.QPlainTextEdit("\n".join(existing))
        files.setReadOnly(True)
        files.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        files.setFixedHeight(min(26 + 18 * len(existing), 150))
        files.setToolTip(f"{len(existing)} file(s) would be replaced")
        lay.addWidget(files)

        if numbered:
            hint = _label("Increment: the new files are numbered after the existing ones "
                          "(e.g. file_0004 after file_0003). Maps exported once are still replaced.")
            hint.setWordWrap(True)
            lay.addWidget(hint)
        lay.addSpacing(4)
        lay.addWidget(_separator())

        cancel = _button("Cancel")
        cancel.clicked.connect(lambda: self.done(self.CANCEL))
        buttons = [cancel]
        if numbered:
            increment = _button("Increment", primary=True)
            increment.setToolTip("Keep the existing files: number the new ones after them")
            increment.clicked.connect(lambda: self.done(self.INCREMENT))
            increment.setDefault(True)
            buttons.append(increment)
        overwrite = _button("Overwrite", primary=not numbered)
        overwrite.setToolTip("Replace the existing files")
        overwrite.clicked.connect(lambda: self.done(self.OVERWRITE))
        buttons.append(overwrite)
        lay.addLayout(_equal_row(*buttons))

    def reject(self):   # Esc / window close
        self.done(self.CANCEL)


class _NameOverrideDialog(QtWidgets.QDialog):
    """Small popup: override the file name, prefix and/or suffix of one output map.
    Empty fields fall back to the main file name / prefix / suffix."""

    def __init__(self, parent, map_label, suffix, current, names, example_index):
        """`current`: core.MapSetting holding this map's overrides; `names`: ExportSettings
        with the main file name, prefix and suffix (the fallbacks)."""
        super().__init__(parent)
        self.setWindowTitle("Override name")
        self.setMinimumWidth(460)
        _themed_root(self)
        self._suffix = suffix
        self._index = example_index
        self._names = names
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        description = _label(f"Overriding file names for \u201c{map_label}\u201d maps", "")
        description.setWordWrap(True)
        lay.addWidget(description)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        map_name = _label("Name", "")
        center = QtCore.Qt.AlignVCenter
        grid.addWidget(map_name, 0, 0, center)
        self.name_edit = QtWidgets.QLineEdit(current.name)
        self.name_edit.setPlaceholderText(names.base_name)
        self.name_edit.setToolTip("File name for this map only. Painter tokens work: $project, "
                                  "$mesh, $textureSet, $sceneMaterial.\n"
                                  "Leave empty to use the main file name.")
        grid.addWidget(self.name_edit, 0, 1, center)
        grid.addWidget(_label("Prefix", ""), 1, 0, center)
        self.prefix_edit = QtWidgets.QLineEdit(current.prefix)
        self.prefix_edit.setPlaceholderText(names.prefix or "none")
        self.prefix_edit.setToolTip("Prefix for this map only (empty = the main prefix)")
        self.suffix_edit = QtWidgets.QLineEdit(current.name_suffix)
        self.suffix_edit.setPlaceholderText(names.name_suffix or "none")
        self.suffix_edit.setToolTip("Suffix for this map only (empty = the main suffix)")
        affixes = QtWidgets.QHBoxLayout()
        affixes.setSpacing(10)
        affixes.addWidget(self.prefix_edit, 1, center)
        affixes.addWidget(_label("Suffix", ""), 0, center)
        affixes.addWidget(self.suffix_edit, 1, center)
        grid.addLayout(affixes, 1, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        # Labels exactly as tall as the fields, everything centred in its row: otherwise the
        # labels sit at the top of the row, a few px above the fields' centre.
        height = self.name_edit.sizeHint().height()
        for label in (map_name, *[w for w in self.findChildren(QtWidgets.QLabel)
                                  if w.text() in ("Prefix", "Suffix")]):
            label.setFixedHeight(height)
            label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        self.example = _label()
        self.example.setWordWrap(True)
        lay.addWidget(self.example)
        lay.addSpacing(4)
        lay.addWidget(_separator())
        cancel = _button("Cancel")
        self.apply_btn = _button("Apply", primary=True)
        self.apply_btn.setDefault(True)
        cancel.clicked.connect(self.reject)
        self.apply_btn.clicked.connect(self.accept)
        lay.addLayout(_equal_row(cancel, self.apply_btn))
        for edit in (self.name_edit, self.prefix_edit, self.suffix_edit):
            edit.textChanged.connect(self._refresh)
        self._refresh()
        self.name_edit.setFocus()

    def overrides(self):
        """(name, prefix, suffix) to store; "" = use the main one."""
        return (self.name_edit.text().strip(), self.prefix_edit.text().strip(),
                self.suffix_edit.text().strip())

    def _refresh(self, *_):
        name, prefix, suffix = self.overrides()
        self.apply_btn.setText("Override" if (name or prefix or suffix) else "Apply")
        example = core.file_name(self._names, core.MapSetting(
            key="", suffix=self._suffix, name=name, prefix=prefix, name_suffix=suffix),
            self._index)
        if name or prefix or suffix:
            self.example.setText(f"e.g.  {example}")
        else:
            self.example.setText("Empty: uses the main file name, prefix and suffix  "
                                 f"·  e.g.  {example}")


class _MapsTable(QtWidgets.QTableWidget):
    COLS = ["", "Map", "Suffix", "Subfolder", "Per variation", ""]
    ON, NAME, SUFFIX, FOLDER, PER_VAR, MORE = range(6)
    # Per-map overrides, stored on the ON item.
    NAME_ROLE = QtCore.Qt.UserRole + 1
    PREFIX_ROLE = QtCore.Qt.UserRole + 2
    SUFFIX_ROLE = QtCore.Qt.UserRole + 3

    changed = QtCore.Signal()   # a subfolder was edited (cell widgets don't emit itemChanged)

    def __init__(self):
        super().__init__(0, len(self.COLS))
        self.setHorizontalHeaderLabels(self.COLS)
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)   # zebra, like the variations list
        _center_checks(self, self.ON, self.PER_VAR)
        self.horizontalHeaderItem(self.ON).setToolTip("Export this map (unticked = skipped)")
        self.itemChanged.connect(self._on_toggled)
        self.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        h = self.horizontalHeader()
        h.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        h.setSectionResizeMode(self.SUFFIX, QtWidgets.QHeaderView.Stretch)
        h.setSectionResizeMode(self.FOLDER, QtWidgets.QHeaderView.Fixed)
        self.setColumnWidth(self.FOLDER, 170)
        h.setSectionResizeMode(self.ON, QtWidgets.QHeaderView.Fixed)
        self.setColumnWidth(self.ON, CHECK_COLUMN_WIDTH)   # same as the Sizes table
        h.setSectionResizeMode(self.MORE, QtWidgets.QHeaderView.Fixed)
        self.setColumnWidth(self.MORE, 40)
        # Set by the export dialog: current main file name and example number for the popup.
        # Set by the export dialog: current main name / prefix / suffix and the example number.
        self.name_settings = lambda: core.ExportSettings()
        self.example_index = lambda: "0001"
        self._more_icons = {False: th.icon("pencil", th.GRAY_500, active_color=th.TEXT),
                            True: th.icon("pencil", th.ACCENT, active_color=th.ACCENT_HOVER)}
        _fit_table(self)
        self.setToolTip("Maps flagged 'Per variation' get the _0001, _0002... number and are "
                        "exported once per variation.\nOther maps are exported a single time.\n"
                        "Subfolder: type a name, or hover it and click the switch for the "
                        "default one (ALB, ROUGH...).")

    def set_maps(self, maps):
        # Build rows silently: change handlers would otherwise run on half-built rows.
        was_blocked = self.blockSignals(True)
        self.setRowCount(0)
        for m in maps:
            r = self.rowCount()
            self.insertRow(r)
            enabled = QtWidgets.QTableWidgetItem()
            enabled.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            enabled.setCheckState(QtCore.Qt.Checked if m.enabled else QtCore.Qt.Unchecked)
            enabled.setData(QtCore.Qt.UserRole, m.key)
            self.setItem(r, self.ON, enabled)
            name = QtWidgets.QTableWidgetItem(core.default_suffix(m.key).lstrip("_") or m.key)
            name.setFlags(QtCore.Qt.ItemIsEnabled)
            name.setToolTip(f"Template file name: {m.key}")
            self.setItem(r, self.NAME, name)
            self.setItem(r, self.SUFFIX, QtWidgets.QTableWidgetItem(m.suffix))
            cell = _FolderCell(m.folder or "", core.map_folder_name(m.key))
            cell.edited.connect(self.changed)
            self.setCellWidget(r, self.FOLDER, cell)
            per_var = QtWidgets.QTableWidgetItem()
            per_var.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            per_var.setCheckState(QtCore.Qt.Checked if m.per_variation else QtCore.Qt.Unchecked)
            self.setItem(r, self.PER_VAR, per_var)
            enabled.setData(self.NAME_ROLE, m.name or "")
            enabled.setData(self.PREFIX_ROLE, m.prefix or "")
            enabled.setData(self.SUFFIX_ROLE, m.name_suffix or "")
            more = QtWidgets.QToolButton()
            more.setObjectName("emMore")
            more.setAutoRaise(True)
            more.setCursor(QtCore.Qt.PointingHandCursor)
            more.setFixedSize(28, 28)
            more.setIconSize(QtCore.QSize(16, 16))
            more.clicked.connect(lambda _=False, item=enabled: self._edit_name(item))
            holder = QtWidgets.QWidget()
            holder.setObjectName("emCell")
            hl = QtWidgets.QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(more, 0, QtCore.Qt.AlignCenter)
            self.setCellWidget(r, self.MORE, holder)
            self._style_more(more, m.name or "", m.prefix or "", m.name_suffix or "")
        self.blockSignals(was_blocked)
        for r in range(self.rowCount()):
            self._style_row(r)
        _fit_table(self)

    def _more_button(self, item):
        return self.cellWidget(item.row(), self.MORE).findChild(QtWidgets.QToolButton)

    def _style_row(self, r):
        """Maps switched off are greyed out, so it's obvious they won't be exported."""
        on = self.item(r, self.ON).checkState() == QtCore.Qt.Checked
        was_blocked = self.blockSignals(True)
        color = QtGui.QColor(th.TEXT if on else th.GRAY_400)
        for col in (self.NAME, self.SUFFIX):
            self.item(r, col).setForeground(color)
        self.item(r, self.ON).setToolTip("Exported" if on else "Not exported \u2014 tick to export")
        self.blockSignals(was_blocked)
        cell = self.cellWidget(r, self.FOLDER)
        if cell is not None:
            cell.setEnabled(on)

    def _on_toggled(self, item):
        if item.column() == self.ON:
            self._style_row(item.row())

    def _style_more(self, button, name, prefix="", suffix=""):
        active = bool(name or prefix or suffix)
        button.setIcon(self._more_icons[active])
        button.setProperty("active", active)
        button.style().unpolish(button)
        button.style().polish(button)
        parts = [f"{label}: {value}" for label, value in
                 (("File name", name), ("Prefix", prefix), ("Suffix", suffix)) if value]
        button.setToolTip("Overridden \u2014 " + ", ".join(parts) + "  (click to change)" if parts
                          else "Override the file name, prefix or suffix for this map")

    def _edit_name(self, item):
        r = item.row()
        label = self.item(r, self.NAME).text()
        suffix = self.item(r, self.SUFFIX).text()
        per_var = self.item(r, self.PER_VAR).checkState() == QtCore.Qt.Checked
        current = core.MapSetting(key="", name=item.data(self.NAME_ROLE) or "",
                                  prefix=item.data(self.PREFIX_ROLE) or "",
                                  name_suffix=item.data(self.SUFFIX_ROLE) or "")
        dlg = _NameOverrideDialog(self.window(), label, suffix, current,
                                  self.name_settings(), self.example_index() if per_var else "")
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.set_name_override(r, *dlg.overrides())

    def set_name_override(self, row, name, prefix="", suffix=""):
        item = self.item(row, self.ON)
        item.setData(self.NAME_ROLE, name)
        item.setData(self.PREFIX_ROLE, prefix)
        item.setData(self.SUFFIX_ROLE, suffix)
        self._style_more(self._more_button(item), name, prefix, suffix)
        self.changed.emit()

    def maps(self):
        return [core.MapSetting(
            key=self.item(r, self.ON).data(QtCore.Qt.UserRole),
            enabled=self.item(r, self.ON).checkState() == QtCore.Qt.Checked,
            suffix=self.item(r, self.SUFFIX).text(),
            per_variation=self.item(r, self.PER_VAR).checkState() == QtCore.Qt.Checked,
            folder=self.cellWidget(r, self.FOLDER).text(),
            name=self.item(r, self.ON).data(self.NAME_ROLE) or "",
            prefix=self.item(r, self.ON).data(self.PREFIX_ROLE) or "",
            name_suffix=self.item(r, self.ON).data(self.SUFFIX_ROLE) or "",
        ) for r in range(self.rowCount())]


class _OutputsTable(QtWidgets.QTableWidget):
    """Export sizes: [on/off] [size] [subfolder].

    Each size can be used once (taken sizes are greyed out in the other rows' dropdowns), and
    the subfolders of the sizes that are switched on must be unique - duplicates turn red.
    """

    changed = QtCore.Signal()
    ON, SIZE, FOLDER = 0, 1, 2

    def __init__(self):
        super().__init__(0, 3)
        self.setHorizontalHeaderLabels(["", "Size", "Subfolder"])
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        header = self.horizontalHeader()
        header.setSectionResizeMode(self.ON, QtWidgets.QHeaderView.Fixed)
        header.setSectionResizeMode(self.SIZE, QtWidgets.QHeaderView.Fixed)
        header.setSectionResizeMode(self.FOLDER, QtWidgets.QHeaderView.Stretch)
        self.setColumnWidth(self.ON, CHECK_COLUMN_WIDTH)   # same as the Output maps table
        self.setAlternatingRowColors(True)   # zebra, like the other lists
        _center_checks(self, self.ON)
        self.setColumnWidth(self.SIZE, 190)
        _fit_table(self)
        self.setToolTip("One row per export size; untick a size to skip it without removing it.\n"
                        "With several sizes each one is exported into its own subfolder.")
        self.itemChanged.connect(self._on_item_changed)

    # ------------------------------------------------------------------ rows

    def _combo(self, r):
        return self.cellWidget(r, self.SIZE)

    def _folder(self, r):
        return self.item(r, self.FOLDER)

    def _is_on(self, r):
        return self.item(r, self.ON).checkState() == QtCore.Qt.Checked

    def add_output(self, output):
        r = self.rowCount()
        # Build the row silently: validation would otherwise run on a half-built row.
        self.blockSignals(True)
        self.insertRow(r)
        toggle = QtWidgets.QTableWidgetItem()
        toggle.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
        toggle.setCheckState(QtCore.Qt.Checked if output.enabled else QtCore.Qt.Unchecked)
        toggle.setToolTip("Export this size")
        self.setItem(r, self.ON, toggle)
        combo = QtWidgets.QComboBox()
        combo.addItem(PROJECT_SIZE, None)
        for s in core.SIZES:
            combo.addItem(f"{s} x {s}", s)
        combo.setCurrentIndex(max(0, combo.findData(output.size)))
        _guard_wheel(combo)
        self.setCellWidget(r, self.SIZE, combo)
        self.setItem(r, self.FOLDER, QtWidgets.QTableWidgetItem(output.folder))
        self.blockSignals(False)
        # The name we would generate for this row; a folder equal to it (or empty) is "ours".
        combo.setProperty("auto_label", _size_label(output.size))
        combo.currentIndexChanged.connect(self.sync_folders)
        _fit_table(self)
        self.sync_folders()

    def remove_rows(self, rows):
        for r in sorted(rows, reverse=True):
            if self.rowCount() > 1:
                self.removeRow(r)
        _fit_table(self)
        self.sync_folders()

    def outputs(self):
        return [core.OutputSetting(size=self._combo(r).currentData(),
                                   folder=self._folder(r).text().strip(),
                                   enabled=self._is_on(r))
                for r in range(self.rowCount())]

    # ------------------------------------------------------------------ sizes

    def used_sizes(self, except_row=None):
        return [self._combo(r).currentData() for r in range(self.rowCount()) if r != except_row]

    def next_free_size(self):
        """Smallest free size above the largest one used, else the largest free one."""
        used = self.used_sizes()
        free = [sz for sz in core.SIZES if sz not in used]
        if not free:
            return False if None in used else None   # False: nothing left at all
        biggest = max((sz for sz in used if sz), default=0)
        bigger = [sz for sz in free if sz > biggest]
        return bigger[0] if bigger else free[-1]

    def _refresh_size_options(self):
        """Hide (and disable), in each row's dropdown, the sizes other rows already use."""
        for r in range(self.rowCount()):
            combo = self._combo(r)
            taken = self.used_sizes(except_row=r)
            model, view = combo.model(), combo.view()
            for i in range(combo.count()):
                free = combo.itemData(i) not in taken
                model.item(i).setEnabled(free)
                if isinstance(view, QtWidgets.QListView):
                    view.setRowHidden(i, not free)

    def sync_folders(self, *_):
        """Auto subfolders: none for a single size, the size label ("2K", "4K") when there are
        several. Folders the user typed themselves are never touched."""
        several = self.rowCount() > 1
        self.blockSignals(True)
        for r in range(self.rowCount()):
            combo, item = self._combo(r), self._folder(r)
            previous_auto = combo.property("auto_label") or ""
            label = _size_label(combo.currentData())
            if item.text().strip() in ("", previous_auto):
                item.setText(label if several else "")
            combo.setProperty("auto_label", label)
        self.blockSignals(False)
        self._refresh_size_options()
        self._validate()

    # ------------------------------------------------------------------ validation

    def enabled_count(self):
        return sum(1 for r in range(self.rowCount()) if self._is_on(r))

    def duplicate_folder_rows(self):
        """Rows switched on that share a subfolder (case-insensitive, like Windows)."""
        seen, dupes = {}, set()
        for r in range(self.rowCount()):
            if not self._is_on(r):
                continue
            key = self._folder(r).text().strip().lower()
            if key in seen:
                dupes.update((r, seen[key]))
            seen.setdefault(key, r)
        return dupes

    def _on_item_changed(self, *_):
        self._validate()

    def _validate(self, *_):
        dupes = self.duplicate_folder_rows()
        self.blockSignals(True)
        for r in range(self.rowCount()):
            on = self._is_on(r)
            item = self._folder(r)
            self._combo(r).setEnabled(on)
            if r in dupes:
                item.setForeground(QtGui.QColor(th.ERROR))
                item.setToolTip("Another size uses this subfolder: its files would be overwritten.")
            else:
                item.setForeground(QtGui.QColor(th.TEXT if on else th.GRAY_400))
                item.setToolTip("" if on else "This size is switched off and won't be exported")
        self.blockSignals(False)
        self.changed.emit()


class ExportPage(QtWidgets.QWidget):
    """The Export tab: output, maps, format and sizes settings, and the export itself."""

    exporting_changed = QtCore.Signal(bool)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self._saved_maps = []
        self.loading = False   # True while open_with fills the widgets: not a user edit

        content = QtWidgets.QWidget()
        content.setObjectName("emContent")
        self.content = content
        lay = QtWidgets.QVBoxLayout(content)
        lay.setSpacing(10)

        # --- output
        lay.addWidget(_section_title("Output"))
        f = _form()
        dir_row = QtWidgets.QHBoxLayout()
        self.dir_edit = QtWidgets.QLineEdit()
        browse = _button("Browse…", "folder-open")
        browse.clicked.connect(self._browse)
        dir_row.addWidget(self.dir_edit, 1, QtCore.Qt.AlignVCenter)
        dir_row.addWidget(browse, 0, QtCore.Qt.AlignVCenter)
        f.addRow("Directory", dir_row)
        # Prefix and Suffix share one line: [Prefix ____]  Suffix [____]
        self.prefix_edit = QtWidgets.QLineEdit()
        self.prefix_edit.setPlaceholderText("none")
        self.prefix_edit.setToolTip("Added before every file name, followed by an underscore "
                                    "(EM -> EM_...)")
        self.suffix_edit = QtWidgets.QLineEdit()
        self.suffix_edit.setPlaceholderText("none")
        self.suffix_edit.setToolTip("Added after the file name, before the map suffix, with an "
                                    "underscore before it (v2 -> ..._v2_alb_0001)")
        affixes = QtWidgets.QHBoxLayout()
        affixes.setSpacing(10)
        affixes.addWidget(self.prefix_edit, 1, QtCore.Qt.AlignVCenter)
        affixes.addWidget(_label("Suffix", ""), 0, QtCore.Qt.AlignVCenter)
        affixes.addWidget(self.suffix_edit, 1, QtCore.Qt.AlignVCenter)
        f.addRow("Prefix", affixes)
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setToolTip("Base file name. Painter tokens work: $project, $mesh, "
                                  "$textureSet, $sceneMaterial")
        f.addRow("File name", self.name_edit)
        self.example_label = _label()
        self.example_label.setWordWrap(True)
        f.addRow("", self.example_label)
        self.template_combo = QtWidgets.QComboBox()
        f.addRow("Template", self.template_combo)
        lay.addLayout(f)

        # --- maps
        lay.addWidget(_section_title("Output maps"))
        self.maps_table = _MapsTable()
        self.maps_table.name_settings = lambda: core.ExportSettings(
            base_name=self.name_edit.text().strip() or "$textureSet",
            prefix=self.prefix_edit.text().strip(), name_suffix=self.suffix_edit.text().strip())
        self.maps_table.example_index = lambda: str(
            self.panel.numbering()[0]).zfill(self.panel.numbering()[1])
        lay.addWidget(_card(self.maps_table))

        # --- format
        lay.addWidget(_section_title("Format"))
        f = _form()
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItem(TEMPLATE_DEFAULT, "")
        for fmt in core.FILE_FORMATS:
            self.format_combo.addItem(fmt, fmt)
        f.addRow("File type", self.format_combo)
        self.depth_combo = QtWidgets.QComboBox()
        self.depth_combo.addItem(TEMPLATE_DEFAULT, "")
        for d in core.BIT_DEPTHS:
            self.depth_combo.addItem(d, d)
        # Bit depth and Padding rows share one right-hand column: [Dithering] / [16 px v ^].
        side_width = 150
        depth = QtWidgets.QHBoxLayout()
        depth.addWidget(self.depth_combo, 1, QtCore.Qt.AlignVCenter)
        dither_box = QtWidgets.QWidget()
        dither_box.setFixedWidth(side_width)
        dither_lay = QtWidgets.QHBoxLayout(dither_box)
        dither_lay.setContentsMargins(4, 0, 0, 0)
        self.dither_cb = QtWidgets.QCheckBox("Dithering")
        dither_lay.addWidget(self.dither_cb, 0, QtCore.Qt.AlignVCenter)
        dither_lay.addStretch(1)
        depth.addWidget(dither_box, 0, QtCore.Qt.AlignVCenter)
        f.addRow("Bit depth", depth)
        pad = QtWidgets.QHBoxLayout()
        self.padding_combo = QtWidgets.QComboBox()
        for p in core.PADDINGS:
            self.padding_combo.addItem(p, p)
        self.dilation_spin = QtWidgets.QSpinBox()
        self.dilation_spin.setRange(0, 256)
        self.dilation_spin.setSuffix(" px")
        pad.addWidget(self.padding_combo, 1, QtCore.Qt.AlignVCenter)
        self.dilation_box = _with_steppers(self.dilation_spin)
        self.dilation_box.setFixedWidth(side_width)
        pad.addWidget(self.dilation_box, 0, QtCore.Qt.AlignVCenter)
        f.addRow("Padding", pad)
        lay.addLayout(f)

        # --- sizes
        lay.addWidget(_section_title("Sizes"))
        self.outputs_table = _OutputsTable()
        lay.addWidget(_card(self.outputs_table))
        row = QtWidgets.QHBoxLayout()
        self.add_size_btn = add_btn = _button("Add size", "plus")
        rem_btn = _button("Remove", "minus")
        add_btn.clicked.connect(self._add_size)
        self.outputs_table.changed.connect(self._on_sizes_changed)
        rem_btn.clicked.connect(self._remove_output)
        lay.addLayout(_equal_row(add_btn, rem_btn))

        # --- log
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        self.log.hide()
        lay.addWidget(self.log)
        lay.addStretch()

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(content)

        self.export_btn = _button("Export", "export", primary=True)
        self.export_btn.setDefault(True)
        close_btn = _button("Close")
        self.close_btn = close_btn
        # Export progress, shown in place of Close / Export while exporting.
        self.progress_label = _label("")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.cancel_btn = _button("Cancel")
        self.cancel_btn.clicked.connect(self._request_cancel)
        self._cancel_requested = False

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(FOOTER_MARGIN, FOOTER_MARGIN, FOOTER_MARGIN, FOOTER_MARGIN)
        outer.addWidget(scroll, 1)
        outer.addWidget(_separator())
        outer.addSpacing(4)
        self.progress_box = QtWidgets.QWidget()
        progress_lay = QtWidgets.QVBoxLayout(self.progress_box)
        progress_lay.setContentsMargins(0, 0, 0, 0)
        progress_lay.setSpacing(6)
        progress_lay.addWidget(self.progress_label)
        progress_lay.addWidget(self.progress_bar)
        self.progress_box.hide()
        outer.addWidget(self.progress_box)
        buttons = _equal_row(close_btn, self.export_btn, self.cancel_btn)
        buttons.setSpacing(FOOTER_SPACING)
        self.mirror_open_dir_btns = []   # same button on the Variations tab footer
        # Square "open output folder" button next to Export (greyed out until the folder exists).
        self.open_dir_btn = QtWidgets.QPushButton()
        self.open_dir_btn.setObjectName("emSquare")
        self.open_dir_btn.setIcon(th.icon("folder-open", th.GRAY_700, disabled_color=th.GRAY_400,
                                          active_color=th.TEXT))
        self.open_dir_btn.setIconSize(QtCore.QSize(16, 16))
        self.open_dir_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.open_dir_btn.clicked.connect(self._open_export_dir)
        buttons.addWidget(self.open_dir_btn)
        outer.addLayout(buttons)
        self.cancel_btn.hide()

        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        self.padding_combo.currentIndexChanged.connect(self._update_dilation)
        self.name_edit.textChanged.connect(self.update_example)
        self.prefix_edit.textChanged.connect(self.update_example)
        self.suffix_edit.textChanged.connect(self.update_example)
        self.maps_table.itemChanged.connect(self.update_example)
        self.maps_table.changed.connect(self.update_example)
        self.export_btn.clicked.connect(self._export)
        close_btn.clicked.connect(lambda: self.window().close())
        # Any edit is saved into the project shortly after it happens.
        for signal in (self.dir_edit.textChanged, self.name_edit.textChanged,
                       self.prefix_edit.textChanged, self.suffix_edit.textChanged,
                       self.template_combo.currentIndexChanged,
                       self.format_combo.currentIndexChanged, self.depth_combo.currentIndexChanged,
                       self.padding_combo.currentIndexChanged, self.dilation_spin.valueChanged,
                       self.dither_cb.toggled, self.maps_table.itemChanged,
                       self.maps_table.changed,
                       self.outputs_table.changed):
            signal.connect(self._on_edited)
        self.dir_edit.textChanged.connect(self._update_open_dir)
        self.panel.variations_changed.connect(self.refresh_export_state)
        self.refresh_export_state()
        _center_form_labels(self)
        _guard_wheel_in(self)

    # ------------------------------------------------------------------ settings <-> widgets

    def _on_edited(self, *_):
        if not self.loading:
            self.panel.schedule_save()

    def open_with(self, s):
        self.loading = True
        try:
            self._fill(s)
        finally:
            self.loading = False

    def _fill(self, s):
        self._load_templates()
        self.dir_edit.setText(s.export_dir)
        self.name_edit.setText(s.base_name)
        self.prefix_edit.setText(s.prefix)
        self.suffix_edit.setText(s.name_suffix)
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(s.file_format)))
        self.depth_combo.setCurrentIndex(max(0, self.depth_combo.findData(s.bit_depth)))
        self.padding_combo.setCurrentIndex(max(0, self.padding_combo.findData(s.padding)))
        self.dilation_spin.setValue(s.dilation)
        self.dither_cb.setChecked(s.dithering)
        self.outputs_table.setRowCount(0)
        for o in s.outputs:
            self.outputs_table.add_output(o)
        self._saved_maps = s.maps
        self.maps_table.setRowCount(0)
        idx = self.template_combo.findData(s.template_url)
        if idx < 0:   # project without a saved choice: EM's preset, else Painter's standard one
            idx = self.template_combo.findText(DEFAULT_EXPORT_PRESET)
        if idx < 0:
            idx = self.template_combo.findText("PBR Metallic Roughness")
        self.template_combo.blockSignals(True)
        self.template_combo.setCurrentIndex(max(0, idx))
        self.template_combo.blockSignals(False)
        self._on_template_changed()
        self._update_dilation()
        self.log.clear()
        self.log.hide()
        self._update_open_dir()

    def store(self, s):
        s.export_dir = self.dir_edit.text().strip()
        s.base_name = self.name_edit.text().strip()
        s.prefix = self.prefix_edit.text().strip()
        s.name_suffix = self.suffix_edit.text().strip()
        s.template_url = self.template_combo.currentData() or ""
        s.file_format = self.format_combo.currentData()
        s.bit_depth = self.depth_combo.currentData()
        s.dithering = self.dither_cb.isChecked()
        s.padding = self.padding_combo.currentData()
        s.dilation = self.dilation_spin.value()
        s.maps = self.maps_table.maps()
        s.outputs = self.outputs_table.outputs()
        return s

    def _settings(self):
        return self.store(self.panel.settings())

    # ------------------------------------------------------------------ widgets logic

    def _load_templates(self):
        current = self.template_combo.currentData()
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        try:
            templates = core.list_templates()
        except Exception:
            templates = []
            self._log_error("Could not list export templates")
        for label, url, _ in templates:
            self.template_combo.addItem(label, url)
        self.template_combo.setCurrentIndex(max(0, self.template_combo.findData(current)))
        self.template_combo.blockSignals(False)

    def _on_template_changed(self, *_):
        url = self.template_combo.currentData()
        if not url or not sp_project.is_open():
            self.maps_table.set_maps([])
            return
        try:
            maps = core.template_maps(url, self.panel.stack())
        except Exception:
            self._log_error("Could not read the template maps")
            maps = []
        # Keep unsaved edits made in the table, fall back on the project's saved choices.
        saved = self._saved_maps + self.maps_table.maps()
        self.maps_table.blockSignals(True)
        self.maps_table.set_maps(core.merge_map_settings(maps, saved))
        self.maps_table.blockSignals(False)
        self.update_example()

    def update_example(self, *_):
        maps = self.maps_table.maps()
        variant = next((m for m in maps if m.enabled and m.per_variation), None)
        static = next((m for m in maps if m.enabled and not m.per_variation), None)
        start, digits = self.panel.numbering()
        index = str(start).zfill(digits)
        names = core.ExportSettings(base_name=self.name_edit.text().strip(),
                                    prefix=self.prefix_edit.text().strip(),
                                    name_suffix=self.suffix_edit.text().strip())
        parts = []
        if variant:
            parts.append(core.file_name(names, variant, index))
        if static:
            parts.append(core.file_name(names, static))
        self.example_label.setText("e.g.  " + "   ·   ".join(parts) if parts else "")

    def _update_dilation(self, *_):
        # Disable the whole box so the stepper buttons grey out with the field.
        self.dilation_box.setEnabled(self.padding_combo.currentData() in core.PADDINGS_WITH_DISTANCE)

    def _add_size(self):
        size = self.outputs_table.next_free_size()
        if size is not False:
            self.outputs_table.add_output(core.OutputSetting(size))

    def _on_sizes_changed(self):
        self.add_size_btn.setEnabled(self.outputs_table.next_free_size() is not False)
        self.refresh_export_state()

    def refresh_export_state(self, *_):
        """Export button: same label as the Variations tab ("Export 10 variations..."), greyed
        out with no checked variation, or when the sizes can't be exported."""
        table = self.outputs_table
        no_variation = self.panel.checked_count() == 0
        dupes = bool(table.duplicate_folder_rows())
        none_on = table.enabled_count() == 0
        self.export_btn.setText(self.panel.export_label())   # same label as the Variations tab
        self.export_btn.setEnabled(not (no_variation or dupes or none_on))
        self.export_btn.setToolTip(
            "No variation to export: pick some on the Variations tab" if no_variation
            else "Two sizes use the same subfolder" if dupes
            else "Switch on at least one size" if none_on else "")

    def _remove_output(self):
        rows = sorted({i.row() for i in self.outputs_table.selectedIndexes()}, reverse=True)
        if not rows and self.outputs_table.rowCount() > 1:
            rows = [self.outputs_table.rowCount() - 1]
        self.outputs_table.remove_rows(rows)

    def _browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Export directory", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    # ------------------------------------------------------------------ actions

    def _validate(self, settings):
        problems = []
        if not self.panel.groups:
            problems.append("No variation group selected.")
        if not settings.export_dir:
            problems.append("No output directory.")
        if not settings.base_name:
            problems.append("No file name.")
        if not any(m.enabled for m in settings.maps):
            problems.append("No output map enabled.")
        if not any(o.enabled for o in settings.outputs):
            problems.append("No size is switched on.")
        folders = [o.folder.lower() for o in settings.outputs if o.enabled]
        if len(set(folders)) != len(folders):
            problems.append("Two sizes use the same subfolder: files would overwrite each other.")
        sizes = [o.size for o in settings.outputs]
        if len(set(sizes)) != len(sizes):
            problems.append("The same size is listed twice.")
        if problems:
            QtWidgets.QMessageBox.warning(self, TITLE, "\n".join(problems))
            return False
        return True

    def _export(self):
        settings = self._settings()
        if not self._validate(settings):
            return
        self.panel.save_settings()
        variations = self.panel.selected_variations()
        if not variations and any(m.per_variation and m.enabled for m in settings.maps):
            QtWidgets.QMessageBox.warning(self, TITLE, "No variation is checked.")
            return
        numbers = self.panel.variation_numbers()

        # Files with the same names already there? Ask before touching anything.
        try:
            jobs = core.build_jobs(settings, variations, self.panel.stack(), numbers)
            existing = core.existing_files(jobs)
        except Exception:
            self._log_error("Could not check the output folder")
            self._show_log()
            return
        if existing:
            dlg = _OverwriteDialog(self, existing, numbered=any(j.index for j in jobs))
            choice = dlg.exec()
            if choice == _OverwriteDialog.CANCEL:
                return
            if choice == _OverwriteDialog.INCREMENT:
                numbers = core.incremented_numbers(jobs, settings, variations, numbers)

        def progress(done, total, label):
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(done)
            step = done + 1 if done < total else total
            self.progress_label.setText(f"Exporting {step}/{total}  \u00b7  {label}")
            QtWidgets.QApplication.processEvents()
            return not self._cancel_requested

        self.log.show()
        self.log.setPlainText("Exporting...")
        self._set_exporting(True)
        try:
            with _HidePainterProgress():
                files, warnings = core.run_export(settings, self.panel.groups, variations,
                                                  self.panel.stack(), progress, numbers)
        except core.ExportCancelled:
            self.log.appendPlainText("Cancelled. Layer visibility has been restored.")
            self._set_exporting(False)
            self._show_log()
            return
        except Exception:
            self._log_error("Export failed (layer visibility has been restored)")
            self._set_exporting(False)
            self._show_log()
            return
        finally:
            self._set_exporting(False)
        lines = [f"Exported {len(files)} file(s) for {len(variations)} variation(s) "
                 f"to {settings.export_dir}"]
        skipped = [core.default_suffix(m.key).lstrip("_") for m in settings.maps if not m.enabled]
        if skipped:
            lines.append(f"Skipped (switched off in Output maps): {', '.join(skipped)}")
        lines += [f"WARNING: {w}" for w in warnings]
        lines += files
        self.log.setPlainText("\n".join(lines))
        sp_log.info(f"[{TITLE}] {lines[0]}")
        self._show_log()

    def _set_exporting(self, exporting):
        """Grey out the whole dialog (and the panel) and swap Close / Export for progress + Cancel."""
        if not exporting:
            self._update_open_dir()
        # The export toggles visibility, which fires layer stack events: ignored meanwhile.
        self.panel.exporting = exporting
        self.panel.setEnabled(not exporting)
        self.content.setEnabled(not exporting)
        self.close_btn.setVisible(not exporting)
        self.open_dir_btn.setVisible(not exporting)
        self.export_btn.setVisible(not exporting)
        self.cancel_btn.setVisible(exporting)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Cancel")
        self.progress_box.setVisible(exporting)
        self._cancel_requested = False
        self.exporting_changed.emit(exporting)
        if exporting:
            self.progress_bar.setValue(0)
            self.progress_label.setText("Preparing export\u2026")
        QtWidgets.QApplication.processEvents()

    def showEvent(self, event):
        super().showEvent(event)
        # Square, as tall as Export: sized once styled (the stylesheet changes button heights).
        side = self.export_btn.sizeHint().height()
        self.open_dir_btn.setFixedSize(side, side)

    def _export_dir(self):
        return os.path.normpath(self.dir_edit.text().strip()) if self.dir_edit.text().strip() else ""

    def _update_open_dir(self, *_):
        path = self._export_dir()
        exists = bool(path) and os.path.isdir(path)
        for btn in [self.open_dir_btn, *self.mirror_open_dir_btns]:
            btn.setEnabled(exists)
            btn.setToolTip(f"Open {path}" if exists else "The output directory doesn't exist yet")

    def make_open_dir_button(self):
        """Another open-output-folder button (for the Variations tab footer), kept in sync."""
        btn = QtWidgets.QPushButton()
        btn.setObjectName("emSquare")
        btn.setIcon(th.icon("folder-open", th.GRAY_700, disabled_color=th.GRAY_400,
                            active_color=th.TEXT))
        btn.setIconSize(QtCore.QSize(16, 16))
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.clicked.connect(self._open_export_dir)
        self.mirror_open_dir_btns.append(btn)
        self._update_open_dir()
        return btn

    def _open_export_dir(self):
        path = self._export_dir()
        if path and os.path.isdir(path):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def _request_cancel(self):
        # Takes effect once the map being written finishes (Painter can't stop mid-file).
        self._cancel_requested = True
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancelling\u2026")

    def _show_log(self):
        area = self.findChild(QtWidgets.QScrollArea)
        QtWidgets.QApplication.processEvents()
        area.ensureWidgetVisible(self.log)

    def _log_error(self, title):
        msg = f"{title}:\n{traceback.format_exc()}"
        self.log.show()
        self.log.appendPlainText(msg)
        sp_log.error(f"[{TITLE}] {msg}")
