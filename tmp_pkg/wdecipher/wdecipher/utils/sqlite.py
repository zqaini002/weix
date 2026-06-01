from __future__ import annotations
import sqlite3
from sqlite3 import Connection
from loguru import logger
from typing import Any, Optional

__all__ = [
    "SQLITE_FILE_HEADER",
    "Connection",
    "get_connection",
    "execute_dql",
]

SQLITE_FILE_HEADER = "SQLite format 3\x00"

def get_connection(path: str, **kwargs: Any) -> Connection:
    """Opens a connection to the SQLite database file database.

    :param path: Specifies the path of the database to be opened
    :param kwargs: Specifies additional database open parameters.
    :return: A sqlite3.Connection object.
    """
    return sqlite3.connect(path, **kwargs)

def execute_dql(conn: Connection, sql: str, params: Optional[Any] = None, **kwargs: Any) -> Any:
    """Executes the given DQL SQL statement and returns the result.

    :param conn: Specifies the database connection used to execute sql commands.
    :param sql: Specifies the DQL SQL statement to be executed.
    :param params: Specifies the parameters that need to be put into the SQL statement.
    :param kwargs: Specifies additional parameters to be used when executing a SQL statement.
    :return: The result obtained by executing the SQL statement.
    """
    flag = kwargs.pop("set_text_factory_bytes", False)
    # noinspection PyBroadException
    try:
        if flag is not None:
            conn.text_factory = bytes
        cursor = conn.cursor()
        cursor.execute(sql, params) if params else cursor.execute(sql)
        return cursor.fetchall()
    except Exception as e:
        if flag is None:
            return execute_dql(conn, sql, params, set_text_factory_bytes=True, **kwargs)
        conn.text_factory = str
        logger.error(f"* SQL: {sql} \n * params: {params} \n * kwargs: {kwargs}")
        raise e
