import logging
import sys


def setup_logging(level: int = logging.INFO):
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    handler.setLevel(level)

    root = logging.getLogger()
    preserved_handlers = [
        h for h in root.handlers
        if getattr(h, "_weix_qt_handler", False)
    ]
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    for preserved in preserved_handlers:
        preserved.setLevel(level)
        root.addHandler(preserved)

    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
