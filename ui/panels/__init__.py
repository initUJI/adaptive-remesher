"""Funciones `build_xxx_panel(window, panel_layout)`, una por seccion del
panel de control. Cada una crea sus widgets, los deja accesibles como
atributos de `window` y conecta sus senales a los metodos que ya existen en
`AdaptiveRemesherWindow` (en `app.py`)."""

from .actions import build_action_buttons
from .batch import build_batch_panel
from .clean import build_clean_panel
from .decimation import build_decimation_panel
from .header import build_header
from .presets import build_presets_panel
from .protection import build_protection_panel
from .validation import build_validation_panel
from .view import build_view_panel

__all__ = [
    "build_action_buttons",
    "build_batch_panel",
    "build_clean_panel",
    "build_decimation_panel",
    "build_header",
    "build_presets_panel",
    "build_protection_panel",
    "build_validation_panel",
    "build_view_panel",
]
