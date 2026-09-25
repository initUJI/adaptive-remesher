"""Interfaz Qt del remesher adaptativo, dividida por responsabilidad:

- `common`: estilos, dialogos y widgets reutilizables (tooltip, cursor de
  espera, panel plegable, comparacion lado a lado).
- `panels`: una funcion `build_xxx_panel(window, panel_layout)` por cada
  seccion del panel de control, que construye sus widgets, los expone como
  atributos de `window` (para que el resto de metodos de
  `AdaptiveRemesherWindow` sigan leyendolos igual que antes) y conecta sus
  senales a los metodos de `window` que ya existian.

Ningun modulo de este paquete depende de `remesh_core`: la logica de
decimacion/limpieza vive fuera de la UI.
"""
