"""Look of the plugin: EM Hub's style (D:/Projects/Repos/EM-Hub/src/styles/tokens.css) - sage
accent, rounded controls, line icons (24x24, 2px round strokes, like EM Hub's assets/ui/icons) -
on Substance 3D Painter's own neutral greys so the panel blends with the rest of the app."""

import os
import tempfile

from PySide6 import QtCore, QtGui

try:   # Painter 12.2 ships QtSvg; if an install lacks it, icons are blank but nothing breaks
    from PySide6 import QtSvg
except ImportError:
    QtSvg = None

# Backgrounds and greys: Painter's (panels #333333, fields #262626 / #2b2b2b).
BG = "#333333"
SURFACE = "#2b2b2b"
TEXT = "#dcdcdc"
GRAY_700 = "#c4c4c4"
GRAY_500 = "#9a9a9a"
GRAY_400 = "#6e6e6e"
GRAY_300 = "#474747"
GRAY_200 = "#3c3c3c"
GRAY_100 = "#262626"
TAB_BG = "#262626"            # inactive tab: darker than the panel
TAB_SELECTED_BG = "#3d3d3d"   # active tab: a bit lighter than the panel
# Accent: EM Hub's sage.
ACCENT = "#8a9d78"
ACCENT_HOVER = "#9db38a"
ACCENT_DISABLED = "#4d6644"
ERROR = "#ef4444"
HOVER_BG = "rgba(138, 157, 120, 0.13)"   # --hover-bg: accent at 13%
SELECTED_BG = "rgba(138, 157, 120, 0.24)"

_ICONS = {
    "ball": '<circle cx="12" cy="12" r="8"/><path d="M8.5 10.5a4 4 0 0 1 3-3"/>',
    "folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "folder-open": '<path d="M6 14l1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1'
                   '-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0'
                   ' 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/>'
              '<polyline points="2 12 12 17 22 12"/>',
    "reverse": '<path d="m21 16-4 4-4-4"/><path d="M17 20V4"/><path d="m3 8 4-4 4 4"/><path d="M7 4v16"/>',
    "reset": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
    "export": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 8 12 3 17 8"/>'
              '<line x1="12" y1="3" x2="12" y2="15"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "switch-off": '<rect x="2" y="6" width="20" height="12" rx="6"/><circle cx="8" cy="12" r="2.5"/>',
    "switch-on": '<rect x="2" y="6" width="20" height="12" rx="6"/><circle cx="16" cy="12" r="2.5"/>',
    "pencil": '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "more": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "arrow-right": '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "minus": '<line x1="5" y1="12" x2="19" y2="12"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "chevron-up": '<polyline points="18 15 12 9 6 15"/>',
    "chevron-down": '<polyline points="6 9 12 15 18 9"/>',
    "chevron-right": '<polyline points="9 18 15 12 9 6"/>',
}


def _svg(name, color, stroke=2.0):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-linejoin="round">{_ICONS[name]}</svg>')


def pixmap(name, color, size=16, opacity=1.0):
    scale = 3   # rendered big, displayed small: sharp on HiDPI screens
    px = QtGui.QPixmap(size * scale, size * scale)
    px.fill(QtCore.Qt.transparent)
    if QtSvg is None:
        px.setDevicePixelRatio(scale)
        return px
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(_svg(name, color).encode()))
    p = QtGui.QPainter(px)
    p.setOpacity(opacity)
    renderer.render(p)
    p.end()
    px.setDevicePixelRatio(scale)
    return px


def icon(name, color=GRAY_500, size=16, disabled_color=GRAY_400, active_color=None):
    """QIcon with a dimmed Disabled mode (and an optional hover/Active colour)."""
    ic = QtGui.QIcon()
    ic.addPixmap(pixmap(name, color, size), QtGui.QIcon.Normal)
    ic.addPixmap(pixmap(name, disabled_color, size, 0.6), QtGui.QIcon.Disabled)
    if active_color:
        ic.addPixmap(pixmap(name, active_color, size), QtGui.QIcon.Active)
    return ic


def _icon_file(name, color, stroke=2.4):
    """Icons used from the stylesheet (url(...)) must exist on disk."""
    folder = os.path.join(tempfile.gettempdir(), "em_variant_exporter_icons")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}_{color.lstrip('#')}.svg")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(_svg(name, color, stroke))
    return path.replace("\\", "/")


