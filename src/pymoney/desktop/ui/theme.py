"""The desktop's look: one palette, shared with the web app.

The colours lean on the Okabe-Ito set -- a deep blue accent, vermillion for
negative amounts, bluish teal where something positive is emphasised -- chosen
to stay apart for the common kinds of colour-blindness. As on the web, colour
is always a *second* cue: a negative amount keeps its minus sign, a chart
keeps its legend.

Everything here is cosmetic. ``apply_theme`` styles an application;
``app_icon`` paints the coin-with-a-rising-line mark the web app wears as its
favicon; ``amount_brush`` is what the tables use to tint a figure. The tests
drive widgets without any of it, and nothing functional may depend on it.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication

__all__ = [
    "ACCENT",
    "INK",
    "NEGATIVE",
    "POSITIVE",
    "amount_brush",
    "app_icon",
    "apply_theme",
]

# The same tokens as frontend/apps/web/src/styles.css, light variant.
ACCENT = "#0b62b8"
ACCENT_STRONG = "#084a8c"
ACCENT_SOFT = "#e8f1fb"
INK = "#122a44"
INK_2 = "#1d4370"
INK_TEXT = "#f2f7fd"
BG = "#f6f7f9"
SURFACE = "#ffffff"
TEXT = "#19212c"
MUTED = "#5d6b7c"
BORDER = "#e3e7ec"
BORDER_STRONG = "#bcc6d2"
ROW_TINT = "#f3f6fa"
NEGATIVE = "#bc4700"  # vermillion, not red: stays apart from the teal
POSITIVE = "#00664f"  # bluish teal
ORANGE = "#e69f00"

# The Okabe-Ito palette, as on the web (frontend .../components/charts.tsx):
# built to stay apart for the common kinds of colour-blindness.
CHART_PALETTE = (
    "#0072b2",  # blue
    "#e69f00",  # orange
    "#009e73",  # bluish green
    "#d55e00",  # vermillion
    "#56b4e9",  # sky blue
    "#cc79a7",  # reddish purple
    "#b0a000",  # yellow, darkened for white backgrounds
    "#6b7280",  # grey
)

STYLE = f"""
QMainWindow, QDialog {{
    background: {BG};
}}
QMenuBar {{
    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 {INK}, stop: 0.6 {INK_2}, stop: 1 {ACCENT});
    color: {INK_TEXT};
    padding: 2px;
}}
QMenuBar::item {{
    background: transparent;
    color: {INK_TEXT};
    padding: 4px 10px;
    border-radius: 4px;
}}
QMenuBar::item:selected {{
    background: rgba(255, 255, 255, 0.18);
}}
QMenu {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 24px 5px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: {ACCENT};
    color: white;
}}
QStatusBar {{
    background: {SURFACE};
    color: {MUTED};
    border-top: 1px solid {BORDER};
}}
QTreeWidget, QTableWidget, QTreeView, QTableView {{
    background: {SURFACE};
    alternate-background-color: {ROW_TINT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    selection-background-color: {ACCENT};
    selection-color: white;
    gridline-color: {BORDER};
}}
QHeaderView::section {{
    background: {ROW_TINT};
    color: {MUTED};
    font-weight: 600;
    border: none;
    border-bottom: 2px solid {BORDER_STRONG};
    border-right: 1px solid {BORDER};
    padding: 4px 8px;
}}
QPushButton {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 14px;
}}
QPushButton:hover {{
    background: {ROW_TINT};
}}
QPushButton:default {{
    background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 {ACCENT}, stop: 1 {ACCENT_STRONG});
    color: white;
    border-color: {ACCENT_STRONG};
    font-weight: 600;
}}
QPushButton:default:hover {{
    background: {ACCENT};
}}
QPushButton:disabled {{
    color: {MUTED};
    background: {BG};
}}
QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 3px 6px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus {{
    border: 2px solid {ACCENT};
    padding: 2px 5px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QCheckBox {{
    color: {TEXT};
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    background: {SURFACE};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT_STRONG};
    image: none;
}}
QLabel {{
    color: {TEXT};
}}
QSplitter::handle {{
    background: {BORDER};
    width: 2px;
}}
QToolTip {{
    background: {INK};
    color: {INK_TEXT};
    border: none;
    padding: 4px 8px;
}}
QScrollBar:vertical {{
    background: {BG};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {BG};
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_STRONG};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
"""


def amount_brush(minor: int) -> QBrush | None:
    """How a signed figure is tinted: vermillion below zero, nothing otherwise.

    The minus sign is already in the text; the colour only repeats it, so a
    reader who cannot tell the hue loses nothing.
    """
    return QBrush(QColor(NEGATIVE)) if minor < 0 else None


def app_icon(size: int = 64) -> QIcon:
    """The coin with a rising line, painted fresh: no asset file to ship."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 32.0

    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#2f8bdd"))
    gradient.setColorAt(1.0, QColor(ACCENT))
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(16 * s, 16 * s), 15 * s, 15 * s)

    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(2.6 * s)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    line = QPolygonF(
        [
            QPointF(8 * s, 20.5 * s),
            QPointF(13 * s, 15 * s),
            QPointF(16.5 * s, 18 * s),
            QPointF(24 * s, 10.5 * s),
        ]
    )
    painter.drawPolyline(line)
    painter.drawPolyline(
        QPolygonF(
            [
                QPointF(20.5 * s, 10.5 * s),
                QPointF(24 * s, 10.5 * s),
                QPointF(24 * s, 14 * s),
            ]
        )
    )

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(ORANGE))
    painter.drawEllipse(QPointF(10.5 * s, 23 * s), 1.6 * s, 1.6 * s)
    painter.end()
    return QIcon(pixmap)


def apply_theme(app: QApplication) -> None:
    """Fusion + the palette + the stylesheet: the whole look, in one call."""
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(app_icon())
