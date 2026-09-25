"""Configuracion de logging de la app: consola + archivo rotativo junto al
script. Sustituye al antiguo flag global `DEBUG` + `print`, para poder
diagnosticar un fallo reportado fuera de la maquina de desarrollo sin
tener que reproducirlo con un depurador. Cada modulo obtiene su logger con
`logging.getLogger(__name__)`; basta llamar a `configure_logging()` una
vez, al arrancar la app, para que todos hereden consola+archivo."""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adaptive_remesher.log")


def configure_logging(verbose: bool = False) -> None:
    """Anade a la raiz un handler de consola (INFO, o DEBUG si `verbose`) y
    uno de archivo rotativo (siempre DEBUG, hasta 3 archivos de 1MB) en
    `LOG_PATH`. No hace nada si ya se habia llamado antes (evita duplicar
    handlers si se invoca mas de una vez, p.ej. en tests)."""
    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s", datefmt="%H:%M:%S")

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        file_handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        # Sin permisos de escritura junto al script (p.ej. instalado en una
        # carpeta de solo lectura): seguir solo con la consola.
        pass
