"""AI call counter for dashboard metrics."""

_counter = 0


def increment() -> None:
    global _counter
    _counter += 1


def get_count() -> int:
    return _counter


def reset() -> None:
    global _counter
    _counter = 0
