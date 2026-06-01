from __future__ import annotations
import os
import json
from functools import wraps, partial
from typing import Optional, Any, List, Iterable, Callable

__all__ = [
    "read_file",
    "get_filename",
    "remove_file",
    "read_json",
    "listify",
    "identity",
    "check_condition",
    "is_windows",
    "win_env_checker"
]

def read_file(path: str, encoding: Optional[str] = "utf-8", count: int = -1) -> str | bytes:
    """Read a file and return its contents.

    :param path: Specifies the path of the file to be read.
    :param encoding: Specifies whether to decode the file contents (None means no decoding).
    :param count: Specifies the number of bytes to read.
    :return: The content of the file.
    """
    with open(path, mode="r" if encoding else "rb", encoding=encoding) as fp:
        return fp.read(count)

def get_filename(path: str) -> str:
    """Returns the file name (without extension) for a given file path.

    :param path: Specifies the path to get the file name.
    :return: The filename of a path.
    """
    return os.path.basename(os.path.splitext(path)[0])

def remove_file(*paths: str) -> None:
    """Deletes one or more files.

    :param paths: Specifies the file to be deleted.
    :return: None.
    """
    for path in listify(paths):
        os.remove(path)

def read_json(path: str, **kwargs: Any) -> Any:
    """Read json file as python object.

    :param path: Specifies the path of the json file to be read.
    :param kwargs: Specify additional parameter configuration for data reading.
    :return: Python object storing json data.
    """
    with open(path, mode="r", encoding=kwargs.pop("encoding", "utf-8")) as fp:
        return json.load(fp, **kwargs)

def listify(obj: Any) -> List[Any]:
    """Convert any Python object to a list type. If the converted object is an iterable
    type other than str, all elements in it are put into a new list and returned. Otherwise,
    the object is put into a new list and returned.

    :param obj: Specifies the object to be converted.
    :return: The converted list object.
    """
    if isinstance(obj, list):
        return obj
    if isinstance(obj, Iterable) and not isinstance(obj, str):
        return list(obj)
    return [obj]

def identity(*args: Any) -> Any:
    """Equivalent function, that is, directly returns the input content. If the input
    parameter is greater than 1, this returns the parameter tuple, otherwise it directly
    returns the parameter content itself.

    :param args: Specifies the parameters to be returned.
    :return: A tuple or a parameter content itself.
    """
    return args if len(args) > 1 else args[0]

def is_windows() -> bool:
    """Returns whether the current operating system is Windows.

    :return: True if the current platform is Windows, False otherwise.
    """
    return os.name == "nt"

def check_condition(c: bool | Callable[[], bool], msg: Optional[str] = None) -> Callable:
    """Asserts whether a given condition is true.

    :param c: Specifies the conditional judgment.
    :param msg: Specifies a message to be displayed when the condition is not met.
    :return: A wrapper function that checks if the condition is true.
    """
    c = ((isinstance(c, Callable) and (c(),)) or (c,))[0]

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            assert c, msg.format(func_name=func.__name__) or "the condition is not met"
            return func(*args, **kwargs)
        return wrapper
    return decorator

# checker used to check whether the function is running on Windows
win_env_checker = partial(
    check_condition,
    c=is_windows,
    msg="the function {func_name} only works on Windows"
)
