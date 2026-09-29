"""App logging configuration: console + rotating file alongside the
script. Replaces the old global `DEBUG` flag + `print`, so a failure
reported outside the development machine can be diagnosed without
having to reproduce it with a debugger. Each module gets its logger with
`logging.getLogger(__name__)`; it's enough to call `configure_logging()`
once, at app startup, for all of them to inherit console+file."""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adaptive_remesher.log")


def configure_logging(verbose: bool = False) -> None:
    """Adds a console handler to the root logger (INFO, or DEBUG if
    `verbose`) and a rotating file handler (always DEBUG, up to 3 files
    of 1MB) at `LOG_PATH`. Does nothing if already called before (avoids
    duplicating handlers if invoked more than once, e.g. in tests)."""
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
        # No write permission next to the script (e.g. installed in a
        # read-only folder): continue with console only.
        pass