def stylesheet():
    down = _icon_file("chevron-down", GRAY_500)
    right = _icon_file("chevron-right", GRAY_500)
    check = _icon_file("check", "#ffffff", 3)
    minus = _icon_file("minus", "#ffffff", 3)
    return f"""
QWidget[emRoot="true"] {{ background: {BG}; color: {TEXT}; }}
QWidget#emContent {{ background: {BG}; }}
QLabel {{ color: {TEXT}; background: transparent; }}
QLabel[role="muted"] {{ color: {GRAY_500}; }}
QLabel[role="section"] {{ color: {GRAY_500}; font-weight: 600; letter-spacing: 1px; }}
QLabel[role="hint"] {{ color: {GRAY_500}; padding: 24px; }}

QPushButton {{
    background: transparent; color: {GRAY_700}; font-weight: 600;
    border: 1.5px solid {GRAY_300}; border-radius: 14px; padding: 4px 14px; min-height: 20px;
}}
QPushButton:hover {{ background: {HOVER_BG}; color: {TEXT}; }}
QPushButton:pressed {{ background: {SELECTED_BG}; }}
QPushButton:disabled {{ color: {GRAY_400}; border-color: {GRAY_200}; }}
QPushButton[variant="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; }}
QPushButton[variant="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[variant="primary"]:disabled {{
    background: {ACCENT_DISABLED}; border-color: {ACCENT_DISABLED}; color: {GRAY_500};
}}

QToolButton {{
    background: transparent; color: {GRAY_500}; font-weight: 600;
    border: 1.5px solid transparent; border-radius: 12px; padding: 3px 9px;
}}
QToolButton:hover {{ background: {HOVER_BG}; color: {TEXT}; }}
QToolButton:disabled {{ color: {GRAY_400}; }}
QWidget#emStepper {{ background: transparent; }}
QWidget#emCell {{ background: transparent; }}
QToolButton#emMore {{ padding: 0; border-radius: 14px; }}
QPushButton#emSquare {{ padding: 0; min-height: 0; }}
QToolButton#emMore[active="true"] {{ background: {SELECTED_BG}; }}
QWidget#emStepper QToolButton {{ padding: 0; border-radius: 13px; }}

QLineEdit, QSpinBox, QComboBox {{
    background: {GRAY_100}; color: {TEXT}; border: 1.5px solid {GRAY_200}; border-radius: 13px;
    padding: 4px 12px; min-height: 20px;
    selection-background-color: {ACCENT}; selection-color: #ffffff;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {GRAY_400}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {GRAY_300}; outline: 0;
    selection-background-color: {SELECTED_BG}; selection-color: {TEXT};
}}
QComboBox QAbstractItemView::item:disabled {{ color: {GRAY_400}; }}

QCheckBox {{
    color: {GRAY_700}; spacing: 8px; background: transparent;
    min-height: 22px; padding: 2px 0;   /* Painter's default line height clips text + box */
}}
QCheckBox::indicator, QTreeView::indicator, QTableView::indicator {{
    width: 13px; height: 13px; border-radius: 5px;
    border: 1.5px solid {GRAY_400}; background: {GRAY_100};
}}
QCheckBox::indicator:hover, QTreeView::indicator:hover, QTableView::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked, QTreeView::indicator:checked, QTableView::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT}; image: url({check});
}}
QTreeView::indicator:indeterminate {{ background: {ACCENT}; border-color: {ACCENT}; image: url({minus}); }}

/* Tabs fill the window width; the line under them is the separator. */
QTabWidget::pane {{ border: none; }}
QTabBar {{ background: transparent; border-bottom: 1px solid {GRAY_300}; }}
QTabBar::tab {{
    background: {TAB_BG}; color: {GRAY_500}; font-weight: 600;
    padding: 5px 22px; margin: 0 2px 0 0; border: none; border-bottom: 2px solid transparent;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{
    background: {TAB_SELECTED_BG}; color: {TEXT}; border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:disabled {{ color: {GRAY_400}; }}
QFrame#emSeparator {{ background: {GRAY_200}; border: none; min-height: 1px; max-height: 1px; }}
QFrame#emCard {{ background: {SURFACE}; border: 1.5px solid {GRAY_200}; border-radius: 16px; }}
QTreeView, QTableView {{
    background: {SURFACE}; alternate-background-color: {GRAY_100}; color: {TEXT};
    border: none; outline: 0;
}}
QFrame#emCard QTreeView {{ background: {SURFACE}; border-radius: 0; }}
QTableView {{ border: 1.5px solid {GRAY_200}; border-radius: 12px; }}
QFrame#emCard QTableView {{ background: {SURFACE}; border: none; border-radius: 0; }}
QTreeView::item, QTableView::item {{ padding: 4px 6px; border: none; }}
QTreeView::item:hover, QTableView::item:hover {{ background: {HOVER_BG}; }}
QTreeView::item:selected, QTableView::item:selected {{ background: {SELECTED_BG}; color: {TEXT}; }}
QTreeView::branch {{ background: transparent; }}
/* Controls living inside table cells: slimmer, so they fit the row. */
QTableView QComboBox, QTableView QLineEdit {{ min-height: 16px; padding: 2px 10px; border-radius: 11px; }}
QTreeView::branch:closed:has-children {{ image: url({right}); }}
QTreeView::branch:open:has-children {{ image: url({down}); }}
QHeaderView {{ background: transparent; }}
/* Transparent: an opaque header paints square corners over the table's rounded border. */
QHeaderView::section {{
    background: transparent; color: {GRAY_500}; font-weight: 600;
    border: none; border-bottom: 1px solid {GRAY_300}; padding: 6px 8px;
}}
QTableCornerButton::section {{ background: transparent; border: none; }}

QPlainTextEdit {{
    background: {GRAY_100}; color: {GRAY_700}; border: 1.5px solid {GRAY_200};
    border-radius: 12px; padding: 6px;
}}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
QScrollBar::handle {{ background: {GRAY_300}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {GRAY_400}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QProgressBar {{
    background: {GRAY_200}; border: none; border-radius: 4px; color: {TEXT};
    text-align: center; min-height: 8px;
}}
/* 1px chunks with no margin = one continuous bar (Painter's style draws spaced blocks). */
QProgressBar::chunk {{ background: {ACCENT}; width: 1px; margin: 0; border-radius: 0; }}
QToolTip {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {GRAY_300};
    padding: 4px 8px; border-radius: 6px;
}}
"""
