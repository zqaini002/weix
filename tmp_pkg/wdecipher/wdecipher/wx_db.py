from __future__ import annotations
import re
import os.path
from tqdm import tqdm
from glob import glob
from loguru import logger
from collections import defaultdict
import hashlib

try:
    from Cryptodome.Cipher import AES
except ImportError:
    from Crypto.Cipher import AES

from typing import List, Dict, Optional
from wdecipher.utils import listify, read_file, remove_file, identity
from wdecipher.utils.sqlite import *

__all__ = [
    "CORE_DB_TYPES",
    "get_wx_dbs",
    "merge_wx_db",
    "decrypt_wx_db",
    "batch_decrypt_wx_db",
]

CORE_DB_TYPES = ["MSG", "MediaMSG", "MicroMsg", "OpenIMContact", "OpenIMMedia", "OpenIMMsg", "Favorite", "PublicMsg"]

def get_wx_dbs(
    wx_dir: str,
    db_types: Optional[str | List[str]] = None,
) -> List[Dict[str, str]]:
    """Returns all databases of the specified type that exist in the specified WeChat
    working directory (all databases are returned by default).

    :param wx_dir: Specifies the WeChat working directory for searching the database.
    :param db_types: Specifies to return a specific type of database.
    :return: List of database path.
    """
    out = []
    db_types = listify(db_types or CORE_DB_TYPES)
    for fname in glob(os.path.join(wx_dir, "**", "*.db"), recursive=True):
        db_type = re.sub(pattern=r"\d*\.db$", repl="", string=os.path.basename(fname))
        if db_type in db_types:
            out.append({"type": db_type, "path": fname})
    return out

# noinspection SqlNoDataSourceInspection,SqlResolve
def merge_wx_db(srcs: List[str], dest: str, verbose: bool = True) -> None:
    """Merges multiple WeChat databases of the same type into one.

    :param srcs: Specifies multiple lists of databases of the same type.
    :param dest: Specify the output path of the merged database.
    :param verbose: Specifies whether to output intermediate log information.
    :return: None.
    """
    # noinspection SqlNoDataSourceInspection,SqlResolve
    def check_create_sync_log(conn: Connection) -> None:
        sql = "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_log'"
        if len(execute_dql(conn, sql)) < 1:
            sql = ("CREATE TABLE sync_log ("
                   "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                   "src_path TEXT NOT NULL, "
                   "tbl_name TEXT NOT NULL, "
                   "src_count INT, "
                   "crt_count INT, "
                   "create_time INT DEFAULT (strftime('%s', 'now')), "
                   "update_time INT DEFAULT (strftime('%s', 'now')))")
            cursor = conn.cursor()
            cursor.execute(sql)
            cursor.execute("CREATE INDEX idx_sync_log_src_path ON sync_log(src_path)")
            cursor.execute("CREATE INDEX idx_sync_log_tbl_name ON sync_log(tbl_name)")
            cursor.execute("CREATE UNIQUE INDEX idx_sync_log_src_tbl ON sync_log(src_path, tbl_name)")
            conn.commit()
            cursor.close()

    conn = get_connection(dest)
    check_create_sync_log(conn)
    to_str = lambda e: e if isinstance(e, str) else e.decode()

    cursor = conn.cursor()
    for i, src_path in enumerate(srcs):
        if verbose:
            logger.info(f"Processing the database {src_path}")
        alias = f"db_{i}"
        cursor.execute(f"ATTACH DATABASE '{src_path}' AS {alias}")
        conn.commit()

        sql = f"SELECT tbl_name, sql FROM {alias}.sqlite_master WHERE type='table' ORDER BY tbl_name;"
        for tbl_name, sql in ((verbose and tqdm) or identity)(execute_dql(conn, sql)):
            tbl_name, sql = to_str(tbl_name), to_str(sql)
            if any((tbl_name == "sqlite_sequence", "create table" not in sql.lower())):
                continue

            columns = execute_dql(conn, sql=f"PRAGMA table_info({tbl_name})")
            columns = [to_str(e[1]) for e in columns]  # got field column names
            if not columns or (tbl_name == "ChatInfo" and len(columns) > 12):
                continue

            # create a table like alias.table
            sql = f"CREATE TABLE IF NOT EXISTS {tbl_name} AS SELECT *  FROM {alias}.{tbl_name} WHERE 0=1"
            cursor.execute(sql)

            # create a unique index to avoid duplicate rows
            index_name = f"{tbl_name}_unique_index"
            coalesce_columns = ",".join(f"COALESCE({column}, '')" for column in columns)
            sql = f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {tbl_name}({coalesce_columns})"
            cursor.execute(sql)

            sql = f"SELECT src_count FROM sync_log WHERE src_path=? AND tbl_name=?"
            src_count = execute_dql(conn, sql, params=(src_path, tbl_name))
            if not src_count:
                sql = "INSERT INTO sync_log(src_path, tbl_name, src_count, crt_count) VALUES (?, ?, ?, ?)"
                cursor.execute(sql, (src_path, tbl_name, 0, 0))
            conn.commit()

            # a simple but potentially ineffective to determine if a table is merged
            log_count = (src_count or ((0,),))[0][0]
            src_count = execute_dql(conn, sql=f"SELECT COUNT(*) FROM {alias}.{tbl_name}")[0][0]
            if src_count and src_count <= log_count:
                logger.info(f"{tbl_name} may have been merged and will be ignored")
                continue

            sql = f"SELECT {','.join([e for e in columns])} FROM {alias}.{tbl_name}"
            if not (data := execute_dql(conn, sql)):
                continue

            try:
                sql = f"INSERT OR IGNORE INTO {tbl_name}({','.join([e for e in columns])}) VALUES({','.join(['?'] * len(columns))})"
                cursor.executemany(sql, data)

                sql = "UPDATE sync_log SET src_count=?, crt_count=? WHERE src_path=? and tbl_name=?"
                cursor.execute(sql, (src_count, log_count + src_count, src_path, tbl_name))
            except Exception as e:
                logger.error(repr(e))

        cursor.execute(f"DETACH DATABASE {alias}")
        conn.commit()

    cursor.close()
    conn.close()

