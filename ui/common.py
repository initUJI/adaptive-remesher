"""Utilities and widgets shared by the different panels: the stylesheet,
the wait cursor, the HTML tooltip, the collapsible panel and the
side-by-side comparison dialog."""

from contextlib import contextmanager

import pyvista as pv
from PySide6.QtWidgets import QApplication, QDialog, QGroupBox, QVBoxLayout
from PySide6.QtCore import Qt
from pyvistaqt import QtInteractor

STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1c1d22;
    color: #e4e6eb;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
}
QLabel#headerLabel {
    color: #ffffff;
    font-size: 17px;
    font-weight: 600;
    padding: 2px 0 10px 0;
}
QGroupBox {
    border: 1px solid #33353d;
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
    color: #8fb2ff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
QGroupBox::indicator { width: 0px; height: 0px; }
QPushButton {
    background-color: #2a2c33;
    border: 1px solid #3d3f48;
    border-radius: 6px;
    padding: 7px 12px;
    color: #e4e6eb;
}
QPushButton:hover { border-color: #5b7cfa; }
QPushButton:pressed { background-color: #23252b; }
QPushButton:checked {
    background-color: #3355cc;
    border-color: #5b7cfa;
    color: white;
}
QPushButton#eraseButton:checked {
    background-color: #b23a3a;
    border-color: #ff6b6b;
}
QPushButton#primaryButton {
    background-color: #2f8f5b;
    border-color: #3fb875;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background-color: #37a568; }
QCheckBox { spacing: 8px; padding: 2px 0; }
QSlider::groove:horizontal {
    height: 4px;
    background: #33353d;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #5b7cfa;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal { background: #4a63c9; border-radius: 2px; }
QSpinBox {
    background-color: #2a2c33;
    border: 1px solid #3d3f48;
    border-radius: 4px;
    padding: 2px 4px;
    min-width: 46px;
}
QToolTip {
    background-color: #2a2c33;
    color: #e4e6eb;
    border: 1px solid #5b7cfa;
    border-radius: 4px;
    padding: 6px 8px;
}
QComboBox {
    background-color: #2a2c33;
    border: 1px solid #3d3f48;
    border-radius: 4px;
    padding: 4px 8px;
}
QComboBox:hover { border-color: #5b7cfa; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background-color: #2a2c33;
    color: #e4e6eb;
    selection-background-color: #3355cc;
    border: 1px solid #3d3f48;
    outline: none;
}
QSplitter::handle {
    background-color: #33353d;
}
QSplitter::handle:hover { background-color: #5b7cfa; }
QScrollArea { border: none; }
QScrollBar:vertical {
    background: #1c1d22;
    width: 12px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3d3f48;
    border-radius: 6px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #5b7cfa; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""


@contextmanager
def busy_cursor():
    """Shows the wait cursor during a potentially long operation (cleanup
    or decimation), so the interface doesn't appear frozen."""
    QApplication.setOverrideCursor(Qt.WaitCursor)
    QApplication.processEvents()
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


def tip(text: str) -> str:
    """Wraps a tooltip text in simple HTML so Qt wraps it across several lines with a readable max width."""
    return f"<div style='max-width:280px'>{text}</div>"


class CollapsibleGroupBox(QGroupBox):
    """QGroupBox whose title acts as a switch: a click collapses or expands
    it. Collapsing only hides its child widgets (they still exist and keep
    their value); this lets many sections fit in the panel without forcing
    constant scrolling through groups that aren't of interest right now."""

    def __init__(self, title: str, parent=None, expanded: bool = True):
        super().__init__(parent)
        self._base_title = title
        self.setCheckable(True)
        self.setChecked(expanded)
        self._apply_expanded(expanded)
        self.toggled.connect(self._apply_expanded)

    def _apply_expanded(self, expanded: bool):
        """Prepends an arrow (expanded/collapsed) to the title and shows or
        hides the direct child widgets of the group's layout."""
        arrow = "▾" if expanded else "▸"
        self.setTitle(f"{arrow} {self._base_title}")
        layout = self.layout()
        if layout is not None:
            for i in range(layout.count()):
                item = layout.itemAt(i)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setVisible(expanded)


class ComparisonDialog(QDialog):
    """Side-by-side comparison window: original mesh on the left, decimated
    result on the right, with both viewport cameras synchronized (orbiting/
    zooming in one moves the other the same way)."""

    def __init__(self, original: pv.PolyData, decimated: pv.PolyData, parent=None):
        """Builds the dialog with two sub-views of the same QtInteractor and frames the camera on the original."""
        super().__init__(parent)
        self.setWindowTitle("Compare original vs. decimated")
        self.resize(1100, 650)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plotter = QtInteractor(self, shape=(1, 2))
        # pyvista wraps these BasePlotter methods with a decorator that the
        # type stubs don't reflect well: mypy sees them as if they expected
        # a positional BasePlotter instead of already being bound to self.plotter.
        self.plotter.set_background("#24262c")  # type: ignore[arg-type]
        layout.addWidget(self.plotter.interactor)

        self.plotter.subplot(0, 0)
        self.plotter.add_mesh(original, show_edges=True, color="#d8dadd", edge_color="#101114")
        self.plotter.add_text(
            f"Original: {original.n_points} vert / {original.n_cells} tris",
            color="white",
            font_size=10,
        )

        self.plotter.subplot(0, 1)
        self.plotter.add_mesh(decimated, show_edges=True, color="#d8dadd", edge_color="#101114")
        self.plotter.add_text(
            f"Decimated: {decimated.n_points} vert / {decimated.n_cells} tris",
            color="white",
            font_size=10,
        )

        self.plotter.link_views()
        self.plotter.subplot(0, 0)
        self.plotter.reset_camera()  # type: ignore[call-arg]
