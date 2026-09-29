"""Qt interface for the adaptive remesher, split by responsibility:

- `common`: shared styles, dialogs and widgets (tooltip helper, busy
  cursor, collapsible panel, side-by-side comparison).
- `panels`: one `build_xxx_panel(window, panel_layout)` function per
  control-panel section, which builds its widgets, exposes them as
  attributes of `window` (so the rest of `AdaptiveRemesherWindow`'s
  methods keep reading them the same way as before) and wires their
  signals to `window`'s already-existing methods.

No module in this package depends on `remesh_core`: decimation/cleanup
logic lives outside the UI.
"""