def decrypt_wx_db(key: str, db_path: str, out_path: str) -> None:
    """Decrypt the WeChat database.

    The encryption algorithm used by WeChat database is 256-bit AES-CBC. The default page
    size of the database is 4096 bytes, or 4KB, and each page is encrypted and decrypted
    separately. Each page of the encrypted file has a random initialization vector IV,
    which is saved in the last 16 bytes of each page. The first 16 bytes of each database
    file save a unique and random salt value for HMAC verification and data decryption.
    In order to ensure that the length of the data part is 16 bytes, which is an integer
    multiple of the AES block size, a section of empty bytes will be filled at the end of
    each page, making the length of the reserved field 48 bytes.

    [16(salt)   4032(data)   16(IV)   16(HMAC)   12(empty-filling)]
    [               4048(data)                  48(reserved-words)]

    :param key: Specifies the key for decryption.
    :param db_path: Specifies the path to the decrypted database.
    :param out_path: Specify the output path of the decrypted database.
    :return: None.
    """
    data = read_file(db_path, encoding=None)
    salt = data[:16]
    password = bytes.fromhex(key.strip())
    pk = hashlib.pbkdf2_hmac(hash_name="sha1", password=password, salt=salt, iterations=64000, dklen=32)

    with open(out_path, "wb") as fp:
        fp.write(SQLITE_FILE_HEADER.encode())
        for i in range(0, len(data), 4096):
            data0 = data[i: i + 4096] if i > 0 else data[16: i + 4096]
            fp.write(AES.new(pk, AES.MODE_CBC, data0[-48: -32]).decrypt(data0[:-48]))
            fp.write(data0[-48:])

def batch_decrypt_wx_db(
    key: str,
    dbs: List[Dict[str, str]],
    out_dir: str,
    merge_db: bool = True,
    verbose: bool = True,
) -> None:
    """Batch decryption of WeChat database.

    :param key: Specifies the key for decryption.
    :param dbs: Specifies the database list to the decrypted database.
    :param out_dir: Specify the output directory of the decrypted database.
    :return: None.
    """
    type_mapping = defaultdict(list)
    os.makedirs(out_dir, exist_ok=True)
    if verbose:
        logger.info("Decrypting wechat database")
    for db in ((verbose and tqdm) or identity)(dbs):
        out_path = os.path.join(out_dir, os.path.basename(db["path"]))
        decrypt_wx_db(key, db["path"], out_path)
        type_mapping[db["type"]].append(out_path)

    if merge_db:
        if verbose:
            logger.info("Merging wechat database")
        for db_type, dbs in type_mapping.items():
            if len(dbs) > 1:
                merge_wx_db(dbs, os.path.join(out_dir, f"{db_type}.db"), verbose=verbose)
                remove_file(*dbs)
